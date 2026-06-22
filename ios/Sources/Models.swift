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

/// One turn-by-turn maneuver: what to do and where it happens.
struct RouteStep: Decodable, Identifiable {
    let instruction: String
    let lat: Double
    let lon: Double
    /// How far this instruction carries you (the length of its road leg).
    let distance_m: Double

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
    var sceneryBreakdown: [(label: String, km: Double)] {
        let displayOrder = ["forest/park", "water", "coast", "hills", "farmland"]
        return displayOrder.compactMap { key in
            guard let km = scenery_km[key], km > 0 else { return nil }
            return (key, km)
        }
    }
}
