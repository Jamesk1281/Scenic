import CoreLocation
import XCTest
@testable import Scenic

/// An end-to-end drive: the real backend, the real decode, the real navigation
/// logic, over real Massachusetts geometry.
///
/// The synthetic fixtures elsewhere pin the maths; this pins the thing they
/// cannot, which is that a route as the router actually shapes it — hundreds of
/// maneuvers, some of them metres apart at an interchange, vertices wherever OSM
/// felt like putting them — can be driven from end to end without the banner
/// getting stuck or the trip ending early.
///
/// Skips unless a server is reachable, so it costs nothing in a plain checkout:
///     SCENIC_DATA=... python server/serve.py
///     cd ios && xcodebuild test -scheme Scenic -destination '...'
@MainActor
final class LiveDriveTests: XCTestCase {

    private static let baseURL = ProcessInfo.processInfo
        .environment["SCENIC_API"] ?? "http://127.0.0.1:5057"

    private func liveRoute(from: String, to: String, pref: Double,
                           weights: String = "") async throws -> RouteResponse {
        let url = URL(string: "\(Self.baseURL)/api/route?from=\(from)&to=\(to)"
                      + "&pref=\(pref)\(weights)")!
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 10
        do {
            let (data, _) = try await URLSession(configuration: configuration).data(from: url)
            return try JSONDecoder().decode(RouteResponse.self, from: data)
        } catch let error as URLError {
            // `URLError` only. A decode failure means the server answered and
            // the two sides disagree about the shape — which is the one thing
            // these tests exist to catch, and which a blanket `catch` reported
            // as a green skip indistinguishable from "no server running".
            throw XCTSkip("no Scenic API at \(Self.baseURL) — start "
                          + "server/serve.py (\(error.code))")
        }
    }

    private let worcester = "42.2626,-71.8023"
    private let boston = "42.3551,-71.0657"
    private let buzzardsBay = "41.6362,-70.9342"
    private let rockport = "42.6559,-70.6206"

    /// Walk the route's own geometry at `metresPerFix`, which is what a GPS
    /// stream looks like: fixes at a cadence, not at the maneuvers.
    private func fixes(along line: [CLLocationCoordinate2D],
                       metresPerFix: Double) -> [CLLocation] {
        // Starting at the route's own first point, the way a driver who taps
        // "Start" at the start of the route does.
        var out: [CLLocation] = line.first.map { [Fixture.fix($0)] } ?? []
        var carried = 0.0
        for (a, b) in zip(line, line.dropFirst()) {
            let start = CLLocation(latitude: a.latitude, longitude: a.longitude)
            let segment = start.distance(from: CLLocation(latitude: b.latitude,
                                                          longitude: b.longitude))
            guard segment > 0 else { continue }
            var travelled = metresPerFix - carried
            while travelled < segment {
                let t = travelled / segment
                out.append(Fixture.fix(CLLocationCoordinate2D(
                    latitude: a.latitude + (b.latitude - a.latitude) * t,
                    longitude: a.longitude + (b.longitude - a.longitude) * t)))
                travelled += metresPerFix
            }
            carried = segment - (travelled - metresPerFix)
        }
        if let last = line.last { out.append(Fixture.fix(last)) }
        return out
    }

    private func drive(_ response: RouteResponse, to destination: CLLocationCoordinate2D,
                       metresPerFix: Double, dropEvery: Int = 0) -> NavigationModel {
        let model = NavigationModel(route: response.scenic, destination: destination,
                                    pref: 0.8, weights: [:])
        // No backend during the drive: a reroute here would mean the logic
        // decided the driver had left a route they are being walked along.
        model.fetchRoute = { _, _, _, _, _ in throw URLError(.notConnectedToInternet) }

        var lastStep = 0
        var lastRemaining = Double.greatestFiniteMagnitude
        for (i, fix) in fixes(along: response.scenic.coordinates,
                              metresPerFix: metresPerFix).enumerated() {
            if dropEvery > 0 && i % dropEvery == 0 { continue }   // a filtered fix
            model.update(fix)
            XCTAssertGreaterThanOrEqual(model.currentStep, lastStep,
                                        "the instruction went backwards")
            lastStep = model.currentStep
            if model.arrived { break }
            XCTAssertLessThanOrEqual(model.remainingMeters, lastRemaining + 1,
                                     "distance remaining went up mid-drive")
            lastRemaining = model.remainingMeters
        }
        return model
    }

    func test_a_real_scenic_route_can_be_driven_from_end_to_end() async throws {
        let response = try await liveRoute(from: worcester, to: boston, pref: 1.0)
        let steps = response.scenic.properties.steps
        XCTAssertGreaterThan(steps.count, 20, "expected a route with real turns in it")

        // 29 m between fixes is 1 Hz at 65 mph.
        let model = drive(response, to: Fixture.fix(response.scenic.coordinates.last!).coordinate,
                          metresPerFix: 29)
        XCTAssertTrue(model.arrived, "drove the whole line without arriving")
        XCTAssertEqual(model.currentStep, steps.count - 1,
                       "finished on step \(model.currentStep) of \(steps.count - 1)")
    }

    /// The road readout, checked against a second instrument on the same real
    /// route.
    ///
    /// The fixture test pins the off-by-one on four invented steps. This pins it
    /// on a real Worcester–Boston route — 40-odd maneuvers of real ramps, forks
    /// and unnamed ways — by comparing the road shown against the road named
    /// *in the instruction of the step already driven through*. The server
    /// renders that sentence from the same leg label it puts in `name`
    /// (`_describe_turn`), so agreeing is a real check and disagreeing means the
    /// index is off by one.
    ///
    /// Parsing a road name out of an instruction is a trap in production — the
    /// wording varies by maneuver and the structured field exists — which is
    /// exactly why it is worth doing here: an independently rendered string is
    /// the only thing on hand that is not the field under test.
    func test_the_road_shown_matches_the_instruction_that_put_the_driver_there() async throws {
        let response = try await liveRoute(from: worcester, to: boston, pref: 1.0)
        let steps = response.scenic.properties.steps
        XCTAssertGreaterThan(steps.filter { !($0.name ?? "").isEmpty }.count, 20,
                             "expected a real route to name most of its roads")

        let model = NavigationModel(route: response.scenic,
                                    destination: response.scenic.coordinates.last!,
                                    pref: 0.8, weights: [:])
        model.fetchRoute = { _, _, _, _, _ in throw URLError(.notConnectedToInternet) }

        var checked = 0
        for fix in fixes(along: response.scenic.coordinates, metresPerFix: 29) {
            model.update(fix)
            if model.arrived { break }
            guard case .named(let road) = model.currentRoad, model.currentStep > 0
            else { continue }
            // "Turn left onto Bolton Road", "Continue onto X", "Merge onto X",
            // "Keep left to stay on X" — take the tail after the last " onto "
            // or " on ", and only when the sentence ends there.
            let driven = steps[model.currentStep - 1].instruction
            guard let said = ["onto ", "on "].lazy.compactMap({ marker in
                driven.range(of: " " + marker, options: .backwards)
                    .map { String(driven[$0.upperBound...]) }
            }).first, !said.isEmpty, !said.contains(":") else { continue }
            XCTAssertEqual(road, said,
                           "on step \(model.currentStep): \"\(driven)\" put the "
                           + "driver on \(said), the screen says \(road)")
            checked += 1
        }
        XCTAssertGreaterThan(checked, 100,
                             "too few fixes cross-checked to mean anything")
    }

    func test_every_maneuver_is_reached_even_with_fixes_dropped() async throws {
        // The defect this guards, on real geometry: one in three fixes thrown
        // away, as the accuracy filter does under an overpass or in a canyon.
        // Advancing by proximity, a maneuver skipped this way was skipped for
        // the rest of the drive.
        let response = try await liveRoute(from: worcester, to: boston, pref: 1.0)
        let steps = response.scenic.properties.steps
        let model = drive(response,
                          to: Fixture.fix(response.scenic.coordinates.last!).coordinate,
                          metresPerFix: 29, dropEvery: 3)
        XCTAssertTrue(model.arrived)
        XCTAssertEqual(model.currentStep, steps.count - 1,
                       "a dropped fix stranded the banner on step \(model.currentStep)")
    }

    func test_a_coarse_fix_stream_still_reaches_every_maneuver() async throws {
        // 120 m between fixes — a bad signal, or a fast road.
        let response = try await liveRoute(from: worcester, to: boston, pref: 1.0)
        let model = drive(response,
                          to: Fixture.fix(response.scenic.coordinates.last!).coordinate,
                          metresPerFix: 120)
        XCTAssertTrue(model.arrived)
        XCTAssertEqual(model.currentStep,
                       response.scenic.properties.steps.count - 1)
    }

    func test_the_long_coastal_route_drives_too() async throws {
        // Longer, and shaped by a tune slider, so the drive is over a route the
        // weights actually chose.
        let response = try await liveRoute(from: boston, to: buzzardsBay, pref: 0.8,
                                           weights: "&w_coast=4&w_town=0&w_farm=0")
        let model = drive(response,
                          to: Fixture.fix(response.scenic.coordinates.last!).coordinate,
                          metresPerFix: 29, dropEvery: 4)
        XCTAssertTrue(model.arrived)
        XCTAssertEqual(model.currentStep,
                       response.scenic.properties.steps.count - 1)
    }

    /// Boston → Rockport, not Boston → Buzzards Bay, and the reason is the
    /// second assertion rather than the first.
    ///
    /// The fastest arm is routed at `pref=0`, so its *path* is identical under
    /// both weight settings; what must differ is its *score*, because the
    /// server re-scores both cards with the caller's weights so the two are
    /// comparable. A server that scored the fastest card at neutral weights
    /// would be a real bug — the cards would be on different rulers — and this
    /// assertion is what catches it.
    ///
    /// On Buzzards Bay it could not. That fastest arm is 78-87% motorway
    /// scoring below 0.5, so reweighting has almost nothing to redistribute:
    /// the score really does move, 0.381331 -> 0.378151, but the API rounds
    /// `mean_score` to two decimals and both serialise as `0.38`. The
    /// assertion was reading a rounding artifact and could not tell it from
    /// the bug it exists to catch. Measured over seven Massachusetts pairs,
    /// Buzzards Bay is the only one that collapses; the rest separate by 0.12
    /// to 0.74. Rockport moves 2.03 -> 2.15 and is still a coastal
    /// destination, so the first assertion keeps its meaning too (coastal km
    /// 53.7 -> 63.8).
    ///
    /// If this starts failing again, check the delta before the wiring: a pair
    /// whose fastest route drifts onto more motorway will reproduce this
    /// exactly, and it is the pair that is wrong, not the server.
    func test_the_reported_scenery_reflects_the_weights_that_were_sent() async throws {
        let plain = try await liveRoute(from: boston, to: rockport, pref: 0.8)
        let coastal = try await liveRoute(from: boston, to: rockport, pref: 0.8,
                                          weights: "&w_coast=4&w_town=0&w_farm=0")
        // The whole point of the tune screen: asking for coast finds more coast.
        let plainCoast = plain.scenic.properties.scenery_km["coast"] ?? 0
        let coastalCoast = coastal.scenic.properties.scenery_km["coast"] ?? 0
        XCTAssertGreaterThan(coastalCoast, plainCoast)
        // ...and both cards are scored the same way, so the delta means something
        XCTAssertNotEqual(coastal.fastest.properties.mean_score,
                          plain.fastest.properties.mean_score)
    }

    func test_water_shows_up_in_the_breakdown() async throws {
        // score.py credits water out to 350 m at 0.45, and the summary used to
        // count only >= 0.5 — so 18% of the network's km scored for water and
        // reported as zero.
        let response = try await liveRoute(from: worcester, to: boston, pref: 1.0)
        let water = response.scenic.properties.scenery_km["water"] ?? 0
        XCTAssertGreaterThan(water, 1.0, "a 77 km scenic route passing no water at all")
        XCTAssertTrue(response.scenic.properties.sceneryBreakdown.contains { $0.label == "water" })
    }
}
