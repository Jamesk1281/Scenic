import CoreLocation
import XCTest
@testable import Scenic

/// `progress` is what every number on the drive screen is built from — how far
/// off the line the driver is, and how much road is left.
final class GeoTests: XCTestCase {

    private let line = (0...20).map { Fixture.north(Double($0) * 250) }   // 5 km north

    func test_a_point_on_the_line_is_not_off_route() {
        let here = progress(of: Fixture.north(1200), along: line)
        XCTAssertEqual(here.offRoute, 0, accuracy: 1)
        XCTAssertEqual(here.remaining, 3800, accuracy: 5)
    }

    func test_a_point_between_two_vertices_is_still_on_route() {
        // Vertices are 250 m apart here and can be kilometres apart on a real
        // road; measuring to the nearest *vertex* rather than the nearest
        // segment would call a driver on a straight highway "off route".
        let here = progress(of: Fixture.north(1125), along: line)
        XCTAssertEqual(here.offRoute, 0, accuracy: 1)
    }

    func test_offset_from_the_line_is_the_perpendicular_distance() {
        let east = CLLocationCoordinate2D(
            latitude: Fixture.north(1200).latitude,
            longitude: -71.0 + 100 / (111_320 * cos(42.0 * .pi / 180)))
        let here = progress(of: east, along: line)
        XCTAssertEqual(here.offRoute, 100, accuracy: 5)
        XCTAssertEqual(here.remaining, 3800, accuracy: 10)
    }

    func test_before_the_start_the_whole_route_is_left() {
        let here = progress(of: Fixture.north(-500), along: line)
        XCTAssertEqual(here.remaining, 5000, accuracy: 5)
        XCTAssertEqual(here.offRoute, 500, accuracy: 5)
    }

    func test_past_the_end_nothing_is_left() {
        let here = progress(of: Fixture.north(5400), along: line)
        XCTAssertEqual(here.remaining, 0, accuracy: 5)
    }

    func test_remaining_never_goes_negative() {
        for metres in stride(from: -1000.0, through: 6000.0, by: 137) {
            XCTAssertGreaterThanOrEqual(
                progress(of: Fixture.north(metres), along: line).remaining, 0)
        }
    }

    func test_remaining_decreases_as_the_driver_advances() {
        var last = Double.greatestFiniteMagnitude
        for metres in stride(from: 0.0, through: 5000.0, by: 250) {
            let remaining = progress(of: Fixture.north(metres), along: line).remaining
            XCTAssertLessThan(remaining, last + 1)
            last = remaining
        }
    }

    func test_a_degenerate_line_does_not_crash() {
        XCTAssertEqual(progress(of: Fixture.origin, along: []).offRoute, .infinity)
        let single = progress(of: Fixture.north(100), along: [Fixture.origin])
        XCTAssertEqual(single.offRoute, 100, accuracy: 5)
        XCTAssertEqual(single.remaining, 0)
    }

    func test_kilometres_convert_to_miles() {
        XCTAssertEqual(1.0.milesFromKm, 0.621371, accuracy: 1e-6)
        XCTAssertEqual(160.9344.milesFromKm, 100, accuracy: 0.01)
    }

    func test_whole_miles_round_rather_than_truncate() {
        // 24.9 km is 15.47 mi. Truncated that reads 15, which is the rounding
        // the scenery breakdown used to do on its way to showing "0 mi".
        XCTAssertEqual(24.9.wholeMilesFromKm, 15)
        XCTAssertEqual(25.5.wholeMilesFromKm, 16)
        // Anything below half a mile has no whole mile in it, which is what the
        // breakdown filter keys off.
        XCTAssertEqual(0.3.wholeMilesFromKm, 0)
        XCTAssertEqual(0.8.wholeMilesFromKm, 0)
        XCTAssertEqual(0.9.wholeMilesFromKm, 1)
    }

    func test_coordinates_compare_by_value() {
        XCTAssertTrue(Fixture.origin.matches(Fixture.origin))
        XCTAssertFalse(Fixture.origin.matches(Fixture.north(1)))
    }
}
