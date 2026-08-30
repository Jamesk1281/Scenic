import CoreLocation
import XCTest
@testable import Scenic

/// The announcement latch against twelve real drives instead of four-maneuver
/// fixtures.
///
/// `VoiceGuideTests` proves each rule in isolation on synthetic input, which is
/// the right way to test a rule and cannot test a *drive*. This runs the whole
/// of `NavigationModel` over recorded GPS — polylines of 1,300 to 6,700 points,
/// fixes that wander, cars that sit at lights, and the reroutes that made the
/// latch a problem in the first place.
///
/// **Skipped when `traces/*.ndjson` is not there**, which will be most
/// environments: the traces are gitignored because they are a record of where
/// someone drove, so they cannot be committed as a fixture. See
/// `DriveReplay.directory`.
///
/// **Slow — around 70 s.** The cost is real map-matching against real
/// polylines, tens of thousands of times. `-skip-testing:ScenicTests/DriveReplayTests`
/// while iterating on something else.
///
/// **What this does not catch, checked rather than assumed.** Two mutations
/// were run against it and *both passed*, so neither is guarded here:
///
/// 1. Keying the latch on the step index rather than the maneuver's place.
/// 2. Clearing the latch on a `merge` as well as an `adopt` — the naive
///    implementation the whole design is arranged to avoid.
///
/// Mutation 2 produces **byte-identical output on all twelve drives**. The
/// reason is `awaitingJoin`: a same-line reply only ever follows the driver
/// leaving the route, the voice is gated off until they rejoin, and by then
/// they have passed the maneuver that would have been repeated. On real
/// driving the off-route gate gets there first, and the latch is defence
/// behind it.
///
/// So the merge/adopt distinction is guarded by
/// `VoiceGuideTests.test_the_same_line_handed_back_does_not_re_announce_anything`
/// and by `VoiceGuideIntegrationTests`' brief stray-and-return, both of which
/// do fail under mutation 2. What *this* file is for is everything those
/// cannot reach: real polylines, real GPS wander, 42 real reroutes, and the
/// two failure modes that only show up over a whole drive — a storm, and a
/// voice that quietly stops.
@MainActor
final class DriveReplayTests: XCTestCase {

    private func recordings() throws -> [DriveReplay.RecordedDrive] {
        let drives = DriveReplay.recordings()
        try XCTSkipIf(drives.isEmpty,
                      "no traces/*.ndjson found. They are gitignored, being a record of "
                      + "where someone drove — copy them off a phone with `xcrun devicectl "
                      + "device copy from`, or point SCENIC_TRACES at them.")
        return drives
    }

    func test_no_recorded_drive_hears_the_same_maneuver_twice() async throws {
        let drives = try recordings()
        var outcomes: [DriveReplay.Outcome] = []

        for drive in drives {
            let outcome = await DriveReplay.run(drive, speaker: VoiceGuideTests.FakeSpeaker())
            outcomes.append(outcome)

            // Nothing is announced twice without a genuinely different route
            // in between. Keyed on the maneuver's *place* as well as its words:
            // keyed on words alone this reported a repeat on the 2026-08-14
            // evening drive that was nothing of the kind, its route turning
            // left onto Washington Street at two junctions 11 km apart.
            XCTAssertEqual(outcome.repeatsWithoutARouteChange, [],
                           "\(drive.name) announced a maneuver twice with no route change")

            // A storm's other signature, and a cheaper one to read.
            for (a, b) in zip(outcome.said, outcome.said.dropFirst()) {
                XCTAssertNotEqual(a, b, "\(drive.name) said the same thing twice running")
            }

            // Chatter. At most a prepare and a final per maneuver, plus slack
            // for the maneuvers a reroute legitimately re-opens; three times
            // the maneuver count is loose enough never to be flaky and tight
            // enough that a latch which re-announced on every reply would blow
            // it on the drives that re-routed a dozen times.
            let budget = 3 * (outcome.maneuversApproached + outcome.repliesGiven)
            XCTAssertLessThanOrEqual(outcome.said.count, budget,
                                     "\(drive.name) talked too much: \(outcome.said.count) "
                                     + "utterances over \(outcome.maneuversApproached) maneuvers")

            // And the opposite failure: a latch that never releases is just as
            // wrong and passes every assertion above.
            //
            // Two of these recordings really do say nothing, and both are right
            // to — one never joined its route at all, the other spent 2,653 of
            // its 2,679 fixes off the line it was given. Silence has to be
            // explained by the step list not describing the road the car is on,
            // which is what `describableFixes` counts, and not by the latch.
            if outcome.describableFixes >= 200 && outcome.maneuversApproached >= 3 {
                XCTAssertFalse(outcome.said.isEmpty,
                               "\(drive.name) was on its route for \(outcome.describableFixes) "
                               + "fixes past \(outcome.maneuversApproached) maneuvers and said "
                               + "nothing")
            }
        }

        // Guard against the suite passing because it exercised nothing: a
        // replay that never re-routed would satisfy every assertion above.
        let replies = outcomes.reduce(0) { $0 + $1.repliesGiven }
        let sameLine = outcomes.reduce(0) { $0 + $1.sameLineReplies }
        XCTAssertGreaterThan(replies, 20, "the replay barely re-routed; it is not testing much")
        XCTAssertGreaterThanOrEqual(sameLine, 3,
                                    "no reply handed back the line already being followed, "
                                    + "which is the case the latch exists for")
    }

    /// The walk-up, and the skip that depends on it.
    ///
    /// Worth its own test because the failure is silent in the worst way: if
    /// the search stops working, `recordings()` returns empty, every replay
    /// assertion is skipped, and the suite goes green having tested nothing.
    func test_the_search_for_traces_finds_them_and_gives_up_cleanly() {
        // Somewhere with no `traces` anywhere above it.
        let nowhere = FileManager.default.temporaryDirectory
            .appendingPathComponent("no-traces-here/ios/Tests/x.swift").path
        XCTAssertNil(DriveReplay.directory(from: nowhere),
                     "should give up rather than return something arbitrary")

        // And that it does find a `traces` sibling several levels up, which is
        // the arrangement in this repo: the tests live in a worktree and the
        // traces only ever exist in the main checkout.
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("replay-walkup-\(UUID().uuidString)")
        let deep = root.appendingPathComponent("a/worktrees/b/ios/Tests")
        let traces = root.appendingPathComponent("traces")
        try? FileManager.default.createDirectory(at: deep, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: traces, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        // A directory with no ndjson in it is not the one we are looking for.
        XCTAssertNil(DriveReplay.directory(from: deep.appendingPathComponent("x.swift").path))
        try? Data().write(to: traces.appendingPathComponent("drive-x.ndjson"))
        XCTAssertEqual(DriveReplay.directory(from: deep.appendingPathComponent("x.swift").path)?
            .resolvingSymlinksInPath(), traces.resolvingSymlinksInPath())
    }

    /// The decoder, independently of whether any traces are on this machine.
    ///
    /// Its job is to put a `route` record back through the app's own
    /// `Decodable` path, and the two things it has to get right are the ones
    /// the writer does not round-trip cleanly: an empty `type` means "the
    /// server sent none", not "a maneuver kind we don't recognise", and the
    /// 2026-08-14 traces have no `type` key at all.
    func test_the_decoder_reads_both_generations_of_trace() throws {
        let modern = """
        {"t":"route","seq":1,"reason":"start","km":1.0,"minutes":2.0,"mean_score":5.0,\
        "coords":[[-71.0,42.0],[-71.0,42.01]],\
        "steps":[{"instruction":"Turn left onto Elm Street","lat":42.0,"lon":-71.0,\
        "distance_m":120,"type":"turn","modifier":"left"},\
        {"instruction":"Arrive","lat":42.01,"lon":-71.0,"distance_m":0,"type":"","modifier":""}]}
        """
        let ancient = """
        {"t":"route","seq":1,"reason":"start","km":1.0,"minutes":2.0,"mean_score":5.0,\
        "coords":[[-71.0,42.0],[-71.0,42.01]],\
        "steps":[{"instruction":"Turn left onto Elm Street","lat":42.0,"lon":-71.0,\
        "distance_m":120}]}
        """
        for (label, line) in [("modern", modern), ("2026-08-14", ancient)] {
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("replay-\(label).ndjson")
            let header = #"{"t":"drive","dest":[42.01,-71.0],"pref":0.6,"weights":{}}"#
            let fixes = (0..<2).map {
                #"{"t":"fix","ts":\#(1_780_000_000 + $0),"lat":42.00\#($0),"lon":-71.0,"# +
                #""acc":5,"spd":10,"crs":0,"route":1,"travelled":0,"remaining":1000,"# +
                #""off":0,"joined":true,"step":0}"#
            }
            try ([header, line] + fixes).joined(separator: "\n").write(
                to: url, atomically: true, encoding: .utf8)
            defer { try? FileManager.default.removeItem(at: url) }

            let drive = try XCTUnwrap(DriveReplay.RecordedDrive(contentsOf: url), label)
            XCTAssertEqual(drive.routes.count, 1, label)
            XCTAssertEqual(drive.fixes.count, 2, label)
            XCTAssertEqual(drive.destination.latitude, 42.01, accuracy: 1e-9, label)
            let steps = drive.routes[0].properties.steps
            XCTAssertEqual(steps[0].instruction, "Turn left onto Elm Street", label)
            XCTAssertEqual(steps[0].distance_m, 120, label)
            XCTAssertEqual(steps[0].type, label == "modern" ? .turn : nil, label)
            if label == "modern" {
                // "" has to arrive as *absent*, not as `.unknown` — the
                // difference between "no type was sent" and "a type we don't
                // recognise", which is the distinction `DriveTrace` documents
                // and analyze_trace.py already got wrong once.
                XCTAssertNil(steps[1].type, "empty type should decode as absent")
            }
        }
    }
}
