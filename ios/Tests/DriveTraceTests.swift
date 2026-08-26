import CoreLocation
import XCTest
@testable import Scenic

/// The recorder that turns a test drive into data. Worth testing precisely
/// because its failures are silent and expensive: you find out the file was
/// truncated, or the reroute went unrecorded, after the drive — and the only way
/// to get the measurement back is to drive it again.
@MainActor
final class DriveTraceTests: XCTestCase {

    private var folder: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("trace-tests-\(UUID().uuidString)")
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
        try super.tearDownWithError()
    }

    private func trace(pref: Double = 0.7) -> DriveTrace {
        let trace = DriveTrace(origin: Fixture.origin, destination: Fixture.north(5000), pref: pref,
                               weights: ["water": 1.5], directory: folder)
        return XCTUnwrap(trace, "the trace should open in a writable directory")
    }

    /// `XCTUnwrap` is throwing; these helpers are called from non-throwing
    /// contexts, so fail loudly instead.
    private func XCTUnwrap<T>(_ value: T?, _ message: String) -> T {
        guard let value else {
            XCTFail(message)
            fatalError(message)
        }
        return value
    }

    /// Every record written, in order, decoded from the file.
    private func records(of trace: DriveTrace) -> [[String: Any]] {
        guard let text = try? String(contentsOf: trace.url, encoding: .utf8) else {
            XCTFail("no trace file at \(trace.url.path)")
            return []
        }
        return text.split(separator: "\n").compactMap { line in
            try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any]
        }
    }

    private func kinds(of trace: DriveTrace) -> [String] {
        records(of: trace).map { $0["t"] as? String ?? "?" }
    }

    private func progress(travelled: Double, remaining: Double = 0,
                          offRoute: Double = 3) -> RouteProgress {
        RouteProgress(offRoute: offRoute, remaining: remaining, travelled: travelled)
    }

    // MARK: - The file

    func test_a_drive_writes_a_header_then_its_route_then_fixes_then_an_end() {
        let trace = self.trace()
        trace.route(Fixture.straightRoute(), reason: "start")
        trace.fix(Fixture.fixAt(100), progress: progress(travelled: 100), joined: true, step: 0)
        trace.end(reason: "arrived")

        XCTAssertEqual(kinds(of: trace), ["drive", "route", "fix", "end"])

        let header = records(of: trace)[0]
        XCTAssertEqual(header["pref"] as? Double, 0.7)
        XCTAssertEqual((header["weights"] as? [String: Double])?["water"], 1.5)
        // The destination is what makes a trace identifiable months later.
        XCTAssertEqual((header["dest"] as? [Double])?.first, Fixture.north(5000).latitude)
        XCTAssertNotNil(header["started"] as? String)
    }

    func test_the_route_line_is_written_in_full() {
        // Without the geometry, `travelled` is a distance along nothing: there
        // is no way to recover which road any fix was on, and the trace is
        // unreadable no matter how many fixes it holds.
        let trace = self.trace()
        let route = Fixture.straightRoute()
        trace.route(route, reason: "start")
        trace.end(reason: "ended")

        let written = records(of: trace)[1]
        XCTAssertEqual((written["coords"] as? [[Double]])?.count,
                       route.geometry.coordinates.count)
        XCTAssertEqual(written["km"] as? Double, route.properties.km)
        XCTAssertEqual(written["minutes"] as? Double, route.properties.minutes)
        XCTAssertEqual((written["steps"] as? [[String: Any]])?.count,
                       route.properties.steps.count)
    }

    func test_a_fix_carries_both_the_raw_position_and_its_match_on_the_route() {
        let trace = self.trace()
        trace.route(Fixture.straightRoute(), reason: "start")
        trace.fix(Fixture.fixAt(1234), progress: progress(travelled: 1234, remaining: 3766),
                  joined: true, step: 2)
        trace.end(reason: "ended")

        let fix = records(of: trace)[2]
        XCTAssertEqual(fix["lat"] as? Double, Fixture.north(1234).latitude)
        XCTAssertEqual(fix["travelled"] as? Double, 1234)
        XCTAssertEqual(fix["remaining"] as? Double, 3766)
        XCTAssertEqual(fix["step"] as? Int, 2)
        XCTAssertEqual(fix["joined"] as? Bool, true)
        // The timestamp is the fix's own, not the moment it was written — the
        // whole measurement is a slope against this number.
        XCTAssertNotNil(fix["ts"] as? Double)
    }

    func test_the_recorded_fields_are_the_ones_the_analysis_reads() {
        // Keep in step with REQUIRED_FIELDS in tools/analyze_trace.py, which
        // asserts the same names from its own side. A trace is written on a
        // phone and read on a laptop, days apart, so a field renamed on one end
        // would otherwise surface as a drive that reads as empty — after the
        // driving is done and with no way to get it back.
        let required = [
            "drive": ["t", "ts", "pref"],
            "route": ["t", "ts", "seq", "reason", "km", "minutes", "coords", "steps"],
            "fix": ["t", "ts", "lat", "lon", "acc", "alt", "off", "travelled",
                    "joined", "route"],
            "mark": ["t", "ts", "verdict", "route", "travelled", "joined", "spd"],
            "phase": ["t", "ts", "phase"],
            "end": ["t", "ts", "reason"],
        ]

        let trace = self.trace()
        trace.route(Fixture.straightRoute(), reason: "start")
        trace.fix(Fixture.fixAt(10), progress: progress(travelled: 10), joined: true, step: 0)
        trace.mark(SceneryVerdict.nice.rawValue, progress: progress(travelled: 10),
                   location: Fixture.fixAt(10), joined: true, step: 0)
        trace.phase("background")
        trace.end(reason: "arrived")

        let written = records(of: trace)
        XCTAssertEqual(Set(written.compactMap { $0["t"] as? String }),
                       Set(required.keys), "every record type should appear")
        for record in written {
            let kind = record["t"] as? String ?? "?"
            for field in required[kind] ?? [] {
                XCTAssertNotNil(record[field],
                                "a \(kind) record must carry '\(field)' — "
                                + "tools/analyze_trace.py reads it")
            }
        }
    }

    func test_the_header_carries_both_ends_so_the_route_can_be_replayed() {
        // Origin, destination, preference and weights together are the exact
        // request that produced this drive. With them, a trace can be re-asked
        // of a rebuilt graph months later; without the origin it can only ever
        // be read the way it was first thought to be read.
        let trace = self.trace()
        trace.end(reason: "ended")

        let header = records(of: trace)[0]
        XCTAssertEqual((header["from"] as? [Double])?.first, Fixture.origin.latitude)
        XCTAssertEqual((header["dest"] as? [Double])?.first, Fixture.north(5000).latitude)
    }

    func test_a_fix_carries_altitude_and_how_much_to_trust_it() {
        // Grade is a real reason a car is slower than the limit and it is
        // concentrated on the hilly roads scenic routes pick. Nothing recovers
        // it later, so the drive that could measure it must not be the one that
        // forgot to.
        let trace = self.trace()
        let location = CLLocation(
            coordinate: Fixture.north(100), altitude: 231.5,
            horizontalAccuracy: 5, verticalAccuracy: 8,
            course: 0, speed: 17, timestamp: Date())
        trace.fix(location, progress: progress(travelled: 100), joined: true, step: 0)
        trace.end(reason: "ended")

        let fix = records(of: trace)[1]
        XCTAssertEqual(fix["alt"] as? Double, 231.5)
        XCTAssertEqual(fix["valt"] as? Double, 8)
        XCTAssertEqual(fix["spd"] as? Double, 17)
    }

    // MARK: - What the driver thought of the road

    func test_a_verdict_is_recorded_against_where_on_the_route_it_was_given() {
        // `travelled` is the anchor, not the coordinate: metres along the route
        // line, so the laptop can resolve which road this was without trusting
        // the phone's position or the graph being byte-identical to the one that
        // planned the drive.
        let trace = self.trace()
        trace.route(Fixture.straightRoute(), reason: "start")
        trace.mark(SceneryVerdict.nice.rawValue, progress: progress(travelled: 2431),
                   location: Fixture.fixAt(2431), joined: true, step: 4)
        trace.end(reason: "ended")

        let mark = records(of: trace).first { $0["t"] as? String == "mark" }
        XCTAssertEqual(mark?["verdict"] as? String, "nice")
        XCTAssertEqual(mark?["travelled"] as? Double, 2431)
        XCTAssertEqual(mark?["route"] as? Int, 0)
        XCTAssertEqual(mark?["step"] as? Int, 4)
    }

    func test_a_verdict_says_how_stale_its_position_was() {
        // The position is the last GPS fix, not the instant of the tap, and at
        // 60 mph a one-second-old fix already trails the car by 27 m. The
        // analysis corrects for reaction time; it can only do that honestly if
        // the staleness it is correcting on top of is recorded rather than
        // assumed.
        let trace = self.trace()
        trace.route(Fixture.straightRoute(), reason: "start")
        let tapped = Date().timeIntervalSince1970
        let location = CLLocation(
            coordinate: Fixture.north(500), altitude: 30,
            horizontalAccuracy: 5, verticalAccuracy: 8, course: 0, speed: 24,
            timestamp: Date(timeIntervalSince1970: tapped - 0.8))
        trace.mark(SceneryVerdict.dull.rawValue, progress: progress(travelled: 500),
                   location: location, joined: true, step: 0, clock: tapped)
        trace.end(reason: "ended")

        let mark = records(of: trace).first { $0["t"] as? String == "mark" }
        XCTAssertEqual(mark?["fix_age"] as? Double ?? 0, 0.8, accuracy: 0.01)
        // The speed is what turns a reaction *time* into a distance, so it has
        // to survive the trip to disk.
        XCTAssertEqual(mark?["spd"] as? Double, 24)
    }

    func test_a_verdict_tapped_before_joining_the_route_says_so() {
        // Its `travelled` is a match onto wherever the line happens to pass
        // nearest, which is not where the driver is. Recorded anyway and
        // flagged: what to do with an unanchored verdict is the analysis's
        // decision, not a phone's.
        let trace = self.trace()
        trace.route(Fixture.straightRoute(), reason: "start")
        trace.mark(SceneryVerdict.nice.rawValue, progress: progress(travelled: 40),
                   location: Fixture.fixAt(40), joined: false, step: 0)
        trace.end(reason: "ended")

        let mark = records(of: trace).first { $0["t"] as? String == "mark" }
        XCTAssertEqual(mark?["joined"] as? Bool, false)
    }

    func test_a_verdict_does_not_wait_for_the_drive_to_end_to_be_written() {
        // Marks are flushed rather than buffered. A fix can afford to sit in
        // memory for twenty seconds — there are thousands of them and they
        // interpolate — but there are a handful of marks in a whole drive and
        // each one is a thing the driver deliberately said, so none of them
        // should be waiting on an `end` that a dead battery may never write.
        //
        // "Flushed" means handed to the writer, not synchronously on disk: the
        // I/O runs on a background queue so the drive never waits for it (only
        // `end` blocks, once, deliberately). So this waits for the queue rather
        // than asserting an ordering the code does not promise.
        let trace = self.trace()
        trace.route(Fixture.straightRoute(), reason: "start")
        trace.mark(SceneryVerdict.nice.rawValue, progress: progress(travelled: 100),
                   location: Fixture.fixAt(100), joined: true, step: 0)
        // No `end` — the battery died here.

        XCTAssertTrue(eventually { kinds(of: trace).contains("mark") },
                      "a verdict should reach disk without the drive ending")
    }

    /// Spin the run loop until `condition` holds, up to `timeout`.
    ///
    /// For the writer's queue, which is deliberately asynchronous. A fixed sleep
    /// would either be flaky on a loaded machine or slow on every run.
    private func eventually(timeout: TimeInterval = 2.0,
                           _ condition: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return true }
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.02))
        }
        return condition()
    }

    func test_the_verdict_vocabulary_is_the_one_the_analysis_knows() {
        // The other half is VERDICTS in tools/analyze_trace.py. A verdict string
        // renamed on this side would be dropped as unknown on that one — counted,
        // at least, but the drive would still be wasted.
        XCTAssertEqual(SceneryVerdict.allCases.map(\.rawValue), ["nice", "dull"])
    }

    func test_a_drive_with_no_recorder_does_not_pretend_to_take_verdicts() {
        // A button that records nothing is worse than no button: the driver
        // stops watching for the scenery they think they are logging.
        let unrecorded = NavigationModel(route: Fixture.straightRoute(),
                                         destination: Fixture.north(5000),
                                         pref: 0.7, weights: [:])
        XCTAssertFalse(unrecorded.canRecordMarks)

        let recording = NavigationModel(route: Fixture.straightRoute(),
                                        destination: Fixture.north(5000),
                                        pref: 0.7, weights: [:], trace: self.trace())
        XCTAssertTrue(recording.canRecordMarks)
    }

    func test_a_verdict_carries_the_position_of_the_last_fix_the_drive_saw() {
        // NavigationModel is what holds the two together: the tap arrives
        // between fixes, so the mark has to reach back for the most recent one
        // rather than have nothing to say about where it happened.
        let trace = self.trace()
        let nav = NavigationModel(route: Fixture.straightRoute(),
                                  destination: Fixture.north(5000),
                                  pref: 0.7, weights: [:], trace: trace)
        nav.update(Fixture.fixAt(1200))
        nav.mark(.nice)
        trace.end(reason: "ended")

        let mark = records(of: trace).first { $0["t"] as? String == "mark" }
        XCTAssertNotNil(mark?["lat"], "the mark should carry the last fix's position")
        XCTAssertEqual(mark?["travelled"] as? Double ?? -1, 1200, accuracy: 30)
        XCTAssertEqual(nav.marksRecorded, 1)
    }

    // MARK: - Knowing whether it worked

    func test_the_app_leaving_and_returning_is_recorded() {
        // A hole in the fixes is a tunnel, a suspended app, or a bug, and the
        // three want completely different responses. Only the app can say which.
        let trace = self.trace()
        trace.phase("background")
        trace.phase("active")
        trace.end(reason: "ended")

        let phases = records(of: trace)
            .filter { $0["t"] as? String == "phase" }
            .compactMap { $0["phase"] as? String }
        XCTAssertEqual(phases, ["background", "active"])
    }

    func test_a_drive_says_so_when_it_is_not_being_recorded() {
        // The screen must never look normal while recording nothing: the drive
        // is the expensive part, and you only find out at home.
        let recording = NavigationModel(route: Fixture.straightRoute(),
                                        destination: Fixture.north(5000),
                                        pref: 0.8, weights: [:], trace: trace())
        XCTAssertNil(recording.recordingProblem)

        let silent = NavigationModel(route: Fixture.straightRoute(),
                                     destination: Fixture.north(5000),
                                     pref: 0.8, weights: [:], trace: nil)
        XCTAssertNotNil(silent.recordingProblem)
    }

    // MARK: - Not losing anything

    func test_buffered_fixes_are_all_on_disk_once_the_drive_ends() {
        // Fixes are buffered, so the tail of every drive exists only in memory
        // until something flushes it. That tail contains the arrival.
        let trace = self.trace()
        trace.route(Fixture.straightRoute(), reason: "start")
        for metres in stride(from: 0.0, to: 250.0, by: 5) {
            trace.fix(Fixture.fixAt(metres), progress: progress(travelled: metres),
                      joined: true, step: 0)
        }
        trace.end(reason: "arrived")

        XCTAssertEqual(kinds(of: trace).filter { $0 == "fix" }.count, 50)
        XCTAssertEqual(kinds(of: trace).last, "end")
    }

    func test_records_land_in_the_order_they_happened() {
        // A `fix` only means something against the `route` record before it, so
        // the file format depends on order — and the writes happen off the main
        // actor, on a queue that has to be serial for that to hold.
        let trace = self.trace()
        for i in 0..<60 {
            trace.fix(Fixture.fixAt(Double(i) * 10),
                      progress: progress(travelled: Double(i) * 10), joined: true, step: 0)
        }
        trace.end(reason: "ended")

        let travelled = records(of: trace)
            .filter { $0["t"] as? String == "fix" }
            .compactMap { $0["travelled"] as? Double }
        XCTAssertEqual(travelled, (0..<60).map { Double($0) * 10 })
    }

    func test_ending_twice_writes_one_ending() {
        // Arrival ends the trace, and so does leaving the nav screen — which a
        // driver does *after* arriving, every time.
        let trace = self.trace()
        trace.end(reason: "arrived")
        trace.end(reason: "ended")

        let endings = records(of: trace).filter { $0["t"] as? String == "end" }
        XCTAssertEqual(endings.count, 1)
        XCTAssertEqual(endings.first?["reason"] as? String, "arrived")
    }

    func test_two_drives_in_the_same_second_do_not_share_a_file() {
        // The filename is second-resolution and the writer appends, so a
        // collision would interleave two drives into one impossible one.
        let clock = Date().timeIntervalSince1970
        let first = DriveTrace(origin: nil, destination: Fixture.north(1), pref: 0, weights: [:],
                               directory: folder, clock: clock)
        let second = DriveTrace(origin: nil, destination: Fixture.north(1), pref: 0, weights: [:],
                                directory: folder, clock: clock)
        first?.end(reason: "ended")
        second?.end(reason: "ended")

        XCTAssertNotEqual(first?.url, second?.url)
        XCTAssertEqual(records(of: XCTUnwrap(first, "first trace")).count, 2)
        XCTAssertEqual(records(of: XCTUnwrap(second, "second trace")).count, 2)
    }

    // MARK: - Failing quietly

    func test_a_trace_that_cannot_be_opened_refuses_to_start() {
        // Nowhere to write is the one failure worth refusing on: every later
        // call would be a no-op pretending to record, and the drive would be
        // spent before anyone noticed.
        let blocked = folder.appendingPathComponent("not-a-directory")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: blocked.path, contents: Data("x".utf8))

        let trace = DriveTrace(origin: nil, destination: Fixture.north(1), pref: 0, weights: [:],
                               directory: blocked.appendingPathComponent("traces"))
        XCTAssertNil(trace)
    }

    func test_a_drive_with_no_recorder_runs_exactly_as_before() {
        // Recording is injected and optional, so nothing about it is on the path
        // a drive takes. This is the guard on that.
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:], trace: nil)
        model.update(Fixture.fixAt(1500))
        XCTAssertTrue(model.hasJoinedRoute)
        XCTAssertEqual(model.currentInstruction, "Turn left onto Oak Street")
    }

    // MARK: - Driven by the navigation model

    func test_navigating_records_the_route_the_fixes_and_the_arrival() {
        let trace = self.trace()
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:], trace: trace)
        for metres in stride(from: 0.0, through: 5000.0, by: 500) {
            model.update(Fixture.fixAt(metres))
        }
        XCTAssertTrue(model.arrived)
        model.finish()   // as leaving the nav screen would

        let written = records(of: trace)
        XCTAssertEqual(written.first?["t"] as? String, "drive")
        XCTAssertEqual(written[1]["t"] as? String, "route")
        XCTAssertEqual(written[1]["reason"] as? String, "start")

        // Distance along the route climbs with the drive, which is the entire
        // measurement — its slope is the real speed.
        let travelled = written
            .filter { $0["t"] as? String == "fix" }
            .compactMap { $0["travelled"] as? Double }
        XCTAssertEqual(travelled.count, 11)
        XCTAssertEqual(travelled, travelled.sorted())
        XCTAssertEqual(travelled.last ?? 0, 5000, accuracy: 5)

        // Arrival closes the trace, and leaving the screen afterwards doesn't
        // reopen or double it.
        XCTAssertEqual(written.last?["t"] as? String, "end")
        XCTAssertEqual(written.last?["reason"] as? String, "arrived")
        XCTAssertEqual(written.filter { $0["t"] as? String == "end" }.count, 1)
    }

    func test_a_reroute_is_recorded_so_travelled_can_be_read_against_the_right_line() async {
        // `travelled` restarts at zero on a new line. A trace that didn't know
        // the line had been replaced would read that reset as the car
        // teleporting 3 km backwards — and would price the whole drive wrong.
        let trace = self.trace()
        let replacement = Fixture.straightRoute(
            start: 150,
            steps: [(0, "Head north on Detour Road"), (5000, "Arrive at your destination")])
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:], trace: trace)
        model.fetchRoute = { _, _, _, _, _ in
            Fixture.response(fastest: replacement, scenic: replacement)
        }

        model.update(Fixture.fixAt(500))                       // on the line
        model.update(Fixture.fix(CLLocationCoordinate2D(       // 300 m east of it
            latitude: Fixture.north(800).latitude, longitude: -71.0 + 300 / 82_600)))

        let deadline = Date().addingTimeInterval(2)
        while model.isRerouting || Date() < deadline {
            if !model.isRerouting, records(of: trace).count > 3 { break }
            try? await Task.sleep(for: .milliseconds(5))
        }
        model.update(Fixture.fixAt(100))
        model.finish()

        let routes = records(of: trace).filter { $0["t"] as? String == "route" }
        XCTAssertEqual(routes.count, 2, "the replacement line should be recorded")
        XCTAssertEqual(routes.last?["reason"] as? String, "offroute")

        // Fixes name the line they were matched against, so the analysis can
        // split the drive at the seam instead of reading one line across both.
        let sequences = records(of: trace)
            .filter { $0["t"] as? String == "fix" }
            .compactMap { $0["route"] as? Int }
        XCTAssertEqual(sequences.first, 0)
        XCTAssertEqual(sequences.last, 1)
    }

    func test_switching_to_fastest_is_recorded_as_its_own_reason() async {
        // Half the drive priced as scenic and half as fastest is two different
        // measurements; the trace has to say where the switch happened.
        let trace = self.trace()
        let model = NavigationModel(route: Fixture.straightRoute(),
                                    destination: Fixture.north(5000),
                                    pref: 0.8, weights: [:], trace: trace)
        model.fetchRoute = { _, _, _, _, _ in
            // A distinct line: the fastest route is a different road from the
            // scenic one, and an identical one would be merged rather than
            // adopted, which is a different test.
            let feature = Fixture.straightRoute(start: 150)
            return Fixture.response(fastest: feature, scenic: feature)
        }
        model.update(Fixture.fixAt(500))
        await model.switchToFastest(from: Fixture.fixAt(500))
        model.finish()

        let routes = records(of: trace).filter { $0["t"] as? String == "route" }
        XCTAssertEqual(routes.map { $0["reason"] as? String }, ["start", "fastest"])
    }
}
