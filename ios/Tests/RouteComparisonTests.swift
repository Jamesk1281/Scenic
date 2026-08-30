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
    ///
    /// The beautiful-kilometre arguments default to nil, which leaves the keys
    /// out of the JSON — the shape the *deployed* backend still sends. So every
    /// case below that does not pass them is a live-server case, and the
    /// fallback sentence is under test wherever the miles are not.
    private func comparison(fastest: Double, scenic: Double,
                            fastestScore: Double = 4.4,
                            scenicScore: Double = 6.1,
                            fastestBeautifulKm: Double? = nil,
                            scenicBeautifulKm: Double? = nil) -> RouteComparison {
        let leg = { (minutes: Double, score: Double, beautifulKm: Double?) -> RouteProps in
            Fixture.decode(Fixture.feature(
                coordinates: [[-71.0, 42.0], [-71.0, 42.1]],
                km: 10, minutes: minutes,
                steps: [(Fixture.origin, "Head north", nil)],
                meanScore: score, beautifulKm: beautifulKm)).properties
        }
        return RouteComparison(fastest: leg(fastest, fastestScore, fastestBeautifulKm),
                               scenic: leg(scenic, scenicScore, scenicBeautifulKm))
    }

    /// Kilometres that read as `miles` on screen, so a case can be written in
    /// the units it is asserted in.
    private func km(miles: Double) -> Double { miles / 0.621371 }

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

    // MARK: - Miles of beautiful road, where the backend reports them

    func test_the_cards_lead_with_miles_when_the_backend_sends_them() {
        // Worcester -> Boston off the live graph: 0 mi of beautiful road on the
        // fastest arm, 3 on the scenic one.
        let c = comparison(fastest: 26.0, scenic: 52.0,
                           fastestBeautifulKm: km(miles: 0.2),
                           scenicBeautifulKm: km(miles: 3.1))

        XCTAssertEqual(c.fastestDetail, "0 mi beautiful")
        XCTAssertEqual(c.scenicDetail, "3 mi beautiful")
        XCTAssertEqual(c.summary,
                       "Scenic adds **26 min** and turns **0 mi** of beautiful road into **3 mi**")
        XCTAssertFalse(c.summary.contains("/10"), "the 0-10 scale is off this screen")
    }

    func test_the_sentence_prints_the_same_integers_as_the_cards() {
        // The property this whole type exists for, now on the mile counts:
        // whatever the cards say, the sentence says the same. 0.4 mi either
        // side of the half is where rounding twice could ever have disagreed.
        for fastestMiles in stride(from: 0.0, through: 20.0, by: 0.1) {
            let scenicMiles = fastestMiles + 4.5
            let c = comparison(fastest: 30, scenic: 50,
                               fastestBeautifulKm: km(miles: fastestMiles),
                               scenicBeautifulKm: km(miles: scenicMiles))
            guard let miles = c.beautifulMiles else {
                return XCTFail("both arms carried a count")
            }
            XCTAssertEqual(c.fastestDetail, "\(miles.fastest) mi beautiful")
            XCTAssertEqual(c.scenicDetail, "\(miles.scenic) mi beautiful")
            XCTAssertTrue(c.summary.contains("**\(miles.fastest) mi**"), c.summary)
            XCTAssertTrue(c.summary.contains("**\(miles.scenic) mi**"), c.summary)
        }
    }

    func test_a_scenic_route_with_less_beautiful_road_says_so() {
        // The 24 trips this change exists to expose: the scenic arm comes back
        // slower *and* with less beautiful road, on 3.1% of 983 sampled trips.
        // The mean rose on 27 of those 30, so the old sentence congratulated
        // itself; the miles cannot.
        let c = comparison(fastest: 40.0, scenic: 42.0,
                           fastestScore: 4.1, scenicScore: 4.7,
                           fastestBeautifulKm: km(miles: 12.0),
                           scenicBeautifulKm: km(miles: 1.0))

        XCTAssertEqual(c.summary,
                       "Scenic adds **2 min** and **cuts** beautiful road from **12 mi** to **1 mi**")
        XCTAssertFalse(c.summary.contains("raises"), c.summary)
        XCTAssertFalse(c.summary.contains("turns"), "a fall is not phrased as a gain")
    }

    func test_a_fall_to_zero_is_neither_clamped_nor_an_error() {
        // Zero is a real answer, and "adds" would be the wrong word for it.
        let c = comparison(fastest: 30.0, scenic: 45.0,
                           fastestBeautifulKm: km(miles: 6.0),
                           scenicBeautifulKm: 0.0)

        XCTAssertEqual(c.scenicBeautifulMiles, 0)
        XCTAssertEqual(c.summary,
                       "Scenic adds **15 min** and **cuts** beautiful road from **6 mi** to **0 mi**")
    }

    func test_beautiful_miles_gained_for_free_are_not_reported_as_zero_minutes() {
        let c = comparison(fastest: 57.6, scenic: 58.2,
                           fastestScore: 4.4, scenicScore: 6.1,
                           fastestBeautifulKm: km(miles: 1.0),
                           scenicBeautifulKm: km(miles: 9.0))

        XCTAssertEqual(c.extraMinutes, 0)
        XCTAssertEqual(c.summary,
                       "Scenic turns **1 mi** of beautiful road into **9 mi** **at no extra time**")
        XCTAssertFalse(c.summary.contains("0 min"), c.summary)
    }

    func test_time_spent_for_no_more_beautiful_road_says_exactly_that() {
        let c = comparison(fastest: 23.0, scenic: 24.0,
                           fastestScore: 5.313, scenicScore: 6.100,
                           fastestBeautifulKm: km(miles: 4.2),
                           scenicBeautifulKm: km(miles: 4.4))

        XCTAssertEqual(c.extraMinutes, 1)
        XCTAssertFalse(c.beautifulMilesMove, "4.2 and 4.4 mi both print 4")
        XCTAssertEqual(c.summary,
                       "Scenic adds **1 min** and leaves beautiful road at **4 mi**")
    }

    func test_a_different_route_that_ties_on_miles_and_costs_nothing() {
        // Past `isSameDrive` because the mean moved, but the whole miles tie.
        // Claiming a gain here would contradict two cards showing 4 mi each.
        let c = comparison(fastest: 57.6, scenic: 57.9,
                           fastestScore: 4.4, scenicScore: 6.1,
                           fastestBeautifulKm: km(miles: 4.1),
                           scenicBeautifulKm: km(miles: 4.4))

        XCTAssertEqual(c.extraMinutes, 0)
        XCTAssertFalse(c.isSameDrive)
        XCTAssertEqual(c.summary,
                       "A different route with the same **4 mi** of beautiful road, at no extra time")
    }

    func test_the_same_drive_still_wins_over_the_mile_counts() {
        // `isSameDrive` is measured on `mean_score` and stays there. Across the
        // 113 trips it fires on, the largest gain is 0.22 beautiful miles —
        // which rounds to the same whole mile, as here, so it hides nothing.
        let c = comparison(fastest: 57.6, scenic: 57.6,
                           fastestScore: 4.4, scenicScore: 4.4,
                           fastestBeautifulKm: km(miles: 4.0),
                           scenicBeautifulKm: km(miles: 4.2))

        XCTAssertTrue(c.isSameDrive)
        XCTAssertEqual(c.summary, "**Same as the fastest route** at this setting.")
    }

    // MARK: - The backend that has not deployed this yet

    func test_a_response_without_beautiful_km_still_decodes_and_reads() {
        // The trap this pair of fields is optional for: the deployed backend
        // does not send them, and an app in the store talks to whichever
        // backend is deployed. A non-optional field would fail every decode.
        let c = comparison(fastest: 57.6, scenic: 106.4,
                           fastestScore: 4.4, scenicScore: 6.1)

        XCTAssertNil(c.fastestBeautifulMiles)
        XCTAssertNil(c.beautifulMiles)
        XCTAssertEqual(c.fastestDetail, "4.4/10")
        XCTAssertEqual(c.scenicDetail, "6.1/10")
        XCTAssertEqual(c.summary,
                       "Scenic adds **48 min** and raises scenery **4.4** → **6.1**")
        XCTAssertEqual(c.summary, c.scoreSummary)
    }

    func test_one_arm_alone_does_not_put_two_scales_on_screen() {
        // Not a shape any single backend sends, but if it ever arrived, a mile
        // count on one card beside a 0-10 score on the other is the one
        // outcome that must not happen.
        let c = comparison(fastest: 57.6, scenic: 106.4,
                           scenicBeautifulKm: km(miles: 9.0))

        XCTAssertNil(c.beautifulMiles)
        XCTAssertEqual(c.fastestDetail, "4.4/10")
        XCTAssertEqual(c.scenicDetail, "6.1/10")
        XCTAssertEqual(c.summary, c.scoreSummary)
    }

    func test_the_fallback_can_still_say_the_score_went_down() {
        // The older backend's sentence keeps its own guard: this is the case
        // `_no_worse_than_fastest` catches server-side, on a server that does
        // not have it.
        let c = comparison(fastest: 26.4, scenic: 26.7,
                           fastestScore: 5.794, scenicScore: 5.210)

        XCTAssertTrue(c.summary.contains("lowers"), c.summary)
        XCTAssertFalse(c.summary.contains("raises"), c.summary)
    }
}
