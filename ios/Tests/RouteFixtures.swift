import CoreLocation
import Foundation
@testable import Scenic

/// Routes built the way the app really gets them — decoded from the backend's
/// JSON — so these tests exercise the decoding path too, and a field renamed on
/// the server breaks them here rather than on a phone.
enum Fixture {

    /// Meters per degree of latitude, matching the constant `Geo.swift`
    /// projects with, so a fixture's stated length is the length the code sees.
    static let metersPerDegLat = 111_320.0

    static let origin = CLLocationCoordinate2D(latitude: 42.0, longitude: -71.0)

    /// A point `meters` due north of the fixture origin.
    static func north(_ meters: Double) -> CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: origin.latitude + meters / metersPerDegLat,
                               longitude: origin.longitude)
    }

    /// A straight 5 km road running north, with vertices every 250 m and
    /// maneuvers at 0 m, 1 km, 3 km and the end.
    ///
    /// `steps` are (metres along the route, instruction).
    static func straightRoute(
        lengthMeters: Double = 5000,
        vertexSpacing: Double = 250,
        steps: [(Double, String)] = [(0, "Head north on Test Road"),
                                     (1000, "Turn right onto Elm Street"),
                                     (3000, "Turn left onto Oak Street"),
                                     (5000, "Arrive at your destination")],
        minutes: Double = 10
    ) -> RouteFeature {
        let count = Int(lengthMeters / vertexSpacing)
        let coordinates = (0...count).map { i -> [Double] in
            let c = north(Double(i) * vertexSpacing)
            return [c.longitude, c.latitude]
        }
        return decode(feature(coordinates: coordinates,
                              km: lengthMeters / 1000, minutes: minutes,
                              steps: steps.map { along, text in
                                  let c = north(along)
                                  return (c, text)
                              }))
    }

    /// A route that runs 3 km north, turns around, and comes back to 500 m —
    /// so it passes close to a destination pin placed near the start long
    /// before the drive is over.
    static func outAndBackRoute() -> RouteFeature {
        var points: [[Double]] = []
        for i in stride(from: 0.0, through: 3000.0, by: 250) {
            let c = north(i); points.append([c.longitude, c.latitude])
        }
        for i in stride(from: 2750.0, through: 500.0, by: -250) {
            let c = north(i); points.append([c.longitude, c.latitude])
        }
        let steps: [(CLLocationCoordinate2D, String)] = [
            (north(0), "Head north on Test Road"),
            (north(3000), "Sharp right onto Return Road"),
            (north(500), "Arrive at your destination"),
        ]
        return decode(feature(coordinates: points, km: 5.5, minutes: 11, steps: steps))
    }

    // MARK: - JSON plumbing

    static func feature(coordinates: [[Double]], km: Double, minutes: Double,
                        steps: [(CLLocationCoordinate2D, String)],
                        meanScore: Double = 6.0,
                        sceneryKm: [String: Double] = ["water": 3.0, "coast": 0.0,
                                                       "forest/park": 2.0]) -> [String: Any] {
        [
            "type": "Feature",
            "geometry": ["type": "LineString", "coordinates": coordinates],
            "properties": [
                "km": km,
                "minutes": minutes,
                "mean_score": meanScore,
                "scenery_km": sceneryKm,
                "steps": steps.map { coordinate, text in
                    ["instruction": text, "lat": coordinate.latitude,
                     "lon": coordinate.longitude, "distance_m": 0]
                },
            ],
        ]
    }

    static func decode(_ object: [String: Any]) -> RouteFeature {
        let data = try! JSONSerialization.data(withJSONObject: object)
        return try! JSONDecoder().decode(RouteFeature.self, from: data)
    }

    static func response(fastest: RouteFeature, scenic: RouteFeature) -> RouteResponse {
        // Round-trip through JSON so the stub hands back exactly the shape the
        // network would.
        let encode = { (feature: RouteFeature) -> [String: Any] in
            Fixture.feature(
                coordinates: feature.geometry.coordinates,
                km: feature.properties.km, minutes: feature.properties.minutes,
                steps: feature.properties.steps.map { ($0.coordinate, $0.instruction) })
        }
        let data = try! JSONSerialization.data(
            withJSONObject: ["fastest": encode(fastest), "scenic": encode(scenic)])
        return try! JSONDecoder().decode(RouteResponse.self, from: data)
    }

    static func fix(_ coordinate: CLLocationCoordinate2D) -> CLLocation {
        CLLocation(coordinate: coordinate, altitude: 0,
                   horizontalAccuracy: 5, verticalAccuracy: 5, timestamp: Date())
    }

    /// A fix `meters` along the straight fixture route.
    static func fixAt(_ meters: Double) -> CLLocation { fix(north(meters)) }
}
