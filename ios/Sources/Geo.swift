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

/// Smallest distance, in meters, from a point to a polyline.
///
/// We check every *segment* (not just the shape points), so a long straight
/// stretch with far-apart vertices doesn't make a driver on the line look "off
/// route". The math runs in a local east/north meter frame centered on the
/// point, which is plenty accurate over the short distances navigation cares
/// about.
func distanceToPolyline(_ point: CLLocationCoordinate2D, _ line: [CLLocationCoordinate2D]) -> Double {
    guard line.count >= 2 else {
        guard let only = line.first else { return .infinity }
        return CLLocation(latitude: point.latitude, longitude: point.longitude).distance(to: only)
    }
    let metersPerDegLat = 111_320.0
    let metersPerDegLon = 111_320.0 * cos(point.latitude * .pi / 180)
    func offset(_ c: CLLocationCoordinate2D) -> (x: Double, y: Double) {
        ((c.longitude - point.longitude) * metersPerDegLon,
         (c.latitude - point.latitude) * metersPerDegLat)
    }

    var best = Double.infinity
    for i in 0 ..< line.count - 1 {
        let a = offset(line[i])
        let b = offset(line[i + 1])
        let dx = b.x - a.x, dy = b.y - a.y
        let lengthSquared = dx * dx + dy * dy
        // Project the origin (our point) onto segment AB, clamped to its ends.
        let t = lengthSquared == 0 ? 0 : max(0, min(1, -(a.x * dx + a.y * dy) / lengthSquared))
        let cx = a.x + t * dx, cy = a.y + t * dy
        best = min(best, (cx * cx + cy * cy).squareRoot())
    }
    return best
}
