import CoreLocation
import Foundation

/// Talks to the Scenic routing API. In the simulator, 127.0.0.1 reaches the
/// Flask server running on the host machine (NSAllowsLocalNetworking permits it).
enum RouteService {
    /// Override via the SCENIC_API env var when running on a device/VPS.
    static let baseURL: String = ProcessInfo.processInfo
        .environment["SCENIC_API"] ?? "http://127.0.0.1:5057"

    enum ServiceError: LocalizedError {
        case server(String)
        var errorDescription: String? {
            if case let .server(m) = self { return m }
            return "Unknown error"
        }
    }

    static func route(
        from a: CLLocationCoordinate2D,
        to b: CLLocationCoordinate2D,
        pref: Double
    ) async throws -> RouteResponse {
        var c = URLComponents(string: "\(baseURL)/api/route")!
        c.queryItems = [
            .init(name: "from", value: "\(a.latitude),\(a.longitude)"),
            .init(name: "to", value: "\(b.latitude),\(b.longitude)"),
            .init(name: "pref", value: String(format: "%.2f", pref)),
        ]
        let (data, resp) = try await URLSession.shared.data(from: c.url!)
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            let msg = (try? JSONDecoder().decode([String: String].self, from: data))?["error"]
            throw ServiceError.server(msg ?? "HTTP \(http.statusCode)")
        }
        return try JSONDecoder().decode(RouteResponse.self, from: data)
    }
}
