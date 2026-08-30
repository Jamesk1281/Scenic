import AVFoundation
import Foundation

/// Which voices are offered for spoken guidance, and which are quietly not.
///
/// iOS ships 25 English voices and **they are not interchangeable for
/// navigation**. Measured on 2026-08-30 by offline synthesis of "Turn right
/// onto Morton Street":
///
/// | voice | seconds |
/// |---|---|
/// | Karen, Samantha, Daniel, Moira, Rishi | 1.69 – 1.80 |
/// | Fred, Kathy, Junior, Ralph, Tessa, Whisper | 1.84 – 1.98 |
/// | Boing, Albert, Bahh | 2.15 – 2.38 |
/// | Cellos, Organ | 2.90 – 3.26 |
/// | Bad News, Jester, Bells | 3.79 – 4.91 |
/// | **Good News** | **6.15** |
///
/// The sixteen real speech voices sit within ±9% of the 1.83 s median the
/// announcement thresholds were sized on, so they are interchangeable and the
/// schedule holds. The six at the bottom are legacy MacinTalk novelties that
/// *sing* the sentence: "Good News" takes 6.15 s to say something that has to
/// be finished three seconds before the driver reaches the junction, so fired
/// at `VoiceGuide.finalAt` it lands after the turn, every time.
///
/// Three things follow, and they are here rather than in `VoiceGuide`:
///
/// - **Provenance decides what is offered.** The modern voices live under
///   `com.apple.voice.*` and the legacy MacinTalk bag under
///   `com.apple.speech.synthesis.voice.*`, and on the measured runtime that
///   split is exact: six real voices on one side, nineteen novelties on the
///   other. This is a namespace rather than a list of names, so it keeps
///   working when Apple ships another voice, and it takes in the enhanced and
///   premium voices a driver downloads for themselves — which are the ones
///   actually worth using.
/// - **Duration is the second gate, because provenance cannot see speed.** It
///   is what excludes a future voice that is well-behaved but too slow. What it
///   *cannot* do is tell a sheep from a slow premium voice: "Bahh" and a
///   hypothetical 2.4 s premium Ava measure the same. Hence both gates.
/// - **What survives still shifts the thresholds.** `VoiceGuide` starts the
///   words earlier by however much longer the chosen voice takes, so the
///   clearance after them is the same whichever is picked.
enum VoiceCatalogue {

    /// One voice, and how long it takes to say a maneuver.
    struct Measured: Identifiable, Equatable {
        let identifier: String
        let name: String
        let language: String
        /// Seconds to speak `reference`, by offline synthesis.
        let seconds: TimeInterval
        /// "Enhanced" or "Premium" for a voice the driver downloaded in
        /// Settings › Accessibility › Spoken Content › Voices, empty for one
        /// that ships with the phone. Worth surfacing: the app cannot install
        /// those, but they sound markedly better and it should offer them the
        /// moment they appear.
        let quality: String

        var id: String { identifier }
        var isDownloaded: Bool { !quality.isEmpty }
        var voice: AVSpeechSynthesisVoice? { AVSpeechSynthesisVoice(identifier: identifier) }

        /// What the picker shows. The language only when it is not the default
        /// one, so a list of American voices is not a column of "en-US".
        var label: String {
            var out = name
            if language != "en-US" { out += " (\(language))" }
            if !quality.isEmpty { out += " · \(quality)" }
            return out
        }
    }

    /// The sentence everything is measured against — the median shape of a real
    /// instruction, from the corpus in `docs/voice-guidance-plan.md` §2.
    static let reference = "Turn right onto Morton Street"

    /// The longest a maneuver may take to say and still be worth offering.
    ///
    /// Not the point at which a voice arrives late — `VoiceGuide` shifts its
    /// thresholds by however long the chosen voice actually takes, so nothing
    /// in this list can be late. It is the point at which shifting them stops
    /// being worth it: firing earlier means more legs too short to fit an
    /// announcement at all, and on this route profile that gets expensive fast.
    ///
    /// 2.6 s sits in the gap the measurement found. Every genuine speech voice
    /// came in at 1.98 s or better, leaving 30% of headroom for a slower
    /// enhanced or premium voice downloaded on a real phone — which the
    /// simulator has none of, so this is the one number here that a device
    /// could still move. Everything excluded is at 2.90 s or worse, and every
    /// one of those is a legacy novelty that *sings* the sentence.
    static let budget: TimeInterval = 2.6

    /// Namespaces a navigation voice may come from.
    ///
    /// `com.apple.voice.*` covers compact, enhanced and premium — everything
    /// modern, including anything downloaded in Settings. `com.apple.ttsbundle.*`
    /// is the older name for the same family and is kept so an upgraded phone
    /// does not silently lose its list.
    ///
    /// What this deliberately excludes is `com.apple.speech.synthesis.voice.*`,
    /// the MacinTalk bag Apple keeps for compatibility: Albert, Bahh, Boing,
    /// Bubbles, Zarvox, Trinoids, Whisper, Bells, Cellos, Organ, Jester, Good
    /// News, Bad News. Some of them are fast enough — "Bubbles" comes in at
    /// 1.84 s — and none of them belong in a car.
    static let namespaces = ["com.apple.voice.", "com.apple.ttsbundle."]

    // MARK: - Selection

    private static let selectionKey = "voice.identifier"

    /// The driver's chosen voice, or nil for the system default.
    ///
    /// Stored as an identifier rather than an `AVSpeechSynthesisVoice`, which
    /// is not archivable, and resolved on every read so a voice deleted from
    /// Settings since it was chosen falls back to the default instead of
    /// leaving the app mute.
    static var selectedIdentifier: String? {
        get { UserDefaults.standard.string(forKey: selectionKey) }
        set { UserDefaults.standard.set(newValue, forKey: selectionKey) }
    }

    static func selectedVoice() -> AVSpeechSynthesisVoice? {
        guard let identifier = selectedIdentifier,
              let voice = AVSpeechSynthesisVoice(identifier: identifier) else {
            return AVSpeechSynthesisVoice(language: "en-US")
        }
        return voice
    }

    /// The measured duration of the selected voice, if it has been measured.
    ///
    /// Synchronous, so `VoiceGuide` can size its thresholds at construction
    /// without waiting on a measurement pass that may not have run yet. Nil
    /// falls back to the reference, which is right: an unmeasured voice is
    /// almost certainly one of the sixteen that sit within 9% of it.
    static func cachedSecondsForSelection() -> TimeInterval? {
        guard let identifier = selectedIdentifier else { return nil }
        return cache[identifier]
    }

    // MARK: - Measuring

    private static let cacheKey = "voice.durations"

    /// Cached measurements, by identifier. Voices do not change speed between
    /// launches, and measuring 25 of them costs about a second.
    private static var cache: [String: Double] {
        get { UserDefaults.standard.dictionary(forKey: cacheKey) as? [String: Double] ?? [:] }
        set { UserDefaults.standard.set(newValue, forKey: cacheKey) }
    }

    /// Every English voice installed, measured, slowest excluded.
    ///
    /// Call from a task rather than inline: the first run synthesises each
    /// voice once. Subsequent runs read the cache and return immediately.
    @MainActor
    static func usable() async -> [Measured] {
        var english = AVSpeechSynthesisVoice.speechVoices()
            .filter { $0.language.hasPrefix("en") }
        let modern = english.filter { voice in
            namespaces.contains { voice.identifier.hasPrefix($0) }
        }
        // Falling back rather than shipping an empty picker: if a future
        // runtime renames the namespace, a list of novelty voices is a worse
        // outcome than no list, but not by as much as no voice at all.
        if !modern.isEmpty { english = modern }

        var durations = cache
        var out: [Measured] = []
        for voice in english {
            let seconds: Double
            if let known = durations[voice.identifier] {
                seconds = known
            } else {
                seconds = await duration(of: voice)
                durations[voice.identifier] = seconds
                // Yield between voices so a first run cannot stutter the map.
                await Task.yield()
            }
            guard seconds > 0, seconds <= budget else { continue }
            let quality: String
            switch voice.quality {
            case .enhanced: quality = "Enhanced"
            case .premium: quality = "Premium"
            default: quality = ""
            }
            out.append(Measured(identifier: voice.identifier, name: voice.name,
                                language: voice.language, seconds: seconds,
                                quality: quality))
        }
        cache = durations
        // Best first, then alphabetically — a downloaded enhanced voice is the
        // one the driver went out of their way to install.
        return out.sorted {
            ($0.isDownloaded ? 0 : 1, $0.name) < ($1.isDownloaded ? 0 : 1, $1.name)
        }
    }

    /// How long this voice takes to say `reference`, in seconds.
    ///
    /// `write` renders to buffers without touching the audio session, so this
    /// measures a voice without making a sound and without disturbing whatever
    /// the driver is listening to. Returns 0 if the renderer produces nothing,
    /// which is how a voice that is listed but not actually installed shows up.
    @MainActor
    static func duration(of voice: AVSpeechSynthesisVoice) async -> TimeInterval {
        let utterance = AVSpeechUtterance(string: reference)
        utterance.voice = voice
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate

        // Held for the duration of the call: `write` is asynchronous and a
        // synthesiser released while it is still producing buffers takes the
        // callback with it.
        let synthesizer = AVSpeechSynthesizer()
        var frames = 0.0
        var sampleRate = 22_050.0
        var resumed = false

        return await withCheckedContinuation { continuation in
            synthesizer.write(utterance) { buffer in
                guard let pcm = buffer as? AVAudioPCMBuffer else { return }
                guard pcm.frameLength > 0 else {
                    // The empty buffer is the end marker, and it can arrive
                    // more than once for a voice that produced nothing.
                    guard !resumed else { return }
                    resumed = true
                    continuation.resume(returning: frames / sampleRate)
                    return
                }
                sampleRate = pcm.format.sampleRate
                frames += Double(pcm.frameLength)
            }
        }
    }
}
