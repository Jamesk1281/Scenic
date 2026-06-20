import CoreLocation
import Observation
import SwiftUI

@Observable
final class RouteModel {
    var start: CLLocationCoordinate2D?
    var end: CLLocationCoordinate2D?
    var pref: Double = 0.5
    var response: RouteResponse?
    var isLoading = false
    var errorText: String?

    init() {
        // Demo mode (set SCENIC_DEMO) preloads a route for screenshots/first run.
        if ProcessInfo.processInfo.environment["SCENIC_DEMO"] != nil {
            start = CLLocationCoordinate2D(latitude: 42.2626, longitude: -71.8023) // Worcester
            end = CLLocationCoordinate2D(latitude: 42.3551, longitude: -71.0657)   // Boston
            Task { await computeRoute() }
        }
    }

    /// First tap sets the start; second sets the end; a third starts over.
    func handleTap(_ coord: CLLocationCoordinate2D) {
        if start == nil || (start != nil && end != nil) {
            start = coord
            end = nil
            response = nil
            errorText = nil
        } else {
            end = coord
            Task { await computeRoute() }
        }
    }

    func swapEnds() {
        guard start != nil, end != nil else { return }
        (start, end) = (end, start)
        Task { await computeRoute() }
    }

    func clear() {
        start = nil; end = nil; response = nil; errorText = nil
    }

    func computeRoute() async {
        guard let a = start, let b = end else { return }
        isLoading = true
        errorText = nil
        defer { isLoading = false }
        do {
            response = try await RouteService.route(from: a, to: b, pref: pref)
        } catch {
            response = nil
            errorText = error.localizedDescription
        }
    }
}
