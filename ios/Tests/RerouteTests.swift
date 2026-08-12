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
        private var pending: [CheckedContinuation<RouteResponse, Error>] = []

        var fetch: RouteFetcher {
            { [self] _, _, pref, _ in
                prefsRequested.append(pref)
                return try await withCheckedThrowingContinuation { continuation in
                    pending.append(continuation)
                }
            }
        }

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

    func test_switching_to_fastest_asks_for_pref_zero() async {
        let backend = Backend()
        let model = joined(backend)
        Task { await model.switchToFastest(from: Fixture.north(600)) }
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
        Task { await model.switchToFastest(from: Fixture.north(800)) }
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
        Task { await model.switchToFastest(from: Fixture.north(800)) }
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
