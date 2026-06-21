import CoreLocation
import MapKit
import Observation
import SwiftUI

/// Which end of the trip a coordinate or address applies to.
enum Endpoint {
    case start
    case end
}

/// The single source of truth for the screen: the chosen start/end points,
/// the scenery preference, and the latest routes from the backend.
///
/// `@Observable` lets SwiftUI re-render automatically whenever any property
/// here changes. `@MainActor` guarantees every mutation happens on the main
/// thread, which is what SwiftUI requires — so we never have to think about
/// threading when updating these values from async work.
@Observable
@MainActor
final class RouteModel {

    // MARK: - Inputs the user controls

    /// Trip start, once chosen (by tap, address search, or current location).
    var start: CLLocationCoordinate2D?
    /// Trip destination.
    var end: CLLocationCoordinate2D?

    /// The text shown in the two address fields. Kept in sync with `start`/`end`
    /// so the fields display a human-readable label ("Boston", "Current location").
    var startQuery = ""
    var endQuery = ""

    /// 0 = fastest route, 1 = most scenic. Drives the backend's `pref` knob.
    var pref: Double = 0.5

    // MARK: - Outputs from the backend

    /// The most recent fastest + scenic routes, or `nil` before the first run.
    var response: RouteResponse?
    /// `true` while a route request is in flight (drives the spinner).
    var isLoading = false
    /// A user-facing message when something goes wrong, else `nil`.
    var errorText: String?

    /// Massachusetts, used to bias address search toward local results so a
    /// query like "Main Street" resolves nearby instead of across the country.
    private static let maRegion = MKCoordinateRegion(
        center: CLLocationCoordinate2D(latitude: 42.15, longitude: -71.8),
        span: MKCoordinateSpan(latitudeDelta: 2.6, longitudeDelta: 2.6)
    )

    init() {
        // Demo mode (launch with the SCENIC_DEMO env var) preloads a route via
        // the real address-search path, so a screenshot also proves search works.
        if ProcessInfo.processInfo.environment["SCENIC_DEMO"] != nil {
            Task {
                await search("Northampton, MA", into: .start)
                await search("Boston, MA", into: .end)
            }
        }
    }

    // MARK: - Setting the endpoints

    /// Handle a tap on the map. The interaction is a simple cycle:
    /// first tap sets the start, second sets the destination (and routes),
    /// and a tap after a route is shown starts a fresh trip.
    func handleTap(_ coord: CLLocationCoordinate2D) {
        let startingOver = start == nil || (start != nil && end != nil)
        if startingOver {
            start = coord
            startQuery = "Dropped pin"
            end = nil
            endQuery = ""
            response = nil
            errorText = nil
        } else {
            end = coord
            endQuery = "Dropped pin"
            Task { await computeRoute() }
        }
    }

    /// Set the start to a known coordinate (e.g. the device's GPS fix) with a
    /// friendly label, and recompute if a destination is already chosen.
    func setStart(_ coord: CLLocationCoordinate2D, label: String) {
        start = coord
        startQuery = label
        errorText = nil
        if end != nil {
            Task { await computeRoute() }
        }
    }

    /// Swap start and destination, then re-route (handy after picking both).
    func swapEnds() {
        guard start != nil, end != nil else { return }
        (start, end) = (end, start)
        (startQuery, endQuery) = (endQuery, startQuery)
        Task { await computeRoute() }
    }

    /// Clear everything back to the empty starting state.
    func clear() {
        start = nil
        end = nil
        startQuery = ""
        endQuery = ""
        response = nil
        errorText = nil
    }

    // MARK: - Address search

    /// Resolve a typed address to a coordinate using Apple's MapKit search
    /// (no backend needed for this), then set the matching endpoint. If both
    /// ends are now set, kick off routing.
    func search(_ query: String, into role: Endpoint) async {
        let trimmed = query.trimmingCharacters(in: .whitespaces)
        guard trimmed.count >= 3 else { return }   // ignore tiny/partial queries

        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = trimmed
        request.region = Self.maRegion

        do {
            let response = try await MKLocalSearch(request: request).start()
            guard let match = response.mapItems.first else {
                errorText = "No match for “\(trimmed)”"
                return
            }

            let coordinate = match.placemark.coordinate
            let label = match.name ?? trimmed
            switch role {
            case .start:
                start = coordinate
                startQuery = label
            case .end:
                end = coordinate
                endQuery = label
            }
            errorText = nil

            if start != nil, end != nil {
                await computeRoute()
            }
        } catch {
            errorText = "Couldn’t find “\(trimmed)”"
        }
    }

    // MARK: - Routing

    /// Ask the backend for the fastest and scenic routes between the chosen
    /// points at the current preference, and store the result (or an error).
    func computeRoute() async {
        guard let a = start, let b = end else { return }

        isLoading = true
        errorText = nil
        defer { isLoading = false }   // always clears, even if we throw/return

        do {
            response = try await RouteService.route(from: a, to: b, pref: pref)
        } catch {
            response = nil
            errorText = error.localizedDescription
        }
    }
}
