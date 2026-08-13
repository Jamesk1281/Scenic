import CoreLocation
import Observation

/// How a replacement route is fetched. A seam so the navigation logic can be
/// driven in tests without a backend; production leaves it at the real service.
typealias RouteFetcher = (CLLocationCoordinate2D, CLLocationCoordinate2D,
                          Double, [String: Double]) async throws -> RouteResponse

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

    /// The route line, decoded once per route rather than on every read.
    /// `RouteFeature.coordinates` rebuilds the whole array of a 70 km route from
    /// its `[[lon, lat]]` pairs each time it is touched, and this is touched on
    /// every GPS fix and every SwiftUI body evaluation.
    private(set) var coordinates: [CLLocationCoordinate2D]

    /// Distance still to drive at each maneuver, measured along the route.
    /// Non-increasing, so `update` can walk it forward. This is what makes step
    /// advancement a function of progress rather than of proximity — see
    /// `update`.
    private var stepRemaining: [Double] = []

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

    /// How close counts as arriving.
    private static let arrivalMeters: Double = 40

    /// How little route may be left for a fix near the destination pin to mean
    /// "arrived" rather than "passing nearby" — see `update`.
    private static let arrivalTailMeters: Double = 250

    /// How far the match may slide backwards along the route between fixes.
    /// Enough for GPS jitter and a car rocking at a light; not enough to
    /// re-match an out-and-back route onto the leg it drove twenty minutes ago.
    private static let backtrackToleranceMeters: Double = 100

    /// How far along the route the driver has been matched, monotonically.
    /// Keeps the match moving forwards over a route that crosses itself.
    private var travelled: Double = 0

    /// The scenic preference we re-route with — preserved on off-route reroutes,
    /// dropped to 0 (fastest) when the user switches.
    private var pref: Double
    private let weights: [String: Double]

    /// How replacement routes are fetched. Tests substitute a stub.
    var fetchRoute: RouteFetcher = { from, to, pref, weights in
        try await RouteService.route(from: from, to: to, pref: pref, weights: weights)
    }

    /// When the last reroute was attempted. Off-route checks run on every GPS
    /// tick (~every 5 m), so without a cooldown a failed reroute — server briefly
    /// unreachable, say — would retry several times a second.
    private var lastRerouteAttempt: Date = .distantPast

    /// Ticks up on every reroute, so a slow reply that lands after a newer
    /// request has started can be recognised as stale and dropped. Two can
    /// genuinely be in flight: `switchToFastest` doesn't wait for an off-route
    /// reroute to finish, and without this the first to return cleared
    /// `isRerouting` while the other was still running — re-arming off-route
    /// recovery for a third, and letting whichever landed last win.
    private var rerouteGeneration = 0

    /// Records the drive for later calibration, or nil to record nothing.
    ///
    /// Injected rather than created here, and nil by default, so constructing a
    /// `NavigationModel` never touches the filesystem. The tests build hundreds
    /// of them; only `RouteModel.startNavigation` — a real drive — passes one in.
    private let trace: DriveTrace?

    init(route: RouteFeature, destination: CLLocationCoordinate2D,
         pref: Double, weights: [String: Double], trace: DriveTrace? = nil) {
        self.route = route
        self.steps = route.properties.steps
        self.coordinates = route.coordinates
        self.destination = destination
        self.pref = pref
        self.weights = weights
        self.remainingMeters = route.properties.km * 1000
        self.remainingMinutes = route.properties.minutes
        self.trace = trace
        // Last, and after every stored property: it reads `steps` and
        // `coordinates` back off `self`.
        self.stepRemaining = Self.remainingAtEachStep(of: steps, along: coordinates)
        trace?.route(route, reason: "start")
    }

    /// Close out the drive — called when the user leaves navigation, however it
    /// ended. Only the trace cares; everything else is thrown away with `self`.
    func finish(reason: String = "ended") {
        trace?.end(reason: reason)
    }

    /// Note the app going to the background or coming back, and flush.
    func recordPhase(_ name: String) {
        trace?.phase(name)
    }

    /// Why this drive isn't being recorded, or nil if it is.
    ///
    /// Surfaced on the nav screen. A test drive is expensive and unrepeatable —
    /// the light was that colour, the traffic was that thick, once — so the one
    /// thing the screen must never do is look normal while recording nothing.
    var recordingProblem: String? {
        guard let trace else { return "Not recording — couldn't open a trace file." }
        guard let failure = trace.failure else { return nil }
        return "Recording stopped — \(failure)"
    }

    /// The instruction shown in the banner right now.
    var currentInstruction: String {
        currentStep < steps.count ? steps[currentStep].instruction : ""
    }

    /// Where each maneuver sits along the route, as distance-still-to-drive.
    ///
    /// Walked in travel order, each step matched only against the road ahead of
    /// the one before it. That is what places a maneuver on the correct pass
    /// when a route runs over the same road twice, and it makes the sequence
    /// non-increasing by construction — which is what `advanceSteps` walks.
    private static func remainingAtEachStep(of steps: [RouteStep],
                                            along line: [CLLocationCoordinate2D]) -> [Double] {
        var floor = 0.0
        return steps.map { step in
            let match = progress(of: step.coordinate, along: line, notBefore: floor)
            floor = match.travelled
            return match.remaining
        }
    }

    // MARK: - Driven by each location update

    func update(_ location: CLLocation) {
        guard !steps.isEmpty, !arrived, coordinates.count >= 2 else { return }

        // Match forwards from where the driver already is, with a little slack
        // for GPS jitter — see `progress`. Before they have joined the route
        // nothing is known, so the whole line is fair game.
        let floor = hasJoinedRoute ? max(0, travelled - Self.backtrackToleranceMeters) : 0
        let here = progress(of: location.coordinate, along: coordinates, notBefore: floor)

        if !hasJoinedRoute {
            if here.offRoute <= Self.offRouteMeters {
                hasJoinedRoute = true
            } else if let lineStart = coordinates.first {
                distanceToRouteStart = location.distance(to: lineStart)
            }
        }
        if hasJoinedRoute {
            travelled = max(travelled, here.travelled)
        }

        // Arrival is having driven the line, not being near a particular point.
        // The searched pin can sit off-road (a town green, a mall's rooftop)
        // while the route necessarily ends at the nearest road node, so being
        // beside the pin counts too — but only once the trip is nearly spent.
        // `arrived` never un-latches, and a scenic route that loops out and back
        // passes its own destination, and its own final coordinate, long before
        // the drive is over.
        let drivenTheLine = hasJoinedRoute && here.remaining < Self.arrivalMeters
        let stoppedAtThePin = hasJoinedRoute
            && location.distance(to: destination) < Self.arrivalMeters
            && here.remaining < Self.arrivalTailMeters
        if drivenTheLine || stoppedAtThePin {
            arrived = true
            remainingMeters = 0
            remainingMinutes = 0
            trace?.fix(location, progress: here, joined: hasJoinedRoute, step: currentStep)
            trace?.end(reason: "arrived")
            return
        }

        advanceSteps(here, from: location)
        updateRemaining(here)
        // Recorded after the step and distance work so the fix carries the state
        // it produced, not the previous fix's.
        trace?.fix(location, progress: here, joined: hasJoinedRoute, step: currentStep)

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

    /// Move the banner past every maneuver the driver has already driven
    /// through, and measure how far the next one is.
    ///
    /// By distance *along the route*, not by proximity to the maneuver's point.
    /// Proximity was a latch with no way back: it advanced only while within
    /// 25 m of the current step, which is a 50 m window, and at 65 mph fixes
    /// arrive about 29 m apart. One fix rejected for poor accuracy — under an
    /// overpass, in an interchange, exactly where maneuvers are — opens a 58 m
    /// gap that can straddle the window. You only ever approach a maneuver
    /// once, so a missed one was missed permanently: `currentStep` stopped
    /// advancing and the banner showed a stale instruction for the rest of the
    /// drive, with no reroute to rescue it because the driver was still on
    /// route. Progress only ever increases, so nothing can be skipped.
    private func advanceSteps(_ here: RouteProgress, from location: CLLocation) {
        guard hasJoinedRoute else {
            // Before joining, the projection onto the line is meaningless (it
            // can land anywhere), so leave the step where it is.
            distanceToNext = location.distance(to: steps[currentStep].coordinate)
            return
        }
        while currentStep < steps.count - 1,
              stepRemaining[currentStep] >= here.remaining {
            currentStep += 1
        }
        distanceToNext = max(0, here.remaining - stepRemaining[currentStep])
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
        await reroute(from: location, reason: "fastest")
    }

    private func reroute(from origin: CLLocationCoordinate2D,
                         reason: String = "offroute") async {
        rerouteGeneration += 1
        let generation = rerouteGeneration
        let wantFastest = pref == 0
        isRerouting = true
        lastRerouteAttempt = Date()
        // Only the newest attempt may clear the flag; an older one finishing
        // must not advertise the newer one as done.
        defer { if generation == rerouteGeneration { isRerouting = false } }

        guard let response = try? await fetchRoute(origin, destination, pref, weights),
              generation == rerouteGeneration
        else { return }

        adopt(wantFastest ? response.fastest : response.scenic, reason: reason)
        // The new route starts from where the driver is standing, so they are
        // on it by construction — arm off-route recovery for the rest of the
        // drive even if they never reached the originally planned start.
        hasJoinedRoute = true
    }

    /// Follow a different route from here on.
    private func adopt(_ feature: RouteFeature, reason: String) {
        // Recorded before the state changes under it. `travelled` restarts at
        // zero on the new line, so a trace that didn't know the line had been
        // replaced would read the reset as the car teleporting backwards.
        trace?.route(feature, reason: reason)
        route = feature
        steps = feature.properties.steps
        coordinates = feature.coordinates
        stepRemaining = Self.remainingAtEachStep(of: steps, along: coordinates)
        currentStep = 0
        travelled = 0
        remainingMeters = feature.properties.km * 1000
        remainingMinutes = feature.properties.minutes
    }
}
