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

    /// Resolve a free-text query (the user pressed return) to a place and set it.
    func search(_ query: String, into role: Endpoint) async {
        let trimmed = query.trimmingCharacters(in: .whitespaces)
        guard trimmed.count >= 3 else { return }   // ignore tiny/partial queries

        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = trimmed
        request.region = .massachusetts
        await resolve(request, label: trimmed, into: role)
    }

    /// Resolve a tapped autocomplete suggestion to a place and set it. The
    /// suggestion is only a label, so we run a MapKit search on it to get the
    /// precise coordinate.
    func choose(_ completion: MKLocalSearchCompletion, into role: Endpoint) async {
        await resolve(MKLocalSearch.Request(completion: completion),
                      label: completion.title, into: role)
    }

    /// Run a MapKit search, set the matching endpoint, and route if both ends
    /// are now known. Shared by the typed and the autocomplete paths.
    private func resolve(_ request: MKLocalSearch.Request, label: String, into role: Endpoint) async {
        do {
            let result = try await MKLocalSearch(request: request).start()
            guard let match = result.mapItems.first else {
                errorText = "No match for “\(label)”"
                return
            }
            let coordinate = match.placemark.coordinate
            let name = match.name ?? label
            switch role {
            case .start: start = coordinate; startQuery = name
            case .end:   end = coordinate;   endQuery = name
            }
            errorText = nil
            if start != nil, end != nil { await computeRoute() }
        } catch {
            errorText = "Couldn’t find “\(label)”"
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
