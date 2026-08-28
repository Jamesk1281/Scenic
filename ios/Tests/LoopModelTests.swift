import CoreLocation
import XCTest
@testable import Scenic

/// Loop-tab tests. Every one of these is a *sequence* of requests — which
/// direction comes next, which of two overlapping responses wins, what stays on
/// screen when a shuffle fails — so they go through `LoopModel.fetchLoop` with a
/// stub rather than a backend.
///
/// The fixtures build their JSON and decode it once. Deliberately not via
/// `Fixture.response`, which re-encodes through `Fixture.feature` and drops
/// every optional step field on the way; a loop test that quietly lost
/// `meta.sector` would pass while testing nothing.
@MainActor
final class LoopModelTests: XCTestCase {

    private static let start = CLLocationCoordinate2D(latitude: 42.28, longitude: -71.23)

    /// A decoded `LoopResponse`, built straight from JSON so the decoding path
    /// is exercised and a field renamed on the server fails here.
    private func loopResponse(
        km: Double = 40.0,
        targetKm: Double = 40.0,
        sector: String = "N",
        sectors: [String] = ["N", "NE", "E", "SE"],
        repeatedKm: Double = 0.0,
        note: String? = nil
    ) -> LoopResponse {
        // A closed square, so the first and last coordinate match the way a real
        // loop's do.
        let ring: [[Double]] = [[-71.23, 42.28], [-71.23, 42.38], [-71.13, 42.38],
                                [-71.13, 42.28], [-71.23, 42.28]]
        var object: [String: Any] = [
            "loop": [
                "type": "Feature",
                "geometry": ["type": "LineString", "coordinates": ring],
                "properties": [
                    "km": km, "minutes": km * 1.6, "mean_score": 5.8,
                    "scenery_km": ["water": 4.0, "forest/park": 9.0],
                    "steps": [
                        ["instruction": "Head north on Test Road", "lat": 42.28,
                         "lon": -71.23, "distance_m": 1000, "type": "depart"],
                        ["instruction": "Arrive back where you started",
                         "lat": 42.28, "lon": -71.23, "distance_m": 0,
                         "type": "arrive"],
                    ],
                ],
            ],
            "meta": [
                "target_km": targetKm, "km": km, "minutes": km * 1.6,
                "mean_score": 5.8, "beautiful_km": km * 0.4,
                "beautiful_score": 7.0, "repeated_km": repeatedKm,
                "turnaround": [42.38, -71.18], "sector": sector,
            ],
            "alternatives": sectors.map { ["sector": $0, "candidates": 500] },
        ]
        object["note"] = note as Any? ?? NSNull()
        let data = try! JSONSerialization.data(withJSONObject: object)
        return try! JSONDecoder().decode(LoopResponse.self, from: data)
    }

    private func model(
        _ handler: @escaping (CLLocationCoordinate2D, Double, String?,
                              [String: Double]) async throws -> LoopResponse
    ) -> LoopModel {
        let model = LoopModel(locationManager: LocationManager())
        model.start = Self.start
        model.fetchLoop = handler
        return model
    }

    // MARK: - Decoding

    func test_the_response_decodes_into_a_route_the_rest_of_the_app_understands() {
        let response = loopResponse()
        // The loop is a plain RouteFeature, which is what lets NavigationModel
        // and the map draw it with nothing loop-specific.
        XCTAssertEqual(response.loop.coordinates.count, 5)
        XCTAssertEqual(response.loop.properties.steps.first?.maneuver, .depart)
        XCTAssertEqual(response.loop.properties.steps.last?.maneuver, .arrive)
        XCTAssertEqual(response.meta.sector, "N")
        XCTAssertEqual(response.meta.turnaroundCoordinate?.latitude ?? 0, 42.38,
                       accuracy: 0.0001)
        XCTAssertNil(response.note)
    }

    func test_the_line_closes_on_itself() {
        let coordinates = loopResponse().loop.coordinates
        XCTAssertEqual(coordinates.first!.latitude, coordinates.last!.latitude,
                       accuracy: 0.000001)
        XCTAssertEqual(coordinates.first!.longitude, coordinates.last!.longitude,
                       accuracy: 0.000001)
    }

    func test_the_derived_numbers_read_the_way_the_panel_shows_them() {
        let meta = loopResponse(km: 42.0, targetKm: 40.0, repeatedKm: 2.1).meta
        XCTAssertEqual(meta.repeatedFraction, 0.05, accuracy: 0.001)
        XCTAssertEqual(meta.distanceError, 0.05, accuracy: 0.001)
    }

    func test_a_note_from_the_server_is_carried_through() {
        let response = loopResponse(note: "The roads here don't really make a loop this short")
        XCTAssertEqual(response.note?.isEmpty, false)
    }

    // MARK: - Asking for a loop

    func test_the_first_loop_lets_the_server_choose_the_direction() async {
        var asked: [String?] = []
        let model = model { _, _, sector, _ in
            asked.append(sector)
            return self.loopResponse()
        }
        await model.generate()
        XCTAssertEqual(asked, [nil])
        XCTAssertTrue(model.hasLoop)
        XCTAssertEqual(model.directionCount, 4)
    }

    func test_regenerate_walks_the_directions_that_exist_and_wraps() async {
        var asked: [String?] = []
        let model = model { _, _, sector, _ in
            asked.append(sector)
            // The server answers in whichever sector was requested.
            return self.loopResponse(sector: sector ?? "N")
        }
        await model.generate()
        for _ in 0..<4 { await model.regenerate() }
        // First request has no opinion; then N -> NE -> E -> SE -> back to N.
        XCTAssertEqual(asked, [nil, "NE", "E", "SE", "N"])
    }

    func test_it_never_asks_for_a_direction_the_server_did_not_offer() async {
        var asked: [String?] = []
        // Only two directions hold a loop here — a coastal start.
        let model = model { _, _, sector, _ in
            asked.append(sector)
            return self.loopResponse(sector: sector ?? "N", sectors: ["N", "S"])
        }
        await model.generate()
        for _ in 0..<3 { await model.regenerate() }
        XCTAssertEqual(asked, [nil, "S", "N", "S"])
        XCTAssertEqual(model.directionCount, 2)
    }

    func test_a_single_direction_leaves_the_choice_to_the_server() async {
        var asked: [String?] = []
        let model = model { _, _, sector, _ in
            asked.append(sector)
            return self.loopResponse(sectors: ["N"])
        }
        await model.generate()
        await model.regenerate()
        // Nothing to rotate through, so don't pin the server to the one sector
        // it already gave us — let it pick again.
        XCTAssertEqual(asked, [nil, nil])
    }

    // MARK: - The distance slider

    func test_the_slider_reads_back_what_the_server_clamped_it_to() async {
        let model = model { _, _, _, _ in self.loopResponse(km: 5.4, targetKm: 5.0) }
        model.targetKm = 1.0          // below the server's minimum
        await model.generate()
        // Otherwise the slider says 1 km while the map shows a 5 km loop.
        XCTAssertEqual(model.targetKm, 5.0)
    }

    func test_the_requested_distance_reaches_the_server() async {
        var asked: [Double] = []
        let model = model { _, km, _, _ in
            asked.append(km)
            return self.loopResponse(km: km, targetKm: km)
        }
        model.targetKm = 65
        await model.generate()
        XCTAssertEqual(asked, [65])
    }

    // MARK: - Overlapping and failing requests

    func test_a_superseded_response_does_not_overwrite_a_newer_one() async {
        // The slow request is the *older* one, exactly as a slider drag followed
        // by a regenerate would be.
        let model = model { _, km, _, _ in
            if km == 20 {
                try? await Task.sleep(nanoseconds: 40_000_000)
                return self.loopResponse(km: 20, targetKm: 20, sector: "S")
            }
            return self.loopResponse(km: 80, targetKm: 80, sector: "NW")
        }
        model.targetKm = 20
        let slow = Task { await model.generate() }
        try? await Task.sleep(nanoseconds: 5_000_000)
        model.targetKm = 80
        await model.generate()
        await slow.value
        XCTAssertEqual(model.response?.meta.sector, "NW")
        XCTAssertEqual(model.targetKm, 80)
    }

    func test_a_failed_shuffle_keeps_the_loop_already_on_screen() async {
        var calls = 0
        let model = model { _, _, _, _ in
            calls += 1
            if calls > 1 { throw RouteService.ServiceError.server("no loop there") }
            return self.loopResponse(sector: "N")
        }
        await model.generate()
        await model.regenerate()
        // Blanking the map because one direction came back empty would throw
        // away a drive the user was looking at.
        XCTAssertEqual(model.response?.meta.sector, "N")
        XCTAssertEqual(model.errorText, "no loop there")
    }

    func test_a_failed_first_search_leaves_nothing_on_screen() async {
        let model = model { _, _, _, _ in
            throw RouteService.ServiceError.server("outside the covered network")
        }
        await model.generate()
        XCTAssertNil(model.response)
        XCTAssertEqual(model.errorText, "outside the covered network")
    }

    func test_it_does_not_call_the_server_without_a_start() async {
        var called = false
        let model = model { _, _, _, _ in
            called = true
            return self.loopResponse()
        }
        model.start = nil
        await model.generate()
        XCTAssertFalse(called)
        XCTAssertFalse(model.hasLoop)
    }

    func test_the_beauty_weights_reach_the_server() async {
        var asked: [String: Double] = [:]
        let model = model { _, _, _, weights in
            asked = weights
            return self.loopResponse()
        }
        model.weights = ["coast": 4.0, "farm": 0.0]
        await model.generate()
        XCTAssertEqual(asked["coast"], 4.0)
        XCTAssertEqual(asked["farm"], 0.0)
    }

    func test_clearing_forgets_the_loop_and_the_start() async {
        let model = model { _, _, _, _ in self.loopResponse() }
        await model.generate()
        model.clear()
        XCTAssertNil(model.start)
        XCTAssertNil(model.response)
        XCTAssertEqual(model.startQuery, "")
    }
}
