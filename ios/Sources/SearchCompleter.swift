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
        completer.region = .massachusetts
    }

    /// Refresh suggestions for the current text, ranked around `region`.
    ///
    /// The caller passes the part of the map the user is looking at (or is
    /// standing in), because the completer ranks by distance from that region's
    /// center: bias it statewide and a search from Needham surfaces places in
    /// central Massachusetts first. Very short fragments are ignored so we don't
    /// show noise for one or two letters.
    func update(for fragment: String, near region: MKCoordinateRegion) {
        let query = fragment.trimmingCharacters(in: .whitespaces)
        guard query.count >= 2 else {
            suggestions = []
            return
        }
        completer.region = region
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
