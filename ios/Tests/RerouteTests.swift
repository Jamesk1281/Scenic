import CoreLocation
import XCTest
@testable import Scenic

/// Two reroutes can genuinely be in flight at once: `switchToFastest` does not
/// wait for an off-route reroute to finish. These drive that race deliberately,
/// through the `fetchRoute` seam, so it does not have to be caught on a road.
@MainActor
final class RerouteTests: XCTestCase {

    /// A stand-in for the backend that hands back control of *when* each request
    /// answers, so the test decides the order replies land in.
    @MainActor
    final class Backend {
        private(set) var prefsRequested: [Double] = []
        private(set) var headingsRequested: [CLLocationDirection?] = []
        private var pending: [CheckedContinuation<RouteResponse, Error>] = []

        var fetch: RouteFetcher {
            { [self] _, _, pref, _, heading in
                prefsRequested.append(pref)
                headingsRequested.append(heading)
                return try await withCheckedThrowingContinuation { continuation in
                    pending.append(continuation)
                }
            }
        }

        /// Requests made, answered or not: `reply` resumes a continuation but
        /// leaves it in place, so this only ever grows. That is what makes it
        /// usable as "has it asked again?" as well as "is one outstanding?".
        var inFlight: Int { pending.count }

        func reply(_ index: Int, with response: RouteResponse) {
            pending[index].resume(returning: response)
        }

        func fail(_ index: Int) {
            pending[index].resume(throwing: URLError(.timedOut))
        }
    }

    private func waitFor(_ condition: @MainActor () -> Bool,
                         _ message: String = "condition never held",
                         file: StaticString = #filePath, line: UInt = #line) async {
        let deadline = Date().addingTimeInterval(2)
        while !condition() {
            if Date() > deadline { return XCTFail(message, file: file, line: line) }
            try? await Task.sleep(for: .milliseconds(2))
        }
    }

    /// A model already on its route, so off-route recovery is armed.
    private func joined(_ backend: Backend) -> NavigationModel {
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:])
        model.fetchRoute = backend.fetch
        model.update(Fixture.fixAt(500))
        XCTAssertTrue(model.hasJoinedRoute)
        return model
    }

    /// Drift 300 m east of the line — well past the 60 m off-route threshold.
    private func offRoute(_ metres: Double) -> CLLocation {
        Fixture.fix(CLLocationCoordinate2D(
            latitude: Fixture.north(metres).latitude,
            longitude: -71.0 + 300 / 82_600))
    }

    private func namedRoute(_ instruction: String) -> RouteResponse {
        let feature = Fixture.straightRoute(
            steps: [(0, instruction), (5000, "Arrive at your destination")])
        return Fixture.response(fastest: feature, scenic: feature)
    }

    func test_straying_off_the_line_asks_for_a_new_route() async {
        let backend = Backend()
        let model = joined(backend)
        model.update(offRoute(800))
        await waitFor { backend.inFlight == 1 }
        XCTAssertTrue(model.isRerouting)
        XCTAssertEqual(backend.prefsRequested, [0.8], "off-route keeps the scenic intent")

        backend.reply(0, with: namedRoute("Continue on New Road"))
        await waitFor { !model.isRerouting }
        XCTAssertEqual(model.currentInstruction, "Continue on New Road")
    }

    // MARK: - The reroute loop the first test drive ran into

    /// A model that has just adopted a replacement route, with the clock under
    /// the test's control. Returns the model and a way to move time forward.
    private func afterAReroute(_ backend: Backend) async -> (NavigationModel, (TimeInterval) -> Void) {
        let model = joined(backend)
        var clock = Date()
        model.now = { clock }
        model.update(offRoute(800))
        await waitFor { backend.inFlight == 1 }
        backend.reply(0, with: namedRoute("Continue on New Road"))
        await waitFor { !model.isRerouting }
        return (model, { clock = clock.addingTimeInterval($0) })
    }

    func test_a_reroute_that_lands_off_the_new_line_does_not_immediately_reroute_again() async {
        // The defect this guards, measured on the first test drive: a
        // replacement route starts at the graph junction `snap` picked — a
        // median 99 m from the driver, p90 217 m — while the off-route
        // threshold is 60 m. So the first fix after a reroute was frequently
        // already off the new line and asked for another. 4 of 12 reroutes did
        // this, and the loop ran 7 times in 61 seconds, resetting the banner to
        // the first instruction each time, until the driver gave up and took
        // the fastest-route escape hatch.
        //
        // Neither existing guard could catch it. The cooldown bounds *failed*
        // attempts; this one succeeded. The movement guard bounds a *parked*
        // car; at 12 m/s this one cleared 50 m between every attempt. Both are
        // stepped past below, deliberately, so only the new guard is under test.
        let backend = Backend()
        let (model, advance) = await afterAReroute(backend)

        advance(20)                                  // well past the 8 s cooldown
        model.update(offRoute(1200))                 // and 400 m further on
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertEqual(backend.inFlight, 1,
                       "asked for another route before reaching the one it just gave")
    }

    func test_reaching_the_new_route_re_arms_off_route_recovery() async {
        // The guard above must not become a latch: once the driver is actually
        // on the replacement line, a genuine wrong turn has to reroute again.
        let backend = Backend()
        let (model, advance) = await afterAReroute(backend)

        model.update(Fixture.fixAt(1000))            // joins the new line
        advance(20)
        model.update(offRoute(1400))                 // then strays off it

        await waitFor { backend.inFlight == 2 }
    }

    func test_a_driver_who_never_reaches_the_new_route_still_re_arms() async {
        // The other way it could latch: turn off before ever touching the
        // replacement line and off-route recovery would never come back, so the
        // rest of the drive would be navigated against a route already
        // abandoned. The grace period bounds the wait.
        let backend = Backend()
        let (model, advance) = await afterAReroute(backend)

        advance(60)                                  // past joinGraceSeconds (45)
        model.update(offRoute(1200))

        await waitFor { backend.inFlight == 2 }
    }

    // MARK: - The reroute storm the 2026-08-22 drives ran into

    /// A point `east` metres off the line, at `northing` metres along it.
    private func beside(_ east: Double, at northing: Double) -> CLLocation {
        Fixture.fix(CLLocationCoordinate2D(
            latitude: Fixture.north(northing).latitude,
            longitude: Fixture.origin.longitude + east / 82_600))
    }

    func test_a_driver_who_keeps_ignoring_the_route_is_asked_less_often() async {
        // The defect this guards, measured on 2026-08-22: a driver on a road
        // the route wants to leave clears every existing guard. The cooldown is
        // for a *failed* attempt, the movement bar for a *parked* one, and
        // `awaitingJoin` is cleared by the few seconds the replacement line
        // spends running along the road the driver is already on. Off-route,
        // reroute, briefly on the new line, off-route again — ten times in 160
        // seconds, the banner resetting to its first instruction each time.
        //
        // Asking again cannot help: the server returns the same route, because
        // it is the right one. So the interval has to grow.
        let backend = Backend()
        let (model, advance) = await afterAReroute(backend)

        // Touch the line just long enough to clear `awaitingJoin` — the overlap
        // that defeated it — and nowhere near long enough to count as having
        // taken the route.
        advance(5)
        model.update(Fixture.fixAt(900))

        // Eleven seconds after the reroute: past the flat 8 s cooldown that let
        // every one of those ten attempts through, inside the doubled one.
        advance(6)
        model.update(beside(300, at: 1000))
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(backend.inFlight, 1,
                       "8 s was enough to ask again before; 11 s must not be")

        // Past the doubled interval it is allowed through, so this is a delay
        // and not a latch.
        advance(20)
        model.update(beside(300, at: 1200))
        await waitFor { backend.inFlight == 2 }
    }

    func test_the_interval_keeps_growing_while_the_driver_keeps_declining() async {
        // One doubling is not enough: the storm ran for 160 seconds. Each
        // reroute the driver does not take has to cost more than the last.
        let backend = Backend()
        let (model, advance) = await afterAReroute(backend)

        advance(5)
        model.update(Fixture.fixAt(900))
        advance(20)                                  // clears 16 s, so it fires
        model.update(beside(300, at: 1000))
        await waitFor { backend.inFlight == 2 }
        backend.reply(1, with: namedRoute("Continue on New Road"))
        await waitFor { !model.isRerouting }

        advance(5)
        model.update(Fixture.fixAt(1100))
        advance(20)                                  // 25 s: cleared 16, not 32
        model.update(beside(300, at: 1200))
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(backend.inFlight, 2,
                       "the second refusal should cost more than the first")
    }

    func test_taking_the_route_it_gave_you_forgives_the_backoff() async {
        // The backoff must not punish a driver who was simply lost once. Stay on
        // the replacement line and the next wrong turn gets the base interval.
        let backend = Backend()
        let (model, advance) = await afterAReroute(backend)

        // Actually drive the new route for longer than rerouteSettledSeconds.
        for northing in stride(from: 600.0, through: 1400.0, by: 100) {
            advance(5)
            model.update(Fixture.fixAt(northing))
        }
        advance(9)                                   // only just past the base 8 s
        model.update(beside(300, at: 1600))

        await waitFor { backend.inFlight == 2 }
    }

    func test_switching_to_fastest_is_never_held_back_by_the_backoff() async {
        // The driver asked for this one. Making them wait out an interval
        // earned by routes they declined is the opposite of what the escape
        // hatch is for.
        let backend = Backend()
        let (model, _) = await afterAReroute(backend)

        // No time advanced at all: the doubled interval is still running, and
        // an off-route reroute here would be refused.
        Task { await model.switchToFastest(from: Fixture.fixAt(700)) }
        await waitFor { backend.inFlight == 2 }
        XCTAssertEqual(backend.prefsRequested.last, 0.0)
        backend.reply(1, with: namedRoute("Continue on Fast Road"))
        await waitFor { !model.isRerouting }
    }

    // MARK: - A route the driver never joined

    func test_a_plan_the_driver_drives_away_from_is_eventually_replaced() async {
        // `hasJoinedRoute` disarms off-route recovery until the driver first
        // reaches the line — which is what stops a trip planned from the sofa
        // being thrown away on the first fix. It had no way out. On 2026-08-22
        // one drive spent its first three and a half minutes and 1.5 km never
        // touching the line, ending 593 m off it, and no reroute could fire the
        // whole time: the banner showed the first instruction throughout.
        let backend = Backend()
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:])
        model.fetchRoute = backend.fetch

        model.update(beside(100, at: 0))             // starts 100 m off the line
        XCTAssertFalse(model.hasJoinedRoute)
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(backend.inFlight, 0, "still plausibly on the way to it")

        model.update(beside(200, at: 0))             // drifting, but not yet far
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(backend.inFlight, 0)

        model.update(beside(500, at: 0))             // 400 m further out than ever
        await waitFor { backend.inFlight == 1 }
    }

    func test_driving_towards_a_route_you_have_not_joined_yet_is_left_alone() async {
        // The whole point of the join gate: closing on the start of a planned
        // route must never look like straying off it, however far out you begin.
        let backend = Backend()
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:])
        model.fetchRoute = backend.fetch

        for east in stride(from: 2000.0, through: 200.0, by: -200) {
            model.update(beside(east, at: 0))
        }
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(backend.inFlight, 0)
        XCTAssertFalse(model.hasJoinedRoute)
    }

    // MARK: - Arriving

    func test_the_last_few_hundred_metres_are_not_re_planned() async {
        // Inside a few hundred metres a reroute cannot help: the remaining line
        // is one or two residential streets, GPS error is a large fraction of
        // their length, and each replacement is a few hundred metres the driver
        // leaves again at once. The Needham drive of 2026-08-22 rerouted five
        // times in its last three minutes, within sight of the pin.
        let backend = Backend()
        let model = joined(backend)

        model.update(beside(300, at: 4900))          // 100 m of route left
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(backend.inFlight, 0)
    }

    func test_straying_with_the_trip_still_ahead_of_you_does_reroute() async {
        // The other side of the guard: it is about the last few hundred metres,
        // not about being off route at all.
        let backend = Backend()
        let model = joined(backend)

        model.update(beside(300, at: 2000))
        await waitFor { backend.inFlight == 1 }
    }

    // MARK: - Which way the driver is pointing

    func test_a_reroute_while_moving_sends_the_heading() async {
        // Without it the server snaps to the *nearer* end of the road, which
        // mid-drive is as often as not the junction just passed — so the
        // replacement route opens by turning the car around. The first test
        // drive was rerouted backwards down a road it was already committed to.
        let backend = Backend()
        let model = joined(backend)
        model.update(Fixture.movingFix(offRoute(800).coordinate,
                                       course: 90, speed: 20))
        await waitFor { backend.inFlight == 1 }
        XCTAssertEqual(backend.headingsRequested, [90])
    }

    func test_a_reroute_at_a_crawl_sends_no_heading() async {
        // CoreLocation derives course from successive positions, so a car
        // inching forward produces a heading that swings through the compass.
        // A confidently wrong one is worse than none: it points the route at
        // the wrong end of the road with no distance check to catch it.
        let backend = Backend()
        let model = joined(backend)
        model.update(Fixture.movingFix(offRoute(800).coordinate,
                                       course: 90, speed: 0.4))
        await waitFor { backend.inFlight == 1 }
        XCTAssertEqual(backend.headingsRequested, [nil])
    }

    func test_a_reroute_with_no_course_sends_no_heading() async {
        // -1 is CoreLocation for "no opinion", and it must not reach the wire:
        // the server drops it, but only because it checks — forwarding it as a
        // number is how it would become a confident due north.
        let backend = Backend()
        let model = joined(backend)
        model.update(offRoute(800))                  // plain fix: course -1
        await waitFor { backend.inFlight == 1 }
        XCTAssertEqual(backend.headingsRequested, [nil])
    }

    func test_switching_to_fastest_asks_for_pref_zero() async {
        let backend = Backend()
        let model = joined(backend)
        Task { await model.switchToFastest(from: Fixture.fixAt(600)) }
        await waitFor { backend.inFlight == 1 }
        XCTAssertEqual(backend.prefsRequested, [0.0])
        XCTAssertTrue(model.followingFastest)
        backend.reply(0, with: namedRoute("Continue on Fast Road"))
        await waitFor { !model.isRerouting }
    }

    /// The drive can end while the "switch to fastest" request is still in the
    /// air. That abandons the attempt with nothing newer behind it, so the pref
    /// and the styling staked on it have to be handed back — otherwise the
    /// arrival screen shows the gray fastest line with the button hidden for a
    /// switch that never happened, and `pref` stays 0 for the rest of the
    /// session, quietly discarding the scenic intent of every later reroute.
    ///
    /// Distinct from a *superseded* attempt, which is left alone on purpose
    /// because a newer request owns the state by then. Folding the two together
    /// is what made this reachable.
    func test_arriving_mid_request_unwinds_the_switch_to_fastest() async {
        let backend = Backend()
        let model = joined(backend)
        XCTAssertEqual(model.pref, 0.8)

        let switching = Task { await model.switchToFastest(from: Fixture.fixAt(600)) }
        await waitFor { backend.inFlight == 1 }
        XCTAssertTrue(model.followingFastest)
        XCTAssertEqual(model.pref, 0)

        // The last fix of the drive lands while the request is outstanding.
        model.update(Fixture.fixAt(5000))
        XCTAssertTrue(model.arrived, "the fixture did not reach the destination")

        backend.reply(0, with: Fixture.response(fastest: Fixture.straightRoute(),
                                                scenic: Fixture.straightRoute()))
        _ = await switching.value

        XCTAssertFalse(model.followingFastest,
                       "followingFastest stayed set for a switch that never landed")
        XCTAssertEqual(model.pref, 0.8,
                       "pref stayed at 0, so later reroutes lose the scenic intent")
    }

    func test_a_superseded_reroute_does_not_overwrite_the_newer_one() async {
        // The defect this guards: tapping "Fastest" while an off-route reroute
        // was still in flight left two requests running, and whichever answered
        // last won — so the driver could end up following the scenic route
        // while the screen said they were on the fastest one.
        let backend = Backend()
        let model = joined(backend)

        model.update(offRoute(800))                       // request 0: scenic
        await waitFor { backend.inFlight == 1 }
        Task { await model.switchToFastest(from: Fixture.fixAt(800)) }
        await waitFor { backend.inFlight == 2 }           // request 1: fastest
        XCTAssertEqual(backend.prefsRequested, [0.8, 0.0])

        backend.reply(1, with: namedRoute("Continue on Fast Road"))
        await waitFor { model.currentInstruction == "Continue on Fast Road" }

        // The older, slower reply lands afterwards and must be discarded.
        backend.reply(0, with: namedRoute("Continue on Scenic Road"))
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(model.currentInstruction, "Continue on Fast Road",
                       "a stale reroute overwrote the newer route")
        XCTAssertTrue(model.followingFastest)
    }

    func test_an_older_reroute_finishing_does_not_clear_the_rerouting_flag() async {
        // `defer { isRerouting = false }` fired for whichever request finished
        // first, advertising the newer one as done — which re-armed off-route
        // recovery and let a third request go out.
        let backend = Backend()
        let model = joined(backend)

        model.update(offRoute(800))
        await waitFor { backend.inFlight == 1 }
        Task { await model.switchToFastest(from: Fixture.fixAt(800)) }
        await waitFor { backend.inFlight == 2 }

        backend.fail(0)                                   // the older one gives up
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(model.isRerouting,
                      "the newer reroute is still in flight")

        backend.reply(1, with: namedRoute("Continue on Fast Road"))
        await waitFor { !model.isRerouting }
    }

    func test_a_failed_reroute_is_not_retried_on_every_fix() async {
        // Off-route checks run on every GPS tick; without a cooldown a server
        // blip would be hammered several times a second.
        let backend = Backend()
        let model = joined(backend)

        model.update(offRoute(800))
        await waitFor { backend.inFlight == 1 }
        backend.fail(0)
        await waitFor { !model.isRerouting }

        for metres in stride(from: 810.0, through: 900.0, by: 10) {
            model.update(offRoute(metres))
        }
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(backend.prefsRequested.count, 1,
                       "the 8 s cooldown should have suppressed the retries")
    }

    // MARK: - The first turn of a reroute, withheld (2026-08-25)

    /// A reroute answered with `feature`, on a clock the test controls.
    ///
    /// The clock matters twice over: `joinGraceSeconds` would otherwise expire
    /// on a slow machine and arm the very reroute these tests are watching for
    /// the absence of, and a frozen clock keeps the cooldown shut so nothing
    /// asks the backend again while the driver is being walked to the new line.
    private func rerouted(_ backend: Backend, onto feature: RouteFeature,
                          from offRouteAt: Double)
        async -> (NavigationModel, (TimeInterval) -> Void) {
        let model = joined(backend)
        var clock = Date()
        model.now = { clock }
        model.update(offRoute(offRouteAt))
        await waitFor { backend.inFlight == 1 }
        backend.reply(0, with: Fixture.response(fastest: feature, scenic: feature))
        await waitFor { !model.isRerouting }
        return (model, { clock = clock.addingTimeInterval($0) })
    }

    func test_a_reroute_does_not_withhold_the_turn_that_gets_you_onto_it() async {
        // 2026-08-25 16:22:53, `drive-2026-08-25-202122`. The replacement line
        // began 481 m up the road and opened with "Turn left onto Cliff
        // Street". The banner skipped it for the maneuver after it — "Turn
        // right onto Granite Street" — and, because the projection stayed
        // pinned to the start of a line the driver had not reached, froze the
        // countdown at 216 m for the 28 seconds it took to drive those 481 m.
        // The driver arrived at the junction never having been told to turn,
        // carried straight on, and was rerouted again 33 seconds later.
        let backend = Backend()
        let cliffStreet = Fixture.straightRoute(
            start: 1000,
            steps: [(0, "Turn left onto Cliff Street"),
                    (216, "Turn right onto Granite Street"),
                    (5000, "Arrive at your destination")])
        let (model, _) = await rerouted(backend, onto: cliffStreet, from: 519)

        // The 481 m drive to the start of the new line.
        var countdown: [Double] = []
        for metres in stride(from: 519.0, through: 999.0, by: 60) {
            model.update(Fixture.fixAt(metres))
            XCTAssertEqual(model.currentInstruction, "Turn left onto Cliff Street",
                           "the turn onto the new route was withheld at \(metres) m")
            countdown.append(model.distanceToNext)
        }
        XCTAssertEqual(countdown.first ?? 0, 481, accuracy: 20)
        XCTAssertEqual(countdown.last ?? 0, 1, accuracy: 20,
                       "the countdown to the first turn froze instead of running down")

        // ...and once they are genuinely past it, the banner does move on. The
        // guard above must hold the step, not strand it.
        model.update(Fixture.fixAt(1040))
        XCTAssertEqual(model.currentInstruction, "Turn right onto Granite Street")
    }

    func test_a_reroute_that_begins_under_the_car_still_gives_its_first_turn() async {
        // The plainest form of the same defect, and the commonest: on 21 of the
        // 51 reroutes recorded across five drives the first fix landed at
        // exactly 0 m along the new line. There the distance from the first
        // maneuver to the end of the route and the distance the driver has left
        // are the same number — the whole route — and the advance condition
        // consumed the instruction on that equality.
        let backend = Backend()
        let lakeAvenue = Fixture.straightRoute(
            start: 800,
            steps: [(0, "Turn right onto Lake Avenue"),
                    (3868, "Continue onto Lake Avenue North"),
                    (5000, "Arrive at your destination")])
        let (model, _) = await rerouted(backend, onto: lakeAvenue, from: 800)

        // Sitting exactly on the first point of the new line, twice: the first
        // fix is held by the join guard, the second by the comparison.
        model.update(Fixture.fixAt(800))
        XCTAssertEqual(model.currentInstruction, "Turn right onto Lake Avenue")
        model.update(Fixture.fixAt(800))
        XCTAssertEqual(model.currentInstruction, "Turn right onto Lake Avenue",
                       "standing at the turn is when you most need to be told about it")
    }

    func test_rounding_the_corner_onto_a_reroute_does_not_count_as_taking_it() async {
        // 2026-08-25 18:21:25, `drive-2026-08-25-211808` — 53 seconds from the
        // driver's own driveway, on the last 500 m of a 49 km drive. The
        // replacement line ran 53.8 m off, close enough that the join guard
        // above lets go almost at once, and the driver's perpendicular foot
        // landed 18.2 m *along* it while they were still short of the corner.
        // That was enough to read "Turn right onto Great Plain Avenue" as
        // already driven. They were shown the turn after it instead, stopped,
        // and worked it out for themselves.
        let backend = Backend()
        let greatPlain = Fixture.straightRoute(
            start: 1000, lengthMeters: 5000,
            steps: [(0, "Turn right onto Great Plain Avenue"),
                    (56, "Turn left onto Linden Street"),
                    (392, "Turn right onto Oak Street"),
                    (5000, "Arrive at your destination")])
        let (model, _) = await rerouted(backend, onto: greatPlain, from: 900)

        // 20 m off the line and 15 m along it: joined, by the 30 m deadband
        // `awaitingJoin` uses, and short of the turn all the same. Twice, so
        // the second one is past the join guard and rests on the deadband.
        for _ in 0..<2 {
            model.update(beside(20, at: 1015))
            XCTAssertEqual(model.currentInstruction, "Turn right onto Great Plain Avenue",
                           "the turn was withheld while the driver was still short of it")
        }

        // Taken. 40 m along the new line is past the corner and short of the
        // next one, so the banner owes them Linden Street.
        model.update(Fixture.fixAt(1040))
        XCTAssertEqual(model.currentInstruction, "Turn left onto Linden Street")
    }

    func test_a_short_first_leg_is_not_swallowed_by_the_deadband() async {
        // The deadband must not become its own way of skipping a turn. The
        // shortest opening leg served across five drives was 20 m — shorter
        // than the 30 m it holds for — so it is capped at half the leg, which
        // leaves a window between the two maneuvers for a fix to land in.
        let backend = Backend()
        let tight = Fixture.straightRoute(
            start: 1000,
            steps: [(0, "Turn left onto Heywood Street"),
                    (20, "Turn right onto Vale Street"),
                    (5000, "Arrive at your destination")])
        let (model, _) = await rerouted(backend, onto: tight, from: 900)

        model.update(Fixture.fixAt(1000))
        XCTAssertEqual(model.currentInstruction, "Turn left onto Heywood Street")
        model.update(Fixture.fixAt(1015))
        XCTAssertEqual(model.currentInstruction, "Turn right onto Vale Street",
                       "the deadband held past a 20 m leg and swallowed its turn")
    }

    func test_a_new_route_is_joined_by_construction() async {
        // The replacement starts from where the driver is standing, so
        // off-route recovery stays armed for the rest of the drive.
        let backend = Backend()
        let model = joined(backend)
        model.update(offRoute(800))
        await waitFor { backend.inFlight == 1 }
        backend.reply(0, with: namedRoute("Continue on New Road"))
        await waitFor { !model.isRerouting }
        XCTAssertTrue(model.hasJoinedRoute)
        XCTAssertEqual(model.currentStep, 0)
    }
}
