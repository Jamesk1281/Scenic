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
        } catch {
            throw XCTSkip("no Scenic API at \(Self.baseURL) — start server/serve.py")
        }
    }

    private let worcester = "42.2626,-71.8023"
    private let boston = "42.3551,-71.0657"
    private let buzzardsBay = "41.6362,-70.9342"

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

    func test_the_reported_scenery_reflects_the_weights_that_were_sent() async throws {
        let plain = try await liveRoute(from: boston, to: buzzardsBay, pref: 0.8)
        let coastal = try await liveRoute(from: boston, to: buzzardsBay, pref: 0.8,
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
