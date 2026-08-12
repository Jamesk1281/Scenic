import CoreLocation

extension Double {
    /// The backend speaks metric; the app is Massachusetts-only, so every
    /// distance the user sees is in miles. Converted at the view layer so the
    /// numbers coming off the API stay in their original units.
    var milesFromKm: Double { self * 0.621371 }
}

extension CLLocation {
    /// Straight-line distance in meters from this location to a coordinate.
    func distance(to coordinate: CLLocationCoordinate2D) -> Double {
        distance(from: CLLocation(latitude: coordinate.latitude, longitude: coordinate.longitude))
    }
}

extension CLLocationCoordinate2D {
    /// `CLLocationCoordinate2D` isn't `Equatable`; this covers the one thing we
    /// ask of it — "is this still the same pin I set earlier?".
    func matches(_ other: CLLocationCoordinate2D) -> Bool {
        latitude == other.latitude && longitude == other.longitude
    }
}

/// Where a driver sits relative to a route line. All values in meters.
struct RouteProgress {
    /// Perpendicular distance to the nearest point of the line — how far off
    /// route we are.
    let offRoute: Double
    /// Distance still to drive, measured *along* the line from that nearest
    /// point to the end. This is what the remaining-distance and ETA readouts
    /// are built from, so it has to follow the road rather than fly straight to
    /// the destination.
    let remaining: Double
    /// How far along the line the driver has been matched to. Feed this back in
    /// as `notBefore` on the next fix to keep the match moving forwards.
    let travelled: Double
}

/// Project a point onto a polyline: how far off it is, and how much line is
/// left ahead of it.
///
/// We check every *segment* (not just the shape points), so a long straight
/// stretch with far-apart vertices doesn't make a driver on the line look "off
/// route". Each segment is measured in its own local east/north meter frame —
/// per-segment rather than one frame for the whole route, because a 60 km route
/// spans enough latitude that a single east-west scale factor would misjudge
/// lengths at the far end by a couple of percent.
///
/// `notBefore` is how far along the driver is already known to be. Segments that
/// end before it are not considered, which is what makes the match work on a
/// route that comes back on itself — and scenic routes do that constantly, since
/// an out-and-back detour is often the prettiest way to spend ten extra minutes.
/// Without it, a driver on the return leg matches the outbound one they drove
/// twenty minutes ago, and the distance remaining jumps back up. Pass a little
/// less than the last known value so GPS jitter can nudge backwards; pass 0
/// before the driver has joined the route, when nothing is known yet.
func progress(of point: CLLocationCoordinate2D,
              along line: [CLLocationCoordinate2D],
              notBefore: Double = 0) -> RouteProgress {
    guard line.count >= 2 else {
        let here = CLLocation(latitude: point.latitude, longitude: point.longitude)
        let only = line.first.map { here.distance(to: $0) } ?? .infinity
        return RouteProgress(offRoute: only, remaining: 0, travelled: 0)
    }

    let metersPerDegLat = 111_320.0
    var best = Double.infinity
    var travelled = 0.0        // length of the line before the current segment
    var bestPrefix = 0.0       // ...at the closest segment
    var bestAlong = 0.0        // how far into the closest segment we project

    for i in 0 ..< line.count - 1 {
        let p = line[i], q = line[i + 1]
        // East-west scale at this segment's own latitude.
        let metersPerDegLon = 111_320.0 * cos((p.latitude + q.latitude) / 2 * .pi / 180)
        func offset(_ c: CLLocationCoordinate2D) -> (x: Double, y: Double) {
            ((c.longitude - point.longitude) * metersPerDegLon,
             (c.latitude - point.latitude) * metersPerDegLat)
        }

        let a = offset(p), b = offset(q)
        let dx = b.x - a.x, dy = b.y - a.y
        let lengthSquared = dx * dx + dy * dy
        let length = lengthSquared.squareRoot()

        // Project the origin (our point) onto segment AB, clamped to its ends.
        let t = lengthSquared == 0 ? 0 : max(0, min(1, -(a.x * dx + a.y * dy) / lengthSquared))
        let cx = a.x + t * dx, cy = a.y + t * dy
        let distance = (cx * cx + cy * cy).squareRoot()

        // Segments wholly behind us belong to an earlier pass along the same
        // road, not to where the driver is now.
        if distance < best, travelled + length >= notBefore {
            best = distance
            bestPrefix = travelled
            bestAlong = t * length
        }
        travelled += length
    }

    // `notBefore` ran past the end of the line: there is nothing ahead to match
    // against, so the driver is at the end of it.
    guard best.isFinite else {
        let here = CLLocation(latitude: point.latitude, longitude: point.longitude)
        return RouteProgress(offRoute: line.last.map { here.distance(to: $0) } ?? .infinity,
                             remaining: 0, travelled: travelled)
    }
    let along = bestPrefix + bestAlong
    return RouteProgress(offRoute: best,
                         remaining: max(0, travelled - along),
                         travelled: along)
}
