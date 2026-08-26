import CoreLocation
import Observation

/// How a replacement route is fetched. A seam so the navigation logic can be
/// driven in tests without a backend; production leaves it at the real service.
///
/// The trailing heading is the driver's course over ground, or nil when they
/// aren't moving fast enough for it to mean anything — see
/// `NavigationModel.usableHeading`.
typealias RouteFetcher = (CLLocationCoordinate2D, CLLocationCoordinate2D,
                          Double, [String: Double],
                          CLLocationDirection?) async throws -> RouteResponse

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

    /// Below this the car is parked rather than crawling, in m/s.
    /// Deliberately the same 1.0 m/s as `STOPPED_MS` in `tools/analyze_trace.py`,
    /// so "stopped" means one thing on the phone and in the analysis.
    private static let parkedSpeed: CLLocationSpeed = 1.0

    /// How long parked, with the trip nearly spent, counts as having arrived.
    ///
    /// Neither existing test fires when the driver stops a little short, and
    /// they stop short constantly: the route ends at a graph junction, and the
    /// space they park in is the other side of a kerb. On 2026-08-22 one drive
    /// finished 118 m from the end of its route and sat there for four minutes;
    /// `drivenTheLine` wants 40 m and `stoppedAtThePin` wants 40 m from a pin
    /// that was 102 m from any road. The drive was recorded as abandoned.
    ///
    /// 90 seconds and not less because the failure mode is latching early: a
    /// driver held at a long light 200 m out would have the last of their drive
    /// thrown away, and `arrived` never un-latches. Ninety seconds stationary is
    /// longer than all but the worst signal cycle and far shorter than parking.
    private static let arrivalStopSeconds: TimeInterval = 90

    /// When the car last stopped moving, or nil while it is moving.
    private var stoppedSince: Date?

    /// Watch for the car being parked, for `arrivalStopSeconds`.
    ///
    /// A negative `speed` is CoreLocation declining to say, which is not
    /// evidence of stopping — treated as movement so an unwilling speedometer
    /// can never latch arrival on its own.
    private func trackStopping(_ location: CLLocation) {
        guard location.speed >= 0, location.speed < Self.parkedSpeed else {
            stoppedSince = nil
            return
        }
        stoppedSince = stoppedSince ?? now()
    }

    private var parkedLongEnough: Bool {
        guard let since = stoppedSince else { return false }
        return now().timeIntervalSince(since) >= Self.arrivalStopSeconds
    }

    /// How far the match may slide backwards along the route between fixes.
    /// Enough for GPS jitter and a car rocking at a light; not enough to
    /// re-match an out-and-back route onto the leg it drove twenty minutes ago.
    private static let backtrackToleranceMeters: Double = 100

    /// How far back the match may be re-seated when the floor turns out to have
    /// been holding it behind the car — see `reseatIfPinned`.
    ///
    /// Releasing the floor has to stay bounded, because the floor is what stops
    /// a match jumping to an earlier pass over the same road. Wide enough to
    /// cover the measured failure (282 m, 2026-08-25), narrow enough that the
    /// outbound leg of a scenic loop — kilometres away along the line, however
    /// close across the ground — can never be mistaken for the return.
    private static let reseatWindowMeters: Double = 500

    /// How far along the route the driver has been matched, monotonically.
    /// Keeps the match moving forwards over a route that crosses itself.
    private var travelled: Double = 0

    /// The scenic preference we re-route with — preserved on off-route reroutes,
    /// dropped to 0 (fastest) when the user switches.
    /// `private(set)` rather than `private`, matching `followingFastest`: the
    /// two are set together by `switchToFastest` and have to be unwound
    /// together, so a test that can see one and not the other can only assert
    /// half of that.
    private(set) var pref: Double
    private let weights: [String: Double]

    /// The clock the re-routing guards read.
    ///
    /// A seam for the same reason `fetchRoute` is one: every guard below is a
    /// duration, and the runaway they exist to stop took 61 seconds of real
    /// driving to appear. Tests that had to sleep through an 8-second cooldown
    /// and a 45-second grace period would not be written, and this bug reached
    /// a car precisely because nothing exercised the timings.
    var now: () -> Date = Date.init

    /// How replacement routes are fetched. Tests substitute a stub.
    var fetchRoute: RouteFetcher = { from, to, pref, weights, heading in
        try await RouteService.route(from: from, to: to, pref: pref,
                                     weights: weights, heading: heading)
    }

    /// When the last reroute was attempted. Off-route checks run on every GPS
    /// tick — about 1 Hz, moving or not, since `LocationManager` carries no
    /// distance filter — so without a cooldown a failed reroute (server briefly
    /// unreachable, say) would retry several times a second.
    private var lastRerouteAttempt: Date = .distantPast

    /// Where the driver was when the last reroute fired, and how far they must
    /// travel before another may.
    ///
    /// The cooldown alone bounds *attempts*, not successes. A replacement route
    /// begins at the nearest road node, so a car parked more than
    /// `offRouteMeters` from any mapped road is still off-route the moment the
    /// new route arrives — and it re-routes again 8 s later, forever. That used
    /// to be starved by the 5 m distance filter, which produced almost no fixes
    /// from a stationary car; with the filter gone it runs at 1 Hz in a pocket,
    /// on background location: roughly 450 reroutes an hour, 900 statewide
    /// Dijkstras against the server, ~49 MB of route geometry appended to the
    /// trace, and a banner resetting to step 0 every 8 seconds. Requiring real
    /// movement in between is what breaks the loop; a user-initiated
    /// `switchToFastest` bypasses this deliberately.
    private var lastRerouteOrigin: CLLocationCoordinate2D?
    private static let rerouteMinMovementMeters: Double = 50

    /// How long to wait between off-route reroutes, and how far that stretches
    /// when they keep coming.
    ///
    /// The three guards above each stop a *different* runaway, and the drives of
    /// 2026-08-22 found a fourth they all pass. A driver on a road the route
    /// wants to leave — because the destination is behind them, or reachable
    /// only the long way round — clears the cooldown, clears the 50 m movement
    /// bar at every attempt, and clears `awaitingJoin` too, because the
    /// replacement route runs along the road they are on for a few seconds
    /// before it peels off. Off-route, reroute, briefly on the new line,
    /// off-route again: measured at ten reroutes in 160 seconds, each resetting
    /// the banner to its first instruction, and twenty statewide Dijkstras.
    ///
    /// Nothing about that is recoverable by asking the server again — it will
    /// return the same route, because it is the right one. So the answer is to
    /// ask less often, not to ask differently: the interval doubles for each
    /// reroute that fails to settle the driver, and resets the moment one does.
    /// A driver who simply missed a turn sees the base interval, reroutes once,
    /// rejoins, and never meets the backoff at all.
    private static let rerouteCooldownSeconds: TimeInterval = 8
    private static let rerouteCooldownCapSeconds: TimeInterval = 120

    /// How long the driver has to stay on the route for it to count as settled,
    /// which is what clears the backoff. Long enough that the few seconds of
    /// overlap between the old road and the new line — the thing that defeated
    /// `awaitingJoin` — cannot be mistaken for having taken it.
    private static let rerouteSettledSeconds: TimeInterval = 30

    private var consecutiveReroutes = 0
    private var onRouteSince: Date?

    /// The interval the off-route check has to clear right now.
    private var rerouteCooldown: TimeInterval {
        min(Self.rerouteCooldownSeconds * pow(2, Double(consecutiveReroutes)),
            Self.rerouteCooldownCapSeconds)
    }

    /// Watch whether the driver has actually taken the route, and forgive the
    /// backoff once they have.
    ///
    /// Deliberately `joinConfirmMeters` and not `offRouteMeters`: the same
    /// deadband, and for the same reason. A route running 60 m off — a frontage
    /// road, the far carriageway — would otherwise read as "settled" while the
    /// driver was nowhere near it.
    private func trackSettling(_ here: RouteProgress) {
        guard here.offRoute <= Self.joinConfirmMeters else {
            onRouteSince = nil
            return
        }
        let since = onRouteSince ?? now()
        onRouteSince = since
        if now().timeIntervalSince(since) >= Self.rerouteSettledSeconds {
            consecutiveReroutes = 0
        }
    }

    /// How much route has to be left for an off-route reroute to be worth
    /// running at all.
    ///
    /// Inside this, a reroute cannot help. The remaining line is one or two
    /// residential streets, GPS error is a large fraction of their length, and
    /// every replacement is a few hundred metres the driver leaves again
    /// immediately. The 2026-08-22 Needham drive rerouted five times in its last
    /// three minutes, inside 1.3 km of the pin, and ended having never announced
    /// arrival; this stops the closest-in of those outright and the backoff
    /// above thins the rest. A driver this close does not need re-planning —
    /// they need to be left alone to park.
    ///
    /// Deliberately measured on `remaining` along the route rather than on the
    /// straight line to the pin, because the two disagree exactly where it
    /// matters: a destination on a cul-de-sac can be 100 m away across a fence
    /// and 3 km away by road.
    private static let noRerouteWithinMeters: Double = 300

    /// How far the driver may drift *away* from a route they have never joined
    /// before the plan is treated as stale.
    ///
    /// `hasJoinedRoute` disarms off-route recovery until the driver first
    /// reaches the line, which is what stops a trip planned from the sofa being
    /// thrown away on the first fix. But it had no way out: a driver who never
    /// touches the line never re-routes, so the app navigates a route they are
    /// not on for as long as they keep driving. On 2026-08-22 that was three and
    /// a half minutes and 1.5 km, ending 593 m off the line, with the banner
    /// showing the first instruction throughout.
    ///
    /// Measured against the *closest* the driver has come to the route start,
    /// not against where they began. Driving two miles to the start of a planned
    /// route is legitimate and shortens that distance the whole way; only
    /// growing it back again says the plan is no longer the one being driven.
    private static let preJoinAbandonMeters: Double = 250
    private var closestToRouteStart: Double = .infinity

    /// Whether off-route recovery is live.
    ///
    /// Normally that means the driver has reached the route at least once —
    /// see `hasJoinedRoute` for why. The second clause is the way out of that
    /// latch: a driver who has never joined and is now further from the route
    /// start than they have ever been is not on their way to it, and holding
    /// the plan for them navigates a route nobody is driving.
    private var armedForReroute: Bool {
        if hasJoinedRoute { return true }
        return distanceToRouteStart > closestToRouteStart + Self.preJoinAbandonMeters
    }

    private func hasMovedSinceLastReroute(_ location: CLLocation) -> Bool {
        guard let origin = lastRerouteOrigin else { return true }
        return location.distance(to: origin) >= Self.rerouteMinMovementMeters
    }

    /// Set when a replacement route is adopted, and cleared once the driver
    /// actually reaches it. Off-route recovery stays disarmed in between.
    ///
    /// A replacement route does *not* start where the driver is standing. It
    /// starts at the graph node `snap` chose, which is a junction — a median
    /// 99 m away, p90 217 m — while `offRouteMeters` is 60. So the fix that
    /// lands immediately after a reroute is frequently already off the new
    /// line, and re-triggers the very reroute that just answered. Measured on
    /// the first test drive: 4 of 12 reroutes placed the driver over the
    /// threshold on their first fix, and the loop ran 7 times in 61 seconds
    /// with the banner resetting to the first instruction each time, until the
    /// driver gave up and took the fastest-route escape hatch.
    ///
    /// The cooldown and the movement guard could not catch this. Both were
    /// written for a *failed* or a *stationary* reroute; this one succeeds, and
    /// at 12 m/s the car clears the 50 m movement bar between every attempt.
    private var awaitingJoin = false
    private var awaitingJoinSince: Date?

    /// How long to let the driver reach a freshly adopted route before arming
    /// off-route recovery regardless.
    ///
    /// Without a bound this would be a one-way latch: a driver who turns off
    /// before ever touching the new line would never re-arm rerouting and would
    /// navigate the rest of the trip against a route they had abandoned. Long
    /// enough to cover the p90 snap offset at town speed, short enough that a
    /// genuinely wrong route is not followed far.
    private static let joinGraceSeconds: TimeInterval = 45

    /// How close counts as having *reached* the new line, as opposed to merely
    /// not being far from it.
    ///
    /// Deliberately tighter than `offRouteMeters`. `hasJoinedRoute` can share
    /// one threshold with the off-route trigger because it is a latch and so
    /// cannot chatter; `awaitingJoin` is re-armed on every `adopt`, so sharing
    /// it there gives the loop a way back. A route whose line runs ~60 m off —
    /// a frontage road, the far carriageway of a divided highway — puts one
    /// fix inside and the next outside, and the single fix inside clears the
    /// suppression for good: reroutes then resume at the cooldown, 8 s apart,
    /// each resetting the banner to the first instruction. The deadband means
    /// clearing it takes a fix that is actually *on* the road, not one
    /// hovering at the boundary.
    private static let joinConfirmMeters: Double = 30

    /// True once the driver has reached a newly adopted route, or waited long
    /// enough that they clearly aren't going to.
    private func settleAwaitingJoin(_ here: RouteProgress) {
        guard awaitingJoin else { return }
        let expired = now().timeIntervalSince(awaitingJoinSince ?? .distantPast)
            > Self.joinGraceSeconds
        if here.offRoute <= Self.joinConfirmMeters || expired {
            awaitingJoin = false
            awaitingJoinSince = nil
        }
    }

    /// The driver's course, or nil when reporting one would be a guess.
    ///
    /// CoreLocation reports -1 when it has no opinion, and its course is
    /// derived from successive positions — so a car inching forward at a light
    /// produces a heading that swings through the compass. Sending one of those
    /// is worse than sending nothing: the server would trust it and start the
    /// replacement route at the wrong end of the road, which is exactly the
    /// failure the heading was added to prevent.
    private static let minSpeedForHeading: CLLocationSpeed = 2.0   // m/s, ~4.5 mph

    static func usableHeading(_ location: CLLocation) -> CLLocationDirection? {
        guard location.course >= 0, location.speed >= minSpeedForHeading else {
            return nil
        }
        return location.course
    }

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
        if trace != nil { startWatchdog() }
    }

    /// Close out the drive — called when the user leaves navigation, however it
    /// ended. Only the trace cares; everything else is thrown away with `self`.
    func finish(reason: String = "ended") {
        watchdog?.cancel()
        watchdog = nil
        trace?.end(reason: reason)
    }

    /// Note the app going to the background or coming back, and flush.
    func recordPhase(_ name: String) {
        trace?.phase(name)
    }

    /// The last fix and where it landed on the route, kept so a verdict tapped
    /// between fixes has a position and an anchor to carry.
    ///
    /// `lastProgress` rather than the `travelled` property because that one is a
    /// running maximum — a mark should carry the same quantity a `fix` does, so
    /// the two are comparable in the trace without knowing which of them
    /// smoothed anything.
    private var lastFix: CLLocation?
    private var lastProgress: RouteProgress?

    /// What the driver has said about the road so far, so the screen can show
    /// their taps registered. Counted rather than listed: the trace is the record,
    /// this is only feedback.
    private(set) var marksRecorded = 0

    /// Whether a verdict tapped now would actually be written down.
    ///
    /// Deliberately not the same condition as `recordingProblem`. That one goes
    /// orange when no GPS fixes have arrived for ten seconds, which is a real
    /// problem for measuring speed and no problem at all for this: a mark still
    /// carries its own timestamp and the last known position, and a driver in a
    /// GPS hole under trees is quite likely looking at something worth marking.
    /// What does make the button a lie is having nowhere to write — no trace
    /// file, or a writer that has already failed.
    var canRecordMarks: Bool {
        guard let trace else { return false }
        return trace.failure == nil
    }

    /// The driver's verdict on the road they're on right now.
    ///
    /// Deliberately unvalidated and unlimited. There is no "too many marks" — a
    /// driver who taps twice through a long beautiful stretch has said something
    /// true twice — and no attempt to reject a tap as a mistake, because this
    /// cannot tell one from a genuine change of mind and the analysis pools
    /// dozens of these anyway. What it must not do is fail: a tap that silently
    /// records nothing is worse than no button, since the driver stops watching
    /// for the scenery they think they are logging.
    func mark(_ verdict: SceneryVerdict) {
        marksRecorded += 1
        trace?.mark(verdict.rawValue, progress: lastProgress, location: lastFix,
                    joined: hasJoinedRoute, step: currentStep)
    }

    /// When this session began, and when the last fix arrived. A recorder with
    /// nothing to record is the failure the indicator exists to catch, and
    /// `DriveTrace.failure` cannot see it: the writer is perfectly healthy, the
    /// fixes just stopped coming (authorization revoked mid-drive, a deep urban
    /// canyon, updates never restarted after a resume).
    private let startedAt = Date()
    private(set) var lastFixAt: Date?

    /// Ticked every couple of seconds purely so SwiftUI re-evaluates the banner.
    /// "No fixes are arriving" is the one condition that cannot trigger its own
    /// redraw — every other change to this model is driven *by* a fix — so
    /// without something observable moving, the screen would keep showing the
    /// state it had when the stream died.
    private var watchdogTick = 0
    private var watchdog: Task<Void, Never>?

    /// How long without a fix counts as not recording. CoreLocation delivers
    /// about 1 Hz during a drive, so ten seconds of silence is not cadence
    /// wobble — it is the stream having stopped.
    private static let fixSilenceSeconds: TimeInterval = 10

    private func startWatchdog() {
        watchdog = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                guard let self else { return }
                self.watchdogTick &+= 1
            }
        }
    }

    /// Why this drive isn't being recorded, or nil if it is.
    ///
    /// Surfaced on the nav screen. A test drive is expensive and unrepeatable —
    /// the light was that colour, the traffic was that thick, once — so the one
    /// thing the screen must never do is look normal while recording nothing.
    var recordingProblem: String? {
        _ = watchdogTick        // observed, so silence still redraws the banner
        guard let trace else { return "Not recording — couldn't open a trace file." }
        if let failure = trace.failure { return "Recording stopped — \(failure)" }
        guard !arrived else { return nil }
        let silence = Date().timeIntervalSince(lastFixAt ?? startedAt)
        guard silence > Self.fixSilenceSeconds else { return nil }
        return lastFixAt == nil
            ? "No GPS fixes yet — nothing is being recorded."
            : "No GPS fixes for \(Int(silence)) s — nothing is being recorded."
    }

    /// The instruction shown in the banner right now.
    var currentInstruction: String {
        currentStep < steps.count ? steps[currentStep].instruction : ""
    }

    /// The icon for that instruction. A maneuver is read at a glance long
    /// before the words are, and an exit and a left turn should not look alike.
    var currentSymbol: String {
        currentStep < steps.count ? steps[currentStep].symbol : "arrow.up"
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
        // Before the guards: this records that a fix *arrived*, which is what
        // `recordingProblem` watches. An early return here is still evidence
        // the stream is alive.
        lastFixAt = Date()
        // Kept here too, and not below with the match, so a verdict tapped
        // before the driver has joined the route still carries a position. The
        // anchor is worthless then and the record says so (`joined`), but the
        // raw fix is the evidence that makes it recoverable.
        lastFix = location
        guard !steps.isEmpty, !arrived, coordinates.count >= 2 else { return }

        // Match forwards from where the driver already is, with a little slack
        // for GPS jitter — see `progress`. Before they have joined the route
        // nothing is known, so the whole line is fair game.
        let floor = hasJoinedRoute ? max(0, travelled - Self.backtrackToleranceMeters) : 0
        let here = reseatIfPinned(progress(of: location.coordinate,
                                           along: coordinates, notBefore: floor),
                                  at: location)
        lastProgress = here

        if !hasJoinedRoute {
            if here.offRoute <= Self.offRouteMeters {
                hasJoinedRoute = true
            } else if let lineStart = coordinates.first {
                distanceToRouteStart = location.distance(to: lineStart)
                closestToRouteStart = min(closestToRouteStart, distanceToRouteStart)
            }
        }
        if hasJoinedRoute {
            travelled = max(travelled, here.travelled)
            // The first trustworthy look at where this line puts the driver.
            // `runningBackwards` measures against it; `adopt` clears it.
            if matchAtAdoption == nil { matchAtAdoption = here.travelled }
        }

        // Arrival is having driven the line, not being near a particular point.
        // The searched pin can sit off-road (a town green, a mall's rooftop)
        // while the route necessarily ends at the nearest road node, so being
        // beside the pin counts too — but only once the trip is nearly spent.
        // `arrived` never un-latches, and a scenic route that loops out and back
        // passes its own destination, and its own final coordinate, long before
        // the drive is over.
        trackStopping(location)
        let drivenTheLine = hasJoinedRoute && here.remaining < Self.arrivalMeters
        let stoppedAtThePin = hasJoinedRoute
            && location.distance(to: destination) < Self.arrivalMeters
            && here.remaining < Self.arrivalTailMeters
        // Parked, with the trip all but done. The two tests above both measure
        // distance to a point the driver may have no way of reaching — the end
        // of the route is a junction and the pin is often not on a road at all
        // — so neither fires for a car that has simply arrived and switched off.
        let parkedAtTheEnd = hasJoinedRoute
            && here.remaining < Self.arrivalTailMeters
            && parkedLongEnough
        if drivenTheLine || stoppedAtThePin || parkedAtTheEnd {
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

        // A route adopted a moment ago starts at a junction the driver has yet
        // to reach, so they are legitimately off it until they get there.
        settleAwaitingJoin(here)
        trackSettling(here)

        // Strayed well off the line — re-route from here, keeping the same
        // scenic intent (or fastest, if that's what we're already following).
        // Every clause guards a different way this loop has actually run away:
        // the cooldown stops a *failed* attempt retrying on every GPS tick,
        // `hasMoved` stops a *successful* one retrying forever from a parked
        // car, `awaitingJoin` stops a successful one retrying while the driver
        // is still on their way to the line it put them on, and the backoff
        // inside `rerouteCooldown` stops a *correct* one being asked for over
        // and over by a driver who is not going to take it.
        if armedForReroute,
           !isRerouting,
           !awaitingJoin,
           here.remaining > Self.noRerouteWithinMeters,
           now().timeIntervalSince(lastRerouteAttempt) > rerouteCooldown,
           hasMovedSinceLastReroute(location),
           here.offRoute > Self.offRouteMeters {
            Task { await reroute(from: location, reason: "offroute") }
        }
    }

    /// Let the match off the floor when the floor is what put it off route.
    ///
    /// `offRoute` is not the distance to the route. It is the distance to the
    /// nearest point of the route *at or after* `notBefore` — `progress` skips
    /// every segment ending before it — and `update` feeds that from a running
    /// maximum. On a route that doubles back over the road the driver is on (a
    /// reroute that opens by passing them, the return leg of a loop) the match
    /// can land on the wrong pass. The driver then drives forwards while their
    /// position *along that pass* runs backwards, and once it has run back
    /// further than `backtrackToleranceMeters` the floor pins the match to a
    /// point they are driving away from. From there `offRoute` measures the
    /// distance to the pin rather than to the road, and climbs without limit
    /// with nothing wrong on the road at all.
    ///
    /// Measured on 2026-08-25: **168.2 m recorded where the whole-line
    /// projection put the car 11.8 m from its route**, on a polyline
    /// byte-identical to the one adopted a second later. It crossed
    /// `offRouteMeters` at 65 m, re-routed, and the reply was the same line —
    /// correctly, the car was on the best route. `adopt` then reset `travelled`
    /// to 0, which released the floor and dropped the reading back to 11.6 m.
    /// The reroute storm *was* the recovery mechanism; 6 of the 8 same-route
    /// adoptions across the recorded drives began this way.
    ///
    /// So: only once the constrained match claims off-route, ask the
    /// unconstrained one. If that says the driver is *on* the line — measured at
    /// `joinConfirmMeters`, the deadband `trackSettling` uses, not the 60 m
    /// trigger — the floor was wrong, and the match is re-seated onto the
    /// whole-line answer.
    ///
    /// This cannot skip the driver forwards. Any match later than the floor is
    /// available to the constrained search too, so the two can only differ by
    /// the free one being *earlier*: the worst it can do is admit the driver is
    /// further back than the floor believed. `currentStep` is re-derived from
    /// zero because an index read off the wrong pass is wrong too, and
    /// `advanceSteps` walks it back up on this same fix.
    ///
    /// Deliberately not run while `awaitingJoin`: a freshly adopted route has
    /// `travelled` at 0 and so no floor to be pinned by, and the gap between
    /// the car and a line starting at the junction ahead is exactly the
    /// legitimate off-route this must not swallow.
    private func reseatIfPinned(_ here: RouteProgress,
                                at location: CLLocation) -> RouteProgress {
        guard hasJoinedRoute, !awaitingJoin,
              here.offRoute > Self.offRouteMeters else { return here }
        let free = progress(of: location.coordinate, along: coordinates, notBefore: 0)
        guard free.offRoute <= Self.joinConfirmMeters,
              travelled - free.travelled <= Self.reseatWindowMeters else { return here }
        travelled = free.travelled
        matchAtAdoption = free.travelled
        currentStep = 0
        return free
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
        guard hasJoinedRoute, !awaitingJoin, !runningBackwards(here) else {
            // Before the driver reaches the line the projection onto it is
            // meaningless (it can land anywhere), so leave the step where it is.
            //
            // `awaitingJoin` and not `hasJoinedRoute` alone, because a reroute
            // sets `hasJoinedRoute` true by hand — the new line starts at a
            // junction ahead of the car, and the banner has to keep giving
            // instructions over that gap rather than fall back to "head to the
            // start of your route". That left this guard unable to fire on the
            // one case it was written for. On 2026-08-25 the 16:22:53 reroute
            // handed back a line whose first maneuver was 481 m away; the
            // banner skipped it, showed the maneuver *after* it, and froze the
            // countdown at 216 m for the 28 seconds it took to drive there.
            distanceToNext = location.distance(to: steps[currentStep].coordinate)
            return
        }
        // Strictly past, not level with. Standing *at* a maneuver is when the
        // driver most needs to be told about it, and at the instant a route is
        // adopted "level with the first maneuver" is exactly where they are:
        // both distances are the whole route, and `>=` consumed the
        // instruction on equality. That happened on 21 of the 51 reroutes
        // recorded across five drives.
        currentStep = firstStepAhead(of: here.remaining, from: currentStep)
        distanceToNext = max(0, here.remaining - stepRemaining[currentStep])
    }

    /// The first maneuver not yet driven through, searching forward from
    /// `index`.
    ///
    /// Split out of `advanceSteps` because `merge` needs the same walk from a
    /// standing start: it swaps the step list under a drive in progress, and an
    /// index into the old list means nothing in the new one.
    private func firstStepAhead(of remaining: Double, from index: Int) -> Int {
        var i = index
        while i < steps.count - 1, remaining < stepRemaining[i] - passedMargin(i) {
            i += 1
        }
        return i
    }

    /// Whether the driver is running *against* the route they were just handed.
    ///
    /// A replacement route can begin ahead of the car and double back over the
    /// road it is already on — it opens with a U-turn, or the line the driver
    /// is standing on is the leg that comes *back*. The match then lands
    /// legitimately, on tarmac the route really does cover, but hundreds of
    /// metres along it; every maneuver before that point reads as driven
    /// through, and the banner hands out an instruction from the far side of a
    /// turn the driver has not made. Measured on 2026-08-25: the 16:23:26
    /// reroute opened 229.6 m along and 0.0 m off, and skipped the U-turn that
    /// was the entire point of the route.
    ///
    /// Neither the join gate nor `passedMargin` can see this — the driver is
    /// genuinely *on* the line, so every "have they reached it?" test passes.
    /// What gives it away is the direction: their position along the line runs
    /// backwards, fix after fix, while they drive forwards. Across five drives
    /// 12 of 53 reroutes did this, sliding as much as 154 m.
    ///
    /// Deliberately measured against where the fresh line first put them rather
    /// than against the previous fix, so one dropped or noisy fix cannot arm it,
    /// and deliberately not a latch: it is re-decided every fix, so the moment
    /// the driver turns around and the match starts climbing again the banner
    /// picks up where they now are. It also cannot bind late in a drive —
    /// `update`'s own backtrack floor keeps the match within
    /// `backtrackToleranceMeters` of a running maximum that has long since
    /// passed the anchor.
    private func runningBackwards(_ here: RouteProgress) -> Bool {
        guard let anchor = matchAtAdoption else { return false }
        return here.travelled < anchor - Self.reverseMatchMeters
    }

    /// Where the match first put the driver on the route now being followed, or
    /// nil until they have reached it. Reset by `adopt`.
    private var matchAtAdoption: Double?

    /// How far the match may slide backwards along a freshly adopted line
    /// before it means the driver is going the wrong way down it.
    ///
    /// Measured rather than picked: on a stopped car sitting on its route, the
    /// match wobbles a median 0.09 m between fixes and never more than 2.9 m
    /// over the 1,028 such fixes recorded. One second of driving is 15 m. Five
    /// metres is clear of the first and inside the second.
    private static let reverseMatchMeters: Double = 5

    /// How far past maneuver `index` the driver has to be before it counts as
    /// driven through.
    ///
    /// Nothing, for a maneuver in the middle of a route: it sits somewhere
    /// along the line, so being past it at all took real driving. The first
    /// maneuver is the exception, and it is the whole reason this exists — it
    /// sits at the line's *origin*, so `stepRemaining[0]` is the entire route
    /// and any projection whatsoever reads as past it.
    ///
    /// That is not a rounding problem to be fixed by comparing strictly. A
    /// driver approaching the corner the new route turns at projects onto the
    /// leg *after* the corner, by roughly their distance from it — and
    /// `joinConfirmMeters` from the line already counts as having reached it.
    /// Measured on 2026-08-25: 9.9 m along at the moment the Needham reroute
    /// counted as joined, which was enough to withhold "Turn right onto Great
    /// Plain Avenue" 53 seconds from the driver's own driveway.
    ///
    /// Capped at half the opening leg, so saving the first maneuver cannot cost
    /// the second. The shortest opening leg served across five drives was 20 m
    /// — shorter than the deadband — and holding the first instruction for the
    /// whole of it would release both maneuvers at the same instant, skipping
    /// the second outright. Half leaves a window for a fix to land in.
    private func passedMargin(_ index: Int) -> Double {
        guard index == 0, steps.count > 1 else { return 0 }
        let firstLeg = stepRemaining[0] - stepRemaining[1]
        return min(Self.firstStepPassedMeters, firstLeg / 2)
    }

    /// How far past the first maneuver of a route the driver must be for it to
    /// count as driven — see `passedMargin`. The same 30 m as
    /// `joinConfirmMeters` and for the same geometry: a driver that far off the
    /// line still counts as on it, and that far off a corner projects that far
    /// past it.
    private static let firstStepPassedMeters: Double = joinConfirmMeters

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
    func switchToFastest(from location: CLLocation) async {
        let previousPref = pref
        let previousReroutes = consecutiveReroutes
        let previousOnRouteSince = onRouteSince
        followingFastest = true
        pref = 0
        // The driver has changed their mind about where they are going, so the
        // history of routes they declined says nothing about this one.
        consecutiveReroutes = 0
        onRouteSince = nil
        // Both are restored if the request never lands. Left set, the screen
        // would draw the gray "fastest" line and hide the button — with no
        // fastest route ever adopted, so no way to retry — while every later
        // off-route reroute silently asked for pref 0, discarding the scenic
        // intent on the strength of a request that failed.
        //
        // A *superseded* attempt is the one case left alone, because a newer
        // request owns the state by then and restoring would clobber it. That
        // is why `ended` is a separate outcome rather than folded into it:
        // arriving mid-flight abandons the attempt with nothing newer behind
        // it, so leaving the state set stranded `followingFastest` and `pref`
        // at 0 for the rest of the session on a switch that never happened.
        switch await reroute(from: location, reason: "fastest") {
        case .failed, .ended:
            followingFastest = false
            pref = previousPref
            // Restored with the rest, and for the same reason. On a switch that
            // never happened the driver declined nothing, so a backoff that had
            // climbed to two minutes must not come back at eight seconds
            // because one request timed out.
            consecutiveReroutes = previousReroutes
            onRouteSince = previousOnRouteSince
        case .adopted, .superseded:
            break
        }
    }

    /// What became of one reroute attempt. All four are worth telling apart:
    /// `adopted` and `superseded` leave the caller's state alone (the route is
    /// live, or a newer request owns it), while `failed` and `ended` mean
    /// nothing else is coming and any state staked on this attempt has to be
    /// unwound by whoever staked it.
    private enum RerouteOutcome {
        case adopted, failed, superseded, ended
    }

    @discardableResult
    private func reroute(from origin: CLLocation,
                         reason: String = "offroute") async -> RerouteOutcome {
        rerouteGeneration += 1
        let generation = rerouteGeneration
        let wantFastest = pref == 0
        isRerouting = true
        lastRerouteAttempt = now()
        lastRerouteOrigin = origin.coordinate
        // Only the newest attempt may clear the flag; an older one finishing
        // must not advertise the newer one as done.
        defer { if generation == rerouteGeneration { isRerouting = false } }

        // The heading goes with it so the server can snap to the end of this
        // road that lies *ahead*. Without it the nearest graph node is as often
        // as not the junction just passed, and the replacement route opens by
        // turning the driver around — which the first test drive did.
        guard let response = try? await fetchRoute(origin.coordinate, destination,
                                                   pref, weights,
                                                   Self.usableHeading(origin))
        else {
            guard generation == rerouteGeneration else { return .superseded }
            // A request that never lands is the plainest case of asking not
            // helping, so it backs off with the rest. Reaching the 120 s cap
            // takes four consecutive failures, by which point the network is
            // gone and retrying every eight seconds is a radio draining the
            // battery to no end. `trackSettling` clears the counter as soon as
            // the driver holds the line for 30 s, so one dropped request costs
            // a single doubling rather than the drive.
            return .failed
        }
        guard generation == rerouteGeneration else { return .superseded }
        // The drive can end while a reroute is in the air. `update` stops
        // looking at fixes once `arrived` latches and NavView stops the
        // location stream with it, so a route adopted after that point is
        // never corrected: the map redraws a fresh multi-kilometre line under
        // "You've arrived", and `remainingMeters` goes from 0 back to a whole
        // new trip, with no fix left to undo either.
        guard !arrived else { return .ended }

        let replacement = wantFastest ? response.fastest : response.scenic
        // The server is entitled to hand back the route the driver is already
        // on: if they have left it and this is still the best way there, that
        // is the right answer and not a fault. Adopting it *as new* is the
        // fault — it restarts the banner, discards `travelled` and re-arms the
        // join gate against a line the car never left.
        if sameLine(as: replacement) {
            merge(replacement, reason: reason)
        } else {
            adopt(replacement, reason: reason)
        }
        // Only off-route reroutes back off. A user tapping "fastest" has asked
        // for this one and is owed it immediately, and counting it would then
        // slow down the recovery they asked for.
        if reason == "offroute" {
            consecutiveReroutes += 1
            onRouteSince = nil
        }
        // Count the driver as on the route even though they are not on it yet:
        // the new line starts at a junction up ahead, not under the car. This
        // is what keeps the banner showing instructions rather than "head to
        // the start of your route" for a driver who is mid-trip and doing
        // nothing wrong. `awaitingJoin` is the other half — it holds off
        // *rerouting* over the same gap, which is what stopped this from
        // becoming an 8-second loop.
        hasJoinedRoute = true
        return .adopted
    }

    /// Whether a replacement covers exactly the ground already being driven.
    ///
    /// Compared on the geometry, because that is the only field that settles
    /// it: `km`, `minutes` and `mean_score` are rounded to one decimal in the
    /// trace and to rather less than that in a driver's judgement, so two
    /// genuinely different routes can agree on all three. Across the recorded
    /// drives 8 of 51 off-route reroutes came back byte-identical to the line
    /// already being followed.
    private func sameLine(as feature: RouteFeature) -> Bool {
        let other = feature.coordinates
        guard other.count == coordinates.count else { return false }
        return zip(coordinates, other).allSatisfy { $0.matches($1) }
    }

    /// Take a replacement's instructions without disturbing the drive.
    ///
    /// Same line, so there is nothing to re-join and no progress worth
    /// discarding — but the *words* can still be better. On 2026-08-25 a
    /// reroute returned the identical 1821-point polyline with its opening
    /// maneuver corrected from "Turn right onto Lake Avenue" to "Head north on
    /// Lake Avenue", because the car's heading had changed since the request
    /// before it. Dropping the reply outright would have thrown that away;
    /// adopting it restarted the drive to collect it. This does neither.
    ///
    /// Recorded in the trace like any other route, with the reason marked, so a
    /// drive that was handed the same line six times still says so.
    private func merge(_ feature: RouteFeature, reason: String) {
        trace?.route(feature, reason: reason + "-same")
        route = feature
        steps = feature.properties.steps
        // Against `coordinates`, which by definition are the feature's own.
        stepRemaining = Self.remainingAtEachStep(of: steps, along: coordinates)
        // Re-derived rather than kept. The line is unchanged, so the driver's
        // place on it is too — but the step *list* has just been replaced, and
        // an index into the old one names a different maneuver in the new one
        // (or none at all, if it is shorter). Walked from zero against the
        // distance already measured, which lands on the same ground the old
        // index did whenever the two lists agree.
        currentStep = lastProgress.map { firstStepAhead(of: $0.remaining, from: 0) } ?? 0
        // Still armed, and this is the half of `adopt` that must survive.
        // Nothing about the line has changed, but the reason the driver was
        // sent a replacement at all is that they had left it — so off-route
        // recovery has to keep holding until they are back on it, exactly as it
        // would for a line they had never seen. Without this a driver drifting
        // beside their route asks again on every cooldown, which is the storm
        // this whole path exists to stop. When they are in fact already on the
        // line — the pinned-match case — `settleAwaitingJoin` clears it on the
        // very next fix, so it costs nothing there.
        awaitingJoin = true
        awaitingJoinSince = now()
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
        matchAtAdoption = nil
        remainingMeters = feature.properties.km * 1000
        remainingMinutes = feature.properties.minutes
        // The driver has not reached this line yet — it begins at a junction
        // ahead of them. Hold off-route recovery until they do, or this route
        // triggers its own replacement on the very next fix.
        awaitingJoin = true
        awaitingJoinSince = now()
    }
}
