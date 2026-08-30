import CoreLocation
import MapKit

/// Putting a name a human recognises on a place the app picked.
///
/// Shared by both planning tabs. It lived privately inside `RouteModel`, so the
/// loop tab — which starts from one point and derives its entire shape from it,
/// with no destination to sanity-check that point against — had a worse copy of
/// half of it and none of the other half: its start field said `My Location` and
/// stayed that way, and a resolved search read "Main Street" where the
/// directions tab read "Main Street, Needham".
enum PlaceNaming {

    /// What a "My Location" start says before the geocode answers.
    static let myLocation = "My Location"

    /// A label a human would recognise for a search result.
    ///
    /// `MKMapItem.name` on its own is often bare to the point of being
    /// wrong-looking — "Main Street", "Post Office", a bare house number — so we
    /// append the town unless the name already carries it. The field's text is
    /// the only confirmation the user gets that we resolved the place they
    /// actually meant.
    static func displayName(for item: MKMapItem, fallback: String) -> String {
        let placemark = item.placemark
        let name = item.name ?? placemark.name ?? fallback
        guard let town = placemark.locality, !name.localizedCaseInsensitiveContains(town) else {
            return name
        }
        return "\(name), \(town)"
    }

    /// "My Location · Highland Ave, Needham" for a fix, or nil if reverse
    /// geocoding has nothing to add.
    ///
    /// Worth the extra call: "My Location" on its own gives the driver no way to
    /// notice we've placed them on the wrong road. That reasoning is stronger on
    /// the loop tab than on the one it was written for — a loop is *only* this
    /// point, so a bad fix is a bad drive with nothing else on screen to give it
    /// away.
    ///
    /// The caller has to re-check its own state afterwards: this suspends for as
    /// long as the geocoder takes, and the user can change the start while it is
    /// in flight.
    static func currentLocationLabel(for fix: CLLocation) async -> String? {
        guard let placemark = try? await CLGeocoder().reverseGeocodeLocation(fix).first
        else { return nil }
        let here = [placemark.thoroughfare, placemark.locality]
            .compactMap { $0 }
            .joined(separator: ", ")
        return here.isEmpty ? nil : "\(myLocation) · \(here)"
    }
}
