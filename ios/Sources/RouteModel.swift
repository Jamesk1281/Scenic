import CoreLocation
import MapKit
import Observation

/// Which end of the trip an address applies to.
enum Endpoint {
    case start
    case end
}

/// Which kind of drive the user is planning.
///
/// A segmented control in the planning sheet rather than a real tab bar: the two
/// modes share the map, the sheet and the one navigation session, and differ only
/// in what goes in the sheet. A `TabView` would have meant two permanent bottom
/// sheets fighting each other for no gain.
enum PlanningMode: String, CaseIterable, Identifiable {
    case directions = "Directions"
    case loops = "Loop"

    var id: String { rawValue }
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
    let locationManager: LocationManager

    /// The loop tab's state. Owned here, and handed this instance's
    /// `LocationManager`, so the app asks for location permission once and only
    /// one stream of fixes exists however the drive was planned.
    let loops: LoopModel

    /// Which half of the planning sheet is showing.
    var mode: PlanningMode = .directions

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

    /// Per-beauty-type weights, keyed by `BeautyType.apiName`. Each starts at
    /// its own `defaultWeight` — neutral for five of the six, zero for `town`;
    /// the tune screen edits them and they're sent to the backend on every
    /// route request.
    var weights: [String: Double] = Dictionary(
        uniqueKeysWithValues: BeautyType.all.map { ($0.apiName, $0.defaultWeight) }
    )

    /// True once the user has moved any type off *its own* default — used to
    /// highlight the Tune button so it's clear a preference is active.
    ///
    /// Measured against `defaultWeight` and not against `neutralWeight`, or the
    /// button would light up on a launch nobody had touched, purely because
    /// `town` starts at zero.
    var isTuned: Bool {
        BeautyType.all.contains { type in
            abs((weights[type.apiName] ?? type.defaultWeight) - type.defaultWeight) > 0.01
        }
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
        // Built here rather than inline so `loops` can be handed the same
        // manager without reading a half-initialised `self`.
        let manager = LocationManager()
        locationManager = manager
        loops = LoopModel(locationManager: manager)

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
            let name = PlaceNaming.displayName(for: match, fallback: label)
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
    /// answers. See `PlaceNaming`, which the loop tab shares.
    private func nameCurrentLocation(_ fix: CLLocation) async {
        guard let label = await PlaceNaming.currentLocationLabel(for: fix) else { return }
        // The user may have changed the start while we were waiting.
        guard startQuery == PlaceNaming.myLocation,
              start?.matches(fix.coordinate) == true else { return }
        startQuery = label
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

    /// Put every beauty type back to where it started, then re-route.
    ///
    /// Back to `defaultWeight`, not to neutral: Reset means "undo my tuning",
    /// and resetting `town` to 1.0 would quietly turn on the one type the app
    /// deliberately ships off.
    func resetWeights() {
        for type in BeautyType.all { weights[type.apiName] = type.defaultWeight }
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
                                      pref: pref, weights: weights, trace: trace,
                                      voice: VoiceGuide(speaker: SystemSpeaker()))
        // Fixes go straight from CoreLocation into the drive, with no view in
        // between. A SwiftUI `onChange` would stop delivering the moment the
        // phone locked — see `LocationManager.onFix` — and a drive that only
        // runs while someone is looking at it is not a drive we can measure.
        locationManager.onFix = { [weak session] location in session?.update(location) }
        nav = session
    }

    /// Begin live navigation around a generated loop.
    ///
    /// The destination is the origin, which is what a loop means. That is also
    /// the one thing `NavigationModel` has never been given before, and it
    /// matters in two places: arrival, which must not latch while the driver is
    /// still sitting at the start with the whole loop ahead of them; and
    /// rerouting, which must rejoin the loop rather than take the short way to a
    /// destination it is already standing on.
    func startLoopDrive(_ response: LoopResponse) {
        guard let origin = loops.start else { return }
        let trace = DriveTrace(origin: origin, destination: origin,
                               pref: 1.0, weights: weights)
        let session = NavigationModel(
            route: response.loop, destination: origin,
            pref: 1.0, weights: weights, trace: trace,
            turnaround: response.meta.turnaroundCoordinate,
            voice: VoiceGuide(speaker: SystemSpeaker()))
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

        // Drop the plan the drive departed from. `RoutePanel` renders the start
        // button whenever a `response` exists, and `startNavigation` leaves from
        // `self.start` without consulting the current fix — so a finished drive
        // left armed can simply be tapped again. On 2026-08-25 it was: 86 seconds
        // after arriving in Needham a fourth drive began carrying Harvard, the
        // previous drive's origin 38 km away, replayed that route verbatim, and
        // recorded a parked car for nine minutes with no way to stop it.
        //
        // `end` and `endQuery` deliberately survive, which is why this isn't
        // `clear()` — that would take the destination with it. A destination is
        // a place, not a route computed from an origin the driver has since
        // left, and heading back from where you just arrived is a real trip.
        // Keeping the pin is safe because it isn't what arms the start button.
        start = nil
        startQuery = ""
        response = nil
        // Same reasoning for the loop tab: `startLoopDrive` leaves from
        // `loops.start` without consulting the current fix, so a finished loop
        // left armed could be tapped again and replay a drive from wherever it
        // began, hours and kilometres ago.
        loops.start = nil
        loops.startQuery = ""
        loops.response = nil
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
