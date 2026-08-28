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
                   "lon": -71.8023, "distance_m": 820, "name": "Main Street"}]}},
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

    /// The road each step goes onto. `pipeline/router.py` has always sent it;
    /// this side dropped it on the floor for want of a declaration, which is
    /// the whole reason the nav screen could not say what road you were on.
    func test_a_step_carries_the_road_it_puts_you_on() throws {
        let step = try decoded().fastest.properties.steps.first!
        XCTAssertEqual(step.name, "Main Street")
    }

    /// The arrival step really does carry an empty name (`router.py:1659`), and
    /// so does any leg whose way has neither a `name` nor a `ref`. Empty has to
    /// stay distinguishable from a road actually called something, because
    /// `NavigationModel.currentRoad` shows nothing for it rather than guessing.
    func test_an_unnamed_road_decodes_as_empty_not_missing() throws {
        let step = try decoded().scenic.properties.steps.first!
        XCTAssertEqual(step.instruction, "Arrive at your destination")
        XCTAssertNil(step.name, "this fixture omits the key entirely")

        let json = """
        {"instruction":"Arrive at your destination","lat":42.1,"lon":-71.2,
         "distance_m":0,"name":""}
        """.data(using: .utf8)!
        XCTAssertEqual(try JSONDecoder().decode(RouteStep.self, from: json).name, "")
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
        // Everything the server sent that has a mile in it is shown. The test is
        // the label's own rule and not `> 0`, which is what let a "0 mi" row
        // through — see `test_a_feature_under_a_mile_is_dropped`.
        XCTAssertEqual(shown,
                       Set(sent.filter { props.scenery_km[$0]!.wholeMilesFromKm > 0 }))
    }

    func test_features_the_route_never_touches_are_dropped() throws {
        let breakdown = try decoded().scenic.properties.sceneryBreakdown
        XCTAssertFalse(breakdown.contains { $0.label == "coast" },
                       "a 0 km feature is noise, not information")
    }

    func test_a_feature_under_a_mile_is_dropped() {
        // The bug this rule exists for. 0.3 km of farmland cleared a `km > 0`
        // filter and then truncated to "farmland  0 mi" beside a dot-sized bar,
        // telling the driver about a feature the drive doesn't have. Seen on a
        // real 24 mi loop out of Needham.
        let json = """
        {"type":"Feature","geometry":{"coordinates":[[0,0],[1,1]]},
         "properties":{"km":40,"minutes":60,"mean_score":5,
           "scenery_km":{"farmland":0.3,"water":21.0},"steps":[]}}
        """.data(using: .utf8)!
        let props = try! JSONDecoder().decode(RouteFeature.self, from: json).properties
        XCTAssertEqual(props.sceneryBreakdown.map(\.label), ["water"])
    }

    func test_a_feature_that_rounds_up_to_a_mile_is_kept() {
        // The other side of the same line: 1.2 km is 0.75 mi, which rounds to 1
        // and is worth a row. Truncating would have shown it as "0 mi".
        let json = """
        {"type":"Feature","geometry":{"coordinates":[[0,0],[1,1]]},
         "properties":{"km":40,"minutes":60,"mean_score":5,
           "scenery_km":{"hills":1.2},"steps":[]}}
        """.data(using: .utf8)!
        let props = try! JSONDecoder().decode(RouteFeature.self, from: json).properties
        XCTAssertEqual(props.sceneryBreakdown.map(\.label), ["hills"])
        XCTAssertEqual(props.sceneryBreakdown[0].km.wholeMilesFromKm, 1)
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

    // MARK: - Maneuvers

    func test_a_structured_maneuver_decodes() {
        let json = """
        {"instruction":"Take exit 26 toward I 93 North: Boston","lat":42.1,
         "lon":-71.2,"distance_m":320,"type":"exit","modifier":"slight right",
         "exit_ref":"26","destination":"I 93 North: Boston","roundabout_exit":0}
        """.data(using: .utf8)!
        let step = try! JSONDecoder().decode(RouteStep.self, from: json)
        XCTAssertEqual(step.maneuver, .exit)
        XCTAssertEqual(step.exit_ref, "26")
        XCTAssertEqual(step.destination, "I 93 North: Boston")
    }

    func test_a_maneuver_type_the_app_does_not_know_still_decodes() {
        // The backend may learn a maneuver before this app is updated. An
        // unrecognised type must degrade to `unknown`, not fail the decode:
        // the whole route would be lost mid-drive over a word, and the
        // `instruction` is perfectly readable either way.
        let json = """
        {"instruction":"Board the ferry","lat":42.1,"lon":-71.2,
         "distance_m":10,"type":"ferry","modifier":"straight",
         "exit_ref":"","destination":"","roundabout_exit":0}
        """.data(using: .utf8)!
        let step = try! JSONDecoder().decode(RouteStep.self, from: json)
        XCTAssertEqual(step.maneuver, .unknown)
        XCTAssertEqual(step.instruction, "Board the ferry")
    }

    func test_a_step_from_before_the_maneuver_rework_still_decodes() {
        // Old cached responses and older fixtures carry only the four original
        // fields; they must not become undecodable.
        let json = """
        {"instruction":"Turn left onto Elm Street","lat":42.1,"lon":-71.2,
         "distance_m":120}
        """.data(using: .utf8)!
        let step = try! JSONDecoder().decode(RouteStep.self, from: json)
        XCTAssertEqual(step.maneuver, .unknown)
        XCTAssertNil(step.exit_ref)
        XCTAssertNil(step.name)
    }

    func test_left_and_right_turns_do_not_share_an_icon() {
        // The glyph is what a driver reads at a glance; both are type `turn`,
        // so the modifier is what has to distinguish them.
        let decode = { (modifier: String) -> RouteStep in
            let json = """
            {"instruction":"x","lat":0,"lon":0,"distance_m":1,"type":"turn",
             "modifier":"\(modifier)","exit_ref":"","destination":"",
             "roundabout_exit":0}
            """.data(using: .utf8)!
            return try! JSONDecoder().decode(RouteStep.self, from: json)
        }
        XCTAssertNotEqual(decode("left").symbol, decode("right").symbol)
        XCTAssertNotEqual(decode("left").symbol, decode("slight left").symbol)
    }

    /// A course just short of due north must not round its way out of the range
    /// the server accepts. `%.1f` turns 359.97 into "360.0", and both
    /// `_parse_heading` and `Router.snap` take a half-open 0..<360 — so the
    /// heading was silently dropped and the reroute fell back to nearer-end
    /// snapping precisely when a driver was heading north.
    func test_a_heading_just_short_of_north_stays_in_range() {
        for course in [359.95, 359.97, 359.99, 360.0] {
            let sent = RouteService.headingParameter(course)
            XCTAssertEqual(sent, "0.0",
                           "course \(course) was sent as \(sent), which the server drops")
        }
    }

    /// ...without disturbing the ordinary cases, including the one the existing
    /// reroute test covers.
    func test_ordinary_headings_are_sent_to_one_decimal() {
        XCTAssertEqual(RouteService.headingParameter(0), "0.0")
        XCTAssertEqual(RouteService.headingParameter(90), "90.0")
        XCTAssertEqual(RouteService.headingParameter(182.44), "182.4")
        XCTAssertEqual(RouteService.headingParameter(359.9), "359.9")
    }
}
