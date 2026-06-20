import CoreLocation
import Foundation

/// Decodes the GeoJSON Feature pair returned by GET /api/route.
struct RouteResponse: Decodable {
    let fastest: RouteFeature
    let scenic: RouteFeature
}

struct RouteFeature: Decodable {
    let geometry: Geometry
    let properties: RouteProps

    var coordinates: [CLLocationCoordinate2D] {
        geometry.coordinates.map { CLLocationCoordinate2D(latitude: $0[1], longitude: $0[0]) }
    }
}

struct Geometry: Decodable {
    let coordinates: [[Double]]   // [[lon, lat], ...]
}

struct RouteProps: Decodable {
    let km: Double
    let minutes: Double
    let mean_score: Double
    let scenery_km: [String: Double]

    /// Scenery features ordered for display, dropping ones the route never touches.
    var sceneryBreakdown: [(label: String, km: Double)] {
        ["forest/park", "water", "coast", "hills", "farmland"]
            .compactMap { key in
                guard let v = scenery_km[key], v > 0 else { return nil }
                return (key, v)
            }
    }
}
