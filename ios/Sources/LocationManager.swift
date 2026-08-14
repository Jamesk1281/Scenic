import CoreLocation
import Observation

/// Thin wrapper around `CLLocationManager` — the one place the app touches the
/// user's location.
///
/// Two ways in. `start()`/`stop()` bracket a live navigation session and stream
/// fixes into `location`; `currentLocation()` takes a single fix so the planning
/// screen can offer "My Location" as a start point without leaving the GPS on.
///
/// Both paths reject junk fixes (see `isUsable`). That matters more than it
/// looks: `startUpdatingLocation` hands back a *cached* fix immediately, often
/// minutes old and derived from Wi-Fi rather than GPS, so the first location the
/// app ever sees is the least trustworthy one it will get. Routing from it puts
/// the driver a street or two from where they actually are.
@Observable
final class LocationManager: NSObject, CLLocationManagerDelegate {
    /// The most recent usable fix, or nil until the first one arrives.
    var location: CLLocation?

    /// Called with every usable fix, as it lands. Set for the duration of a
    /// drive by `RouteModel`, cleared when the drive ends.
    ///
    /// This exists because `location` alone is not enough. Reading it from a
    /// SwiftUI `onChange` works only while the app is on screen: backgrounded,
    /// the scene stops rendering, body evaluation is suspended, and observed
    /// changes coalesce — so a locked phone delivers *one* callback carrying the
    /// latest fix when it wakes, and every fix in between is gone. Steps would
    /// stop advancing, arrival would never fire, and the drive trace would hold
    /// a hole exactly the length of the drive that wasn't watched.
    ///
    /// A closure called straight from the delegate has no view in the path, so
    /// the drive runs whether or not anything is drawing it.
    var onFix: (@MainActor (CLLocation) -> Void)?
    /// Current permission state, so the UI can prompt or explain if denied.
    var authorization: CLAuthorizationStatus

    /// Worst horizontal accuracy we'll act on, in meters. Roughly "we know which
    /// road you're on"; a good GPS fix in the open is 5–10 m.
    private static let usableAccuracy: Double = 65
    /// Oldest fix we'll act on. Only ever excludes the cached fix delivered at
    /// startup — during a drive, fixes arrive sub-second fresh.
    private static let usableAge: TimeInterval = 15

    private let manager = CLLocationManager()

    /// Whether this *build* declares the background location mode.
    ///
    /// `allowsBackgroundLocationUpdates = true` raises `NSInvalidArgumentException`
    /// when the bundle lacks `UIBackgroundModes: location` — an Objective-C
    /// exception, so no Swift `catch` can reach it and the app simply dies. That
    /// is not a hypothetical: `ios/Generated/Info.plist` is a *gitignored build
    /// output* of `project.yml`, regenerated only by `xcodegen generate`, so a
    /// checkout whose plist predates the entry crashes the instant the driver
    /// taps Start — with a clean `git status` and nothing to suggest why.
    /// Reading the bundle turns that into a degraded drive (foreground-only
    /// updates) instead of a crash, and `recordingProblem` will notice if fixes
    /// then stop while backgrounded.
    private static let backgroundLocationDeclared: Bool = {
        let modes = Bundle.main.object(forInfoDictionaryKey: "UIBackgroundModes") as? [String]
        return modes?.contains("location") ?? false
    }()

    /// True between `start()` and `stop()`, so a one-shot request knows whether
    /// it may switch the GPS back off when it's done.
    private var navigating = false

    /// A `currentLocation()` call waiting for a fix good enough to answer with.
    private var pendingFix: PendingFix?
    /// A `currentLocation()` call waiting for the permission prompt to resolve.
    private var pendingAuth: CheckedContinuation<CLAuthorizationStatus, Never>?

    /// One in-flight one-shot request: who to answer, and the best fix seen so
    /// far in case we time out before an accurate one arrives.
    private final class PendingFix {
        var continuation: CheckedContinuation<CLLocation?, Never>?
        var best: CLLocation?

        init(_ continuation: CheckedContinuation<CLLocation?, Never>) {
            self.continuation = continuation
        }

        /// Answer at most once — the fix and the timeout race each other.
        func finish(with location: CLLocation?) {
            continuation?.resume(returning: location)
            continuation = nil
        }
    }

    override init() {
        authorization = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        // No distance filter, deliberately. A filter cannot make fixes arrive
        // faster — GPS produces them at about 1 Hz and the filter only
        // *suppresses* the ones that moved too little. So at driving speed a 5 m
        // filter changed nothing (1 Hz is already ~29 m apart at 65 mph, which is
        // what `advanceSteps` is written around), while a stopped car moved less
        // than 5 m per second and its updates were dropped almost entirely.
        //
        // That silence is invisible to navigation and fatal to `DriveTrace`:
        // time spent stopped at lights and stop signs is exactly the cost the
        // router charges nothing for, and with the filter on, a 40-second red
        // light left no evidence it had happened.
        manager.distanceFilter = kCLDistanceFilterNone
        // Tell CoreLocation this is a car, so it filters the fix stream for
        // road travel instead of walking.
        manager.activityType = .automotiveNavigation
        // iOS otherwise decides on its own that we've stopped moving and pauses
        // updates — which, mid-drive, silently freezes the whole nav screen.
        manager.pausesLocationUpdatesAutomatically = false
    }

    /// Whether a fix is fresh and accurate enough to act on. Invalid fixes carry
    /// a negative accuracy, which would otherwise sail through a `<` comparison.
    private static func isUsable(_ location: CLLocation) -> Bool {
        location.horizontalAccuracy > 0
            && location.horizontalAccuracy <= usableAccuracy
            && -location.timestamp.timeIntervalSinceNow <= usableAge
    }

    // MARK: - Live navigation

    /// Ask permission (if needed) and start receiving location updates.
    ///
    /// Background updates are switched on here rather than in `init` because
    /// they are only legal while the app is actually navigating, and iOS raises
    /// if the capability isn't declared — so the setting stays paired with the
    /// `UIBackgroundModes` entry in `project.yml` and with the drive it's for.
    /// `showsBackgroundLocationIndicator` puts the blue bar up: this app follows
    /// you from your pocket only while a drive is running, and says so.
    func start() {
        navigating = true
        manager.requestWhenInUseAuthorization()
        // Only if this build actually declares the capability — see
        // `backgroundLocationDeclared`. Setting it without the entry raises an
        // Objective-C exception that no Swift `catch` can reach.
        if Self.backgroundLocationDeclared {
            manager.allowsBackgroundLocationUpdates = true
            manager.showsBackgroundLocationIndicator = true
        }
        manager.startUpdatingLocation()
    }

    /// Stop updates when leaving navigation, to save battery.
    func stop() {
        navigating = false
        // Surrendered as soon as the drive is over. Left on, a one-shot "My
        // Location" from the planning screen would quietly hold a background
        // location grant the user only ever agreed to for a drive. (Clearing it
        // is always safe; only setting it true needs the capability.)
        manager.allowsBackgroundLocationUpdates = false
        guard pendingFix == nil else { return }   // a one-shot still needs them
        manager.stopUpdatingLocation()
    }

    // MARK: - One-shot fix, for "My Location" while planning

    /// Wait for a single accurate fix, or give up after `timeout` and return the
    /// best we saw (nil if we saw nothing, or permission was refused).
    ///
    /// Pinned to the main actor deliberately. A `nonisolated` async method runs
    /// on the concurrent executor, not on its caller's actor, so without this
    /// the `CLLocationManager` calls below would be made from a background
    /// thread with no run loop — while its delegate callbacks still arrive on
    /// main, leaving `pendingFix` touched from two threads.
    @MainActor
    func currentLocation(timeout: TimeInterval = 8) async -> CLLocation? {
        if manager.authorizationStatus == .notDetermined {
            _ = await requestAuthorization()
        }
        guard manager.authorizationStatus == .authorizedWhenInUse
                || manager.authorizationStatus == .authorizedAlways else { return nil }

        // A fix we already have in hand, if it's still good, saves the wait.
        if let known = location, Self.isUsable(known) { return known }

        let fix = await withCheckedContinuation { (continuation: CheckedContinuation<CLLocation?, Never>) in
            // A second request supersedes the first, and the first has to be
            // answered on its way out. Replacing `pendingFix` while its
            // continuation was unresumed left that caller suspended forever —
            // its timeout task bails out on seeing itself superseded — so
            // `useMyLocation` never returned, and the spinner in the field
            // never stopped. Two taps could do it: the button and the
            // "My Location" row in the suggestion list are separate controls.
            if let superseded = pendingFix {
                superseded.finish(with: superseded.best)
            }
            let pending = PendingFix(continuation)
            pendingFix = pending
            manager.startUpdatingLocation()

            Task { @MainActor in
                try? await Task.sleep(for: .seconds(timeout))
                guard self.pendingFix === pending else { return }
                self.pendingFix = nil
                self.settleAfterOneShot()
                pending.finish(with: pending.best)
            }
        }
        return fix
    }

    /// Ask for permission and wait for the user to answer the system prompt.
    @MainActor
    private func requestAuthorization() async -> CLAuthorizationStatus {
        await withCheckedContinuation { continuation in
            pendingAuth = continuation
            manager.requestWhenInUseAuthorization()
        }
    }

    /// Switch the GPS back off if a one-shot turned it on outside navigation.
    private func settleAfterOneShot() {
        if !navigating { manager.stopUpdatingLocation() }
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let newest = locations.last else { return }

        // A one-shot holds out for an accurate fix, but keeps the best it has
        // seen so a timeout can still answer with something.
        if let pending = pendingFix {
            if pending.best.map({ newest.horizontalAccuracy < $0.horizontalAccuracy }) ?? true {
                pending.best = newest
            }
            if Self.isUsable(newest) {
                pendingFix = nil
                settleAfterOneShot()
                pending.finish(with: newest)
            }
        }

        guard Self.isUsable(newest) else { return }
        location = newest
        // Delivered here rather than observed from a view — see `onFix`. Core
        // Location calls its delegate on the run loop the manager was created
        // on, which for this app is main, so the isolation is real rather than
        // assumed away.
        MainActor.assumeIsolated { onFix?(newest) }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorization = manager.authorizationStatus
        // `.notDetermined` is the state *before* the prompt is answered, so it
        // isn't an answer — keep waiting.
        if manager.authorizationStatus != .notDetermined, let waiting = pendingAuth {
            pendingAuth = nil
            waiting.resume(returning: manager.authorizationStatus)
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // A refusal is terminal for a one-shot; transient errors just mean the
        // next fix hasn't landed yet, and the timeout will cover us.
        guard (error as? CLError)?.code == .denied, let pending = pendingFix else { return }
        pendingFix = nil
        settleAfterOneShot()
        pending.finish(with: nil)
    }
}
