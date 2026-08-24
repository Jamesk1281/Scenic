"""The junction-cost fit: does a parked car end up priced as a traffic signal?

`tools/fit_junction_cost.py` had no tests at all, which is how it came to be the
one consumer of `analyze_trace.stops` that ignored the `parked` flag. `b9951d1`
introduced that flag and fixed the *report*; the fit went on charging a lunch
break to whichever signal it was parked beside, and the whole suite stayed green
because nothing here looked.

Measured on drive-2026-08-22-202700, which parked for 7.1 minutes: the fit read
7 signal stops totalling 11.7 min where the drive had 4 totalling 4.1, and the
pooled cost of a signal came out at 18.7 s instead of 13.0 s. That number is
what `router.py` charges at every one of Massachusetts' 29,772 controls.
"""

import pandas as pd
import pytest

import analyze_trace as at
import fit_junction_cost as fj
from test_trace import fixes


def steps_with_a_parked_run():
    """A minute of driving, a long stop, a minute of driving."""
    return at.steps(fixes([(20.0, 60), (0.0, at.PARKED_S + 60), (20.0, 60)]))


def test_a_parked_run_is_not_junction_cost():
    # The defect: this run is stationary next to whatever the driver parked
    # beside, so `attribute` charges it to that control and the fit calls a
    # lunch break the price of a red light.
    steps = steps_with_a_parked_run()
    stops = at.stops(steps)
    assert stops.parked.any(), "the fixture is meant to contain a parked run"

    _, kept, _ = fj.hold_out_parked(steps, stops)
    assert len(kept) == 0
    assert not kept.parked.any()


def test_parked_minutes_come_off_the_clock_the_fit_is_scored_against():
    # Both halves, or the fit is judged against a clock that includes time the
    # numerator no longer explains — which reads as the model under-predicting.
    steps = steps_with_a_parked_run()
    kept_steps, _, _ = fj.hold_out_parked(steps, at.stops(steps))

    assert kept_steps.dt_s.sum() == pytest.approx(120.0, abs=10.0)
    assert steps.dt_s.sum() > kept_steps.dt_s.sum() + at.PARKED_S


def test_the_minutes_held_out_are_reported_rather_than_vanishing():
    # A row that never existed cannot be reported as an exclusion, and the
    # PARKED_S threshold is a judgement the reader has to be able to overrule.
    steps = steps_with_a_parked_run()
    _, _, parked_min = fj.hold_out_parked(steps, at.stops(steps))
    assert parked_min == pytest.approx((at.PARKED_S + 60) / 60.0, rel=0.05)


def test_a_long_red_light_still_counts():
    # The guard against over-applying the fix. A two-minute light is exactly the
    # sample this fit exists to collect, and holding it out would cost the fit
    # the roads it most needs to price.
    steps = at.steps(fixes([(20.0, 30), (0.0, 120), (20.0, 30)]))
    stops = at.stops(steps)
    kept_steps, kept_stops, parked_min = fj.hold_out_parked(steps, stops)

    assert len(kept_stops) == 1
    assert parked_min == 0.0
    assert len(kept_steps) == len(steps)


def test_a_drive_that_never_stopped_is_untouched():
    steps = at.steps(fixes([(20.0, 60)]))
    kept_steps, kept_stops, parked_min = fj.hold_out_parked(steps, at.stops(steps))
    assert len(kept_steps) == len(steps)
    assert kept_stops.empty
    assert parked_min == 0.0


def test_an_empty_drive_holds_out_nothing():
    # `main` reaches here with a bare DataFrame when no segment of a trace
    # joined the route, and `mark_parked` would have no columns to work with.
    empty = pd.DataFrame()
    kept_steps, kept_stops, parked_min = fj.hold_out_parked(empty, pd.DataFrame())
    assert kept_steps.empty
    assert kept_stops.empty
    assert parked_min == 0.0


def test_the_fit_and_the_report_hold_out_the_same_runs():
    """The invariant that actually broke.

    Both tools read the same drive and must agree on which stops are the road's
    fault. They disagreed for two days — 4 stops and 4.1 min in the report
    against 7 and 11.7 in the fit — and each looked internally consistent.
    """
    steps = steps_with_a_parked_run()
    stops = at.stops(steps)

    report_keeps = stops[~stops.parked]
    _, fit_keeps, _ = fj.hold_out_parked(steps, stops)
    assert len(fit_keeps) == len(report_keeps)

    report_steps = at.mark_parked(steps, stops)
    report_steps = report_steps[~report_steps.parked]
    fit_steps, _, _ = fj.hold_out_parked(steps, stops)
    assert fit_steps.dt_s.sum() == pytest.approx(report_steps.dt_s.sum())
