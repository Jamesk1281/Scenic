import CoreLocation
import XCTest
@testable import Scenic

/// The drive logic. None of this can be checked by looking at the app, and a
/// real drive tests it once, slowly, in one shape — so it is tested here.
@MainActor
final class NavigationModelTests: XCTestCase {

    private func nav(_ route: RouteFeature? = nil,
                     destination: CLLocationCoordinate2D? = nil,
                     pref: Double = 0.8) -> NavigationModel {
        let feature = route ?? Fixture.straightRoute()
        return NavigationModel(
            route: feature,
            destination: destination ?? Fixture.north(5000),
            pref: pref, weights: [:])
    }

    // MARK: - Joining the route

    func test_off_route_recovery_stays_disarmed_until_the_driver_joins() {
        let model = nav()
        // Standing 2 km east of the line: far off it, but this is the planned
        // trip, not a wrong turn.
        model.update(Fixture.fix(CLLocationCoordinate2D(
            latitude: 42.0, longitude: -71.0 + 2000 / 82_600)))
        XCTAssertFalse(model.hasJoinedRoute)
        XCTAssertFalse(model.isRerouting)
        XCTAssertGreaterThan(model.distanceToRouteStart, 1000)
        // ...and the whole trip is still ahead of them
        XCTAssertEqual(model.remainingMeters, 5000, accuracy: 1)
    }

    func test_joining_the_route_latches() {
        let model = nav()
        model.update(Fixture.fixAt(500))
        XCTAssertTrue(model.hasJoinedRoute)
        // wandering off afterwards must not un-join
        model.update(Fixture.fix(CLLocationCoordinate2D(
            latitude: 42.01, longitude: -71.0 + 500 / 82_600)))
        XCTAssertTrue(model.hasJoinedRoute)
    }

    // MARK: - Step advancement

    func test_steps_advance_as_each_maneuver_is_passed() {
        let model = nav()
        model.update(Fixture.fixAt(100))
        XCTAssertEqual(model.currentInstruction, "Turn right onto Elm Street")

        model.update(Fixture.fixAt(1500))
        XCTAssertEqual(model.currentInstruction, "Turn left onto Oak Street")

        model.update(Fixture.fixAt(3500))
        XCTAssertEqual(model.currentInstruction, "Arrive at your destination")
    }

    func test_a_maneuver_missed_between_fixes_does_not_strand_the_banner() {
        // The defect this guards. Steps used to advance only while within 25 m of
        // the maneuver — a 50 m window — and at 65 mph fixes land about 29 m
        // apart, so one fix dropped for poor accuracy (an overpass, an
        // interchange) could straddle it. You approach a maneuver exactly once, so
        // the step then never advanced again and the banner showed a stale
        // instruction for the rest of the drive.
        let model = nav()
        model.update(Fixture.fixAt(900))
        XCTAssertEqual(model.currentInstruction, "Turn right onto Elm Street")

        // The next fix lands 200 m later: the maneuver at 1000 m was never
        // within 25 m of any fix.
        model.update(Fixture.fixAt(1100))
        XCTAssertEqual(model.currentInstruction, "Turn left onto Oak Street",
                       "a maneuver passed between fixes must still advance")
    }

    func test_a_whole_leg_skipped_between_fixes_still_lands_on_the_right_step() {
        let model = nav()
        model.update(Fixture.fixAt(100))
        model.update(Fixture.fixAt(3400))   // past two maneuvers at once
        XCTAssertEqual(model.currentInstruction, "Arrive at your destination")
    }

    func test_stopping_at_a_maneuver_does_not_drop_it_from_the_banner() {
        // The advance condition used to fire on equality — `stepRemaining >=
        // remaining` — so the instant the driver's progress drew level with a
        // maneuver the banner moved past it. Standing at the turn is when you
        // most need to be told about it, and at a light that is where you sit.
        //
        // Level is also exactly where a freshly adopted route puts you: its
        // first maneuver is at the line's origin, so the distance from it to
        // the end and the distance you have left are the same number. That is
        // how "one step ahead of where it should place me" reached a car.
        let model = nav()
        model.update(Fixture.fixAt(1000))          // dead level with the maneuver
        XCTAssertEqual(model.currentInstruction, "Turn right onto Elm Street")
        XCTAssertEqual(model.distanceToNext, 0, accuracy: 15)
    }

    func test_steps_never_go_backwards() {
        let model = nav()
        model.update(Fixture.fixAt(1500))
        let advanced = model.currentStep
        // GPS jitter pulling the fix back down the road must not rewind the
        // instruction the driver is following.
        model.update(Fixture.fixAt(1400))
        model.update(Fixture.fixAt(1450))
        XCTAssertEqual(model.currentStep, advanced)
    }

    func test_distance_to_next_is_measured_along_the_route() {
        let model = nav()
        model.update(Fixture.fixAt(700))
        // 300 m of road to the maneuver at 1000 m.
        XCTAssertEqual(model.distanceToNext, 300, accuracy: 15)
    }

    func test_remaining_distance_follows_the_road() {
        let model = nav()
        model.update(Fixture.fixAt(2000))
        XCTAssertEqual(model.remainingMeters, 3000, accuracy: 15)
        XCTAssertEqual(model.remainingMinutes, 6, accuracy: 0.1)   // 10 min * 3/5
    }

    // MARK: - Arrival

    func test_reaching_the_end_of_the_line_arrives() {
        let model = nav()
        model.update(Fixture.fixAt(500))
        model.update(Fixture.fixAt(4990))
        XCTAssertTrue(model.arrived)
        XCTAssertEqual(model.remainingMeters, 0)
    }

    func test_arriving_works_when_the_pin_sits_off_road() {
        // Search pins land on rooftops and town greens; the route can only end
        // at the nearest road node, so the line's own end has to count.
        let model = nav(destination: CLLocationCoordinate2D(
            latitude: Fixture.north(5000).latitude, longitude: -71.0 + 300 / 82_600))
        model.update(Fixture.fixAt(500))
        model.update(Fixture.fixAt(4995))
        XCTAssertTrue(model.arrived)
    }

    func test_parking_short_of_the_end_of_the_route_arrives() {
        // The defect, measured on 2026-08-22: the driver stopped 118 m from the
        // end of the route and sat there four minutes. `drivenTheLine` wants
        // 40 m of route left and `stoppedAtThePin` wants to be 40 m from a pin
        // that was 102 m from any road, so neither could fire and the drive was
        // recorded as abandoned. The route ends at a junction; the space you
        // park in is the other side of a kerb.
        let model = nav()
        var clock = Date()
        model.now = { clock }
        model.update(Fixture.fixAt(500))
        // 118 m short, as they really stopped, and stationary past the 90 s bar.
        for _ in 0..<4 {
            model.update(Fixture.movingFix(Fixture.north(4880), course: 0, speed: 0))
            clock = clock.addingTimeInterval(40)
        }
        XCTAssertTrue(model.arrived)
        XCTAssertEqual(model.remainingMeters, 0)
    }

    func test_a_long_light_short_of_the_destination_is_not_an_arrival() {
        // The risk this buys: `arrived` never un-latches, so latching at a red
        // light throws away the rest of the recording. Half a minute stopped is
        // a signal, not a parking space.
        let model = nav()
        var clock = Date()
        model.now = { clock }
        model.update(Fixture.fixAt(500))
        for _ in 0..<3 {
            model.update(Fixture.movingFix(Fixture.north(4880), course: 0, speed: 0))
            clock = clock.addingTimeInterval(10)
        }
        XCTAssertFalse(model.arrived)
    }

    func test_parking_with_the_trip_still_ahead_of_you_is_not_an_arrival() {
        // Lunch, fuel, a photograph. Being stationary is only arrival when
        // there is essentially no route left.
        let model = nav()
        var clock = Date()
        model.now = { clock }
        model.update(Fixture.fixAt(500))
        for _ in 0..<10 {
            model.update(Fixture.movingFix(Fixture.north(2000), course: 0, speed: 0))
            clock = clock.addingTimeInterval(60)
        }
        XCTAssertFalse(model.arrived)
    }

    func test_a_phone_with_no_opinion_on_speed_cannot_latch_arrival() {
        // CoreLocation reports -1 when it will not say. Reading that as "not
        // moving" would arrive on the first fix within 250 m of the end, on
        // exactly the phones whose data is least trustworthy.
        let model = nav()
        var clock = Date()
        model.now = { clock }
        model.update(Fixture.fixAt(500))
        for _ in 0..<5 {
            model.update(Fixture.fixAt(4880))          // speed -1
            clock = clock.addingTimeInterval(60)
        }
        XCTAssertFalse(model.arrived)
    }

    func test_passing_near_the_destination_early_is_not_an_arrival() {
        // `arrived` never un-latches, so a route that merely runs past the
        // destination pin on its way out used to end the drive on the spot — and
        // an out-and-back scenic route does exactly that.
        let model = nav(Fixture.outAndBackRoute(), destination: Fixture.north(500))
        model.update(Fixture.fixAt(300))            // joins the route
        XCTAssertTrue(model.hasJoinedRoute)

        model.update(Fixture.fixAt(500))            // right beside the pin, 5 km left
        XCTAssertFalse(model.arrived, "passing the pin outbound is not arriving")

        model.update(Fixture.fixAt(2000))
        XCTAssertFalse(model.arrived)
    }

    func test_the_out_and_back_route_does_arrive_at_its_end() {
        let model = nav(Fixture.outAndBackRoute(), destination: Fixture.north(500))
        model.update(Fixture.fixAt(300))
        for metres in stride(from: 500.0, through: 3000.0, by: 250) {
            model.update(Fixture.fixAt(metres))
        }
        XCTAssertFalse(model.arrived, "still on the outbound leg")
        // now the return leg, which ends 500 m north of the origin
        for metres in stride(from: 2750.0, through: 750.0, by: -250) {
            model.update(Fixture.fixAt(metres))
            XCTAssertFalse(model.arrived, "not there yet at \(metres) m")
        }
        model.update(Fixture.fixAt(500))
        XCTAssertTrue(model.arrived)
    }

    func test_the_return_leg_is_matched_to_the_return_leg() {
        // Driving back down a road already driven must not re-match the
        // outbound pass and put the remaining distance back up.
        let model = nav(Fixture.outAndBackRoute(), destination: Fixture.north(500))
        model.update(Fixture.fixAt(300))
        for metres in stride(from: 500.0, through: 3000.0, by: 250) {
            model.update(Fixture.fixAt(metres))
        }
        let atTurnaround = model.remainingMeters
        model.update(Fixture.fixAt(2500))          // now heading back south
        XCTAssertLessThan(model.remainingMeters, atTurnaround,
                          "the return leg should be eating into the trip, not adding to it")
        XCTAssertEqual(model.remainingMeters, 2000, accuracy: 60)
    }

    func test_updates_after_arrival_are_ignored() {
        let model = nav()
        model.update(Fixture.fixAt(500))
        model.update(Fixture.fixAt(4995))
        XCTAssertTrue(model.arrived)
        model.update(Fixture.fixAt(2000))
        XCTAssertTrue(model.arrived)
        XCTAssertEqual(model.remainingMeters, 0)
    }
}
