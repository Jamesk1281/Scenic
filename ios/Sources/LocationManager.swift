import CoreLocation
import Observation

/// A thin wrapper around `CLLocationManager` that exposes just what the UI
/// needs: a way to ask "where am I right now?" and a flag for when the user
/// has denied location access.
///
/// `CLLocationManager` works through a delegate (it calls us back when the
/// permission state changes or a location fix arrives), so this class adopts
/// `CLLocationManagerDelegate` and forwards the interesting events into
/// `@Observable` properties that SwiftUI can watch.
@Observable
final class LocationManager: NSObject, CLLocationManagerDelegate {

    /// The most recent coordinate we resolved, or `nil` until we get a fix.
    var coordinate: CLLocationCoordinate2D?

    /// `true` once the user has explicitly denied location access, so the
    /// UI can fall back to typing an address instead.
    var accessDenied = false

    /// The underlying system object that actually does the GPS work.
    private let manager = CLLocationManager()

    /// A one-time callback, remembered between "ask for permission" and
    /// "a location finally arrived," then cleared so it only fires once.
    private var pendingFix: ((CLLocationCoordinate2D) -> Void)?

    override init() {
        super.init()
        manager.delegate = self
        // City-block accuracy is plenty for picking a routing start point,
        // and it gets a fix faster / uses less battery than best accuracy.
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    /// Ask for a single "where am I now" fix. If we don't have permission yet
    /// we request it first; the delegate callback below resumes the request
    /// once the user responds. `completion` runs exactly once on success.
    func requestCurrentLocation(_ completion: @escaping (CLLocationCoordinate2D) -> Void) {
        pendingFix = completion

        switch manager.authorizationStatus {
        case .notDetermined:
            // Triggers the system permission prompt. When the user answers,
            // `locationManagerDidChangeAuthorization` fires and continues.
            manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways:
            manager.requestLocation()
        default:
            // Denied or restricted: nothing we can do but tell the UI.
            accessDenied = true
            pendingFix = nil
        }
    }

    // MARK: - CLLocationManagerDelegate

    /// Called whenever the permission state changes (including right after the
    /// user answers the prompt). If we were waiting on a fix, continue now.
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways:
            accessDenied = false
            if pendingFix != nil { manager.requestLocation() }
        case .denied, .restricted:
            accessDenied = true
            pendingFix = nil
        default:
            break
        }
    }

    /// A location fix arrived. Publish it and fire the one-time callback.
    func locationManager(_ manager: CLLocationManager,
                         didUpdateLocations locations: [CLLocation]) {
        guard let fix = locations.last?.coordinate else { return }
        coordinate = fix
        pendingFix?(fix)
        pendingFix = nil
    }

    /// The fix failed (no signal, simulator with no location set, etc.).
    /// Drop the callback so we don't leave the UI waiting forever.
    func locationManager(_ manager: CLLocationManager,
                         didFailWithError error: Error) {
        pendingFix = nil
    }
}
