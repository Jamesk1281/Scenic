import MapKit
import Observation

/// Live address autocomplete, wrapping Apple's `MKLocalSearchCompleter` — the
/// same engine behind Apple Maps' search. You feed it the text as the user
/// types; it streams back suggestions (a title + subtitle, e.g. "Northeastern
/// University" / "Boston, MA") through its delegate, which we publish here for
/// SwiftUI to show in a dropdown.
///
/// A suggestion is only a *label*, not a location yet — resolving it to actual
/// coordinates is a second step (see `RouteModel.choose`).
@Observable
final class SearchCompleter: NSObject, MKLocalSearchCompleterDelegate {
    /// Suggestions for the most recent query fragment.
    var suggestions: [MKLocalSearchCompletion] = []

    private let completer = MKLocalSearchCompleter()

    override init() {
        super.init()
        completer.delegate = self
        completer.resultTypes = [.address, .pointOfInterest]
        // Bias results toward Massachusetts so "northea" surfaces Northeastern.
        completer.region = MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: 42.15, longitude: -71.8),
            span: MKCoordinateSpan(latitudeDelta: 2.6, longitudeDelta: 2.6)
        )
    }

    /// Refresh suggestions for the current text. Very short fragments are
    /// ignored so we don't show noise for one or two letters.
    func update(for fragment: String) {
        let query = fragment.trimmingCharacters(in: .whitespaces)
        guard query.count >= 2 else {
            suggestions = []
            return
        }
        completer.queryFragment = query
    }

    /// Hide the dropdown (after the user picks a suggestion or dismisses it).
    func clear() {
        suggestions = []
        completer.queryFragment = ""
    }

    // MARK: - MKLocalSearchCompleterDelegate (called on the main thread)

    func completerDidUpdateResults(_ completer: MKLocalSearchCompleter) {
        suggestions = completer.results
    }

    func completer(_ completer: MKLocalSearchCompleter, didFailWithError error: Error) {
        suggestions = []
    }
}
