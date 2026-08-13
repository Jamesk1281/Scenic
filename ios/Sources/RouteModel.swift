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
    /// Owns the user's location. Planning uses it for "My Location" and to bias
    /// search; live navigation streams from the same instance, so permission is
    /// asked for once and the drive starts with a fix already in hand.
    let locationManager = LocationManager()

    var start: CLLocationCoordinate2D?
    var end: CLLocationCoordinate2D?

    /// Text shown in the two search fields, kept in sync with the resolved place.
    var startQuery = ""
    var endQuery = ""

    /// The part of the world search results are ranked around — the map's
    /// current camera, updated as the user pans. Apple's search sorts by
    /// distance from this region's center, so leaving it at the statewide box
    /// (centered near Oxford, 40 miles from Boston) is what made searches from
    /// eastern MA return places in the middle of the state.
    var searchRegion: MKCoordinateRegion = .massachusetts

    /// True while the one-shot "My Location" fix is in flight, so the button
    /// can show a spinner instead of looking dead for a couple of seconds.
    var isLocatingUser = false

    /// 0 = fastest, 1 = most scenic. The *overall* scenery strength; the
    /// per-type weights below shape *what kind* of scenery.
    var pref: Double = 0.5

    /// Per-beauty-type weights, keyed by `BeautyType.apiName`. Each starts
    /// neutral (1.0); the tune screen edits them and they're sent to the
    /// backend on every route request.
    var weights: [String: Double] = Dictionary(
        uniqueKeysWithValues: BeautyType.all.map { ($0.apiName, BeautyType.neutralWeight) }
    )

    /// True once the user has moved any type off neutral — used to highlight the
    /// Tune button so it's clear a preference is active.
    var isTuned: Bool {
        weights.values.contains { abs($0 - BeautyType.neutralWeight) > 0.01 }
    }

    var response: RouteResponse?
    var isLoading = false
    var errorText: String?

    /// Ticks up on every route request, so a slow response that comes back
    /// after a newer request has started can be recognized as stale and
    /// dropped — otherwise the older route could overwrite the newer one.
    private var requestGeneration = 0

    /// The live navigation session, non-nil while the user is driving a route.
    /// The screen switches to the nav view whenever this is set.
    var nav: NavigationModel?

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

    // MARK: - Choosing places

    /// Resolve a free-text query (the user pressed return) to a place and set it.
    func search(_ query: String, into role: Endpoint) async {
        let trimmed = query.trimmingCharacters(in: .whitespaces)
        guard trimmed.count >= 3 else { return }   // ignore tiny/partial queries

        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = trimmed
        request.region = searchRegion
        await resolve(request, label: trimmed, into: role)
    }

    /// Resolve a tapped autocomplete suggestion to a place and set it. The
    /// suggestion is only a label, so we run a MapKit search on it to get the
    /// precise coordinate. No region needed — the completion already names one
    /// specific place.
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
            let name = displayName(for: match, fallback: label)
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

    /// Start the trip from wherever the user is standing.
    ///
    /// Takes a fresh fix rather than trusting the last one: CoreLocation's
    /// cached location is often Wi-Fi-derived and a street or two out, which is
    /// exactly the error a driver notices at the start of a drive.
    func useMyLocation() async {
        isLocatingUser = true
        defer { isLocatingUser = false }

        guard let fix = await locationManager.currentLocation() else {
            errorText = locationManager.authorization == .denied
                    || locationManager.authorization == .restricted
                ? "Location access is off — allow it in Settings to start from here."
                : "Couldn’t get a location fix. Try again in a moment."
            return
        }

        start = fix.coordinate
        startQuery = "My Location"
        errorText = nil
        // Everything the user searches for next is probably near them.
        searchRegion = .around(fix.coordinate)

        if end != nil { await computeRoute() }
        await nameCurrentLocation(fix)
    }

    /// Put a street name on the "My Location" start once reverse geocoding
    /// answers. Worth the extra call: "My Location" on its own gives the driver
    /// no way to notice we've placed them on the wrong road.
    private func nameCurrentLocation(_ fix: CLLocation) async {
        guard let placemark = try? await CLGeocoder().reverseGeocodeLocation(fix).first
        else { return }
        // The user may have changed the start while we were waiting.
        guard startQuery == "My Location", start?.matches(fix.coordinate) == true else { return }

        let here = [placemark.thoroughfare, placemark.locality]
            .compactMap { $0 }
            .joined(separator: ", ")
        if !here.isEmpty { startQuery = "My Location · \(here)" }
    }

    // MARK: - Editing the trip

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

    /// Put every beauty type back to neutral, then re-route.
    func resetWeights() {
        for type in BeautyType.all { weights[type.apiName] = BeautyType.neutralWeight }
        Task { await computeRoute() }
    }

    // MARK: - Navigation

    /// Begin live navigation along one of the computed routes (the scenic one by
    /// default). Carries the current preference + weights so any mid-trip
    /// re-route still reflects what the user wanted.
    /// Every drive is recorded to `Documents/traces` — see `DriveTrace`. Free-flow
    /// travel times are the biggest known inaccuracy in the app, and a drive is
    /// the only place the real numbers exist; recording by default is what makes
    /// each one count instead of being a drive you have to take again.
    func startNavigation(_ feature: RouteFeature) {
        guard let end else { return }
        let trace = DriveTrace(origin: start, destination: end,
                               pref: pref, weights: weights)
        let session = NavigationModel(route: feature, destination: end,
                                      pref: pref, weights: weights, trace: trace)
        // Fixes go straight from CoreLocation into the drive, with no view in
        // between. A SwiftUI `onChange` would stop delivering the moment the
        // phone locked — see `LocationManager.onFix` — and a drive that only
        // runs while someone is looking at it is not a drive we can measure.
        locationManager.onFix = { [weak session] location in session?.update(location) }
        nav = session
    }

    /// Leave navigation and return to route planning.
    func endNavigation() {
        // Flush the trace before dropping the session. Nothing else here needs
        // the notice, but the last unwritten fixes are only in memory.
        locationManager.onFix = nil
        nav?.finish()
        nav = nil
    }

    /// Ask the backend for the fastest and scenic routes at the current preference.
    func computeRoute() async {
        guard let a = start, let b = end else { return }

        requestGeneration += 1
        let generation = requestGeneration
        isLoading = true
        errorText = nil

        do {
            let result = try await RouteService.route(from: a, to: b, pref: pref, weights: weights)
            guard generation == requestGeneration else { return }   // a newer request superseded us
            response = result
        } catch {
            guard generation == requestGeneration else { return }
            response = nil
            errorText = error.localizedDescription
        }
        isLoading = false
    }
}

/// A label a human would recognise for a search result.
///
/// `MKMapItem.name` on its own is often bare to the point of being wrong-looking
/// — "Main Street", "Post Office", a bare house number — so we append the town
/// unless the name already carries it. The field's text is the only confirmation
/// the user gets that we resolved the place they actually meant.
private func displayName(for item: MKMapItem, fallback: String) -> String {
    let placemark = item.placemark
    let name = item.name ?? placemark.name ?? fallback
    guard let town = placemark.locality, !name.localizedCaseInsensitiveContains(town) else {
        return name
    }
    return "\(name), \(town)"
}
