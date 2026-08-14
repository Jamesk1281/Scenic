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
