import CoreLocation
import Observation

/// Thin wrapper around `CLLocationManager` for live navigation — the one place
/// the app touches the user's location.
///
/// It only runs while you're navigating: `start()` requests permission and
/// begins updates, `stop()` ends them. The published `location` drives the
/// nav screen (advancing steps, detecting arrival and off-route). This is the
/// location capability we deliberately removed when the app was search-only;
/// turn-by-turn is the reason it's back.
@Observable
final class LocationManager: NSObject, CLLocationManagerDelegate {
    /// The most recent fix, or nil until the first one arrives.
    var location: CLLocation?
    /// Current permission state, so the UI can prompt or explain if denied.
    var authorization: CLAuthorizationStatus

    private let manager = CLLocationManager()

    override init() {
        authorization = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        manager.distanceFilter = 5   // meters between updates
    }

    /// Ask permission (if needed) and start receiving location updates.
    func start() {
        manager.requestWhenInUseAuthorization()
        manager.startUpdatingLocation()
    }

    /// Stop updates when leaving navigation, to save battery.
    func stop() {
        manager.stopUpdatingLocation()
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        location = locations.last
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorization = manager.authorizationStatus
    }
}
