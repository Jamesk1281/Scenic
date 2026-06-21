import CoreLocation
import MapKit
import Observation
import SwiftUI

enum Endpoint { case start, end }

@Observable
@MainActor
final class RouteModel {
    var start: CLLocationCoordinate2D?
    var end: CLLocationCoordinate2D?
    var startQuery = ""
    var endQuery = ""
    var pref: Double = 0.5
    var response: RouteResponse?
    var isLoading = false
    var errorText: String?

    /// Massachusetts, used to bias address search toward local results.
    private static let maRegion = MKCoordinateRegion(
        center: CLLocationCoordinate2D(latitude: 42.15, longitude: -71.8),
        span: MKCoordinateSpan(latitudeDelta: 2.6, longitudeDelta: 2.6)
    )

    init() {
        // Demo mode (set SCENIC_DEMO) preloads a route via the real address
        // search path, so screenshots double as an end-to-end check.
        if ProcessInfo.processInfo.environment["SCENIC_DEMO"] != nil {
            Task {
                await search("Northampton, MA", into: .start)
                await search("Boston, MA", into: .end)
            }
        }
    }

    /// First tap sets the start; second sets the end; a third starts over.
    func handleTap(_ coord: CLLocationCoordinate2D) {
        if start == nil || (start != nil && end != nil) {
            start = coord; startQuery = "Dropped pin"
            end = nil; endQuery = ""
            response = nil
            errorText = nil
        } else {
            end = coord; endQuery = "Dropped pin"
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
        startQuery = ""; endQuery = ""
    }

    /// Resolve a typed address to a coordinate via MapKit and set the endpoint.
    func search(_ query: String, into role: Endpoint) async {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard q.count >= 3 else { return }
        let req = MKLocalSearch.Request()
        req.naturalLanguageQuery = q
        req.region = Self.maRegion
        do {
            let resp = try await MKLocalSearch(request: req).start()
            guard let item = resp.mapItems.first else {
                errorText = "No match for “\(q)”"; return
            }
            let coord = item.placemark.coordinate
            let name = item.name ?? q
            if role == .start { start = coord; startQuery = name }
            else { end = coord; endQuery = name }
            errorText = nil
            if start != nil, end != nil { await computeRoute() }
        } catch {
            errorText = "Couldn’t find “\(q)”"
        }
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
