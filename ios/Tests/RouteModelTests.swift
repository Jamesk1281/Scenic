import CoreLocation
import XCTest
@testable import Scenic

/// What the planning screen is holding once a drive is over.
///
/// A finished drive used to leave its whole plan armed — the origin it departed
/// from and the routes computed for that origin — and `RoutePanel` renders the
/// start button whenever a `response` exists. So arriving somewhere handed the
/// driver a button that replayed the drive they had just completed, from a place
/// they were no longer standing. On 2026-08-25 that button was tapped 86 seconds
/// after arriving in Needham and recorded a parked car for nine minutes, and it
/// could not be stopped: arrival is gated on having joined the route, which a
/// car 38 km from the line's start never does.
///
/// These tests are about `RouteModel`'s state after `endNavigation()`, not about
/// progress along a route, so the fixture route below is a bare two-point line.
@MainActor
final class RouteModelTests: XCTestCase {

    /// The origin the 2026-08-25 phantom drive carried, straight from that
    /// trace's header (`from: [42.47824, -71.62095]`).
    private static let harvard = CLLocationCoordinate2D(latitude: 42.47824, longitude: -71.62095)

    /// Where the car actually was — Needham, ~38 km from that origin.
    private static let needham = CLLocationCoordinate2D(latitude: 42.28090, longitude: -71.23780)

    /// A model in the state the app was really in the moment the Harvard→Needham
    /// drive latched `arrived`: mid-drive, with the plan it left Harvard on.
    ///
    /// 49.2 km is that drive's real length, between two points 38 km apart —
    /// which is just what a scenic route between them looks like.
    private func modelMidDrive() -> RouteModel {
        let leg = Fixture.decode(Fixture.feature(
            coordinates: [[Self.harvard.longitude, Self.harvard.latitude],
                          [Self.needham.longitude, Self.needham.latitude]],
            km: 49.2, minutes: 62,
            steps: [(Self.harvard, "Head south on the scenic route", nil),
                    (Self.needham, "Arrive at your destination", nil)]))

        let model = RouteModel()
        model.start = Self.harvard
        model.startQuery = "My Location · Ayer Road, Harvard"
        model.end = Self.needham
        model.endQuery = "Needham, MA"
        model.response = Fixture.response(fastest: leg, scenic: leg)
        model.startNavigation(leg)
        return model
    }

    // MARK: - The finished drive must not still be armed

    func test_a_finished_drive_leaves_no_route_armed() {
        let model = modelMidDrive()
        XCTAssertNotNil(model.nav, "precondition: a drive is running")
        XCTAssertNotNil(model.response, "precondition: a route is armed")

        model.endNavigation()

        // `RoutePanel` gates the results card and the start button on
        // `model.response` (`RoutePanel.swift:45`, `:52`). With no response
        // there is no button, so replaying the finished drive is structurally
        // impossible rather than merely discouraged.
        XCTAssertNil(model.response, "no response, no start button, no second drive")
        XCTAssertNil(model.nav, "and no drive left running")
    }

    func test_a_finished_drive_forgets_the_origin_it_departed_from() {
        let model = modelMidDrive()
        model.endNavigation()

        // Leaving the text behind would be worse than leaving the coordinate:
        // a field still reading "Ayer Road, Harvard" is how the driver would
        // fail to notice the trip is being planned from an hour ago.
        XCTAssertNil(model.start, "Harvard is where the driver was, not where they are")
        XCTAssertTrue(model.startQuery.isEmpty, "the field must not claim a start that is gone")
    }

    func test_a_finished_drive_keeps_the_destination() {
        // This locks a product decision rather than the defect — it passed
        // before the fix too. `endNavigation()` deliberately isn't `clear()`,
        // which would take the destination with it. A destination is a place,
        // not a route computed from an origin the driver has left, and heading
        // back from where you just arrived is a real trip: one "My Location"
        // tap should be the whole of it.
        let model = modelMidDrive()
        model.endNavigation()

        XCTAssertEqual(model.end?.latitude, Self.needham.latitude)
        XCTAssertEqual(model.end?.longitude, Self.needham.longitude)
        XCTAssertEqual(model.endQuery, "Needham, MA")
    }

    func test_the_stale_plan_cannot_be_recomputed_without_a_fresh_origin() async {
        // The second lock. Clearing `response` removes the button; clearing
        // `start` means nothing can put a route back until the driver says
        // where they now are. Without it a stray re-route — a slider nudge, a
        // weights reset — would quietly recompute Harvard→Needham and re-arm
        // the screen from 38 km away.
        let model = modelMidDrive()
        model.endNavigation()

        await model.computeRoute()

        // `errorText` is part of the assertion because it is how a request that
        // was actually attempted announces itself when the backend is down. A
        // no-op leaves all three untouched.
        XCTAssertNil(model.response, "a route with no origin is not a route")
        XCTAssertNil(model.errorText, "computeRoute should not have reached the network at all")
        XCTAssertFalse(model.isLoading)
    }

    // MARK: - Beauty weights out of the box

    func test_a_fresh_model_starts_every_type_at_its_own_default() {
        let model = RouteModel()
        for type in BeautyType.all {
            XCTAssertEqual(model.weights[type.apiName], type.defaultWeight,
                           "\(type.apiName) did not start at its default")
        }
        // The one that matters: this is what puts `w_town=0` on the wire.
        XCTAssertEqual(model.weights["town"], 0.0)
    }

    func test_an_untouched_model_does_not_claim_to_be_tuned() {
        // `isTuned` lights the Tune button. Measured against neutral rather
        // than against each type's default, shipping `town` at zero would light
        // it on every launch and the highlight would stop meaning anything.
        XCTAssertFalse(RouteModel().isTuned)
    }

    func test_moving_a_slider_marks_the_model_tuned() {
        let model = RouteModel()
        model.weights["town"] = BeautyType.neutralWeight
        XCTAssertTrue(model.isTuned, "asking for town back is a tuning")
    }

    func test_reset_restores_the_defaults_and_not_neutral() {
        // Reset means "undo my tuning". Resetting town to 1.0 would quietly
        // switch on the one type the app deliberately ships off.
        let model = RouteModel()
        model.weights["town"] = 2.0
        model.weights["coast"] = 0.0

        model.resetWeights()

        XCTAssertEqual(model.weights["town"], 0.0)
        XCTAssertEqual(model.weights["coast"], BeautyType.neutralWeight)
        XCTAssertFalse(model.isTuned)
    }
}
