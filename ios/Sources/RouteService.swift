import CoreLocation
import Foundation

/// The network layer: turns a (start, end, preference) into routes by calling
/// the backend. It's an `enum` with only static members because it holds no
/// state — it's just a namespace for the `route(...)` function.
enum RouteService {

    /// Where the backend lives, in order of precedence:
    ///
    ///   1. `SCENIC_API` in the environment — for developing against a local
    ///      server. Add it to the Run action in Xcode's scheme editor.
    ///   2. `ScenicAPIBaseURL` from Info.plist — the deployed backend, baked
    ///      into the bundle at build time (set in `ios/project.yml`).
    ///   3. localhost, as a last resort.
    ///
    /// The Info.plist entry is what makes the app usable away from a Mac. An
    /// environment variable only exists when *Xcode* launches the process, so
    /// tapping the icon — or iOS relaunching the app after jettisoning it
    /// mid-drive — starts it with no environment at all. With only the env var,
    /// that silently fell back to localhost, i.e. the phone itself, and every
    /// request failed at the worst possible moment.
    static let baseURL: String = {
        if let override = ProcessInfo.processInfo.environment["SCENIC_API"],
           !override.isEmpty {
            return override
        }
        if let baked = Bundle.main.object(forInfoDictionaryKey: "ScenicAPIBaseURL") as? String,
           !baked.isEmpty {
            return baked
        }
        return "http://127.0.0.1:5057"
    }()

    /// An error carrying the backend's own message (e.g. "no route found").
    enum ServiceError: LocalizedError {
        case server(String)
        case badResponse

        var errorDescription: String? {
            switch self {
            case let .server(message): return message
            // A decode failure otherwise surfaces as Foundation's "The data
            // couldn't be read because it isn't in the correct format", which
            // tells a driver nothing about what to do.
            case .badResponse: return "The routing server sent something unreadable."
            }
        }
    }

    /// Requests time out well inside a drive's patience.
    ///
    /// `URLSession.shared` waits 60 seconds, and mid-drive that is the worst of
    /// both worlds: `NavigationModel` holds `isRerouting` for the whole minute,
    /// which both pins "Rerouting…" on the banner and blocks every retry — the
    /// 8-second reroute cooldown cannot fire while a request is still in flight.
    /// Failing fast and retrying is what recovers a drive on a patchy signal.
    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 20
        // Never park a request waiting for the network to come back: the driver
        // has moved on and the answer would be for where they used to be.
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }()

    /// Request the fastest and scenic routes between two points.
    /// - Parameters:
    ///   - pref: 0 = fastest, 1 = most scenic (overall scenery strength).
    ///   - weights: per-beauty-type weights keyed by `BeautyType.apiName`
    ///     (1.0 = neutral). Sent as `w_<type>` params; omitted types default to
    ///     neutral on the server, so an empty dictionary is the plain behavior.
    ///   - heading: the driver's course over ground, for a reroute taken while
    ///     moving. It decides which end of the current road the route starts
    ///     from, so a replacement doesn't open by turning the car around. Leave
    ///     it nil when planning from a standstill — the server then falls back
    ///     to the nearer end, which is the right answer for a parked car.
    static func route(
        from start: CLLocationCoordinate2D,
        to end: CLLocationCoordinate2D,
        pref: Double,
        weights: [String: Double] = [:],
        heading: CLLocationDirection? = nil
    ) async throws -> RouteResponse {
        // Build the URL: /api/route?from=lat,lon&to=lat,lon&pref=0.50&w_coast=...
        var components = URLComponents(string: "\(baseURL)/api/route")!
        components.queryItems = [
            URLQueryItem(name: "from", value: "\(start.latitude),\(start.longitude)"),
            URLQueryItem(name: "to", value: "\(end.latitude),\(end.longitude)"),
            URLQueryItem(name: "pref", value: String(format: "%.2f", pref)),
        ] + weights.map { type, weight in
            URLQueryItem(name: "w_\(type)", value: String(format: "%.2f", weight))
        } + (heading.map {
            [URLQueryItem(name: "heading", value: String(format: "%.1f", $0))]
        } ?? [])

        let (data, response) = try await session.data(from: components.url!)

        // On an error status, surface the backend's JSON {"error": "..."} message.
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            let body = try? JSONDecoder().decode([String: String].self, from: data)
            // A rate limiter or a tunnel answers in HTML, not our JSON shape,
            // so there is often no message to lift.
            throw ServiceError.server(body?["error"] ?? "HTTP \(http.statusCode)")
        }

        do {
            return try JSONDecoder().decode(RouteResponse.self, from: data)
        } catch {
            throw ServiceError.badResponse
        }
    }
}
