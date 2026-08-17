import CoreLocation
import Foundation

// These types mirror the JSON the backend returns from GET /api/route.
// The backend speaks GeoJSON, so each route is a "Feature" with a geometry
// (the line) and properties (the stats). `Decodable` lets JSONDecoder turn
// the response straight into these structs.

/// The top-level response: a fastest route and a scenic route for the same trip.
struct RouteResponse: Decodable {
    let fastest: RouteFeature
    let scenic: RouteFeature
}

/// One route: its shape on the map plus its summary stats.
struct RouteFeature: Decodable {
    let geometry: Geometry
    let properties: RouteProps

    /// The route line as MapKit coordinates. GeoJSON stores points as
    /// `[longitude, latitude]`, so we flip the order when converting.
    var coordinates: [CLLocationCoordinate2D] {
        geometry.coordinates.map { point in
            CLLocationCoordinate2D(latitude: point[1], longitude: point[0])
        }
    }
}

/// The raw GeoJSON LineString: an array of `[lon, lat]` pairs.
struct Geometry: Decodable {
    let coordinates: [[Double]]
}

/// What kind of maneuver a step is.
///
/// The vocabulary is the backend's, which is in turn OSRM's and Valhalla's —
/// so if the routing engine is ever swapped this type does not move. Decoded
/// from a string with an `unknown` fallback rather than as a bare enum,
/// because a backend that learns a new maneuver must not make the whole route
/// undecodable on a phone that hasn't been updated: an unrecognised type still
/// has a perfectly good `instruction` to show.
enum ManeuverType: String, Decodable {
    /// `fork` is "keep left/right" — a junction where the road you are on bends
    /// past one that runs straighter, so holding the wheel takes you off route.
    /// Not a turn: the arrow has to lean, not point.
    case depart, turn, `continue`, roundabout, exit, merge, fork, arrive, unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = ManeuverType(rawValue: raw) ?? .unknown
    }

    /// The SF Symbol for this maneuver, before the modifier refines it.
    var symbol: String {
        switch self {
        case .depart:     return "location.fill"
        case .roundabout: return "arrow.triangle.turn.up.right.circle"
        case .exit:       return "arrow.turn.up.right"
        case .merge:      return "arrow.merge"
        case .fork:       return "arrow.triangle.branch"
        case .arrive:     return "flag.checkered"
        case .turn, .continue, .unknown: return "arrow.up"
        }
    }
}

/// One turn-by-turn maneuver: what to do and where it happens.
///
/// Everything past `distance_m` is optional so an older cached response, or a
/// fixture written before the maneuver rework, still decodes.
struct RouteStep: Decodable, Identifiable {
    let instruction: String
    let lat: Double
    let lon: Double
    /// How far this instruction carries you (the length of its road leg).
    let distance_m: Double

    /// The structured maneuver behind `instruction`. Present so the app can
    /// style a motorway exit differently from a left turn, and so voice
    /// guidance can assemble its own phrasing from the parts rather than
    /// reading a sentence built for the screen.
    let type: ManeuverType?
    let modifier: String?
    /// The signed exit number, when the junction carries one.
    let exit_ref: String?
    /// Where a ramp says it goes, as it reads on the sign.
    let destination: String?
    /// Which exit to take at a rotary, 1-based; 0 when it can't be counted.
    let roundabout_exit: Int?

    var maneuver: ManeuverType { type ?? .unknown }

    /// The icon for this step, refined by the turn direction where there is
    /// one — a left turn and a right turn are the same `type`, and the arrow
    /// is the part a driver reads at a glance.
    /// Exits and forks carry a side too, and this used to ignore it: an `exit`
    /// drew a hardcoded right arrow next to "Take the exit on the left", and
    /// "Keep left" and "Keep right" drew the same branch glyph. A fork is the
    /// one maneuver whose *entire* content is which side to hold, so that was
    /// the arrow with the least to say and the most riding on it.
    var symbol: String {
        switch maneuver {
        case .turn:
            switch modifier ?? "" {
            case "left":         return "arrow.turn.up.left"
            case "right":        return "arrow.turn.up.right"
            case "slight left":  return "arrow.up.left"
            case "slight right": return "arrow.up.right"
            case "sharp left":   return "arrow.uturn.left"
            case "sharp right":  return "arrow.uturn.right"
            case "uturn":        return "arrow.uturn.down"
            default:             return maneuver.symbol
            }
        case .exit: return leansLeft ? "arrow.turn.up.left" : "arrow.turn.up.right"
        // Leaning rather than pointing — a fork is not a turn, and an arrow
        // that says one would be worse than none.
        case .fork: return leansLeft ? "arrow.up.left" : "arrow.up.right"
        default:    return maneuver.symbol
        }
    }

    /// Which way the modifier leans. Right is the fallback because it is the
    /// common side for both maneuvers that use this, and because the server's
    /// own wording falls back the same way.
    private var leansLeft: Bool { modifier?.hasSuffix("left") ?? false }

    var id: String { "\(lat),\(lon),\(instruction)" }
    var coordinate: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: lat, longitude: lon)
    }
}

/// The per-route stats the backend computes.
struct RouteProps: Decodable {
    let km: Double
    let minutes: Double
    /// Average scenic score (0–10) along the route, length-weighted.
    let mean_score: Double
    /// Kilometers of the route that pass each scenery feature, e.g.
    /// `["forest/park": 36.0, "water": 27.0, ...]`.
    let scenery_km: [String: Double]
    /// Turn-by-turn maneuvers from start to destination, for live navigation.
    let steps: [RouteStep]

    /// The scenery features in display order, dropping any the route never
    /// actually touches (0 km), so the breakdown only shows what's relevant.
    /// Keep this list in sync with SCENERY_BREAKDOWN in pipeline/router.py —
    /// a label missing here silently vanishes from the app's breakdown.
    var sceneryBreakdown: [(label: String, km: Double)] {
        let displayOrder = ["forest/park", "water", "coast", "hills", "farmland", "town"]
        return displayOrder.compactMap { key in
            guard let km = scenery_km[key], km > 0 else { return nil }
            return (key, km)
        }
    }
}
