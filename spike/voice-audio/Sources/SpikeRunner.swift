import AVFoundation
import CoreLocation
import Foundation
import UIKit

/// The spike proper: run the app's own background-location configuration, and
/// try to speak from the location callback on a fixed cadence.
///
/// Deliberately a copy of `LocationManager`'s setup rather than an import of
/// it — same authorization level (when-in-use), same
/// `allowsBackgroundLocationUpdates`, same indicator, same accuracy, same
/// `pausesLocationUpdatesAutomatically = false`. If those differ the answer
/// does not transfer.
@MainActor
final class SpikeRunner: NSObject, CLLocationManagerDelegate, AVSpeechSynthesizerDelegate {
    static let shared = SpikeRunner()

    private let manager = CLLocationManager()
    private let synth = AVSpeechSynthesizer()
    private let log = SpikeLog.shared

    /// Seconds between attempted utterances.
    private let cadence: TimeInterval = 12
    private var lastSpokenAt: Date = .distantPast
    private var counter = 0
    private var startedSpeakingAt: Date?
    private var fixCount = 0
    /// So a fix stream that dies while backgrounded is visible as a gap rather
    /// than mistaken for "audio failed".
    private var lastFixLoggedAt: Date = .distantPast

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        manager.distanceFilter = kCLDistanceFilterNone
        manager.activityType = .automotiveNavigation
        manager.pausesLocationUpdatesAutomatically = false
        synth.delegate = self
        observeSession()
    }

    var declaredModes: [String] {
        (Bundle.main.object(forInfoDictionaryKey: "UIBackgroundModes") as? [String]) ?? []
    }

    func start() {
        log.log("start", SpikeLog.snapshot().merging([
            "modes": declaredModes.joined(separator: ","),
            "bundle": Bundle.main.bundleIdentifier ?? "?",
            "auth": String(describing: manager.authorizationStatus.rawValue),
        ]) { a, _ in a })
        manager.requestWhenInUseAuthorization()
        if declaredModes.contains("location") {
            manager.allowsBackgroundLocationUpdates = true
            manager.showsBackgroundLocationIndicator = true
        }
        manager.startUpdatingLocation()
    }

    // MARK: - Location

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateLocations locations: [CLLocation]) {
        MainActor.assumeIsolated {
            guard let fix = locations.last else { return }
            fixCount += 1
            // One line per second would drown the file; one every 5 s is enough
            // to prove the stream is alive.
            if Date().timeIntervalSince(lastFixLoggedAt) >= 5 {
                lastFixLoggedAt = Date()
                log.log("fix", SpikeLog.snapshot().merging([
                    "n": fixCount, "speed": fix.speed, "acc": fix.horizontalAccuracy,
                ]) { a, _ in a })
            }
            guard Date().timeIntervalSince(lastSpokenAt) >= cadence else { return }
            lastSpokenAt = Date()
            speak()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didChangeAuthorization: Void) {}

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        MainActor.assumeIsolated {
            log.log("auth", ["auth": manager.authorizationStatus.rawValue])
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        MainActor.assumeIsolated { log.log("locFail", errorFields(error)) }
    }

    // MARK: - Speaking

    /// One utterance, with the session activated around it and torn down after
    /// — the shape the plan proposes, so the spike measures the real thing.
    private func speak() {
        counter += 1
        let session = AVAudioSession.sharedInstance()
        var fields = SpikeLog.snapshot()
        fields["n"] = counter

        do {
            try session.setCategory(.playback, mode: .voicePrompt,
                                    options: [.duckOthers,
                                              .interruptSpokenAudioAndMixWithOthers])
            fields["setCategory"] = "ok"
        } catch {
            fields["setCategory"] = "throw"
            fields.merge(errorFields(error)) { a, _ in a }
        }
        do {
            try session.setActive(true)
            fields["setActive"] = "ok"
        } catch {
            fields["setActive"] = "throw"
            fields.merge(errorFields(error)) { a, _ in a }
        }

        let utterance = AVSpeechUtterance(string:
            "Announcement \(counter). In four hundred metres, turn right onto Morton Street.")
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        startedSpeakingAt = nil
        synth.speak(utterance)
        fields["isSpeakingAfterCall"] = synth.isSpeaking
        log.log("speakCalled", fields)

        // If nothing has started within 2 s, the utterance was swallowed. That
        // negative is the finding, so it has to be recorded rather than
        // inferred from an absent line.
        let n = counter
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self, self.startedSpeakingAt == nil else { return }
            self.log.log("noStartWithin2s", SpikeLog.snapshot().merging([
                "n": n, "isSpeaking": self.synth.isSpeaking,
            ]) { a, _ in a })
        }
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer,
                                       didStart utterance: AVSpeechUtterance) {
        MainActor.assumeIsolated {
            startedSpeakingAt = Date()
            log.log("didStart", SpikeLog.snapshot().merging(["n": counter]) { a, _ in a })
        }
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        MainActor.assumeIsolated {
            // The duration is the tell. A renderer that actually produced audio
            // takes about as long as the sentence; one that was dropped
            // finishes instantly or never fires at all.
            let spoken = startedSpeakingAt.map { Date().timeIntervalSince($0) } ?? -1
            log.log("didFinish", SpikeLog.snapshot().merging([
                "n": counter, "spokenSeconds": (spoken * 1000).rounded() / 1000,
            ]) { a, _ in a })
            deactivate()
        }
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        MainActor.assumeIsolated {
            log.log("didCancel", ["n": counter])
            deactivate()
        }
    }

    /// Hand the session back so the driver's music comes out of its duck. A nav
    /// app that holds an active playback session suppresses other audio for the
    /// whole drive.
    private func deactivate() {
        do {
            try AVAudioSession.sharedInstance()
                .setActive(false, options: .notifyOthersOnDeactivation)
            log.log("deactivate", ["result": "ok"])
        } catch {
            log.log("deactivate", errorFields(error).merging(["result": "throw"]) { a, _ in a })
        }
    }

    // MARK: - Session and lifecycle notifications

    private func observeSession() {
        let centre = NotificationCenter.default
        centre.addObserver(forName: AVAudioSession.interruptionNotification,
                           object: nil, queue: .main) { [weak self] note in
            let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt ?? 99
            let opts = note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            MainActor.assumeIsolated {
                self?.log.log("interruption", ["type": raw, "options": opts])
            }
        }
        centre.addObserver(forName: AVAudioSession.routeChangeNotification,
                           object: nil, queue: .main) { [weak self] note in
            let reason = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt ?? 99
            MainActor.assumeIsolated {
                self?.log.log("routeChange", ["reason": reason])
            }
        }
        centre.addObserver(forName: AVAudioSession.silenceSecondaryAudioHintNotification,
                           object: nil, queue: .main) { [weak self] note in
            let type = note.userInfo?[AVAudioSessionSilenceSecondaryAudioHintTypeKey] as? UInt ?? 99
            MainActor.assumeIsolated {
                self?.log.log("silenceHint", ["type": type])
            }
        }
        for (name, label) in [
            (UIApplication.didEnterBackgroundNotification, "didEnterBackground"),
            (UIApplication.willEnterForegroundNotification, "willEnterForeground"),
            (UIApplication.didBecomeActiveNotification, "didBecomeActive"),
            (UIApplication.willResignActiveNotification, "willResignActive"),
            (UIApplication.protectedDataWillBecomeUnavailableNotification, "lockedProbably"),
            (UIApplication.protectedDataDidBecomeAvailableNotification, "unlockedProbably"),
        ] {
            centre.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    self?.log.log(label, SpikeLog.snapshot())
                }
            }
        }
    }
}
