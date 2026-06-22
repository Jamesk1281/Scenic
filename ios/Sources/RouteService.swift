import CoreLocation
import Foundation

/// The network layer: turns a (start, end, preference) into routes by calling
/// the backend. It's an `enum` with only static members because it holds no
/// state — it's just a namespace for the `route(...)` function.
enum RouteService {

    /// Where the backend lives. Defaults to localhost for the simulator (which
    /// can reach the Flask server running on the Mac). Override with the
    /// SCENIC_API env var to point at a hosted server on a real device.
    static let baseURL: String = ProcessInfo.processInfo
        .environment["SCENIC_API"] ?? "http://127.0.0.1:5057"

    /// An error carrying the backend's own message (e.g. "no route found").
    enum ServiceError: LocalizedError {
        case server(String)

        var errorDescription: String? {
            switch self {
            case let .server(message): return message
            }
        }
    }

    /// Request the fastest and scenic routes between two points.
    /// - Parameters:
    ///   - pref: 0 = fastest, 1 = most scenic (overall scenery strength).
    ///   - weights: per-beauty-type weights keyed by `BeautyType.apiName`
    ///     (1.0 = neutral). Sent as `w_<type>` params; omitted types default to
    ///     neutral on the server, so an empty dictionary is the plain behavior.
    static func route(
        from start: CLLocationCoordinate2D,
        to end: CLLocationCoordinate2D,
        pref: Double,
        weights: [String: Double] = [:]
    ) async throws -> RouteResponse {
        // Build the URL: /api/route?from=lat,lon&to=lat,lon&pref=0.50&w_coast=...
        var components = URLComponents(string: "\(baseURL)/api/route")!
        components.queryItems = [
            URLQueryItem(name: "from", value: "\(start.latitude),\(start.longitude)"),
            URLQueryItem(name: "to", value: "\(end.latitude),\(end.longitude)"),
            URLQueryItem(name: "pref", value: String(format: "%.2f", pref)),
        ] + weights.map { type, weight in
            URLQueryItem(name: "w_\(type)", value: String(format: "%.2f", weight))
        }

        let (data, response) = try await URLSession.shared.data(from: components.url!)

        // On an error status, surface the backend's JSON {"error": "..."} message.
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            let body = try? JSONDecoder().decode([String: String].self, from: data)
            throw ServiceError.server(body?["error"] ?? "HTTP \(http.statusCode)")
        }

        return try JSONDecoder().decode(RouteResponse.self, from: data)
    }
}
