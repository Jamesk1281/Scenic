import XCTest
@testable import Scenic

/// The two summary cards and the sentence beneath them, which used to
/// contradict each other by a minute.
///
/// Observed on screen, Needham → Rockport at the default preference: cards
/// reading `58 min` and `106 min` over the words **"Scenic adds 49 min"**. The
/// API had answered 57.6 and 106.4, each card rounded its own value, and the
/// delta was rounded from the raw difference — so all three numbers were
/// individually correct and jointly impossible, over a subtraction any reader
/// can do in their head.
@MainActor
final class RouteComparisonTests: XCTestCase {

    /// A comparison built the way the screen builds one: from decoded
    /// properties, so a change to the JSON shape breaks this too.
    private func comparison(fastest: Double, scenic: Double,
                            fastestScore: Double = 4.4,
                            scenicScore: Double = 6.1) -> RouteComparison {
        let leg = { (minutes: Double, score: Double) -> RouteProps in
            Fixture.decode(Fixture.feature(
                coordinates: [[-71.0, 42.0], [-71.0, 42.1]],
                km: 10, minutes: minutes,
                steps: [(Fixture.origin, "Head north", nil)],
                meanScore: score)).properties
        }
        return RouteComparison(fastest: leg(fastest, fastestScore),
                               scenic: leg(scenic, scenicScore))
    }

    // MARK: - The sentence has to be arithmetic the reader can check

    func test_the_delta_is_the_difference_between_the_two_cards() {
        // The exact numbers off the Needham → Rockport screenshot.
        let c = comparison(fastest: 57.6, scenic: 106.4)

        XCTAssertEqual(c.fastestMinutes, 58)
        XCTAssertEqual(c.scenicMinutes, 106)
        XCTAssertEqual(c.extraMinutes, 48, "106 − 58, not 49")
        XCTAssertTrue(c.summary.contains("**48 min**"), c.summary)
    }

    func test_no_pair_of_minutes_can_make_the_sentence_disagree() {
        // The property, not one example: the sentence is the subtraction of the
        // two numbers printed above it, whatever they are. The deltas sweep
        // across the half-minute, which is the only place rounding twice could
        // ever have disagreed.
        for fastest in stride(from: 5.0, through: 120.0, by: 0.25) {
            for delta in [0.0, 0.4, 0.5, 0.6, 1.0, 48.8] {
                let c = comparison(fastest: fastest, scenic: fastest + delta)
                XCTAssertEqual(c.extraMinutes, c.scenicMinutes - c.fastestMinutes,
                               "\(fastest) → \(fastest + delta)")
            }
        }
    }

    // MARK: - Nothing to add is not a charge of zero

    func test_the_same_route_twice_says_so() {
        // At pref 0 the server answers with the same route for both, and
        // "Scenic adds 0 min and raises scenery 4.4 → 4.4" is a sentence about
        // nothing.
        let c = comparison(fastest: 57.6, scenic: 57.6,
                           fastestScore: 4.4, scenicScore: 4.4)

        XCTAssertEqual(c.extraMinutes, 0)
        XCTAssertTrue(c.isSameDrive)
        XCTAssertEqual(c.summary, "**Same as the fastest route** at this setting.")
        XCTAssertFalse(c.summary.contains("0 min"))
    }

    func test_scenery_gained_for_free_is_not_reported_as_zero_minutes() {
        // Both round to 58, so the cards agree — but the routes are different
        // and one of them is prettier. That is the best news this screen ever
        // has to deliver and it must not read as "adds 0 min".
        let c = comparison(fastest: 57.6, scenic: 58.2,
                           fastestScore: 4.4, scenicScore: 6.1)

        XCTAssertEqual(c.extraMinutes, 0)
        XCTAssertFalse(c.isSameDrive, "different scenery is a different drive")
        XCTAssertTrue(c.summary.contains("no extra time"), c.summary)
        XCTAssertTrue(c.summary.contains("**4.4**"), c.summary)
        XCTAssertTrue(c.summary.contains("**6.1**"), c.summary)
    }

    func test_the_scores_are_compared_at_the_precision_they_are_printed_to() {
        // 4.42 and 4.44 both print as "4.4", so claiming a scenery gain between
        // them would be a claim the cards visibly contradict.
        let c = comparison(fastest: 57.6, scenic: 57.6,
                           fastestScore: 4.42, scenicScore: 4.44)
        XCTAssertTrue(c.isSameDrive)
    }

    func test_scores_a_tolerance_apart_still_go_by_what_is_printed() {
        // The two cases `abs(difference) < 0.05` got backwards, in both
        // directions. Neither is exotic: measured over 983 sampled routes, four
        // of them printed an identical score under the word "raises".
        let wider = comparison(fastest: 57.6, scenic: 57.6,
                               fastestScore: 4.851, scenicScore: 4.949)
        XCTAssertTrue(wider.isSameDrive,
                      "0.098 apart, but both print 4.9 — the reader sees no change")

        let narrower = comparison(fastest: 57.6, scenic: 57.6,
                                  fastestScore: 4.949, scenicScore: 4.951)
        XCTAssertFalse(narrower.isSameDrive,
                       "0.002 apart, but they print 4.9 and 5.0 — the reader sees one")
    }

    // MARK: - The sentence cannot claim a rise the numbers do not show

    func test_a_scenic_route_that_scores_lower_is_not_described_as_raising() {
        // Worcester-area trip, 983-route census: the scenic arm came back
        // 4.8 km shorter and 0.3 min slower, scoring 5.21 against 5.79, and the
        // sentence read "adds 1 min and raises scenery 5.8 → 5.2". The server
        // now returns the fastest route in that case, but the app must not be
        // the only thing standing between an older backend and that sentence.
        let c = comparison(fastest: 26.4, scenic: 26.7,
                           fastestScore: 5.794, scenicScore: 5.210)

        XCTAssertFalse(c.isSameDrive)
        XCTAssertFalse(c.summary.contains("raises"), c.summary)
        XCTAssertTrue(c.summary.contains("lowers"), c.summary)
        XCTAssertTrue(c.summary.contains("**5.8**"), c.summary)
        XCTAssertTrue(c.summary.contains("**5.2**"), c.summary)
    }

    func test_time_spent_for_no_change_in_scenery_says_exactly_that() {
        // A different route that costs a minute and lands on the same printed
        // score. "Raises scenery 5.3 → 5.3" is the sentence about nothing that
        // `isSameDrive` catches only when the drive is also no slower.
        let c = comparison(fastest: 23.0, scenic: 24.0,
                           fastestScore: 5.313, scenicScore: 5.316)

        XCTAssertEqual(c.extraMinutes, 1)
        XCTAssertFalse(c.isSameDrive, "it costs a minute, so it is not the same drive")
        XCTAssertFalse(c.summary.contains("raises"), c.summary)
        XCTAssertTrue(c.summary.contains("leaves scenery at **5.3**"), c.summary)
        XCTAssertTrue(c.summary.contains("**1 min**"), c.summary)
    }

    func test_the_ordinary_case_still_reads_as_before() {
        // The guard above must not have cost the sentence everybody sees.
        let c = comparison(fastest: 57.6, scenic: 106.4,
                           fastestScore: 4.4, scenicScore: 6.1)
        XCTAssertEqual(c.summary,
                       "Scenic adds **48 min** and raises scenery **4.4** → **6.1**")
    }

    func test_the_markdown_resolves_rather_than_being_shown_raw() {
        let c = comparison(fastest: 57.6, scenic: 106.4)
        let rendered = String(c.attributedSummary.characters)
        XCTAssertFalse(rendered.contains("**"), rendered)
        XCTAssertTrue(rendered.contains("48 min"), rendered)
    }
}
