import AVFoundation
import CoreLocation
import Foundation

/// Spoken turn-by-turn guidance.
///
/// Two pieces: `Speaker`, which owns the audio session and the synthesiser and
/// knows nothing about routes, and `VoiceGuide`, which decides what to say and
/// when and has no idea how sound is made. The seam is there so the schedule —
/// the part with all the judgement in it — can be tested without a phone.
///
/// **The schedule is time-based, not distance-based, and that is the whole
/// design.** A scenic route is deliberately turn-dense: measured over 455
/// maneuver legs on six routes, half are shorter than half a mile and 12% are
/// under 100 m. On that profile the usual "in 2 miles / in 1 mile / in 500
/// feet" ladder has every rung already behind the driver at the moment the
/// previous maneuver ends, so it fires three prompts at once or queues stale
/// ones that arrive after the turn. Announcing on *projected seconds to the
/// maneuver* is the only thing that adapts. See `docs/voice-guidance-plan.md`.

// MARK: - Speaker

/// Somewhere to say a sentence out loud.
///
/// Main-actor throughout: the only caller is `NavigationModel.update`, which
/// runs there, and `AVSpeechSynthesizer` calls its delegate there too.
@MainActor
protocol Speaker: AnyObject {
    /// An utterance finished on its own.
    var onFinished: (() -> Void)? { get set }
    /// Something else took the audio — a phone call, Siri — so whatever was
    /// being said was not heard, and the guide must un-latch it.
    var onInterrupted: (() -> Void)? { get set }
    /// Why nothing was said, in the words the driver sees. A voice that has
    /// silently stopped working is indistinguishable from a route with no turns
    /// on it, and the `'!pla'` finding is what that looks like in practice.
    var onProblem: ((String) -> Void)? { get set }

    /// Say this, pre-empting anything already in flight.
    ///
    /// Returns false when the audio session refused, which is not hypothetical:
    /// it is exactly what a build missing `UIBackgroundModes: audio` does on
    /// every attempt made from the background. A refusal must leave the
    /// announcement unlatched so the next fix tries again.
    @discardableResult func say(_ text: String) -> Bool

    /// Abandon anything in flight, mid-word.
    func stop()
}

/// `Speaker` on `AVSpeechSynthesizer`.
@MainActor
final class SystemSpeaker: NSObject, Speaker {
    var onFinished: (() -> Void)?
    var onInterrupted: (() -> Void)?
    var onProblem: ((String) -> Void)?

    private let synth = AVSpeechSynthesizer()

    /// Handing the session back measured **573 ms** on an iPhone 17 (n=27, min
    /// 570, max 577) — and 0 ms on a simulator, so it is real hardware cost and
    /// not the instrument. That is most of a location fix, on a stream that
    /// delivers about one a second, so it does not happen on the main actor.
    private static let sessionQueue = DispatchQueue(label: "app.scenic.audio-session")

    /// So a session that refuses on every fix reports once rather than
    /// becoming its own storm.
    private var lastProblemAt: Date = .distantPast
    private static let problemCooldown: TimeInterval = 30

    override init() {
        super.init()
        synth.delegate = self
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil, queue: .main) { [weak self] note in
                let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
                guard raw == AVAudioSession.InterruptionType.began.rawValue else {
                    // Deliberately nothing on `.ended`. The interrupted
                    // utterance is *not* resumed: the next fix re-decides from
                    // scratch, so a maneuver still ahead is re-announced with a
                    // currently correct distance and one now behind is dropped.
                    return
                }
                MainActor.assumeIsolated {
                    guard let self else { return }
                    self.synth.stopSpeaking(at: .immediate)
                    self.releaseSession()
                    self.onInterrupted?()
                }
            }
    }

    @discardableResult
    func say(_ text: String) -> Bool {
        // Never a queue. `AVSpeechSynthesizer` enqueues by default, and a stale
        // "in a quarter mile" delivered after the turn is worse than silence.
        // `.word` rather than `.immediate` because the pre-emption here is
        // normally a final overtaking a prepare, and a clipped word alarms more
        // than a quarter-second wait; `stop()` is the one that cuts.
        if synth.isSpeaking { synth.stopSpeaking(at: .word) }

        let session = AVAudioSession.sharedInstance()
        do {
            // `.playback` means this speaks through the ring/silent switch,
            // which is deliberate — a driver whose phone is on silent still
            // needs telling about the turn, and the mute control is the
            // affordance for wanting quiet. `.duckOthers` dips music;
            // `.interruptSpokenAudioAndMixWithOthers` pauses a podcast
            // outright, because ducked speech under speech is unintelligible.
            try session.setCategory(.playback, mode: .voicePrompt,
                                    options: [.duckOthers,
                                              .interruptSpokenAudioAndMixWithOthers])
            try session.setActive(true)
        } catch {
            report(error)
            return false
        }

        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        synth.speak(utterance)
        return true
    }

    func stop() {
        guard synth.isSpeaking else { return }
        synth.stopSpeaking(at: .immediate)
        releaseSession()
    }

    /// Hand the session back so other audio comes out of its duck.
    ///
    /// Never held across utterances: an app that keeps an active playback
    /// session suppresses the driver's music for the whole drive, which is both
    /// rude and the thing App Review looks for when an app claims the `audio`
    /// background mode.
    private func releaseSession() {
        Self.sessionQueue.async {
            try? AVAudioSession.sharedInstance()
                .setActive(false, options: .notifyOthersOnDeactivation)
        }
    }

    private func report(_ error: Error) {
        let now = Date()
        guard now.timeIntervalSince(lastProblemAt) > Self.problemCooldown else { return }
        lastProblemAt = now
        onProblem?("Couldn't speak — \((error as NSError).localizedDescription)")
    }
}

extension SystemSpeaker: AVSpeechSynthesizerDelegate {
    // Same isolation argument as `LocationManager`'s delegate methods:
    // `AVSpeechSynthesizer` calls these on the main queue. Measured over ~50
    // callbacks on device and simulator by the spike behind
    // `docs/voice-guidance-plan.md` §1, none of which tripped the assumption.
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        MainActor.assumeIsolated {
            releaseSession()
            onFinished?()
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        // No `onFinished` — a cancelled utterance was pre-empted by the next
        // one, which is about to activate the session again. Releasing it here
        // would race that.
    }
}

// MARK: - The schedule

/// Decides what to say about the route and when, from the same location fixes
/// everything else about the drive hangs off.
@MainActor
final class VoiceGuide {

    // MARK: Thresholds
    //
    // All in projected seconds to the maneuver, and every one of them sized on
    // a measurement rather than picked. The utterance lengths quoted are from
    // offline synthesis over the 176 unique instructions in four real routes,
    // calibrated to within 2% of what the phone actually spoke.

    /// Speak the maneuver itself. A bare instruction takes a median 1.83 s and
    /// a p90 of 2.38 s, and fixes land about 1 Hz so a crossing is noticed up
    /// to a second late — six seconds therefore starts the words 5–6 s out and
    /// finishes them ~3 s before the turn even for a long instruction. Less and
    /// the longest instructions are still being spoken at the junction; more
    /// and "now" stops meaning now (8 s is 161 m at 45 mph).
    static let finalAt: TimeInterval = 6

    /// Speak it anyway at this distance, whatever the projection says. A time
    /// rule divides by speed, so a car stopped 30 m short of its turn at a
    /// light has an infinite projection and would never be told. The same 40 m
    /// as `NavigationModel.arrivalMeters`, so "you are at the point" means one
    /// thing in both places.
    static let finalFloorMeters: Double = 40

    /// Speak "In a quarter mile, …". Long enough to change lane and start
    /// slowing, short enough to still be on the same leg: 25 s is 335 m at
    /// 30 mph, inside the median leg.
    static let prepareAt: TimeInterval = 25

    /// Below this when the maneuver becomes current, no prepare at all — there
    /// is no room for two utterances and a crammed one is worse than one.
    /// 6 s (the final) + 3.7 s (a p90 prepare) + 4 s of silence between them.
    static let prepareFloor: TimeInterval = 14

    /// When the leg *after* the maneuver being announced is shorter than this,
    /// the maneuver beyond it is named in the same breath — "turn right onto
    /// Morton Street, then left onto Webster Street". Below 12 s it cannot be
    /// announced on its own account in time: 6 s for its own final, 2.4 s for a
    /// p90 instruction, a fix of latency, and margin.
    ///
    /// Not a nicety. Over the corpus this chains a fifth of all maneuvers at
    /// town speed and a third at 45, and a schedule without it cannot deliver
    /// 13.7% of maneuvers at 30 mph before the driver reaches them.
    static let chainWithin: TimeInterval = 12

    // MARK: State

    private let speaker: Speaker

    /// Silence, remembered across drives and across a relaunch — see
    /// `VoiceGuide.isMuted`. Latches still advance while muted, so unmuting
    /// mid-leg cannot produce a burst of catch-up.
    var muted: Bool {
        didSet {
            guard muted != oldValue else { return }
            VoiceGuide.isMuted = muted
            if muted { speaker.stop() }
        }
    }

    /// What has already been said, keyed by maneuver *place* rather than by
    /// step index or by wording. Both alternatives are wrong here:
    ///
    /// - **Index.** `adopt` sets `currentStep = 0` and `merge` re-derives it
    ///   from zero, so an index-keyed latch re-speaks the opening maneuver on
    ///   every reroute. Across five recorded drives that is 51 of them.
    /// - **Wording.** `merge` exists precisely because the words can improve on
    ///   an unchanged line — on 2026-08-25 a reroute rewrote an opening
    ///   maneuver from "Turn right onto Lake Avenue" to "Head north on Lake
    ///   Avenue" at the same junction. Keyed on text that re-announces.
    ///
    /// Place survives both, and it is also what a driver's memory is indexed by.
    private var spoken: Set<Announcement> = []

    /// Maneuvers mentioned inside someone else's utterance ("…, then left onto
    /// Webster Street"). Kept apart from `spoken` because a named maneuver is
    /// still allowed to speak later if it has something new to add — see
    /// `considerFinal`.
    private var named: Set<Announcement> = []

    /// Bumped by `routeAdopted` and never by `routeMerged`, which is exactly
    /// the distinction `NavigationModel.sameLine` draws. A different line is a
    /// different drive and its opening maneuver is owed an announcement; the
    /// same line is the same drive and nothing about it should be repeated.
    private var generation = 0

    /// What is being said right now, so an interruption can un-latch it.
    private var inFlight: Announcement?

    /// Speed, smoothed. One noisy fix should not fire an announcement 200 m
    /// early.
    private var smoothedSpeed: CLLocationSpeed?
    private static let smoothing = 0.3

    /// Forwarded to `NavigationModel.report`, which prints under the controls
    /// without displacing the banner.
    var onProblem: ((String) -> Void)?

    init(speaker: Speaker, muted: Bool = VoiceGuide.isMuted) {
        self.speaker = speaker
        self.muted = muted
        speaker.onProblem = { [weak self] message in self?.onProblem?(message) }
        speaker.onFinished = { [weak self] in self?.inFlight = nil }
        speaker.onInterrupted = { [weak self] in
            // Not heard, so not said. The next fix decides again from scratch.
            guard let self, let interrupted = inFlight else { return }
            spoken.remove(interrupted)
            named.remove(interrupted)
            inFlight = nil
        }
    }

    // MARK: Persisted mute

    private static let muteKey = "voice.muted"

    /// Read at construction rather than watched from a view, and persisted
    /// because iOS can jettison and relaunch the app mid-drive — a driver who
    /// silenced the voice and then had it come back on at 60 km/h has been
    /// ambushed by their own phone. Global rather than per-drive on purpose:
    /// someone who wants quiet today probably wants it tomorrow, and a mute
    /// that resets itself is the worse of the two surprises.
    /// `nonisolated` because it is the default for `init`'s `muted:`, and a
    /// default argument is evaluated at the call site, which is not necessarily
    /// the main actor. `UserDefaults` is thread-safe, so there is nothing here
    /// that wanted the isolation.
    nonisolated static var isMuted: Bool {
        get { UserDefaults.standard.bool(forKey: muteKey) }
        set { UserDefaults.standard.set(newValue, forKey: muteKey) }
    }

    // MARK: Driven by each location update

    /// - Parameter describesWhereWeAre: whether the step list still describes
    ///   the road the car is on. The same predicate behind
    ///   `NavigationModel.currentRoad` returning something other than
    ///   `.offRoute`, and shared rather than copied so the readout and the
    ///   voice can never disagree: a driver 500 m down the wrong road still
    ///   projects onto the abandoned line and `advanceSteps` keeps walking the
    ///   index off that projection, which on screen is a plausible wrong street
    ///   name and out loud would be a plausible wrong instruction.
    /// - Parameter plannedPace: the route's own average speed, m/s, for use
    ///   before any fix has reported one.
    func consider(steps: [RouteStep], currentStep: Int, distanceToNext: Double,
                  from location: CLLocation, plannedPace: Double,
                  describesWhereWeAre: Bool) {
        updatePace(location)
        guard describesWhereWeAre, steps.indices.contains(currentStep) else { return }

        let pace = pace(fallback: plannedPace)
        let eta = distanceToNext / pace
        let step = steps[currentStep]

        if considerFinal(steps, currentStep, step, eta, distanceToNext, pace) { return }
        considerPrepare(step, eta, distanceToNext)
    }

    /// The maneuver itself, with the one after it folded in when it is too
    /// close to announce separately. Returns whether this fix was spent on it.
    private func considerFinal(_ steps: [RouteStep], _ index: Int, _ step: RouteStep,
                               _ eta: TimeInterval, _ distance: Double,
                               _ pace: Double) -> Bool {
        let key = Announcement(generation, at: step.coordinate, phase: .final)
        guard !spoken.contains(key) else { return false }
        guard eta <= Self.finalAt || distance <= Self.finalFloorMeters else { return false }

        // Is the maneuver after this one too close to get its own announcement?
        // `distance_m` is how far *this* instruction carries the driver, so it
        // is the gap between this maneuver and the next.
        var chained: RouteStep?
        if steps.indices.contains(index + 1), step.distance_m / pace < Self.chainWithin {
            chained = steps[index + 1]
        }

        // Already named inside the previous utterance, and nothing new to add.
        // Repeating it in the four seconds before the turn is worse than
        // silence — but if there is a further maneuver to warn about, this is
        // the only chance to do it, so it speaks after all.
        if named.contains(key), chained == nil {
            spoken.insert(key)
            return true
        }

        var text = step.spokenInstruction
        if let chained {
            text += ", then \(chained.spokenInstruction.firstLowercased)"
            // Named, not spoken: it may yet need to speak for its own successor.
            let follows = Announcement(generation, at: chained.coordinate, phase: .final)
            named.insert(follows)
            // It has just been announced ahead of time; a prepare would be a
            // third utterance about the same maneuver.
            spoken.insert(Announcement(generation, at: chained.coordinate, phase: .prepare))
        }
        deliver(key, text)
        return true
    }

    private func considerPrepare(_ step: RouteStep, _ eta: TimeInterval, _ distance: Double) {
        let key = Announcement(generation, at: step.coordinate, phase: .prepare)
        guard !spoken.contains(key) else { return }

        // Too late to fit one. Latched as done so it cannot fire later, which
        // is what "the prepare is skipped when the leg is too short" means in
        // code: on this route profile that is one maneuver in eight.
        guard eta >= Self.prepareFloor else {
            spoken.insert(key)
            return
        }
        guard eta <= Self.prepareAt, step.wantsPrepare else { return }
        deliver(key, "In \(Self.distancePhrase(distance)), \(step.spokenInstruction.firstLowercased)")
    }

    /// Arrival. Spoken from `update`'s early return, because `arrived` latches
    /// there and nothing after it ever runs again.
    func announceArrival() {
        let key = Announcement(generation, at: CLLocationCoordinate2D(latitude: 0, longitude: 0),
                               phase: .arrival)
        guard !spoken.contains(key) else { return }
        deliver(key, "You have arrived.")
    }

    /// Latch first, speak second — and latch even when muted, so unmuting does
    /// not release a backlog. A refusal from the audio session un-latches
    /// again, so the next fix retries.
    private func deliver(_ key: Announcement, _ text: String) {
        spoken.insert(key)
        guard !muted else { return }
        inFlight = key
        if !speaker.say(text) {
            spoken.remove(key)
            inFlight = nil
        }
    }

    // MARK: Route changes

    /// The same line, re-handed with possibly better words. Nothing is
    /// re-announced and an utterance in flight is still true, so it finishes.
    func routeMerged() {}

    /// A genuinely different route. Anything in flight may be about a maneuver
    /// that is no longer on it, which is not merely stale but wrong — the one
    /// place where clipping a word is the lesser harm.
    ///
    /// Nothing more is needed to hold the silence over the gap to the new line:
    /// `adopt` sets `awaitingJoin`, and announcements are gated on the same
    /// predicate the banner is, so the voice waits for the driver to reach the
    /// junction exactly as the screen does.
    func routeAdopted() {
        speaker.stop()
        generation += 1
        inFlight = nil
        spoken.removeAll()
        named.removeAll()
    }

    // MARK: Pace

    private func updatePace(_ location: CLLocation) {
        // Negative is CoreLocation declining to say, which is not a speed of
        // zero. A crawl of 0.4 m/s, on the other hand, is 0.4 m/s: it is a
        // genuine reading and flooring it would make a car queuing in traffic
        // appear to be arriving at its turn and fire the final several hundred
        // metres early, on every fix, for the length of the queue.
        guard location.speed >= 0 else { return }
        smoothedSpeed = smoothedSpeed.map {
            $0 * (1 - Self.smoothing) + location.speed * Self.smoothing
        } ?? location.speed
    }

    /// Metres per second to project with. The route's own planned average is
    /// the fallback because it is per-route and already in hand — a motorway
    /// trip and a lane in Vermont deserve different guesses. Clamped because
    /// the backend's minutes are free-flow and measurably optimistic on exactly
    /// the small roads scenic routes favour.
    private func pace(fallback: Double) -> Double {
        // The lower bound is only there to keep the projection finite; it is far
        // below any speed that should trigger anything, and the distance floor
        // is what actually covers a stopped car.
        max(0.1, smoothedSpeed ?? min(max(fallback, 4), 35))
    }

    /// "a quarter mile", "500 feet" — the spoken twin of `NavView.distanceText`,
    /// rounded harder because a voice saying "zero point four miles" is worse
    /// than one saying "a quarter mile".
    static func distancePhrase(_ meters: Double) -> String {
        // Rounded *before* the comparison, or 984 ft rounds up to a "1000 feet"
        // that the feet branch has already accepted and the miles branch will
        // never see.
        let feet = max(100, Int((meters * 3.28084 / 100).rounded()) * 100)
        if feet < 1000 { return "\(feet) feet" }
        let miles = meters / 1609.34
        switch miles {
        case ..<0.375:  return "a quarter mile"
        case ..<0.625:  return "half a mile"
        case ..<0.875:  return "three quarters of a mile"
        case ..<1.5:    return "a mile"
        default:        return "\(Int(miles.rounded())) miles"
        }
    }
}

// MARK: - What a latch is keyed on

/// One announcement, identified by the maneuver's place rather than its index
/// or its wording — see `VoiceGuide.spoken`.
private struct Announcement: Hashable {
    enum Phase { case prepare, final, arrival }

    let generation: Int
    /// Hundred-thousandths of a degree, which is about a metre. Two maneuvers
    /// that close together are the same maneuver.
    let lat: Int
    let lon: Int
    let phase: Phase

    init(_ generation: Int, at coordinate: CLLocationCoordinate2D, phase: Phase) {
        self.generation = generation
        self.lat = Int((coordinate.latitude * 100_000).rounded())
        self.lon = Int((coordinate.longitude * 100_000).rounded())
        self.phase = phase
    }
}

// MARK: - Saying it rather than showing it

extension RouteStep {
    /// The instruction, rewritten for the ear.
    ///
    /// The screen keeps the original. Only two rewrites survived measurement,
    /// and the ones that did not are worth recording so nobody adds them back:
    /// the synthesiser already expands `2nd` to "second" and `exit 14` to "exit
    /// fourteen" (identical durations to the millisecond), already spells out
    /// `YMCA`, and already treats a colon as the comma it would be replaced
    /// with. An abbreviation expander has nothing to do either — `Rd`, `St`,
    /// `Ave` and friends appear in 0 of 194 real steps, because OSM names in
    /// this region come through spelled out.
    var spokenInstruction: String { instruction.spokenAloud }
}

extension String {
    var spokenAloud: String {
        var out = self
        // A slash is *dropped*, not read: "Soldiers Field Road West / Newton"
        // measured shorter than either replacement, so two destinations run
        // together into one name. "and" is both audible and the right word.
        out = out.replacingOccurrences(of: " / ", with: " and ")
        // "MA 60" is read as either "em-ay sixty" or "ma sixty" and "US 1" as
        // the word "us" — neither is what a driver is looking for on a sign.
        // Every state prefix is listed rather than matching any two capitals,
        // so "YMCA Drive" cannot become "Route CA".
        out = out.replacing(#/\b(?:MA|RI|NH|VT|ME|CT|US)[ -](\d+[A-Z]?)\b/#) { "Route \($0.1)" }
        // Polish rather than repair: "I 93" already reads as "eye ninety-three",
        // which is what people say. "Interstate" costs 0.38 s and cannot be
        // misheard as the pronoun.
        out = out.replacing(#/\bI[ -](\d+[A-Z]?)\b/#) { "Interstate \($0.1)" }
        return out
    }

    /// For the second half of a chained utterance: "…, then turn left onto X".
    var firstLowercased: String {
        guard let first else { return self }
        return first.lowercased() + dropFirst()
    }
}

extension RouteStep {
    /// Whether this maneuver is worth a "prepare" as well as a "now".
    ///
    /// `continue` is 25% of all steps (49 of 194 in the corpus) and every one is
    /// "Continue onto <road>" — `pipeline/router.py` already folds the bare ones
    /// away. It is still not an action. Spending three seconds and a whole
    /// announcement slot on "In a quarter mile, continue onto Wolcott Street"
    /// tells the driver to keep doing what they are doing, and on this route
    /// profile that slot is frequently the one the next real turn needed.
    /// Dropping it removes about a quarter of all prepares, which is the
    /// cheapest reduction in chatter available.
    ///
    /// `depart` is excluded for a different reason: it fires at the start of the
    /// drive with the whole route still ahead, so there is nothing to prepare
    /// for.
    var wantsPrepare: Bool {
        switch maneuver {
        case .continue, .depart: return false
        default: return true
        }
    }
}
