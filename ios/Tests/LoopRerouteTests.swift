import CoreLocation
import XCTest
@testable import Scenic

/// Rerouting a loop, which is the one thing about driving a loop that is not
/// simply driving a route.
///
/// A loop ends where it began, so its `destination` is the driver's own
/// driveway. Ask the server for a route there and it correctly returns the short
/// way home — which deletes the rest of the drive. Measured against a real 24 mi
/// Needham loop, a missed turn two miles in would have swapped 22 remaining miles
/// for about three. These tests are what stop that coming back.
///
/// In its own file rather than in RerouteTests, so the two can be read (and
/// merged) independently.
@MainActor
final class LoopRerouteTests: XCTestCase {

    /// A loop as a straight fixture line: out 5 km north and back down the same
    /// line, so the far point is the 5 km mark and the drive is 10 km.
    ///
    /// Straight rather than a ring because the reroute logic only cares about
    /// distance along the line and proximity to the far point, and a fixture
    /// whose geometry can be read off by eye is worth more here than a realistic
    /// one.
    private func loopRoute(lengthMeters: Double = 10_000) -> RouteFeature {
        Fixture.straightRoute(lengthMeters: lengthMeters,
                              steps: [(0, "Head north on Test Road"),
                                      (lengthMeters / 2, "Turn around"),
                                      (lengthMeters, "Arrive back where you started")],
                              minutes: 20)
    }

    private var origin: CLLocationCoordinate2D { Fixture.origin }
    private var turnaround: CLLocationCoordinate2D { Fixture.north(5_000) }

    /// Counts which fetcher a drive reached for.
    ///
    /// A counter rather than `XCTFail` inside the stub, deliberately. A reroute
    /// is asynchronous, so a stub can fire after the test method has returned —
    /// and an `XCTFail` that lands then is reported against whichever test is
    /// running at the time. That is exactly what happened while writing these:
    /// a failure from here surfaced inside `RerouteIdentityTests`, blaming a
    /// test that had nothing to do with it.
    private final class Asked {
        var plain = 0
        var resume = 0
        var via: CLLocationCoordinate2D?
        var to: CLLocationCoordinate2D?
    }

    /// A loop drive, off-route recovery already armed.
    private func loopDrive(_ asked: Asked,
                           expectation: XCTestExpectation? = nil) -> NavigationModel {
        let nav = NavigationModel(route: loopRoute(), destination: origin,
                                  pref: 1.0, weights: [:], trace: nil,
                                  turnaround: turnaround)
        nav.fetchLoopResume = { [self] _, via, to, _, _, _ in
            asked.resume += 1
            asked.via = via
            asked.to = to
            expectation?.fulfill()
            return Fixture.response(fastest: loopRoute(), scenic: loopRoute())
        }
        nav.fetchRoute = { [self] _, to, _, _, _ in
            asked.plain += 1
            asked.to = to
            expectation?.fulfill()
            return Fixture.response(fastest: loopRoute(), scenic: loopRoute())
        }
        // Join the line so off-route recovery is live, the way every reroute
        // test here has to.
        nav.update(Fixture.fixAt(100))
        return nav
    }

    /// Drag the driver a long way sideways and let the guards run.
    private func goOffRoute(_ nav: NavigationModel, at meters: Double) {
        let away = CLLocationCoordinate2D(
            latitude: Fixture.north(meters).latitude,
            longitude: Fixture.origin.longitude + 0.02)   // ~1.6 km east
        nav.update(Fixture.fix(away))
    }

    // MARK: - Before the far point

    func test_a_loop_reroutes_through_its_far_point_and_not_straight_home() async {
        let asked = Asked()
        let expectation = expectation(description: "a replacement was fetched")
        let nav = loopDrive(asked, expectation: expectation)
        goOffRoute(nav, at: 2_000)
        await fulfillment(of: [expectation], timeout: 2)

        XCTAssertEqual(asked.resume, 1)
        XCTAssertEqual(asked.plain, 0, "a loop before its far point must not "
                       + "reroute straight home")
        XCTAssertEqual(asked.via?.latitude ?? 0, turnaround.latitude, accuracy: 0.0001)
        // Still going home in the end — the waypoint is a detour, not a new
        // destination.
        XCTAssertEqual(asked.to?.latitude ?? 0, origin.latitude, accuracy: 0.0001)
    }

    func test_the_far_point_is_not_yet_passed_two_kilometres_in() {
        let nav = loopDrive(Asked())
        nav.update(Fixture.fixAt(2_000))
        XCTAssertFalse(nav.passedTurnaround)
    }

    // MARK: - After the far point

    func test_once_past_the_far_point_a_loop_reroutes_home_like_any_trip() async {
        let asked = Asked()
        let expectation = expectation(description: "a replacement was fetched")
        let nav = loopDrive(asked, expectation: expectation)
        // Drive out to the far point and past it.
        nav.update(Fixture.fixAt(5_000))
        XCTAssertTrue(nav.passedTurnaround)

        goOffRoute(nav, at: 6_000)
        await fulfillment(of: [expectation], timeout: 2)
        XCTAssertEqual(asked.plain, 1)
        XCTAssertEqual(asked.resume, 0, "past the far point there is nothing "
                       + "left to pin through")
        XCTAssertEqual(asked.to?.latitude ?? 0, origin.latitude, accuracy: 0.0001)
    }

    func test_driving_near_the_far_point_counts_as_passing_it() {
        let nav = loopDrive(Asked())
        // Beside the far point but matched short of it on the line — which is
        // what a coarse fix on a real loop looks like.
        nav.update(Fixture.fix(CLLocationCoordinate2D(
            latitude: turnaround.latitude, longitude: turnaround.longitude + 0.0002)))
        XCTAssertTrue(nav.passedTurnaround)
    }

    func test_passing_the_far_point_never_un_latches() {
        let nav = loopDrive(Asked())
        nav.update(Fixture.fixAt(5_000))
        XCTAssertTrue(nav.passedTurnaround)
        // A fix that matches earlier on the line — GPS jitter, or the return leg
        // of a real loop running back beside the outbound one — must not put the
        // far point back in front of the driver.
        nav.update(Fixture.fixAt(4_000))
        XCTAssertTrue(nav.passedTurnaround)
    }

    // MARK: - An ordinary route is untouched

    func test_a_route_with_no_far_point_reroutes_exactly_as_before() async {
        let expectation = expectation(description: "plain reroute asked")
        let destination = Fixture.north(5_000)
        let asked = Asked()
        let nav = NavigationModel(route: Fixture.straightRoute(),
                                  destination: destination,
                                  pref: 1.0, weights: [:])
        nav.fetchLoopResume = { _, _, _, _, _, _ in
            asked.resume += 1
            throw RouteService.ServiceError.badResponse
        }
        nav.fetchRoute = { _, to, _, _, _ in
            asked.plain += 1
            asked.to = to
            expectation.fulfill()
            return Fixture.response(fastest: Fixture.straightRoute(),
                                    scenic: Fixture.straightRoute())
        }
        nav.update(Fixture.fixAt(100))
        goOffRoute(nav, at: 2_000)
        await fulfillment(of: [expectation], timeout: 2)
        XCTAssertEqual(asked.resume, 0, "a point-to-point trip has no waypoint "
                       + "to pin through")
        XCTAssertEqual(asked.to?.latitude ?? 0, destination.latitude, accuracy: 0.0001)
        XCTAssertFalse(nav.passedTurnaround)
    }

    // MARK: - Arrival

    func test_a_loop_does_not_announce_arrival_while_sitting_at_the_start() {
        // The case that makes a loop different: the driver begins the drive
        // within a few metres of the destination, with the whole loop ahead.
        let nav = NavigationModel(route: loopRoute(), destination: origin,
                                  pref: 1.0, weights: [:], trace: nil,
                                  turnaround: turnaround)
        nav.update(Fixture.fixAt(0))
        nav.update(Fixture.fixAt(20))
        XCTAssertFalse(nav.arrived, "arrival must be having driven the line, "
                       + "not standing next to the pin")
    }

    func test_a_loop_announces_arrival_once_it_is_actually_driven() {
        let nav = NavigationModel(route: loopRoute(), destination: origin,
                                  pref: 1.0, weights: [:], trace: nil,
                                  turnaround: turnaround)
        nav.update(Fixture.fixAt(100))
        nav.update(Fixture.fixAt(5_000))
        nav.update(Fixture.fixAt(9_990))
        XCTAssertTrue(nav.arrived)
    }
}
