import CoreLocation
import MapKit
import Observation

/// Which end of the trip an address applies to.
enum Endpoint {
    case start
    case end
}

/// The single source of truth for the screen: the chosen start/end points, the
/// scenery preference, and the latest routes from the backend.
///
/// `@Observable` re-renders the UI on any change; `@MainActor` keeps every
/// mutation on the main thread (what SwiftUI requires), so async work below
/// never has to worry about threading.
@Observable
@MainActor
final class RouteModel {
    var start: CLLocationCoordinate2D?
    var end: CLLocationCoordinate2D?

    /// Text shown in the two search fields, kept in sync with the resolved place.
    var startQuery = ""
    var endQuery = ""

    /// 0 = fastest, 1 = most scenic.
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
        // Demo mode (launch with SCENIC_DEMO set) preloads a route via the real
        // search path, so a screenshot doubles as an end-to-end check.
        if ProcessInfo.processInfo.environment["SCENIC_DEMO"] != nil {
            Task {
                await search("Northampton, MA", into: .start)
                await search("Boston, MA", into: .end)
            }
        }
    }

    /// Resolve a typed address to a coordinate with Apple's MapKit search, set
    /// the matching endpoint, and route once both ends are known.
    func search(_ query: String, into role: Endpoint) async {
        let trimmed = query.trimmingCharacters(in: .whitespaces)
        guard trimmed.count >= 3 else { return }   // ignore tiny/partial queries

        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = trimmed
        request.region = Self.maRegion

        do {
            let result = try await MKLocalSearch(request: request).start()
            guard let match = result.mapItems.first else {
                errorText = "No match for “\(trimmed)”"
                return
            }
            let coordinate = match.placemark.coordinate
            let label = match.name ?? trimmed
            switch role {
            case .start: start = coordinate; startQuery = label
            case .end:   end = coordinate;   endQuery = label
            }
            errorText = nil
            if start != nil, end != nil { await computeRoute() }
        } catch {
            errorText = "Couldn’t find “\(trimmed)”"
        }
    }

    /// Swap start and destination, then re-route.
    func swapEnds() {
        guard start != nil, end != nil else { return }
        (start, end) = (end, start)
        (startQuery, endQuery) = (endQuery, startQuery)
        Task { await computeRoute() }
    }

    /// Reset to the empty starting state.
    func clear() {
        start = nil; end = nil
        startQuery = ""; endQuery = ""
        response = nil; errorText = nil
    }

    /// Ask the backend for the fastest and scenic routes at the current preference.
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
