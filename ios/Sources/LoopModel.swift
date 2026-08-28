import CoreLocation
import MapKit
import Observation

/// How loops are fetched. Tests substitute a stub, the same seam and for the
/// same reason as `RouteFetcher`: every interesting behaviour here — which
/// direction comes next, which of two overlapping requests wins, what survives a
/// failed shuffle — is a sequence of requests, and a test that needs a live
/// backend and a real Massachusetts graph to see it does not get written.
typealias LoopFetcher = (CLLocationCoordinate2D, Double, String?,
                         [String: Double]) async throws -> LoopResponse

/// State for the loop tab: one start point, one distance, and the loop that came
/// back. Regenerating cycles the compass direction.
///
/// Separate from `RouteModel` because it shares almost nothing with it — no
/// destination, no swap, no fastest-versus-scenic comparison — but it does *not*
/// own a `LocationManager`. It takes `RouteModel`'s, so location permission is
/// asked for once for the whole app and there is only ever one stream of fixes.
/// Live navigation stays with `RouteModel` for the same reason: there is one
/// drive at a time whichever tab planned it.
@Observable
@MainActor
final class LoopModel {
    private let locationManager: LocationManager

    init(locationManager: LocationManager) {
        self.locationManager = locationManager
    }

    var start: CLLocationCoordinate2D?
    /// Text in the start field, kept in sync with the resolved place.
    var startQuery = ""

    /// The distance slider, in kilometers. 40 km is a bit over an hour's drive,
    /// which is what most people mean by "go for a drive".
    ///
    /// The bounds match the server's clamp. They are not graph limits — the
    /// longest available loop from a Massachusetts start is over 500 km — they
    /// are the range where the answer is a drive rather than an expedition.
    var targetKm: Double = 40
    static let minKm: Double = 5
    static let maxKm: Double = 200

    var response: LoopResponse?
    var isLoading = false
    var errorText: String?

    /// True while regenerating rather than searching fresh, so the button can say
    /// so without the whole panel flashing its loading state.
    var isRegenerating = false

    /// Ticks up per request so a slow response that lands after a newer one has
    /// started can be recognised as stale and dropped. Same reason
    /// `RouteModel.requestGeneration` exists: dragging the distance slider and
    /// then pressing regenerate is two requests in flight, and the older one
    /// must not win.
    private var requestGeneration = 0

    /// How loops are fetched. Tests substitute a stub.
    var fetchLoop: LoopFetcher = { start, km, sector, weights in
        try await RouteService.loop(from: start, km: km, sector: sector,
                                    weights: weights)
    }

    /// True once there is something on screen to drive.
    var hasLoop: Bool { response != nil }

    /// True while the user is standing still waiting for a location fix.
    var isLocatingUser = false

    // MARK: - Choosing where to start

    /// Resolve a free-text query to a place and set it as the start.
    func search(_ query: String) async {
        let trimmed = query.trimmingCharacters(in: .whitespaces)
        guard trimmed.count >= 3 else { return }
        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = trimmed
        request.region = searchRegion
        await resolve(request, label: trimmed)
    }

    /// Resolve a tapped autocomplete suggestion. The suggestion is only a label,
    /// so it still needs a search to get a coordinate.
    func choose(_ completion: MKLocalSearchCompletion) async {
        await resolve(MKLocalSearch.Request(completion: completion),
                      label: completion.title)
    }

    /// Where autocomplete results are ranked around — the map's camera, same as
    /// the directions tab. Without it every search is answered from the middle of
    /// the state.
    var searchRegion: MKCoordinateRegion = .massachusetts

    private func resolve(_ request: MKLocalSearch.Request, label: String) async {
        do {
            let result = try await MKLocalSearch(request: request).start()
            guard let match = result.mapItems.first else {
                errorText = "No match for “\(label)”"
                return
            }
            start = match.placemark.coordinate
            startQuery = match.name ?? label
            errorText = nil
            await generate()
        } catch {
            errorText = "Couldn’t find “\(label)”"
        }
    }

    /// Start the loop from wherever the user is standing — the common case for
    /// this tab, since a loop starts and ends at home.
    ///
    /// Takes a fresh fix rather than the cached one for the same reason
    /// `RouteModel.useMyLocation` does: a Wi-Fi-derived location is often a
    /// street or two out, and here that error decides which roads are within
    /// reach.
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
        searchRegion = .around(fix.coordinate)
        errorText = nil
        await generate()
    }

    func clear() {
        start = nil
        startQuery = ""
        response = nil
        errorText = nil
    }

    // MARK: - Asking for loops

    /// Ask for a loop, letting the server choose the direction. Used for the
    /// first loop and after the distance changes.
    func generate() async {
        await fetch(sector: nil, regenerating: false)
    }

    /// Ask for a loop in the next direction that has one.
    ///
    /// The sectors come from the last response's `alternatives`, which lists only
    /// the directions that actually hold a loop of this length — asking blindly
    /// would offer the user a button that fails, since a coastal start has fewer
    /// than eight.
    func regenerate() async {
        await fetch(sector: nextSector(), regenerating: true)
    }

    /// The direction after the current one, wrapping. Nil if there is nothing to
    /// rotate through, which leaves the choice to the server.
    private func nextSector() -> String? {
        guard let current = response?.meta.sector,
              let options = response?.alternatives.map(\.sector),
              options.count > 1 else { return nil }
        guard let at = options.firstIndex(of: current) else { return options.first }
        return options[(at + 1) % options.count]
    }

    /// How many different directions the user can shuffle through from here.
    /// Shown so the button does not imply endless variety when the geography
    /// offers five.
    var directionCount: Int { response?.alternatives.count ?? 0 }

    private func fetch(sector: String?, regenerating: Bool) async {
        guard let origin = start else { return }

        requestGeneration += 1
        let generation = requestGeneration
        if regenerating { isRegenerating = true } else { isLoading = true }
        errorText = nil

        do {
            let result = try await fetchLoop(origin, targetKm, sector, weights)
            guard generation == requestGeneration else { return }
            response = result
            // The server clamps the distance to its own range; show what it
            // actually used rather than what was asked for, so the slider and
            // the result never disagree.
            targetKm = result.meta.target_km
        } catch {
            guard generation == requestGeneration else { return }
            // A failed regenerate leaves the loop that is already on screen
            // alone. Blanking the map because one direction had nothing in it
            // would throw away a perfectly good drive.
            if !regenerating { response = nil }
            errorText = error.localizedDescription
        }
        isLoading = false
        isRegenerating = false
    }

    /// Per-beauty-type weights, so the tune screen shapes a loop the same way it
    /// shapes a route. Set by whoever owns this model from the shared value —
    /// `pref` is deliberately absent, because the loop tab pins it at full
    /// scenery (see `RouteService.loop`).
    var weights: [String: Double] = [:]
}
