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
    ///
    /// `start` moves the whole line that many metres north of the fixture
    /// origin, which is how a replacement route really arrives: it begins at
    /// the graph junction `snap` chose — a median 99 m from the car, p90 217 m
    /// — rather than under it. A route built with `start` therefore has its
    /// first maneuver somewhere the driver still has to reach.
    static func straightRoute(
        start: Double = 0,
        lengthMeters: Double = 5000,
        vertexSpacing: Double = 250,
        steps: [(Double, String)] = [(0, "Head north on Test Road"),
                                     (1000, "Turn right onto Elm Street"),
                                     (3000, "Turn left onto Oak Street"),
                                     (5000, "Arrive at your destination")],
        minutes: Double = 10
    ) -> RouteFeature {
        // Names omitted, which is also worth keeping: a step with no `name` key
        // is what a response cached before the field existed looks like, and
        // `RouteStep` has to go on decoding one.
        routeWithRoadNames(start: start, lengthMeters: lengthMeters,
                           vertexSpacing: vertexSpacing,
                           steps: steps.map { ($0.0, $0.1, nil) },
                           minutes: minutes)
    }

    /// `straightRoute`'s geometry, with the `name` the server really puts on
    /// every step: the road that maneuver takes you **onto**.
    ///
    /// `steps` are (metres along the route, instruction, name), and a nil name
    /// omits the key altogether. The default list is the same four maneuvers
    /// `straightRoute` uses, named the way `pipeline/router.py` names them —
    /// including the empty name on arrival, which is what it really sends
    /// (`router.py:1659`).
    ///
    /// The names deliberately are not derivable from the instructions by
    /// position: reading "Turn right onto Elm Street" and displaying Elm Street
    /// is the off-by-one this fixture exists to catch, so a test that passes by
    /// accident has to be impossible.
    static func routeWithRoadNames(
        start: Double = 0,
        lengthMeters: Double = 5000,
        vertexSpacing: Double = 250,
        steps: [(Double, String, String?)] = [(0, "Head north on Test Road", "Test Road"),
                                              (1000, "Turn right onto Elm Street", "Elm Street"),
                                              (3000, "Turn left onto Oak Street", "Oak Street"),
                                              (5000, "Arrive at your destination", "")],
        minutes: Double = 10
    ) -> RouteFeature {
        let count = Int(lengthMeters / vertexSpacing)
        let coordinates = (0...count).map { i -> [Double] in
            let c = north(start + Double(i) * vertexSpacing)
            return [c.longitude, c.latitude]
        }
        return decode(feature(coordinates: coordinates,
                              km: lengthMeters / 1000, minutes: minutes,
                              steps: steps.map { along, text, name in
                                  (north(start + along), text, name)
                              }))
    }

    /// A replacement route that turns the driver around: the line begins
    /// `start` metres north of the fixture origin and runs back *south* from
    /// there, so a driver heading north is matched some way along it and their
    /// position along it falls as they keep going.
    ///
    /// This is the shape the 2026-08-25 16:23:26 reroute really had. The line
    /// began up the road, opened with "Make a U-turn on Millbury Street", and
    /// came back over the road the car was already on — so the match landed
    /// 229.6 m along at 0.0 m off, which is both perfectly legitimate and
    /// exactly wrong for deciding which maneuvers have been driven.
    ///
    /// `steps` are (metres along the route, instruction), as in `straightRoute`.
    static func uTurnRoute(start: Double, lengthMeters: Double = 530,
                           vertexSpacing: Double = 50,
                           steps: [(Double, String)],
                           minutes: Double = 2) -> RouteFeature {
        let count = Int(lengthMeters / vertexSpacing)
        let coordinates = (0...count).map { i -> [Double] in
            let c = north(start - Double(i) * vertexSpacing)
            return [c.longitude, c.latitude]
        }
        return decode(feature(coordinates: coordinates,
                              km: lengthMeters / 1000, minutes: minutes,
                              steps: steps.map { along, text in
                                  (north(start - along), text, nil)
                              }))
    }

    /// A point `east`/`north` metres from the fixture origin.
    static func offset(east: Double, north: Double) -> CLLocationCoordinate2D {
        let metersPerDegLon = metersPerDegLat * cos(origin.latitude * .pi / 180)
        return CLLocationCoordinate2D(latitude: origin.latitude + north / metersPerDegLat,
                                      longitude: origin.longitude + east / metersPerDegLon)
    }

    /// A loop that actually closes: north, east, south, west, back to the
    /// coordinate it set off from, so `coordinates.last == coordinates.first`.
    ///
    /// This is the one property that makes a loop different from a route, and
    /// `straightRoute` cannot express it — its last point is a whole route
    /// length from its first, so a fix at the start is nowhere near the end and
    /// nothing about arrival can be tested there. On a real loop those two are
    /// the same place, and a fix beside the start node is as close to the
    /// closing segment as to the opening one.
    ///
    /// The far point is the opposite corner, `2 * sideMeters` along.
    static func closedLoopRoute(sideMeters: Double = 2_000,
                                spacing: Double = 100) -> RouteFeature {
        var points: [[Double]] = []
        func add(_ east: Double, _ north: Double) {
            let c = offset(east: east, north: north)
            points.append([c.longitude, c.latitude])
        }
        for n in stride(from: 0.0, through: sideMeters, by: spacing) { add(0, n) }
        for e in stride(from: spacing, through: sideMeters, by: spacing) { add(e, sideMeters) }
        for n in stride(from: sideMeters - spacing, through: 0.0, by: -spacing) { add(sideMeters, n) }
        for e in stride(from: sideMeters - spacing, through: 0.0, by: -spacing) { add(e, 0) }
        let steps: [(CLLocationCoordinate2D, String, String?)] = [
            (offset(east: 0, north: 0), "Head north on Test Road", "Test Road"),
            (offset(east: sideMeters, north: sideMeters), "Turn right onto Far Road", "Far Road"),
            (offset(east: 0, north: 0), "Arrive back where you started", ""),
        ]
        return decode(feature(coordinates: points, km: 4 * sideMeters / 1000,
                              minutes: 8, steps: steps))
    }

    /// The far point of `closedLoopRoute`.
    static func closedLoopTurnaround(sideMeters: Double = 2_000) -> CLLocationCoordinate2D {
        offset(east: sideMeters, north: sideMeters)
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
        let steps: [(CLLocationCoordinate2D, String, String?)] = [
            (north(0), "Head north on Test Road", nil),
            (north(3000), "Sharp right onto Return Road", nil),
            (north(500), "Arrive at your destination", nil),
        ]
        return decode(feature(coordinates: points, km: 5.5, minutes: 11, steps: steps))
    }

    // MARK: - JSON plumbing

    /// `steps` are (maneuver coordinate, instruction, name), where a nil name
    /// leaves the key off the step entirely.
    static func feature(coordinates: [[Double]], km: Double, minutes: Double,
                        steps: [(CLLocationCoordinate2D, String, String?)],
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
                "steps": steps.map { coordinate, text, name -> [String: Any] in
                    var step: [String: Any] = ["instruction": text,
                                               "lat": coordinate.latitude,
                                               "lon": coordinate.longitude,
                                               "distance_m": 0]
                    if let name { step["name"] = name }
                    return step
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
                steps: feature.properties.steps.map {
                    ($0.coordinate, $0.instruction, $0.name)
                })
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

    /// A fix that carries a course and a speed.
    ///
    /// `fix` leaves both at CoreLocation's -1 "no opinion", which is what a
    /// stationary phone reports and what most of these tests want. A reroute
    /// only forwards a heading when the car is actually moving, so testing
    /// that needs a fix that says so.
    static func movingFix(_ coordinate: CLLocationCoordinate2D,
                          course: CLLocationDirection,
                          speed: CLLocationSpeed) -> CLLocation {
        CLLocation(coordinate: coordinate, altitude: 0,
                   horizontalAccuracy: 5, verticalAccuracy: 5,
                   course: course, speed: speed, timestamp: Date())
    }
}
