import CoreLocation
import XCTest
@testable import Scenic

/// The decode is the contract with the backend. A field renamed on the server
/// is a silent failure on the phone, so the shape is pinned here against the
/// literal JSON `server/app.py` returns.
final class ModelsTests: XCTestCase {

    /// Trimmed from a real `GET /api/route` response.
    private let payload = """
    {"fastest": {"type": "Feature",
      "geometry": {"type": "LineString",
                   "coordinates": [[-71.8023, 42.2626], [-71.7, 42.3], [-71.0657, 42.3551]]},
      "properties": {"km": 71.2, "minutes": 46.1, "mean_score": 3.42,
        "scenery_km": {"water": 4.1, "coast": 0.0, "forest/park": 12.5,
                       "hills": 0.0, "farmland": 1.2, "town": 20.3},
        "steps": [{"instruction": "Head east on Main Street", "lat": 42.2626,
                   "lon": -71.8023, "distance_m": 820}]}},
     "scenic": {"type": "Feature",
      "geometry": {"type": "LineString", "coordinates": [[-71.8, 42.26], [-71.07, 42.35]]},
      "properties": {"km": 77.2, "minutes": 86.1, "mean_score": 6.0,
        "scenery_km": {"water": 21.0, "coast": 0.0, "forest/park": 30.4,
                       "hills": 9.8, "farmland": 3.0, "town": 25.1},
        "steps": [{"instruction": "Arrive at your destination", "lat": 42.3551,
                   "lon": -71.0657, "distance_m": 0}]}}}
    """.data(using: .utf8)!

    private func decoded() throws -> RouteResponse {
        try JSONDecoder().decode(RouteResponse.self, from: payload)
    }

    func test_decodes_both_routes() throws {
        let response = try decoded()
        XCTAssertEqual(response.fastest.properties.km, 71.2)
        XCTAssertEqual(response.scenic.properties.minutes, 86.1)
        XCTAssertEqual(response.scenic.properties.mean_score, 6.0)
    }

    func test_geojson_coordinates_are_flipped_to_mapkit_order() {
        // GeoJSON is [lon, lat]; CLLocationCoordinate2D is (lat, lon). Getting
        // this backwards puts Massachusetts in the Indian Ocean.
        let response = try! decoded()
        let first = response.fastest.coordinates.first!
        XCTAssertEqual(first.latitude, 42.2626, accuracy: 1e-6)
        XCTAssertEqual(first.longitude, -71.8023, accuracy: 1e-6)
    }

    func test_steps_decode_with_their_maneuver_point() throws {
        let step = try decoded().fastest.properties.steps.first!
        XCTAssertEqual(step.instruction, "Head east on Main Street")
        XCTAssertEqual(step.distance_m, 820)
        XCTAssertEqual(step.coordinate.latitude, 42.2626, accuracy: 1e-6)
    }

    // MARK: - The scenery breakdown

    /// The labels the server sends, from SCENERY_BREAKDOWN in pipeline/router.py.
    /// `test_the_client_and_server_agree_on_the_labels` in tests/test_routing.py
    /// asserts the same list from the other side, so a type renamed on either
    /// side fails a suite instead of quietly dropping a bar from the app.
    private let serverLabels = ["water", "coast", "forest/park", "hills",
                                "farmland", "town"]

    func test_every_server_label_can_be_displayed() throws {
        let props = try decoded().scenic.properties
        let shown = Set(props.sceneryBreakdown.map(\.label))
        let sent = Set(props.scenery_km.keys)
        XCTAssertEqual(Set(serverLabels), sent, "fixture drifted from the server")
        // everything the server sent with a non-zero length is shown
        XCTAssertEqual(shown, Set(sent.filter { props.scenery_km[$0]! > 0 }))
    }

    func test_features_the_route_never_touches_are_dropped() throws {
        let breakdown = try decoded().scenic.properties.sceneryBreakdown
        XCTAssertFalse(breakdown.contains { $0.label == "coast" },
                       "a 0 km feature is noise, not information")
    }

    func test_breakdown_keeps_its_display_order() throws {
        let breakdown = try decoded().scenic.properties.sceneryBreakdown
        XCTAssertEqual(breakdown.map(\.label),
                       ["forest/park", "water", "hills", "farmland", "town"])
    }

    func test_an_unknown_label_from_the_server_is_ignored_not_crashed() {
        // Adding a beauty type server-side ships before the app catches up.
        let json = """
        {"type":"Feature","geometry":{"coordinates":[[0,0],[1,1]]},
         "properties":{"km":1,"minutes":1,"mean_score":5,
           "scenery_km":{"water":2.0,"volcanoes":9.9},"steps":[]}}
        """.data(using: .utf8)!
        let feature = try! JSONDecoder().decode(RouteFeature.self, from: json)
        XCTAssertEqual(feature.properties.sceneryBreakdown.map(\.label), ["water"])
    }

    // MARK: - Beauty types

    func test_beauty_types_match_the_weights_the_app_sends() {
        // BeautyType.all mirrors BEAUTY_TYPES in pipeline/router.py; the
        // apiNames are what `w_<name>` is built from, so a typo silently makes
        // a slider do nothing.
        XCTAssertEqual(Set(BeautyType.all.map(\.apiName)),
                       ["coast", "forest", "town", "water", "hills", "farm"])
        XCTAssertTrue(BeautyType.weightRange.contains(BeautyType.neutralWeight))
    }

    func test_neutral_sits_at_the_middle_of_the_slider() {
        // "Centered means no preference" is what the tune screen tells the user.
        let range = BeautyType.weightRange
        XCTAssertEqual((range.lowerBound + range.upperBound) / 2,
                       BeautyType.neutralWeight, accuracy: 1e-9)
    }
}
