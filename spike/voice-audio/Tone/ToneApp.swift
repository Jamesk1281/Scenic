import AVFoundation
import SwiftUI

/// Stands in for "music playing from another app": a separate process holding
/// a long-form playback session, so the spike's ducking behaviour is measured
/// against a real competitor rather than against nothing.
@main
struct ToneApp: App {
    init() { Tone.shared.start() }
    var body: some Scene {
        WindowGroup {
            VStack {
                Text("Tone").font(.largeTitle.bold())
                Text("Looping A440, .playback / .longFormAudio")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
    }
}

final class Tone {
    static let shared = Tone()
    private var player: AVAudioPlayer?

    func start() {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .default, policy: .longFormAudio)
        try? session.setActive(true)
        guard let url = writeTone() else { return }
        player = try? AVAudioPlayer(contentsOf: url)
        player?.numberOfLoops = -1
        player?.volume = 0.6
        player?.play()
        NSLog("TONE playing=%d", player?.isPlaying == true ? 1 : 0)
    }

    /// A 5-second 440 Hz WAV written at launch, so the app carries no asset.
    private func writeTone() -> URL? {
        let rate = 44_100.0, seconds = 5.0
        let frames = Int(rate * seconds)
        var samples = [Int16](repeating: 0, count: frames)
        for i in 0..<frames {
            let v = sin(2 * Double.pi * 440 * Double(i) / rate)
            samples[i] = Int16(v * 12_000)
        }
        var data = Data()
        func le(_ n: UInt32) { withUnsafeBytes(of: n.littleEndian) { data.append(contentsOf: $0) } }
        func le16(_ n: UInt16) { withUnsafeBytes(of: n.littleEndian) { data.append(contentsOf: $0) } }
        let bytes = UInt32(frames * 2)
        data.append(contentsOf: Array("RIFF".utf8)); le(36 + bytes)
        data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8)); le(16); le16(1); le16(1)
        le(UInt32(rate)); le(UInt32(rate) * 2); le16(2); le16(16)
        data.append(contentsOf: Array("data".utf8)); le(bytes)
        samples.withUnsafeBufferPointer { data.append(Data(buffer: $0)) }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("tone.wav")
        try? data.write(to: url)
        return url
    }
}
