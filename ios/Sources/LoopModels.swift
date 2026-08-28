import CoreLocation
import Foundation

// These types mirror the JSON the backend returns from GET /api/loop.
//
// The loop itself is a plain `RouteFeature` — the same type a point-to-point
// route decodes into — because the backend deliberately returns the same GeoJSON
// shape for both. That is what lets a loop be handed to `NavigationModel` and
// drawn by the same map code with nothing special about it. Everything that *is*
// specific to a loop lives in `meta` instead of being added to the Feature's
// properties, so neither side had to grow a variant type.

/// The top-level response: one loop, its numbers, and where else the user could
/// be sent instead.
struct LoopResponse: Decodable {
    let loop: RouteFeature
    let meta: LoopMeta
    /// The compass directions that actually hold a loop of this length. Fewer
    /// than eight is normal and not an error — from a coastal start some of those
    /// directions are the ocean.
    let alternatives: [LoopAlternative]
    /// Set when the geography could not really answer, e.g. a short loop from a
    /// rural start that has to double back. Shown to the user as-is.
    let note: String?
}

/// What a loop is, beyond being a route.
struct LoopMeta: Decodable {
    /// What was asked for, after the server clamped it to the slider's range.
    let target_km: Double
    let km: Double
    let minutes: Double
    /// Average scenic score (0–10), length-weighted. A fair comparison between
    /// loops here in a way it isn't between routes, because the distance slider
    /// pins every candidate to the same length.
    let mean_score: Double
    /// Kilometers on roads scoring `beautiful_score` or better — the legible
    /// number, and the one that separates a scenic loop from a fast one of the
    /// same length far more sharply than the mean does.
    let beautiful_km: Double
    let beautiful_score: Double
    /// Kilometers spent re-driving a road already driven. Normally near zero;
    /// it is the defect that tells a driver their 40 km loop is really a 20 km
    /// drive twice, so it is always shown rather than shown when bad.
    let repeated_km: Double
    /// `[lat, lon]` of the farthest point, i.e. where the loop turns for home.
    let turnaround: [Double]
    /// Which compass octant this loop heads off in — what the regenerate button
    /// varies.
    let sector: String

    var turnaroundCoordinate: CLLocationCoordinate2D? {
        guard turnaround.count == 2 else { return nil }
        return CLLocationCoordinate2D(latitude: turnaround[0], longitude: turnaround[1])
    }

    var repeatedFraction: Double { km > 0 ? repeated_km / km : 0 }

    /// How far off the requested distance this landed. Around 5% at worst,
    /// because the loop that gets built is never exactly the one the candidate
    /// filter predicted.
    var distanceError: Double { target_km > 0 ? (km - target_km) / target_km : 0 }
}

/// One direction the user could regenerate into.
struct LoopAlternative: Decodable, Identifiable {
    let sector: String
    /// How many turnaround points this direction offers at this length. Not
    /// shown to the user; useful when deciding whether a direction is a real
    /// choice or a technicality.
    let candidates: Int

    var id: String { sector }

    /// The compass direction spelled out, for a label with room for it.
    var name: String {
        switch sector {
        case "N":  return "North"
        case "NE": return "Northeast"
        case "E":  return "East"
        case "SE": return "Southeast"
        case "S":  return "South"
        case "SW": return "Southwest"
        case "W":  return "West"
        case "NW": return "Northwest"
        default:   return sector
        }
    }
}
