import CoreLocation
import XCTest
@testable import Scenic

/// The settings that decide whether a drive is recorded at all.
///
/// These are pairings between Swift and `project.yml`, and every one of them
/// fails at the worst possible moment: `start()` is called when the driver taps
/// Start, which is the first thing that happens on a test drive and the last
/// place you want to find out.
@MainActor
final class LocationManagerTests: XCTestCase {

    func test_the_app_declares_the_background_mode_it_asks_for() {
        // `allowsBackgroundLocationUpdates = true` raises an exception if
        // UIBackgroundModes doesn't contain "location" — a pairing across two
        // files with nothing but this to hold it together.
        let modes = Bundle.main.object(forInfoDictionaryKey: "UIBackgroundModes") as? [String]
        XCTAssertEqual(modes, ["location"],
                       "set in ios/project.yml; LocationManager.start() depends on it")
    }

    func test_starting_navigation_does_not_raise() {
        // The exception above is a hard crash, not a Swift error, so nothing
        // catches it. Exercising the real call is the only thing that proves
        // the pairing holds.
        let manager = LocationManager()
        manager.start()
        manager.stop()
    }

    // MARK: - The drive must not depend on being watched

    func test_a_fix_reaches_the_drive_with_no_view_in_the_path() {
        // The defect this guards. `nav.update` used to hang off a SwiftUI
        // `onChange`, and SwiftUI suspends body evaluation when the scene stops
        // rendering — so a locked phone delivered one coalesced callback on
        // wake and dropped every fix in between. Steps stopped advancing,
        // arrival never fired, and the trace held a hole the length of the
        // drive nobody was looking at.
        //
        // No view is constructed anywhere in this test. That is the point.
        let manager = LocationManager()
        var seen: [CLLocation] = []
        manager.onFix = { seen.append($0) }

        manager.locationManager(CLLocationManager(),
                                didUpdateLocations: [Fixture.fixAt(100)])
        manager.locationManager(CLLocationManager(),
                                didUpdateLocations: [Fixture.fixAt(200)])

        XCTAssertEqual(seen.count, 2, "every fix should reach the drive, not just the last")
    }

    func test_a_junk_fix_still_never_reaches_the_drive() {
        // Delivering straight from the delegate must not smuggle past the
        // accuracy and staleness checks the drive relies on.
        let manager = LocationManager()
        var seen = 0
        manager.onFix = { _ in seen += 1 }

        let stale = CLLocation(
            coordinate: Fixture.origin, altitude: 0,
            horizontalAccuracy: 5, verticalAccuracy: 5,
            timestamp: Date(timeIntervalSinceNow: -600))
        let vague = CLLocation(
            coordinate: Fixture.origin, altitude: 0,
            horizontalAccuracy: 500, verticalAccuracy: 5, timestamp: Date())

        manager.locationManager(CLLocationManager(), didUpdateLocations: [stale])
        manager.locationManager(CLLocationManager(), didUpdateLocations: [vague])
        XCTAssertEqual(seen, 0)
    }

    func test_ending_a_drive_unhooks_it() {
        // Otherwise the next "My Location" tap on the planning screen would
        // still be feeding a drive that finished hours ago.
        let model = RouteModel()
        model.end = Fixture.north(5000)
        model.startNavigation(Fixture.straightRoute())
        XCTAssertNotNil(model.locationManager.onFix)

        model.endNavigation()
        XCTAssertNil(model.locationManager.onFix)
    }

    func test_traces_can_be_taken_off_the_phone() {
        // The whole export path. Without this key the traces are real, correct,
        // and unreachable behind the app sandbox — every drive recorded and no
        // way to read one.
        XCTAssertEqual(
            Bundle.main.object(forInfoDictionaryKey: "UIFileSharingEnabled") as? Bool, true,
            "set in ios/project.yml; Documents/traces is only reachable with it")
    }
}
