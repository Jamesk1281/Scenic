import CoreLocation
import Foundation
import Observation

/// Records one drive to disk, so a test drive produces measurements instead of
/// impressions.
///
/// The backend's `minutes` are free-flow — speed limit ÷ distance, with nothing
/// charged for lights, stop signs, turns or traffic (see `SPEED_KMH` in
/// `pipeline/graph.py`). Calibrating that needs ground truth: where the car
/// actually was, when, and how that compares to what the route predicted. The
/// app already computes exactly that and throws it away — every fix, `update`
/// projects the driver onto the route line and gets `travelled`, meters along
/// *this* route. `(timestamp, travelled)` is the measurement; its slope is the
/// real speed at a known place on a known road.
///
/// So this writes one NDJSON file per drive, of five record types:
///
///   - `drive` — one header: when, from where to where, at what preference.
///   - `route` — the line being followed, written again on every reroute
///     (`travelled` restarts from zero, so the analysis has to know).
///   - `fix`   — one per GPS update: the raw fix and its match onto the route.
///   - `phase` — the app going to the background and coming back, so a hole in
///     the fixes can be told apart from a GPS dropout.
///   - `end`   — arrived, or the driver stopped.
///
/// `tools/analyze_trace.py` reads these back. NDJSON rather than one JSON array
/// because a drive that ends in a crash, a jettison or a dead battery still
/// leaves every line before it readable.
///
/// Nothing here may interrupt a drive. Every filesystem operation is best
/// effort, and a failure sets `failure` rather than throwing. But it does not
/// fail *silently*: `failure` drives an indicator on the nav screen, because a
/// recorder that quietly stopped an hour ago costs a whole drive, and the drive
/// is the expensive part.
@Observable
@MainActor
final class DriveTrace {

    /// Where this drive is being written, for anyone who wants to show or share
    /// it. Traces live in Documents so `UIFileSharingEnabled` exposes them to
    /// Files.app and Finder — that's the export path, and the delete path too.
    let url: URL

    /// Why recording stopped, if it did. The drive is deliberately unaffected,
    /// so this is the only evidence.
    private(set) var failure: String?

    private let writer: LineWriter
    private var buffer: [String] = []
    private var routeSeq = -1
    private var finished = false

    /// Fixes buffered before hitting the disk. CoreLocation delivers about one
    /// per second (see `LocationManager`), so this is a write every ~20 s.
    /// Buffering matters because the records are handed over on the main actor's
    /// clock even though the I/O itself isn't; losing the last few seconds to a
    /// crash costs nothing, and the records that matter — route changes, the
    /// arrival — flush immediately regardless.
    private static let flushEvery = 20

    /// Timestamps are epoch seconds — a difference, which is all the analysis
    /// wants, is then a subtraction with no calendar in the way.
    ///
    /// `nonisolated` because it is also the default for `init`'s `clock`, and a
    /// default argument is evaluated at the call site, outside the main actor.
    nonisolated private static func now() -> TimeInterval { Date().timeIntervalSince1970 }

    /// Open a trace for a drive from `origin` to `destination`.
    ///
    /// Returns nil if the traces directory can't be made, which is the one
    /// failure worth refusing to start on: there is nowhere to write, and every
    /// later call would be a no-op pretending to record.
    ///
    /// `origin` is here so the trace is self-describing. With the origin, the
    /// destination, the preference and the weights, the exact request that
    /// produced this drive can be replayed against a rebuilt graph months later
    /// — which is the difference between a trace you can re-examine and one you
    /// can only read the way you first thought to read it.
    init?(origin: CLLocationCoordinate2D?, destination: CLLocationCoordinate2D,
          pref: Double, weights: [String: Double],
          directory: URL? = nil, clock: TimeInterval = DriveTrace.now()) {
        guard let folder = directory ?? Self.defaultDirectory() else { return nil }
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        } catch {
            return nil
        }
        url = Self.freeName(in: folder, base: Self.name(at: clock))
        writer = LineWriter(url: url)

        var header: [String: Any] = [
            "t": "drive",
            "ts": clock,
            // Round-trips to a real date without needing the filename parsed.
            "started": ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: clock)),
            "app": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?",
            "api": RouteService.baseURL,
            "dest": [destination.latitude, destination.longitude],
            "pref": pref,
            "weights": weights,
        ]
        if let origin {
            header["from"] = [origin.latitude, origin.longitude]
        }
        append(header, flush: true)
    }

    /// `Documents/traces`, or nil if there is no Documents directory to speak of.
    private static func defaultDirectory() -> URL? {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)
            .first?.appendingPathComponent("traces", isDirectory: true)
    }

    /// A filename that sorts chronologically and survives every filesystem:
    /// `drive-2026-08-13-142205`. Colons are legal in APFS but are displayed as
    /// `/` in Finder and break on export, so the ISO time is hyphenated.
    private static func name(at time: TimeInterval) -> String {
        let formatter = DateFormatter()
        // A fixed format needs a fixed locale, or the device's calendar supplies
        // the year: a phone set to Thai gives 2569, and the traces sort into a
        // different decade from the rest.
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd-HHmmss"
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        return "drive-" + formatter.string(from: Date(timeIntervalSince1970: time))
    }

    /// Claim a path in `folder` that nothing else is using.
    ///
    /// The name is only second-resolution and the writer *appends*, so starting
    /// a drive, changing your mind, and starting again within the same second
    /// would otherwise interleave two drives into one file that reads as a
    /// single impossible drive.
    ///
    /// The empty file is created here, now, rather than left to the first write.
    /// Writes happen on a background queue, so "does this name exist yet?" is
    /// answered before the previous trace has written anything — both drives
    /// look at an empty directory and pick the same name. `withoutOverwriting`
    /// makes claiming the name and testing for it the same operation.
    private static func freeName(in folder: URL, base: String) -> URL {
        for suffix in 1..<100 {
            let name = suffix == 1 ? "\(base).ndjson" : "\(base)-\(suffix).ndjson"
            let candidate = folder.appendingPathComponent(name)
            if (try? Data().write(to: candidate, options: .withoutOverwriting)) != nil {
                return candidate
            }
        }
        return folder.appendingPathComponent("\(base)-\(UUID().uuidString).ndjson")
    }

    // MARK: - Recording

    /// A route was adopted: the first one, or a replacement after a reroute.
    ///
    /// Written in full, geometry included. Without the line, `travelled` is a
    /// number of meters along nothing — there is no way to recover which road a
    /// fix was on, and the whole trace is unreadable. A 70 km route is a few
    /// hundred KB of coordinates, once per reroute.
    func route(_ feature: RouteFeature, reason: String) {
        routeSeq += 1
        append([
            "t": "route",
            "ts": Self.now(),
            "seq": routeSeq,
            "reason": reason,
            "km": feature.properties.km,
            "minutes": feature.properties.minutes,
            "mean_score": feature.properties.mean_score,
            "coords": feature.geometry.coordinates,
            "steps": feature.properties.steps.map {
                ["instruction": $0.instruction, "lat": $0.lat, "lon": $0.lon,
                 "distance_m": $0.distance_m] as [String: Any]
            },
        ], flush: true)
    }

    /// One GPS update and where it landed on the route.
    ///
    /// Both halves are needed. The raw fix is the only unprocessed evidence — if
    /// the map-matching turns out to be wrong, it is what lets the trace be
    /// re-analysed from scratch. The match is what makes the fix *mean*
    /// something without re-deriving it: `travelled` is distance along a known
    /// line, so consecutive fixes give speed along a known road.
    ///
    /// `speed` and `course` come straight off CoreLocation and are negative when
    /// it has no opinion; they're logged as-is rather than cleaned, because the
    /// analysis prefers the derivative of `travelled` anyway and a cleaned value
    /// would hide that CoreLocation was guessing.
    ///
    /// For the same reason `travelled` is this fix's own match, not the model's
    /// monotonic running maximum, so it can dip a metre or two backwards on
    /// jitter. The monotonic version can be recovered from this one; the jitter
    /// it hides cannot be recovered from the monotonic version, and how far the
    /// match wobbles is itself worth being able to see.
    func fix(_ location: CLLocation, progress: RouteProgress,
             joined: Bool, step: Int) {
        append([
            "t": "fix",
            "ts": location.timestamp.timeIntervalSince1970,
            "lat": location.coordinate.latitude,
            "lon": location.coordinate.longitude,
            "acc": location.horizontalAccuracy,
            // Altitude, and how much to trust it. Grade is a real reason a car
            // is slower than the speed limit, and it is concentrated on exactly
            // the hilly back roads a scenic route picks — so the one drive that
            // could measure it should not be the drive that forgot to.
            "alt": location.altitude,
            "valt": location.verticalAccuracy,
            // CoreLocation's own speed, and its confidence in it. The analysis
            // prefers the slope of `travelled`, but a Doppler speed that
            // disagrees is how you find out the map-matching went wrong.
            "spd": location.speed,
            "spda": location.speedAccuracy,
            "crs": location.course,
            "route": routeSeq,
            "travelled": progress.travelled,
            "remaining": progress.remaining,
            "off": progress.offRoute,
            "joined": joined,
            "step": step,
        ])
    }

    /// The app went to the background, or came back.
    ///
    /// Without this a hole in the fixes is ambiguous — a tunnel, a suspended
    /// app, or a bug — and the three want completely different responses. It
    /// also flushes, so backgrounding costs no buffered fixes if iOS decides not
    /// to wake us again.
    /// Ignored once the drive has ended. `end` is the file's terminator, and
    /// NavView stays on screen after arrival — so pocketing the phone there
    /// fired a scene-phase change that appended *past* the ending, leaving a
    /// trace with no terminator and an away-marker `analyze_trace.py` counts.
    func phase(_ name: String) {
        guard !finished else { return }
        append(["t": "phase", "ts": Self.now(), "phase": name], flush: true)
    }

    /// Close the trace. Safe to call twice — `RouteModel` ends the session and
    /// arrival can both reach here, and a drive has exactly one ending.
    func end(reason: String) {
        guard !finished else { return }
        finished = true
        append(["t": "end", "ts": Self.now(), "reason": reason], flush: true)
        writer.close()
    }

    // MARK: - Writing

    private func append(_ record: [String: Any], flush: Bool = false) {
        guard failure == nil else { return }
        guard let data = try? JSONSerialization.data(withJSONObject: record),
              let line = String(data: data, encoding: .utf8) else {
            // A record we can't encode is a bug in this file, not a disk
            // problem, and it would repeat on every fix for the rest of the
            // drive. Stop rather than fill the file with the same complaint.
            failure = "could not encode a \(record["t"] ?? "?") record"
            return
        }
        buffer.append(line)
        if flush || buffer.count >= Self.flushEvery { self.flush() }
    }

    private func flush() {
        guard !buffer.isEmpty else { return }
        let lines = buffer
        buffer.removeAll(keepingCapacity: true)
        writer.append(lines) { [weak self] message in
            Task { @MainActor in self?.failure = message }
        }
    }
}

/// Appends lines to a file on a serial background queue.
///
/// Split out so the drive never waits on the disk: `DriveTrace` runs on the main
/// actor, alongside the map and the maneuver banner. The queue is serial, so
/// lines land in the order they were recorded — which the file format depends
/// on, since a `fix` is only interpretable against the `route` record before it.
///
/// `handle` is touched only from inside `queue`, which is what makes this safe
/// to call from the main actor.
private final class LineWriter: @unchecked Sendable {
    private let queue = DispatchQueue(label: "app.scenic.drive-trace", qos: .utility)
    private let url: URL
    private var handle: FileHandle?
    private var broken = false
    /// Set by `close()`. Without it, `append`'s nil-handle branch reads a
    /// deliberately closed writer as "not opened yet" and transparently
    /// reopens the file — so anything recorded after the ending both wrote
    /// past it and leaked the descriptor, since `close()` never runs twice.
    private var closed = false

    init(url: URL) {
        self.url = url
    }

    /// Write these lines, reporting the first failure and then going quiet.
    func append(_ lines: [String], onFailure: @escaping @Sendable (String) -> Void) {
        queue.async {
            guard !self.broken, !self.closed else { return }
            do {
                if self.handle == nil {
                    // `FileHandle(forWritingTo:)` needs the file to exist first.
                    if !FileManager.default.fileExists(atPath: self.url.path) {
                        FileManager.default.createFile(atPath: self.url.path, contents: nil)
                    }
                    self.handle = try FileHandle(forWritingTo: self.url)
                    try self.handle?.seekToEnd()
                }
                let blob = Data((lines.joined(separator: "\n") + "\n").utf8)
                try self.handle?.write(contentsOf: blob)
            } catch {
                // Out of space, or the container went away. Either way there is
                // no recovering mid-drive, and retrying on every fix would only
                // burn battery.
                self.broken = true
                try? self.handle?.close()
                self.handle = nil
                onFailure(error.localizedDescription)
            }
        }
    }

    /// Finish writing, and don't come back until it's done.
    ///
    /// Synchronous on purpose. This runs once, when the drive is over, and what
    /// follows it is the app going quiet — backgrounded at a trailhead, or shut
    /// by a driver who is done. Returning before the queue had drained would
    /// leave the last fixes, including the arrival, in a buffer that nothing
    /// will ever come back to write. There's no deadlock to fear: the queue
    /// never waits on the caller (failures are reported with an async hop).
    func close() {
        queue.sync {
            self.closed = true
            try? self.handle?.close()
            self.handle = nil
        }
    }
}
