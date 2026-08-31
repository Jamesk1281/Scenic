import AVFoundation
import Foundation
import UIKit

/// Append-only JSONL log written straight to Documents.
///
/// A file and not `os_log`, because the whole point of this spike is what
/// happens while the phone is locked and nothing is attached to a console.
/// File protection is forced to `.none` so a write during a locked-device
/// wakeup cannot fail for a reason that has nothing to do with audio.
final class SpikeLog {
    static let shared = SpikeLog()

    let url: URL
    private let queue = DispatchQueue(label: "spike.log")
    private var handle: FileHandle?
    private let t0 = Date()

    private init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        url = docs.appendingPathComponent("spike-log.jsonl")
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil,
                                           attributes: [.protectionKey: FileProtectionType.none])
        }
        try? (url as NSURL).setResourceValue(URLFileProtection.none, forKey: .fileProtectionKey)
        handle = try? FileHandle(forWritingTo: url)
        _ = try? handle?.seekToEnd()
    }

    /// Everything about the process that might explain an audio failure,
    /// captured at the instant of the event rather than reconstructed after.
    @MainActor
    static func snapshot() -> [String: Any] {
        let app = UIApplication.shared
        let session = AVAudioSession.sharedInstance()
        let state: String
        switch app.applicationState {
        case .active: state = "active"
        case .inactive: state = "inactive"
        case .background: state = "background"
        @unknown default: state = "unknown"
        }
        return [
            "appState": state,
            "protectedData": app.isProtectedDataAvailable,
            "brightness": UIScreen.main.brightness,
            "otherAudio": session.isOtherAudioPlaying,
            "silenceHint": session.secondaryAudioShouldBeSilencedHint,
            "outVolume": session.outputVolume,
            "category": session.category.rawValue,
            "route": session.currentRoute.outputs.map(\.portType.rawValue).joined(separator: ","),
        ]
    }

    func log(_ event: String, _ fields: [String: Any] = [:]) {
        let stamp = Date()
        let elapsed = stamp.timeIntervalSince(t0)
        queue.async { [weak self] in
            guard let self else { return }
            var row: [String: Any] = fields
            row["t"] = ISO8601DateFormatter().string(from: stamp)
            row["dt"] = (elapsed * 1000).rounded() / 1000
            row["event"] = event
            guard let data = try? JSONSerialization.data(withJSONObject: row,
                                                         options: [.sortedKeys]) else { return }
            self.handle?.write(data)
            self.handle?.write(Data("\n".utf8))
            try? self.handle?.synchronize()
            NSLog("SPIKE %@", String(data: data, encoding: .utf8) ?? event)
        }
    }
}

/// An `Error` reduced to the two things that identify it in a log.
func errorFields(_ error: Error) -> [String: Any] {
    let ns = error as NSError
    var out: [String: Any] = ["errDomain": ns.domain, "errCode": ns.code,
                              "errDesc": ns.localizedDescription]
    // AVAudioSession error codes are four-character codes; the string form is
    // the only readable version ('!pla' = cannot start playing).
    if ns.domain == NSOSStatusErrorDomain || ns.domain == "NSOSStatusErrorDomain" {
        out["errFourCC"] = fourCC(ns.code)
    }
    out["errFourCC"] = fourCC(ns.code)
    return out
}

func fourCC(_ code: Int) -> String {
    let value = UInt32(truncatingIfNeeded: code)
    let bytes = [UInt8((value >> 24) & 0xff), UInt8((value >> 16) & 0xff),
                 UInt8((value >> 8) & 0xff), UInt8(value & 0xff)]
    guard bytes.allSatisfy({ $0 >= 0x20 && $0 < 0x7f }) else { return "" }
    return String(bytes: bytes, encoding: .ascii) ?? ""
}
