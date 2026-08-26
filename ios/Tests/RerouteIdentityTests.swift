import CoreLocation
import XCTest
@testable import Scenic

/// Two defects the 2026-08-26 trace audit found, and the pair of smaller ones
/// beside them. Kept out of `RerouteTests` because they are about a reroute
/// that changes *nothing* — the server handing back the line already being
/// driven — which is the opposite of everything that file sets up.
@MainActor
final class RerouteIdentityTests: XCTestCase {

    private func waitFor(_ condition: @MainActor () -> Bool,
                         _ message: String = "condition never held",
                         file: StaticString = #filePath, line: UInt = #line) async {
        let deadline = Date().addingTimeInterval(2)
        while !condition() {
            if Date() > deadline { return XCTFail(message, file: file, line: line) }
            try? await Task.sleep(for: .milliseconds(2))
        }
    }

    /// `east` metres to the side of the line, at `northing` along it.
    private func beside(_ east: Double, at northing: Double) -> CLLocation {
        Fixture.fix(CLLocationCoordinate2D(
            latitude: Fixture.north(northing).latitude,
            longitude: Fixture.origin.longitude + east / 82_600))
    }

    /// The default fixture route, with one instruction reworded — the same
    /// polyline, byte for byte, and better words on it.
    private func sameLineReworded() -> RouteResponse {
        let feature = Fixture.straightRoute(
            steps: [(0, "Head north on Test Road"),
                    (1000, "Turn right onto Elm Street"),
                    (3000, "Bear left onto Oak Street"),
                    (5000, "Arrive at your destination")])
        return Fixture.response(fastest: feature, scenic: feature)
    }

    /// A model on the default route, already on the line, with a controllable
    /// clock. Returns the model, the backend, and a way to move time on.
    private func joined(_ backend: RerouteTests.Backend)
        -> (NavigationModel, (TimeInterval) -> Void) {
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:])
        var clock = Date()
        model.now = { clock }
        model.fetchRoute = backend.fetch
        model.update(Fixture.fixAt(500))
        XCTAssertTrue(model.hasJoinedRoute)
        return (model, { clock = clock.addingTimeInterval($0) })
    }

    // MARK: - The same line, handed back

    func test_the_same_line_back_again_keeps_the_drive_where_it_is() async {
        // Measured on the recorded drives: 8 of 51 off-route reroutes came back
        // byte-identical to the line already being followed — twice within 17 s
        // and 46 s. The server is right to send it; the driver has left the road
        // and this is still the best way. Adopting it *as new* is the fault,
        // because `adopt` restarts the banner at the first instruction and puts
        // `travelled` back to zero on a line the car never left.
        let backend = RerouteTests.Backend()
        let (model, _) = joined(backend)

        model.update(Fixture.fixAt(1500))
        XCTAssertEqual(model.currentStep, 2, "1.5 km along is past two maneuvers")

        model.update(beside(300, at: 1500))
        await waitFor { backend.inFlight == 1 }
        backend.reply(0, with: sameLineReworded())
        await waitFor { !model.isRerouting }

        XCTAssertEqual(model.currentStep, 2,
                       "the same line put the banner back to the first instruction")
    }

    func test_the_same_line_back_again_still_takes_the_better_words() async {
        // Why this is a merge and not simply "ignore the reply". On 2026-08-25 a
        // reroute returned the identical 1821-point polyline whose opening
        // maneuver had changed from "Turn right onto Lake Avenue" to "Head north
        // on Lake Avenue" — the server re-deriving the departure for a car whose
        // heading had moved on. Discarding the reply throws that away.
        let backend = RerouteTests.Backend()
        let (model, _) = joined(backend)

        model.update(Fixture.fixAt(1500))
        XCTAssertEqual(model.currentInstruction, "Turn left onto Oak Street")

        model.update(beside(300, at: 1500))
        await waitFor { backend.inFlight == 1 }
        backend.reply(0, with: sameLineReworded())
        await waitFor { !model.isRerouting }

        XCTAssertEqual(model.currentInstruction, "Bear left onto Oak Street",
                       "the corrected wording was dropped with the reply")
    }

    func test_the_same_line_back_again_still_holds_off_the_next_reroute() async {
        // The half of `adopt` that must survive a merge. The driver was sent a
        // replacement because they had left the route; nothing about the line
        // being unchanged means they are back on it. Without the join gate they
        // ask again on every cooldown, which is the storm this path exists to
        // stop.
        let backend = RerouteTests.Backend()
        let (model, advance) = joined(backend)

        model.update(Fixture.fixAt(1500))
        model.update(beside(300, at: 1500))
        await waitFor { backend.inFlight == 1 }
        backend.reply(0, with: sameLineReworded())
        await waitFor { !model.isRerouting }

        advance(20)                              // well past the 8 s cooldown
        model.update(beside(300, at: 1900))      // still 300 m off, still moving
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertEqual(backend.inFlight, 1,
                       "asked again while still on its way back to the same line")
    }

    func test_a_genuinely_new_line_is_still_adopted() async {
        // The control. A different polyline must still reset the banner: the
        // driver is being sent somewhere new and the first instruction is the
        // one they need.
        let backend = RerouteTests.Backend()
        let (model, _) = joined(backend)

        model.update(Fixture.fixAt(1500))
        XCTAssertEqual(model.currentStep, 2)

        model.update(beside(300, at: 1500))
        await waitFor { backend.inFlight == 1 }
        let elsewhere = Fixture.straightRoute(
            start: 150, steps: [(0, "Turn right onto Detour Road"),
                                (5000, "Arrive at your destination")])
        backend.reply(0, with: Fixture.response(fastest: elsewhere, scenic: elsewhere))
        await waitFor { !model.isRerouting }

        XCTAssertEqual(model.currentStep, 0)
        XCTAssertEqual(model.currentInstruction, "Turn right onto Detour Road")
    }

    // MARK: - A match pinned behind the car

    /// A route that runs *south* from `start`, so a car driving north along it
    /// has its position along the line fall as it goes. This is the shape that
    /// pins the match: `update` feeds `progress` a floor built from a running
    /// maximum, and once the car has run back further than
    /// `backtrackToleranceMeters` the floor holds the match at a point the car
    /// is driving away from.
    private func doublingBackRoute() -> RouteFeature {
        Fixture.uTurnRoute(start: 2000, lengthMeters: 2000, vertexSpacing: 50,
                           steps: [(0, "Make a U-turn on Test Road"),
                                   (2000, "Arrive at your destination")])
    }

    func test_a_match_pinned_behind_the_car_does_not_read_as_off_route() async {
        // The defect, measured on 2026-08-25: 168.2 m recorded where the
        // whole-line projection put the car 11.8 m from its route, on a polyline
        // byte-identical to the one adopted a second later. `offRoute` was
        // measuring the distance to a pinned match rather than to the road, and
        // it climbed until it crossed 60 m and asked for a route the car was
        // already on. Here the car never leaves the line at all.
        let backend = RerouteTests.Backend()
        let model = NavigationModel(route: doublingBackRoute(),
                                    destination: Fixture.north(0),
                                    pref: 0.8, weights: [:])
        model.fetchRoute = backend.fetch

        model.update(Fixture.fixAt(500))         // joins, 1500 m along the line
        XCTAssertTrue(model.hasJoinedRoute)
        model.update(Fixture.fixAt(700))         // driving north: 1300 m along
        model.update(Fixture.fixAt(800))         // 1200 m along, and 150 m "off"
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertEqual(backend.inFlight, 0,
                       "re-routed a car that never left its own route")
    }

    func test_a_driver_who_really_has_left_the_route_still_reroutes() async {
        // The control, and the one that keeps the fix above honest: releasing
        // the floor must not swallow a real departure. Same route, same floor,
        // but the car is genuinely 300 m to the side of it.
        let backend = RerouteTests.Backend()
        let model = NavigationModel(route: doublingBackRoute(),
                                    destination: Fixture.north(0),
                                    pref: 0.8, weights: [:])
        model.fetchRoute = backend.fetch

        model.update(Fixture.fixAt(500))
        model.update(beside(300, at: 500))
        await waitFor { backend.inFlight == 1 }
    }

    // MARK: - What a request that never lands costs

    func test_a_fastest_switch_that_never_lands_leaves_the_backoff_alone() async {
        // `switchToFastest` clears the backoff on the reasoning that the driver
        // has changed their mind. If the request never lands they changed
        // nothing, and an interval that had climbed to two minutes must not come
        // back at eight seconds because a request timed out.
        let backend = RerouteTests.Backend()
        let (model, advance) = joined(backend)

        model.update(beside(300, at: 800))                 // request 0
        await waitFor { backend.inFlight == 1 }
        let replacement = Fixture.straightRoute(
            start: 150, steps: [(0, "Continue on New Road"),
                                (5000, "Arrive at your destination")])
        backend.reply(0, with: Fixture.response(fastest: replacement,
                                                scenic: replacement))
        await waitFor { !model.isRerouting }                // one refusal: 16 s
        model.update(Fixture.fixAt(900))                   // back on the line

        advance(5)
        Task { await model.switchToFastest(from: Fixture.fixAt(950)) }
        await waitFor { backend.inFlight == 2 }
        backend.fail(1)                                    // ...and it never lands
        await waitFor { !model.isRerouting }

        advance(9)                                         // clears 8 s, not 16 s
        model.update(beside(300, at: 1100))
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertEqual(backend.inFlight, 2,
                       "a fastest tap that failed handed back the base interval")
    }

}
