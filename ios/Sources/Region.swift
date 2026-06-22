import MapKit

extension MKCoordinateRegion {
    /// The whole state of Massachusetts.
    ///
    /// Defined once and shared by everything that needs "roughly where the user
    /// is": the initial map camera, the address search, and the autocomplete
    /// completer (which both bias their results toward this box). When the app
    /// expands beyond MA, this is the single place to widen.
    static let massachusetts = MKCoordinateRegion(
        center: CLLocationCoordinate2D(latitude: 42.15, longitude: -71.8),
        span: MKCoordinateSpan(latitudeDelta: 2.6, longitudeDelta: 2.6)
    )
}
