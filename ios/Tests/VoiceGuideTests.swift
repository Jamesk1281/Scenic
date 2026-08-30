import CoreLocation
import XCTest
@testable import Scenic

/// The announcement schedule, and the latch that stops a reroute re-speaking
/// the drive.
///
/// All of this is a pure function of (distance, speed, step list, what has been
/// said), which is exactly why `Speaker` is a protocol: none of it needs a
/// phone, and the parts that would need one — whether the audio session
/// activates — are settled by measurement in `docs/voice-guidance-plan.md` §1
/// rather than by a test that could only lie about them.
@MainActor
final class VoiceGuideTests: XCTestCase {

    // MARK: - Doubles

    final class FakeSpeaker: Speaker {
        var onFinished: (() -> Void)?
        var onInterrupted: (() -> Void)?
        var onProblem: ((String) -> Void)?

        private(set) var said: [String] = []
        private(set) var stops = 0
        /// Make `say` report the audio session refusing — which is not a
        /// hypothetical failure mode but the measured behaviour of a build
        /// missing `UIBackgroundModes: audio`, on every attempt from the
        /// background.
        var refuses = false

        @discardableResult func say(_ text: String) -> Bool {
            guard !refuses else { return false }
            said.append(text)
            return true
        }

        func stop() { stops += 1 }

        /// The synthesiser reaching the end of an utterance.
        func finish() { onFinished?() }
        /// A phone call taking the audio mid-sentence.
        func interrupt() { onInterrupted?() }
    }

    /// Maneuvers as the backend really sends them: a place, a sentence, and how
    /// far that sentence carries the driver before the next one.
    ///
    /// Built here rather than through `Fixture`, whose `distance_m` is always 0
    /// — which would make every leg infinitely short and every maneuver chain.
    private func makeSteps(_ items: [(along: Double, text: String,
                                      leg: Double, type: String)]) -> [RouteStep] {
        let json = items.map { item -> [String: Any] in
            let place = Fixture.north(item.along)
            return ["instruction": item.text, "lat": place.latitude,
                    "lon": place.longitude, "distance_m": item.leg, "type": item.type]
        }
        let data = try! JSONSerialization.data(withJSONObject: json)
        return try! JSONDecoder().decode([RouteStep].self, from: data)
    }

    /// 30 mph, the speed most of these thresholds were sized at.
    private static let townSpeed: CLLocationSpeed = 13.4

    private func fix(_ speed: CLLocationSpeed = townSpeed) -> CLLocation {
        Fixture.movingFix(Fixture.origin, course: 0, speed: speed)
    }

    /// A guide with nothing said yet, unmuted regardless of what is on disk.
    private func guide() -> (VoiceGuide, FakeSpeaker) {
        let speaker = FakeSpeaker()
        return (VoiceGuide(speaker: speaker, muted: false), speaker)
    }

    private func run(_ voice: VoiceGuide, _ steps: [RouteStep], _ index: Int,
                     _ distance: Double, speed: CLLocationSpeed = townSpeed) {
        voice.consider(steps: steps, currentStep: index, distanceToNext: distance,
                       from: fix(speed), plannedPace: 15, describesWhereWeAre: true)
    }

    private var twoTurns: [RouteStep] {
        makeSteps([(0, "Head north on Test Road", 1000, "depart"),
                   (1000, "Turn right onto Elm Street", 1000, "turn"),
                   (2000, "Turn left onto Oak Street", 500, "turn"),
                   (2500, "Arrive at your destination", 0, "arrive")])
    }

    // MARK: - The schedule

    func test_nothing_is_said_while_the_maneuver_is_still_far_off() {
        let (voice, speaker) = guide()
        // 400 m at 30 mph is 30 s — past the prepare threshold, so silence.
        run(voice, twoTurns, 1, 400)
        XCTAssertEqual(speaker.said, [])
    }

    func test_the_prepare_comes_at_twenty_five_seconds_and_the_final_at_six() {
        let (voice, speaker) = guide()
        run(voice, twoTurns, 1, 400)                    // 30 s — nothing
        run(voice, twoTurns, 1, 300)                    // 22 s — prepare
        XCTAssertEqual(speaker.said, ["In a quarter mile, turn right onto Elm Street"])
        run(voice, twoTurns, 1, 150)                    // 11 s — nothing more
        XCTAssertEqual(speaker.said.count, 1, "a second prepare would be chatter")
        run(voice, twoTurns, 1, 70)                     // 5.2 s — the maneuver
        XCTAssertEqual(speaker.said.last, "Turn right onto Elm Street")
    }

    func test_neither_announcement_repeats_however_many_fixes_land() {
        let (voice, speaker) = guide()
        // Fixes arrive about 1 Hz for the whole approach; the driver hears two
        // things, not twenty.
        for distance in stride(from: 400.0, through: 10.0, by: -10) {
            run(voice, twoTurns, 1, distance)
        }
        XCTAssertEqual(speaker.said.count, 2, "expected one prepare and one final, got \(speaker.said)")
    }

    func test_a_car_stopped_short_of_its_turn_is_still_told() {
        let (voice, speaker) = guide()
        // Held at a light 30 m from the junction. The projection is infinite —
        // a time-based rule divides by a speed of zero — so only the distance
        // floor can save this, and it is the case the floor exists for.
        run(voice, twoTurns, 1, 30, speed: 0)
        XCTAssertEqual(speaker.said, ["Turn right onto Elm Street"])
    }

    func test_a_speed_coreLocation_refuses_to_give_falls_back_to_the_route_average() {
        let (voice, speaker) = guide()
        // -1 is CoreLocation declining, not a stopped car. `plannedPace: 15`
        // makes 300 m a 20 s projection, which is inside the prepare window.
        voice.consider(steps: twoTurns, currentStep: 1, distanceToNext: 300,
                       from: Fixture.fix(Fixture.origin), plannedPace: 15,
                       describesWhereWeAre: true)
        XCTAssertEqual(speaker.said, ["In a quarter mile, turn right onto Elm Street"])
    }

    func test_the_prepare_is_dropped_when_the_leg_is_too_short_to_fit_it() {
        let (voice, speaker) = guide()
        // The maneuver becomes current with 150 m — 11 s — already left. There
        // is no room for two utterances, so the prepare is not merely late, it
        // is abandoned: on this route profile that is one maneuver in eight.
        run(voice, twoTurns, 1, 150)
        XCTAssertEqual(speaker.said, [])
        run(voice, twoTurns, 1, 70)
        XCTAssertEqual(speaker.said, ["Turn right onto Elm Street"],
                       "the prepare must not surface late, behind the final")
    }

    func test_continue_gets_a_final_but_never_a_prepare() {
        // 25% of all steps are "Continue onto <road>". It is not an action, and
        // spending a prepare on it costs the slot the next real turn needed.
        let steps = makeSteps([(0, "Head north on Test Road", 1000, "depart"),
                               (1000, "Continue onto Wolcott Street", 1000, "continue"),
                               (2000, "Turn left onto Oak Street", 0, "turn")])
        let (voice, speaker) = guide()
        run(voice, steps, 1, 300)
        XCTAssertEqual(speaker.said, [], "no prepare for a maneuver that is not one")
        run(voice, steps, 1, 70)
        XCTAssertEqual(speaker.said, ["Continue onto Wolcott Street"])
    }

    // MARK: - Chaining

    func test_a_maneuver_too_close_to_announce_alone_is_named_in_the_one_before() {
        // 100 m at 30 mph is 7.5 s: the second turn cannot be announced on its
        // own account before the driver reaches it, so it has to ride along.
        let steps = makeSteps([(0, "Head north on Test Road", 1000, "depart"),
                               (1000, "Turn right onto Morton Street", 100, "turn"),
                               (1100, "Turn left onto Webster Street", 1000, "turn")])
        let (voice, speaker) = guide()
        run(voice, steps, 1, 70)
        XCTAssertEqual(speaker.said,
                       ["Turn right onto Morton Street, then turn left onto Webster Street"])
    }

    func test_a_maneuver_already_named_is_not_announced_again_for_nothing() {
        let steps = makeSteps([(0, "Head north on Test Road", 1000, "depart"),
                               (1000, "Turn right onto Morton Street", 100, "turn"),
                               (1100, "Turn left onto Webster Street", 2000, "turn")])
        let (voice, speaker) = guide()
        run(voice, steps, 1, 70)
        XCTAssertEqual(speaker.said.count, 1)

        // The driver turns at Morton and Webster becomes current. It was named
        // four seconds ago and nothing has been added since; saying it again on
        // top of the turn is worse than silence.
        run(voice, steps, 2, 70)
        XCTAssertEqual(speaker.said.count, 1, "unexpected repeat: \(speaker.said)")
    }

    func test_but_it_does_speak_again_when_there_is_a_third_turn_to_warn_about() {
        // Three short legs in a row. Webster was named inside Morton's
        // utterance, but this is the only chance to say anything about Elm, so
        // Webster speaks after all and carries Elm with it. A chain is never
        // longer than two: "X, then Y, then Z" measures over six seconds.
        let steps = makeSteps([(0, "Head north on Test Road", 1000, "depart"),
                               (1000, "Turn right onto Morton Street", 100, "turn"),
                               (1100, "Turn left onto Webster Street", 100, "turn"),
                               (1200, "Turn right onto Elm Street", 2000, "turn")])
        let (voice, speaker) = guide()
        run(voice, steps, 1, 70)
        run(voice, steps, 2, 70)
        XCTAssertEqual(speaker.said, [
            "Turn right onto Morton Street, then turn left onto Webster Street",
            "Turn left onto Webster Street, then turn right onto Elm Street",
        ])
        // And Elm, having been named, then has nothing of its own to add.
        run(voice, steps, 3, 70)
        XCTAssertEqual(speaker.said.count, 2)
    }

    func test_a_chained_maneuver_never_also_gets_a_prepare() {
        let steps = makeSteps([(0, "Head north on Test Road", 1000, "depart"),
                               (1000, "Turn right onto Morton Street", 100, "turn"),
                               (1100, "Turn left onto Webster Street", 2000, "turn")])
        let (voice, speaker) = guide()
        run(voice, steps, 1, 70)
        run(voice, steps, 2, 300)   // inside the prepare window for Webster
        XCTAssertEqual(speaker.said.count, 1,
                       "a third utterance about a maneuver already named twice over")
    }

    // MARK: - Muting

    func test_muting_silences_the_voice_but_still_advances_the_latch() {
        let (voice, speaker) = guide()
        voice.muted = true
        run(voice, twoTurns, 1, 300)
        run(voice, twoTurns, 1, 70)
        XCTAssertEqual(speaker.said, [])

        // Unmuting mid-leg must not release a backlog of everything missed.
        voice.muted = false
        run(voice, twoTurns, 1, 40)
        XCTAssertEqual(speaker.said, [], "unmuting replayed announcements already passed")
    }

    func test_muting_cuts_off_whatever_is_being_said() {
        let (voice, speaker) = guide()
        run(voice, twoTurns, 1, 300)
        voice.muted = true
        XCTAssertEqual(speaker.stops, 1)
    }

    // MARK: - When the audio refuses, or something else takes it

    func test_a_refused_audio_session_is_retried_rather_than_counted_as_said() {
        // The measured failure: with `UIBackgroundModes` missing `audio`,
        // `setActive(true)` throws on every attempt from the background. A
        // latch that recorded those as delivered would leave the driver in
        // silence for the rest of the drive even once audio came back.
        let (voice, speaker) = guide()
        speaker.refuses = true
        run(voice, twoTurns, 1, 300)
        XCTAssertEqual(speaker.said, [])

        speaker.refuses = false
        run(voice, twoTurns, 1, 290)
        XCTAssertEqual(speaker.said, ["In a quarter mile, turn right onto Elm Street"])
    }

    func test_an_interrupted_announcement_is_not_counted_as_heard() {
        let (voice, speaker) = guide()
        run(voice, twoTurns, 1, 300)
        XCTAssertEqual(speaker.said.count, 1)

        // A phone call took the audio a syllable in. The driver heard nothing
        // useful, so the next fix should be free to say it again.
        speaker.interrupt()
        run(voice, twoTurns, 1, 290)
        XCTAssertEqual(speaker.said.count, 2)
    }

    func test_a_finished_announcement_stays_said_when_the_next_is_interrupted() {
        let (voice, speaker) = guide()
        run(voice, twoTurns, 1, 300)
        speaker.finish()
        // An interruption now belongs to nothing in flight and must not reach
        // back and un-latch the one that completed.
        speaker.interrupt()
        run(voice, twoTurns, 1, 290)
        XCTAssertEqual(speaker.said.count, 1)
    }

    // MARK: - Off the line

    func test_nothing_is_said_while_the_step_list_describes_some_other_road() {
        let (voice, speaker) = guide()
        voice.consider(steps: twoTurns, currentStep: 1, distanceToNext: 70,
                       from: fix(), plannedPace: 15, describesWhereWeAre: false)
        XCTAssertEqual(speaker.said, [],
                       "a driver 500 m down the wrong road still projects onto the "
                       + "abandoned line, and the index walked off it means nothing")
    }

    // MARK: - Route changes

    func test_the_same_line_handed_back_does_not_re_announce_anything() {
        // The trap. `merge` re-derives `currentStep` from zero, and across five
        // recorded drives 8 of 51 reroutes came back byte-identical. A latch
        // keyed on the step index re-speaks the opening maneuver on every one.
        let (voice, speaker) = guide()
        run(voice, twoTurns, 1, 70)
        XCTAssertEqual(speaker.said.count, 1)

        voice.routeMerged()
        run(voice, twoTurns, 1, 65)
        XCTAssertEqual(speaker.said.count, 1, "the same line re-announced: \(speaker.said)")
    }

    func test_a_merge_that_only_improves_the_wording_does_not_re_announce_either() {
        // On 2026-08-25 a reroute returned the identical polyline with its
        // opening maneuver rewritten from "Turn right onto Lake Avenue" to
        // "Head north on Lake Avenue". Keyed on the words, that re-announces;
        // keyed on the place, it does not.
        let (voice, speaker) = guide()
        run(voice, twoTurns, 1, 70)

        let reworded = makeSteps([(0, "Head north on Test Road", 1000, "depart"),
                                  (1000, "Bear right onto Elm Street", 1000, "turn"),
                                  (2000, "Turn left onto Oak Street", 500, "turn")])
        voice.routeMerged()
        run(voice, reworded, 1, 65)
        XCTAssertEqual(speaker.said.count, 1)
    }

    func test_a_genuinely_different_route_gets_its_opening_maneuver_spoken() {
        let (voice, speaker) = guide()
        run(voice, twoTurns, 1, 70)

        let replacement = makeSteps([(3000, "Turn left onto Chestnut Street", 800, "turn"),
                                     (3800, "Arrive at your destination", 0, "arrive")])
        voice.routeAdopted()
        XCTAssertEqual(speaker.stops, 1, "anything in flight is about the old route")
        run(voice, replacement, 0, 70)
        XCTAssertEqual(speaker.said.last, "Turn left onto Chestnut Street")
    }

    // MARK: - Phrasing

    func test_the_rewrites_that_survived_measurement() {
        // A slash is *dropped* by the synthesiser rather than read, so two
        // destinations run together into one name.
        XCTAssertEqual("Take the exit toward Soldiers Field Road West / Newton".spokenAloud,
                       "Take the exit toward Soldiers Field Road West and Newton")
        // "MA 60" reads as "em-ay sixty" or "ma sixty"; "US 1" as the pronoun.
        XCTAssertEqual("Keep right onto MA 60".spokenAloud, "Keep right onto Route 60")
        XCTAssertEqual("Take the exit toward US 1 North".spokenAloud,
                       "Take the exit toward Route 1 North")
        XCTAssertEqual("Take the exit toward I 93 South: Quincy".spokenAloud,
                       "Take the exit toward Interstate 93 South: Quincy")
        XCTAssertEqual("Take exit 14 toward I-93 South / US 1 South: Columbia Road".spokenAloud,
                       "Take exit 14 toward Interstate 93 South and Route 1 South: Columbia Road")
    }

    func test_the_rewrites_that_did_not_survive_it() {
        // Measured, not assumed: the synthesiser renders each of these
        // identically to its expanded form, so a rule for them would be
        // untested code standing between the driver and the words.
        XCTAssertEqual("Take the 2nd exit onto High Street".spokenAloud,
                       "Take the 2nd exit onto High Street", "ordinals expand already")
        XCTAssertEqual("Take exit 9 toward West Quincy".spokenAloud,
                       "Take exit 9 toward West Quincy", "cardinals read correctly already")
        XCTAssertEqual("Turn left onto YMCA Drive".spokenAloud,
                       "Turn left onto YMCA Drive", "YMCA is spelled out already")
    }

    func test_a_state_prefix_rule_cannot_eat_a_road_name() {
        // The rule lists its prefixes exhaustively rather than matching any two
        // capitals, so the one all-caps road name in the corpus is safe.
        XCTAssertFalse("Turn left onto YMCA Drive".spokenAloud.contains("Route"))
        XCTAssertEqual("Turn right onto Maine Street".spokenAloud,
                       "Turn right onto Maine Street")
    }

    func test_spoken_distances_are_rounded_the_way_a_person_would_say_them() {
        XCTAssertEqual(VoiceGuide.distancePhrase(400), "a quarter mile")
        XCTAssertEqual(VoiceGuide.distancePhrase(800), "half a mile")
        XCTAssertEqual(VoiceGuide.distancePhrase(1609), "a mile")
        XCTAssertEqual(VoiceGuide.distancePhrase(4800), "3 miles")
        XCTAssertEqual(VoiceGuide.distancePhrase(150), "500 feet")
    }
}

/// The wiring, exercised through the real thing: fixes into
/// `NavigationModel.update`, utterances out.
///
/// The schedule is unit-tested above; what this covers is everything between —
/// that `distanceToNext` and the speed reach the guide, that the gate matches
/// the banner's, that arrival gets a word in before `update` stops looking, and
/// that a reroute does not restart the commentary.
@MainActor
final class VoiceGuideIntegrationTests: XCTestCase {

    private func drive() -> (NavigationModel, VoiceGuideTests.FakeSpeaker) {
        let speaker = VoiceGuideTests.FakeSpeaker()
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:],
                                    voice: VoiceGuide(speaker: speaker, muted: false))
        return (model, speaker)
    }

    /// A fix `meters` along the fixture route, moving at 30 mph.
    private func rolling(_ meters: Double) -> CLLocation {
        Fixture.movingFix(Fixture.north(meters), course: 0, speed: 13.4)
    }

    func test_a_drive_up_the_route_says_each_maneuver_once() {
        let (model, speaker) = drive()
        // The fixture turns at 1 km, 3 km and arrives at 5 km.
        for metres in stride(from: 0.0, through: 3100.0, by: 20) {
            model.update(rolling(metres))
        }
        XCTAssertEqual(speaker.said, [
            "Head north on Test Road",
            "In a quarter mile, turn right onto Elm Street",
            "Turn right onto Elm Street",
            "In a quarter mile, turn left onto Oak Street",
            "Turn left onto Oak Street",
        ], "unexpected commentary for a plain drive up a straight road")
    }

    func test_arrival_is_spoken_before_the_drive_stops_listening() {
        let (model, speaker) = drive()
        for metres in stride(from: 0.0, through: 5000.0, by: 20) {
            model.update(rolling(metres))
        }
        XCTAssertTrue(model.arrived)
        XCTAssertEqual(speaker.said.last, "You have arrived.")
    }

    func test_nothing_is_said_before_the_driver_has_joined_the_route() {
        let (model, speaker) = drive()
        // Planned from somewhere else entirely — the banner says "head to the
        // start of your route", and the voice has nothing honest to add.
        model.update(Fixture.movingFix(Fixture.offset(east: 4000, north: 500),
                                       course: 0, speed: 13.4))
        XCTAssertFalse(model.hasJoinedRoute)
        XCTAssertEqual(speaker.said, [])
    }

    func test_the_same_line_handed_back_mid_drive_re_announces_nothing() async {
        // The trap, end to end. `merge` re-derives `currentStep` from zero on a
        // line the car never left; across the recorded drives that happened 8
        // times.
        //
        // The excursion has to be *brief* for this to test anything. A driver
        // who strays and takes a while to come back has `awaitingJoin` holding
        // the voice off, and by the time it clears they have passed the
        // maneuver — which is why replaying the twelve real drives does **not**
        // catch a latch that clears on every reply (see
        // `docs/voice-guidance-plan.md` §10). What is needed is a stray and
        // return quick enough that the driver is still approaching a maneuver
        // they have already been told about. A GPS glitch does exactly this.
        let backend = RerouteTests.Backend()
        let speaker = VoiceGuideTests.FakeSpeaker()
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:],
                                    voice: VoiceGuide(speaker: speaker, muted: false))
        model.fetchRoute = backend.fetch

        // Up to 680 m: the turn at 1 km is 320 m off, which at 30 mph is 24 s
        // and inside the prepare window.
        for metres in stride(from: 0.0, through: 680.0, by: 20) {
            model.update(rolling(metres))
        }
        XCTAssertEqual(speaker.said, ["Head north on Test Road",
                                      "In a quarter mile, turn right onto Elm Street"])

        // One fix 300 m to the side. Past `offRouteCertainMeters`, so it arms
        // the whole streak at once and re-routes immediately.
        let aside = CLLocationCoordinate2D(latitude: Fixture.north(700).latitude,
                                           longitude: Fixture.origin.longitude + 300 / 82_600)
        model.update(Fixture.movingFix(aside, course: 0, speed: 13.4))
        let deadline = Date().addingTimeInterval(2)
        while backend.inFlight == 0, Date() < deadline {
            try? await Task.sleep(for: .milliseconds(2))
        }
        XCTAssertEqual(backend.inFlight, 1, "expected an off-route reroute")
        let same = Fixture.straightRoute()
        backend.reply(0, with: Fixture.response(fastest: same, scenic: same))
        while model.isRerouting, Date() < deadline {
            try? await Task.sleep(for: .milliseconds(2))
        }

        // Straight back onto the line, still 280 m short of the turn — 21 s,
        // squarely inside the prepare window it was already given.
        for metres in stride(from: 700.0, through: 740.0, by: 20) {
            model.update(rolling(metres))
        }
        // Both halves matter: if the driver were still off the line, or had
        // already passed the turn, this test would pass for the wrong reason.
        XCTAssertTrue(model.stepsDescribeWhereWeAre, "not back on the line")
        XCTAssertEqual(model.currentStep, 1, "the turn should still be ahead")
        XCTAssertEqual(speaker.said, ["Head north on Test Road",
                                      "In a quarter mile, turn right onto Elm Street"],
                       "the same line spoke again")
    }
}
