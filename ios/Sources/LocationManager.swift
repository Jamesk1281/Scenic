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
    /// Current permission state, so the UI can prompt or explain if denied.
    var authorization: CLAuthorizationStatus

    /// Worst horizontal accuracy we'll act on, in meters. Roughly "we know which
    /// road you're on"; a good GPS fix in the open is 5–10 m.
    private static let usableAccuracy: Double = 65
    /// Oldest fix we'll act on. Only ever excludes the cached fix delivered at
    /// startup — during a drive, fixes arrive sub-second fresh.
    private static let usableAge: TimeInterval = 15

    private let manager = CLLocationManager()

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
        manager.distanceFilter = 5   // meters between updates
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
    func start() {
        navigating = true
        manager.requestWhenInUseAuthorization()
        manager.startUpdatingLocation()
    }

    /// Stop updates when leaving navigation, to save battery.
    func stop() {
        navigating = false
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
