import CoreLocation
import Observation

/// Drives one live navigation session: which route we're following, which step
/// is current, how far to the next maneuver, and how much trip is left. It's
/// fed a stream of locations from `LocationManager` (via `update`) and reshapes
/// the drive in response — advancing steps, noticing arrival, re-routing if you
/// stray off the line, and bailing to the fastest route on request.
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

    /// What's left of the trip, measured along the route rather than as the
    /// crow flies. Seeded with the whole route so the screen has honest numbers
    /// before the first GPS fix lands.
    private(set) var remainingMeters: Double
    private(set) var remainingMinutes: Double

    /// Clock time we expect to arrive. Read fresh each time, so it slides later
    /// while the driver sits at a light instead of freezing.
    var eta: Date { Date().addingTimeInterval(remainingMinutes * 60) }

    /// False until the driver has actually reached the route line.
    ///
    /// This gate is why a planned trip survives contact with GPS. Set up
    /// "Waltham to Boston" while sitting in Needham and the very first fix is
    /// miles off the line — indistinguishable, to the off-route check, from
    /// having missed a turn. Rerouting on it silently threw the planned trip
    /// away and navigated Needham to Boston instead. So off-route recovery
    /// stays disarmed until we've seen the driver on the route at least once.
    private(set) var hasJoinedRoute = false

    /// While they're still on their way to it, how far the driver is from the
    /// start of the planned route.
    private(set) var distanceToRouteStart: Double = 0

    /// Meters from the line beyond which the driver counts as off route — and,
    /// until they've joined, within which they count as having arrived on it.
    /// One threshold rather than two: joining is a latch, so it can't chatter.
    private static let offRouteMeters: Double = 60

    /// The scenic preference we re-route with — preserved on off-route reroutes,
    /// dropped to 0 (fastest) when the user switches.
    private var pref: Double
    private let weights: [String: Double]

    /// When the last reroute was attempted. Off-route checks run on every GPS
    /// tick (~every 5 m), so without a cooldown a failed reroute — server briefly
    /// unreachable, say — would retry several times a second.
    private var lastRerouteAttempt: Date = .distantPast

    init(route: RouteFeature, destination: CLLocationCoordinate2D,
         pref: Double, weights: [String: Double]) {
        self.route = route
        self.steps = route.properties.steps
        self.destination = destination
        self.pref = pref
        self.weights = weights
        self.remainingMeters = route.properties.km * 1000
        self.remainingMinutes = route.properties.minutes
    }

    /// The instruction shown in the banner right now.
    var currentInstruction: String {
        currentStep < steps.count ? steps[currentStep].instruction : ""
    }

    // MARK: - Driven by each location update

    func update(_ location: CLLocation) {
        guard !steps.isEmpty, !arrived else { return }

        // Arrived when close to the searched destination — or to the route's
        // own final point. The two can differ: the search pin may sit off-road
        // (a town green, a mall's rooftop), while the route necessarily ends at
        // the nearest road node. Without the second check a driver could reach
        // the end of the line yet never trigger arrival.
        let routeEnd = steps[steps.count - 1].coordinate
        if location.distance(to: destination) < 40 || location.distance(to: routeEnd) < 40 {
            arrived = true
            remainingMeters = 0
            remainingMinutes = 0
            return
        }

        let here = progress(of: location.coordinate, along: route.coordinates)

        if !hasJoinedRoute {
            if here.offRoute <= Self.offRouteMeters {
                hasJoinedRoute = true
            } else if let lineStart = route.coordinates.first {
                distanceToRouteStart = location.distance(to: lineStart)
            }
        }

        // Tick past any maneuvers we've now reached (more than one can fall
        // within a single update if they're close together).
        while currentStep < steps.count - 1,
              location.distance(to: steps[currentStep].coordinate) < 25 {
            currentStep += 1
        }
        distanceToNext = location.distance(to: steps[currentStep].coordinate)

        updateRemaining(here)

        // Strayed well off the line — re-route from here, keeping the same
        // scenic intent (or fastest, if that's what we're already following).
        // The cooldown stops a failed attempt from retrying on every GPS tick.
        if hasJoinedRoute,
           !isRerouting,
           Date().timeIntervalSince(lastRerouteAttempt) > 8,
           here.offRoute > Self.offRouteMeters {
            Task { await reroute(from: location.coordinate) }
        }
    }

    /// Distance and time still to drive.
    ///
    /// Before the driver joins the route, their nearest point on the line is
    /// meaningless — approaching a Waltham-to-Boston route from Needham, it
    /// lands somewhere in the middle — so we quote the whole trip until they're
    /// actually on it.
    ///
    /// Time is the route's own estimate scaled by the fraction left, which
    /// assumes the rest of the drive averages the same speed as the whole. The
    /// backend's minutes are free-flow to begin with (no lights, no traffic, and
    /// measurably optimistic on the small roads scenic routes favour), so this
    /// is an estimate on top of an estimate — good enough to plan by, not to
    /// promise by.
    private func updateRemaining(_ here: RouteProgress) {
        let total = route.properties.km * 1000
        remainingMeters = hasJoinedRoute ? here.remaining : total
        remainingMinutes = total > 0
            ? route.properties.minutes * (remainingMeters / total)
            : 0
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
        lastRerouteAttempt = Date()
        defer { isRerouting = false }

        guard let response = try? await RouteService.route(
            from: origin, to: destination, pref: pref, weights: weights
        ) else { return }

        // pref 0 means we want the fastest line; otherwise the scenic one.
        let feature = pref == 0 ? response.fastest : response.scenic
        route = feature
        steps = feature.properties.steps
        currentStep = 0
        // The new route starts from where the driver is standing, so they are
        // on it by construction — arm off-route recovery for the rest of the
        // drive even if they never reached the originally planned start.
        hasJoinedRoute = true
        remainingMeters = feature.properties.km * 1000
        remainingMinutes = feature.properties.minutes
    }
}
