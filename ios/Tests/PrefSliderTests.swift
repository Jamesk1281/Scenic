import XCTest
@testable import Scenic

/// The map between where the preference slider's handle sits and the `pref`
/// the API is asked for.
///
/// The two are different numbers on purpose: the router squares the preference
/// (`PREF_CURVE = 2.0`, `pipeline/router.py:152`), so a handle linear in `pref`
/// spends its first quarter in the flat part of the curve. Measured over 252
/// pairs, pref 0.25 is 6% strength — a median 0.5 extra minutes and 0.20 extra
/// beautiful miles over pref 0, with a third of trips handing back the route
/// they already had.
@MainActor
final class PrefSliderTests: XCTestCase {

    // MARK: - The round trip, and the two ends of it

    func test_the_far_left_is_exactly_zero_in_both_directions() {
        // `pref == 0` is magic in three places: `server/app.py` short-circuits
        // (`scenic = fastest if pref == 0.0`), `NavigationModel.reroute` reads
        // it as `wantFastest`, and `switchToFastest` writes it. A handle pushed
        // to the far left that produced 1e-9 would quietly stop asking for the
        // fastest route, and nothing on screen would say so.
        XCTAssertEqual(PrefSlider.pref(atPosition: 0), 0)
        XCTAssertEqual(PrefSlider.position(forPref: 0), 0)

        // Not "close to zero" — the comparisons that read it are `== 0`.
        XCTAssertTrue(PrefSlider.pref(atPosition: 0) == 0.0)
        XCTAssertTrue(PrefSlider.position(forPref: 0) == 0.0)
    }

    func test_the_far_right_is_exactly_one_in_both_directions() {
        // The other end has no magic behind it, but a slider that could not
        // reach its own maximum would silently cap the scenic route.
        XCTAssertEqual(PrefSlider.pref(atPosition: 1), 1)
        XCTAssertEqual(PrefSlider.position(forPref: 1), 1)
    }

    func test_position_and_pref_round_trip_across_the_whole_track() {
        // Both directions, because the binding does both: it reads
        // `position(forPref:)` to place the handle and writes
        // `pref(atPosition:)` when the handle moves. A drift either way would
        // make the handle creep on every redraw.
        for step in 0...1000 {
            let position = Double(step) / 1000
            let roundTripped = PrefSlider.position(forPref: PrefSlider.pref(atPosition: position))
            XCTAssertEqual(roundTripped, position, accuracy: 1e-12, "position \(position)")
        }
        for step in 0...1000 {
            let pref = Double(step) / 1000
            let roundTripped = PrefSlider.pref(atPosition: PrefSlider.position(forPref: pref))
            XCTAssertEqual(roundTripped, pref, accuracy: 1e-12, "pref \(pref)")
        }
    }

    // MARK: - What the mapping is for

    func test_the_handle_is_linear_in_the_strength_the_router_applies() {
        // `router.py:919` is `strength = clamp(pref) ** PREF_CURVE` with
        // PREF_CURVE = 2.0, so the strength a position asks for is that
        // position. This is the property the whole change exists to get, and it
        // is what lets the caption print the position and call it strength.
        for step in 0...100 {
            let position = Double(step) / 100
            let pref = PrefSlider.pref(atPosition: position)
            let strength = pow(pref, 2.0)   // PREF_CURVE
            XCTAssertEqual(strength, position, accuracy: 1e-12, "position \(position)")
        }
    }

    func test_the_dead_quarter_is_gone() {
        // Before: a handle a quarter along asked for pref 0.25, which is 6%
        // strength — the census measured 2% of the slider's whole effect there.
        // After: a handle a quarter along asks for pref 0.5, which is the 25%
        // strength the census measured at 55% of the available beautiful miles.
        XCTAssertEqual(PrefSlider.pref(atPosition: 0.25), 0.5, accuracy: 1e-12)
        XCTAssertEqual(pow(PrefSlider.pref(atPosition: 0.25), 2.0), 0.25, accuracy: 1e-12)

        // The old linear handle at the same place, for contrast.
        XCTAssertEqual(pow(0.25, 2.0), 0.0625, accuracy: 1e-12)
    }

    func test_the_shipped_default_still_asks_for_the_pref_it_always_did() {
        // The consequence to be deliberate about: `RouteModel.pref` stays 0.5,
        // so the default *route* is unchanged and the census and the twelve
        // recorded drives stay comparable — but the handle now rests a quarter
        // along rather than halfway, because a quarter is honestly where 0.5's
        // strength sits.
        XCTAssertEqual(RouteModel().pref, 0.5)
        XCTAssertEqual(PrefSlider.position(forPref: 0.5), 0.25, accuracy: 1e-12)
    }

    // MARK: - Nothing out of range reaches the API

    func test_a_position_outside_the_track_is_clamped_rather_than_producing_a_nan() {
        // `sqrt` of a negative is NaN, and a NaN `pref` would be formatted into
        // the query string as "nan". The slider is declared `in: 0...1` so this
        // should be unreachable, which is the reason to pin it: an unreachable
        // input is exactly the one nobody notices changing.
        XCTAssertEqual(PrefSlider.pref(atPosition: -0.5), 0)
        XCTAssertEqual(PrefSlider.pref(atPosition: 1.5), 1)
        XCTAssertEqual(PrefSlider.position(forPref: -0.5), 0)
        XCTAssertEqual(PrefSlider.position(forPref: 1.5), 1)
    }
}
