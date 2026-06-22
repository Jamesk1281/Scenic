import CoreLocation
import Observation

/// Drives one live navigation session: which route we're following, which step
/// is current, and how far to the next maneuver. It's fed a stream of locations
/// from `LocationManager` (via `update`) and reshapes the drive in response —
/// advancing steps, noticing arrival, re-routing if you stray off the line, and
/// bailing to the fastest route on request.
@Observable
@MainActor
final class NavigationModel {
    /// Where we're headed (used for arrival and for re-routing from "here").
    let destination: CLLocationCoordinate2D

    /// The route line currently being followed and its turn-by-turn steps.
    private(set) var route: RouteFeature
    private(set) var steps: [RouteStep]
    private(set) var currentStep = 0

    /// Meters from the driver to the next maneuver, refreshed each update.
    private(set) var distanceToNext: Double = 0
    private(set) var arrived = false
    /// True once the user has bailed to the fastest route.
    private(set) var followingFastest = false
    /// True while a re-route request is in flight (off-route or switch).
    private(set) var isRerouting = false

    /// The scenic preference we re-route with — preserved on off-route reroutes,
    /// dropped to 0 (fastest) when the user switches.
    private var pref: Double
    private let weights: [String: Double]

    init(route: RouteFeature, destination: CLLocationCoordinate2D,
         pref: Double, weights: [String: Double]) {
        self.route = route
        self.steps = route.properties.steps
        self.destination = destination
        self.pref = pref
        self.weights = weights
    }

    /// The instruction shown in the banner right now.
    var currentInstruction: String {
        currentStep < steps.count ? steps[currentStep].instruction : ""
    }

    // MARK: - Driven by each location update

    func update(_ location: CLLocation) {
        guard !steps.isEmpty, !arrived else { return }

        if location.distance(to: destination) < 40 {
            arrived = true
            return
        }

        // Tick past any maneuvers we've now reached (more than one can fall
        // within a single update if they're close together).
        while currentStep < steps.count - 1,
              location.distance(to: steps[currentStep].coordinate) < 25 {
            currentStep += 1
        }
        distanceToNext = location.distance(to: steps[currentStep].coordinate)

        // Strayed well off the line — re-route from here, keeping the same
        // scenic intent (or fastest, if that's what we're already following).
        if !isRerouting, distanceToPolyline(location.coordinate, route.coordinates) > 60 {
            Task { await reroute(from: location.coordinate) }
        }
    }

    // MARK: - Re-routing

    /// Abandon the scenic route and head straight there the quick way.
    func switchToFastest(from location: CLLocationCoordinate2D) async {
        followingFastest = true
        pref = 0
        await reroute(from: location)
    }

    private func reroute(from origin: CLLocationCoordinate2D) async {
        isRerouting = true
        defer { isRerouting = false }

        guard let response = try? await RouteService.route(
            from: origin, to: destination, pref: pref, weights: weights
        ) else { return }

        // pref 0 means we want the fastest line; otherwise the scenic one.
        let feature = pref == 0 ? response.fastest : response.scenic
        route = feature
        steps = feature.properties.steps
        currentStep = 0
    }
}
