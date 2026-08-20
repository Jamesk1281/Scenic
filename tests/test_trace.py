"""The drive-trace analysis: does a recorded drive give back the right numbers?

These matter more than most tests here because the input costs a drive. If the
analysis quietly mis-reads a trace, the answer is a plausible-looking speed
factor fitted to a bug, and the only way to find out is to drive again.

Traces are built here rather than fixtured from a file, so each test states the
drive it is describing — 20 m/s for 10 s, one 30-second stop — and the expected
output is arithmetic the reader can do in their head.
"""

import json

import numpy as np
import pandas as pd
import pytest

import analyze_trace as at


def fixes(speeds_and_seconds, start_ts=1_000_000.0, accuracy=5.0, joined=True,
          climb=0.0):
    """A run of fixes from (speed m/s, duration s) pairs, one fix per second.

    `climb` is metres of altitude gained per metre driven, so a test can state
    the grade it means and check the analysis reads it back.
    """
    rows, ts, travelled = [], start_ts, 0.0
    for speed, seconds in speeds_and_seconds:
        for _ in range(int(seconds)):
            rows.append({"t": "fix", "ts": ts, "lat": 42.0, "lon": -71.0,
                         "acc": accuracy, "alt": 100.0 + travelled * climb,
                         "valt": 6.0, "spd": speed, "spda": 1.0, "crs": 0.0,
                         "route": 0, "travelled": travelled, "remaining": 0.0,
                         "off": 2.0, "joined": joined, "step": 0})
            ts += 1.0
            travelled += speed
    return pd.DataFrame(rows)


def trace_file(tmp_path, records, truncate_last=False):
    text = "\n".join(json.dumps(r) for r in records)
    if truncate_last:
        text = text[:-8]
    path = tmp_path / "drive.ndjson"
    path.write_text(text + ("" if truncate_last else "\n"))
    return path


# --- reading -----------------------------------------------------------------

def test_a_truncated_final_line_costs_one_fix_not_the_drive(tmp_path, capsys):
    # NDJSON is the format precisely so a drive that ends in a dead battery or a
    # jettison is still readable up to the moment it stopped.
    records = [{"t": "drive", "ts": 1.0, "pref": 0.5}]
    records += fixes([(20.0, 30)]).to_dict("records")
    path = trace_file(tmp_path, records, truncate_last=True)

    loaded = at.load(path)
    assert len(loaded) == len(records) - 1
    assert "truncated" in capsys.readouterr().out


def test_each_route_keeps_its_own_fixes(tmp_path):
    # `travelled` restarts at zero on a reroute. Read as one series it looks like
    # the car jumped backwards, which would price the whole drive wrong.
    records = [{"t": "drive", "ts": 1.0}]
    records += [{"t": "route", "seq": 0, "ts": 1.0, "km": 5.0, "minutes": 10.0}]
    records += fixes([(20.0, 10)]).to_dict("records")
    records += [{"t": "route", "seq": 1, "ts": 20.0, "km": 3.0, "minutes": 6.0}]
    second = fixes([(20.0, 10)], start_ts=1_000_020.0)
    second["route"] = 1
    records += second.to_dict("records")

    parts = at.segments(records)
    assert [route["seq"] for route, _ in parts] == [0, 1]
    assert all(len(f) == 10 for _, f in parts)
    # Each segment starts from zero again, and neither sees the other's reset.
    assert all(f.travelled.iloc[0] == 0.0 for _, f in parts)


# --- speed -------------------------------------------------------------------

def test_speed_is_distance_along_the_route_over_time():
    steps = at.steps(fixes([(20.0, 10)]))
    assert len(steps) == 9                      # pairs, not fixes
    assert steps.dist_m.sum() == pytest.approx(180.0)
    assert steps.speed_ms.mean() == pytest.approx(20.0)


def test_a_route_followed_for_a_single_fix_does_not_break_the_analysis():
    """A reroute seconds before arrival is ordinary, and used to be fatal.

    Everything downstream reads these columns by name, so a segment too short to
    measure has to come back the same shape as one that was — otherwise the
    whole drive is unreadable, after the drive, over one stray fix.
    """
    one_fix = fixes([(20.0, 1)])
    empty = at.steps(one_fix)
    assert empty.empty
    assert list(empty.columns) == at.STEP_COLUMNS

    # And the numbers built on top of it all cope with having nothing to say.
    labelled = empty.assign(highway=None, assumed_ms=np.nan)
    assert at.stops(empty).empty
    assert at.by_grade(labelled).empty
    assert at.by_road_class(labelled).empty
    assert at.headline(labelled)["km"] == 0


def test_every_step_frame_has_the_same_columns():
    # The full path and the empty path are read by the same code.
    full = at.steps(fixes([(20.0, 10)]))
    assert list(full.columns) == at.STEP_COLUMNS


def test_fixes_before_joining_the_route_are_not_measured():
    # Before the driver reaches the line their match lands wherever the route
    # passes nearest — often miles from them — so the distance between two such
    # matches is fiction, not movement.
    assert at.steps(fixes([(20.0, 10)], joined=False)).empty


def test_an_inaccurate_fix_is_dropped():
    good, bad = fixes([(20.0, 5)]), fixes([(20.0, 5)], start_ts=1_000_010.0,
                                          accuracy=60.0)
    steps = at.steps(pd.concat([good, bad], ignore_index=True))
    # A 60 m fix is fine for "which road" and useless for "how fast".
    assert steps.acc.max() <= at.MAX_ACCURACY_M


def test_a_backwards_nudge_from_gps_jitter_does_not_become_negative_distance():
    f = fixes([(20.0, 4)])
    f.loc[2, "travelled"] -= 3.0            # the match wobbles back a few metres
    steps = at.steps(f)
    assert (steps.dist_m >= 0).all()


# --- gaps: the exclusion that can delete the answer ---------------------------

def test_a_dropout_is_dropped_but_counted(tmp_path):
    # If the phone goes quiet while the car is stationary, every stop becomes a
    # gap and the junction cost reads zero. That has to be visible, or the
    # missing measurement looks like good news.
    before = fixes([(20.0, 5)])
    after = fixes([(20.0, 5)], start_ts=1_000_000.0 + 125)
    silence = after.ts.iloc[0] - before.ts.iloc[-1]
    steps = at.steps(pd.concat([before, after], ignore_index=True))

    assert steps.attrs["gap_count"] == 1
    assert steps.attrs["gap_seconds"] == pytest.approx(silence)
    assert steps.dt_s.max() <= at.MAX_GAP_S


# --- stops -------------------------------------------------------------------

def test_a_stop_is_measured_at_its_real_length():
    steps = at.steps(fixes([(20.0, 10), (0.0, 30), (20.0, 10)]))
    stops = at.stops(steps)
    assert len(stops) == 1
    # The stop is the stationary span; a second either side is the car slowing
    # and pulling away.
    assert stops.seconds.iloc[0] == pytest.approx(30.0, abs=2.0)


def test_a_rolling_stop_is_not_a_stop():
    # Two jittery fixes at a give-way are not a red light, and counting them
    # would inflate the junction cost with something the router should not pay.
    steps = at.steps(fixes([(20.0, 10), (0.0, 2), (20.0, 10)]))
    assert at.stops(steps).empty


def test_several_stops_are_counted_separately():
    steps = at.steps(fixes([(20.0, 10), (0.0, 20), (20.0, 10),
                            (0.0, 25), (20.0, 10)]))
    stops = at.stops(steps)
    assert len(stops) == 2
    assert stops.seconds.sum() == pytest.approx(45.0, abs=3.0)


# --- the numbers that come out ------------------------------------------------

def test_the_speed_factor_is_measured_distance_weighted():
    # A hundred crawling fixes and one fast one are a hundred metres and a
    # kilometre. Averaging the per-step speeds would weight them equally and
    # report a road far slower than it was driven.
    steps = at.steps(fixes([(5.0, 100), (30.0, 40)]))
    steps["highway"] = "secondary"
    steps["assumed_ms"] = 20.0

    row = at.by_road_class(steps).loc["secondary"]
    expected_ms = steps.dist_m.sum() / steps.dt_s.sum()
    assert row.measured_kmh == pytest.approx(expected_ms * 3.6, rel=1e-6)
    assert row.factor == pytest.approx(expected_ms / 20.0, rel=1e-6)


def test_time_spent_stopped_stays_out_of_the_speed_factor():
    # The two defects need different fixes — one scales with distance, the other
    # with junction count — so folding the stop into the speed would fit this
    # drive and misprice every route with a different number of intersections.
    moving = fixes([(20.0, 60)])
    with_stop = at.steps(fixes([(20.0, 30), (0.0, 60), (20.0, 30)]))
    with_stop["highway"] = "tertiary"
    with_stop["assumed_ms"] = 20.0

    clean = at.steps(moving)
    clean["highway"] = "tertiary"
    clean["assumed_ms"] = 20.0

    assert at.by_road_class(with_stop).loc["tertiary"].factor == pytest.approx(
        at.by_road_class(clean).loc["tertiary"].factor, rel=0.02)


def test_the_headline_prices_the_ground_actually_covered():
    # Not by scaling the route's total: a drive abandoned halfway, or one that
    # rerouted twice, still has to be compared against a prediction for the same
    # ground it really drove.
    steps = at.steps(fixes([(15.0, 60)]))       # driven at 15 m/s
    steps["highway"] = "primary"
    steps["assumed_ms"] = 20.0                  # the graph assumed 20 m/s

    h = at.headline(steps)
    assert h["actual_min"] == pytest.approx(h["predicted_min"] * 20.0 / 15.0, rel=0.01)


def test_a_class_the_graph_has_no_speed_for_is_left_out_of_the_prediction():
    # A fix in a tunnel or on a road OSM lacks can't be priced. Counting its
    # distance as free would make the router look better than it is.
    steps = at.steps(fixes([(20.0, 20)]))
    steps["highway"] = None
    steps["assumed_ms"] = np.nan

    h = at.headline(steps)
    assert h["predicted_min"] == 0.0
    assert h["matched_km"] == 0.0
    assert h["km"] > 0                          # the distance is still reported


# --- how much of the drive the measurement accounts for -----------------------

def test_wall_clock_is_kept_alongside_the_measured_time():
    # Steps that get dropped take their seconds with them, so measured time is
    # always <= the drive that happened. Reporting only the measured total would
    # quietly describe a shorter, tidier drive, and the headline ratio would look
    # fine while a chunk of the evidence was missing.
    before = fixes([(20.0, 5)])
    after = fixes([(20.0, 5)], start_ts=1_000_000.0 + 125)
    steps = at.steps(pd.concat([before, after], ignore_index=True))

    assert steps.attrs["elapsed_s"] == pytest.approx(after.ts.iloc[-1] - before.ts.iloc[0])
    assert steps.dt_s.sum() < steps.attrs["elapsed_s"]


def test_a_gap_is_blamed_on_the_app_when_the_app_says_so():
    # A tunnel, a suspended app and a bug all look identical in the fixes. Only
    # the phase records tell them apart, and they want different responses.
    before = fixes([(20.0, 5)])
    after = fixes([(20.0, 5)], start_ts=1_000_000.0 + 125)
    steps = at.steps(pd.concat([before, after], ignore_index=True))
    spans = steps.attrs["gap_spans"]

    went_away = [{"t": "phase", "ts": before.ts.iloc[-1] + 1, "phase": "background"}]
    assert at.backgrounded(went_away, spans) == 1
    # A drive that never left the foreground blames nothing on the app.
    assert at.backgrounded([], spans) == 0


# --- what the stops were ------------------------------------------------------

def test_a_stop_at_a_mapped_signal_is_told_apart_from_one_at_nothing():
    """The split the whole exercise turns on.

    A stop at a signal is structural — it is there every time you drive that
    road, it belongs in graph.py, and no traffic feed is needed to know about
    it. A stop at nothing is congestion. Adding them together would bake one
    afternoon's traffic into the graph permanently.
    """
    import geopandas as gpd

    stops = pd.DataFrame({
        "start_ts": [1.0, 2.0],
        "seconds": [40.0, 40.0],
        "lat": [42.0, 42.5],           # the second is ~55 km away from anything
        "lon": [-71.0, -71.0],
    })
    control = gpd.GeoDataFrame(
        {"kind": ["traffic_signals"]},
        geometry=gpd.points_from_xy([-71.0], [42.0]), crs=4326)

    classified = at.classify_stops(stops, control, maneuvers=[])
    assert list(classified.cause) == ["traffic_signals", "unexplained"]


def test_a_stop_at_a_turn_with_no_mapped_control_is_blamed_on_the_turn():
    stops = pd.DataFrame({"start_ts": [1.0], "seconds": [30.0],
                          "lat": [42.0], "lon": [-71.0]})
    maneuvers = [{"instruction": "Turn left onto Elm", "lat": 42.0, "lon": -71.0}]

    classified = at.classify_stops(stops, control=None, maneuvers=maneuvers)
    assert classified.cause.iloc[0] == "maneuver"


def test_a_mapped_control_outranks_a_turn_at_the_same_junction():
    # Most turns happen at junctions and most junctions have something. "There
    # was a signal here" is a better explanation than "there was a turn here",
    # and it is the one that can go into the graph.
    import geopandas as gpd

    stops = pd.DataFrame({"start_ts": [1.0], "seconds": [30.0],
                          "lat": [42.0], "lon": [-71.0]})
    control = gpd.GeoDataFrame(
        {"kind": ["stop"]}, geometry=gpd.points_from_xy([-71.0], [42.0]), crs=4326)
    maneuvers = [{"instruction": "Turn left onto Elm", "lat": 42.0, "lon": -71.0}]

    classified = at.classify_stops(stops, control, maneuvers)
    assert classified.cause.iloc[0] == "stop"


def test_classifying_no_stops_at_all_is_not_an_error():
    empty = pd.DataFrame(columns=["start_ts", "seconds", "lat", "lon"])
    assert at.classify_stops(empty, control=None, maneuvers=[]).empty


# --- grade --------------------------------------------------------------------

def test_grade_is_read_back_from_a_climb_that_was_really_driven():
    steps = at.steps(fixes([(20.0, 60)], climb=0.05))    # a steady 5% climb
    steps["highway"] = "tertiary"
    steps["assumed_ms"] = 20.0

    bands = at.by_grade(steps)
    assert list(bands.index) == ["uphill"]


def test_grade_does_not_invent_a_hill_out_of_altitude_noise():
    """The curvature mistake, guarded against.

    Raw per-step altitude differences on a phone are mostly noise, and a flat
    drive read that way splits evenly across uphill and downhill — producing
    two confident, meaningless factors. Altitude is smoothed before it is
    differenced; this is the test that says so.
    """
    rng = np.random.default_rng(0)
    flat = fixes([(20.0, 300)])
    flat["alt"] = 100.0 + rng.normal(0, 5, len(flat))    # metres of GPS noise

    steps = at.steps(flat)
    steps["highway"] = "tertiary"
    steps["assumed_ms"] = 20.0

    bands = at.by_grade(steps)
    level_km = bands.loc["level", "km"] if "level" in bands.index else 0.0
    assert level_km / bands.km.sum() > 0.9, (
        "a flat road read as hills — altitude smoothing is not doing its job")


# --- scenery marks ------------------------------------------------------------
# What the driver thought of the road. These are the only records in a trace that
# a laptop cannot reconstruct, so mis-reading one is unrecoverable in a way that
# mis-reading a fix is not: the fixes are still on disk to re-analyse, the
# driver's opinion of a road they drove three weeks ago is not.

def mark(travelled, verdict="nice", ts=1_000_000.0, speed=20.0, joined=True,
         route=0, **extra):
    return {"t": "mark", "ts": ts, "verdict": verdict, "route": route,
            "travelled": travelled, "joined": joined, "spd": speed, **extra}


def test_a_mark_is_charged_to_the_road_behind_it_not_under_it():
    # The driver taps after seeing something, so the tap is downstream of what
    # prompted it. Charging the verdict to the road under the tap would credit
    # the score for whatever came next — which at 20 m/s is 60 m of road the
    # driver had not looked at yet when they decided.
    got = at.marks([mark(1000.0, speed=20.0)])
    assert len(got) == 1
    row = got.iloc[0]
    assert row.hi_m == pytest.approx(1000.0 - 20.0 * at.MARK_REACTION_S)
    assert row.lo_m == pytest.approx(row.hi_m - at.MARK_WINDOW_M)


def test_the_reaction_allowance_scales_with_speed():
    # Three seconds is 40 m through a village and 110 m on a highway. A fixed
    # metre offset would be wrong at one end of that range or the other, which is
    # why the mark carries `spd` at all.
    town = at.marks([mark(1000.0, speed=13.0)]).iloc[0]
    highway = at.marks([mark(1000.0, speed=36.0)]).iloc[0]
    assert town.hi_m > highway.hi_m
    assert (town.hi_m - highway.hi_m) == pytest.approx(23.0 * at.MARK_REACTION_S)


def test_a_mark_at_the_very_start_of_a_route_does_not_go_negative():
    # Tapping in the first few metres is ordinary — the route starts at a
    # junction the driver is already sitting at — and a negative window would
    # sample the line from the wrong end.
    row = at.marks([mark(10.0, speed=20.0)]).iloc[0]
    assert row.lo_m == 0.0
    assert row.hi_m == 0.0


def test_a_mark_with_no_speed_falls_back_to_the_fixes_around_it():
    # CoreLocation reports -1 when it has no opinion. Reading the speed off the
    # trace's own fixes is better than assuming one, and the fallback has to be
    # the *measured* speed or the window lands somewhere the driver wasn't.
    records = fixes([(20.0, 30)]).to_dict("records")
    tapped_at = records[20]
    row = at.marks(records + [mark(tapped_at["travelled"], ts=tapped_at["ts"],
                                   speed=-1.0)]).iloc[0]
    assert row.speed_ms == pytest.approx(20.0, abs=0.5)


def test_a_mark_with_no_speed_and_no_fixes_charges_no_reaction_distance():
    # Zero is the conservative error: it attributes the verdict to road the
    # driver had definitely reached, rather than to road they might not have.
    row = at.marks([mark(500.0, speed=-1.0)]).iloc[0]
    assert row.speed_ms == 0.0
    assert row.hi_m == pytest.approx(500.0)


def test_a_mark_tapped_before_joining_the_route_is_dropped_and_counted():
    # Its `travelled` is a match onto wherever the line happened to pass
    # nearest, which is not where the driver was — so there is no road to
    # attribute it to. Dropped here rather than on the phone, and counted,
    # because a silently thinner sample is the failure this file exists to avoid.
    got = at.marks([mark(100.0, joined=False), mark(200.0)])
    assert len(got) == 1
    assert got.attrs["dropped_unjoined"] == 1


def test_a_verdict_whose_position_went_stale_is_dropped_and_counted():
    # Found by driving the simulator: a parked phone stops producing fixes, so
    # every mark carried a 32-second-old position and `travelled` from whenever
    # the stream stalled. Read without this guard they became confident verdicts
    # on the route's first metre.
    got = at.marks([mark(400.0, fix_age=32.0), mark(800.0, fix_age=0.4)])
    assert list(got.anchor_m) == [800.0]
    assert got.attrs["dropped_stale_fix"] == 1


def test_a_trace_recorded_before_fix_age_existed_is_still_read():
    # Absent is not stale. These traces predate the field, and silently dropping
    # every mark in them would be the same silent-thinning failure in reverse.
    got = at.marks([mark(400.0)])
    assert len(got) == 1
    assert got.attrs["dropped_stale_fix"] == 0


def test_a_verdict_the_tool_does_not_know_is_dropped_rather_than_pooled():
    got = at.marks([mark(100.0, verdict="meh"), mark(200.0, verdict="nice")])
    assert list(got.verdict) == ["nice"]
    assert got.attrs["dropped_unknown_verdict"] == 1


def test_marks_keep_the_route_they_were_tapped_on():
    # `travelled` restarts at zero on every reroute, so a window without its
    # sequence number points at the wrong road the moment a drive re-routes.
    got = at.marks([mark(500.0, route=0), mark(500.0, route=3)])
    assert sorted(got.seq) == [0, 3]


def test_a_drive_whose_every_mark_was_unusable_does_not_read_as_an_untapped_one(capsys):
    # These call for opposite responses — "tap next drive" versus "the tapping was
    # fine, something else is broken" — so the report must not render them with the
    # same sentence. It did: `marks()` returned empty either way and the empty
    # branch printed "no marks", which would have sent a driver out to repeat work
    # they had already done correctly.
    stale = at.marks([mark(400.0, fix_age=99.0), mark(800.0, fix_age=99.0)])
    pooled = pd.DataFrame(columns=["verdict", "model_score"])
    pooled.attrs.update(stale.attrs)
    at._mark_report(pooled, drives=1)
    said = capsys.readouterr().out
    assert "2 marks" in said and "none of them usable" in said
    assert "no marks." not in said

    at._mark_report(pd.DataFrame(columns=["verdict", "model_score"]), drives=1)
    assert "no marks." in capsys.readouterr().out


def test_separation_is_a_coin_when_the_score_says_nothing():
    # The null this whole exercise is testing against. Identical distributions
    # have to come out at 0.5 — including via the tie handling, since a score
    # that gives every road the same number is exactly the useless case.
    assert at.separation([5.0, 5.0, 5.0], [5.0, 5.0, 5.0]) == 0.5
    assert at.separation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 0.5


def test_separation_is_one_when_the_score_gets_every_pair_right():
    assert at.separation([8.0, 9.0], [2.0, 3.0]) == 1.0
    assert at.separation([2.0, 3.0], [8.0, 9.0]) == 0.0


def test_separation_is_not_carried_by_one_outlier():
    # The reason this is a rank statistic and not a difference of means: one
    # marked stretch that snapped to a 9.6 must not be able to certify a score
    # that got every other pair backwards.
    nice = [1.0, 1.0, 1.0, 40.0]
    dull = [2.0, 2.0, 2.0, 2.0]
    assert np.mean(nice) > np.mean(dull)        # the mean is fooled
    assert at.separation(nice, dull) == 0.25    # the rank statistic is not


def test_separation_with_only_one_verdict_is_not_a_number():
    assert np.isnan(at.separation([5.0], []))


def test_the_noise_floor_falls_as_marks_accumulate():
    # The separation number cannot be read without this beside it. Off five marks
    # each way a score that knows nothing reaches 0.82, so a 0.75 from a first
    # drive is not evidence of anything — read against 0.50 it looks like the
    # premise confirmed.
    assert at.null_ceiling(5, 5) > 0.75, "a tiny sample should have a high bar"
    assert at.null_ceiling(5, 5) > at.null_ceiling(30, 30) > at.null_ceiling(200, 200)
    # It approaches 0.5 from above and never reaches it: more marks lower the bar,
    # they never remove it.
    assert at.null_ceiling(2000, 2000) > 0.5


def test_the_same_separation_is_believed_at_one_sample_size_and_not_another():
    # The whole reason the floor is computed per sample rather than fixed. An
    # identical 0.60 is noise off a first drive and a real finding off ten, and a
    # single hard-coded threshold has to be wrong about one of them.
    nice = [6.0] * 3 + [4.0] * 2
    dull = [5.0] * 5
    small = at.separation(nice, dull)
    big = at.separation(nice * 40, dull * 40)
    assert small == pytest.approx(big), "same distributions, same separation"
    assert small < at.null_ceiling(len(nice), len(dull)), "not believable off one drive"
    assert big > at.null_ceiling(len(nice) * 40, len(dull) * 40), "believable off many"


def test_a_verdict_with_no_marks_of_one_kind_has_no_floor_to_clear():
    assert np.isnan(at.null_ceiling(0, 10))


def test_no_score_column_is_reported_as_that_and_not_as_a_road_problem(edges):
    # This was live: `main` loaded the graph without its `score` column and every
    # mark came back unscored, which the report announced as "didn't snap to a
    # road". A diagnostic that blames the road for a bug in the loader sends you
    # out to re-drive something that was never the problem.
    road = edges[edges.length_m > 1200].iloc[0]
    route = _route_along(road)
    got = at.attach_scenery(at.marks([mark(1000.0, speed=1.0)]),
                            [(route, None)], edges.drop(columns=["score"]))
    assert got.model_score.isna().all()
    assert got.attrs["no_score_column"] == 1
    assert "no_graph" not in got.attrs


def test_no_graph_at_all_is_reported_as_that(edges):
    road = edges[edges.length_m > 1200].iloc[0]
    got = at.attach_scenery(at.marks([mark(1000.0, speed=1.0)]),
                            [(_route_along(road), None)], None)
    assert got.attrs["no_graph"] == 1


# --- the contract with the app ------------------------------------------------

def test_the_analysis_reads_only_fields_it_declares(tmp_path):
    """A trace holding exactly REQUIRED_FIELDS is enough to produce every number.

    The other half of this is in ios/Tests/DriveTraceTests.swift, which asserts
    the app writes these names. Between them, renaming a field on either side
    fails a test rather than yielding traces that read as an empty drive — which
    is only discoverable after the driving is done.
    """
    minimal = []
    for row in fixes([(20.0, 10), (0.0, 20), (20.0, 10)]).to_dict("records"):
        minimal.append({k: v for k, v in row.items()
                        if k in at.REQUIRED_FIELDS["fix"]})
    records = [
        {k: 1.0 if k != "t" else "drive" for k in at.REQUIRED_FIELDS["drive"]},
        {"t": "route", "ts": 1.0, "seq": 0, "reason": "start", "km": 1.0,
         "minutes": 2.0, "coords": [[-71.0, 42.0], [-71.0, 42.01]]},
        *minimal,
        {"t": "end", "ts": 99.0, "reason": "arrived"},
    ]
    path = trace_file(tmp_path, records)

    loaded = at.load(path)
    parts = at.segments(loaded)
    assert len(parts) == 1
    steps = at.steps(parts[0][1])
    assert not steps.empty
    assert len(at.stops(steps)) == 1
    assert at.headline(steps.assign(assumed_ms=20.0))["km"] > 0


# --- against the real graph ---------------------------------------------------

def test_fixes_snap_to_the_road_they_were_driven_on(edges):
    # Snapping per fix, rather than walking the planned edge list, is what makes
    # the analysis survive a reroute and a graph rebuild between the drive and
    # the reading of it.
    road = edges[edges.highway == "motorway"].iloc[0]
    points = np.asarray(road.geometry.coords)[:3]

    steps = pd.DataFrame({
        "lon": points[:, 0], "lat": points[:, 1],
        "dist_m": 10.0, "dt_s": 1.0, "speed_ms": 10.0, "ts": 0.0,
    })
    labelled = at.attach_road_class(steps, edges)
    assert (labelled.highway == "motorway").all()
    assert labelled.assumed_ms.gt(0).all()


def test_a_fix_nowhere_near_a_road_is_left_unlabelled(edges):
    # A parking garage, a ferry, or a road MA has and OSM does not. Better
    # unpriced than priced against whatever happened to be nearest.
    steps = pd.DataFrame({
        "lon": [-71.0], "lat": [41.0],          # out in Rhode Island Sound
        "dist_m": [10.0], "dt_s": [1.0], "speed_ms": [10.0], "ts": [0.0],
    })
    labelled = at.attach_road_class(steps, edges)
    assert labelled.highway.isna().all()


def _route_along(edge, seq=0):
    """A `route` record following one real graph edge, as the app would write it."""
    coords = [[x, y] for x, y in edge.geometry.coords]
    return {"t": "route", "ts": 1_000_000.0, "seq": seq, "reason": "start",
            "km": edge.length_m / 1000.0, "minutes": edge.minutes,
            "coords": coords, "steps": []}


def test_a_marked_stretch_is_scored_off_the_graph_it_was_driven_on(edges):
    # End to end against real geometry: a verdict tapped part-way along a real
    # road has to come back carrying that road's own score, its class and its
    # name. Everything upstream of this is arithmetic on made-up numbers; this is
    # the step that decides whether a mark is about the right piece of tarmac.
    long_enough = edges[(edges.length_m > 1200) & edges.name.notna()
                        & edges.score.notna()]
    road = long_enough.iloc[0]
    route = _route_along(road)
    # Tapped 1 km in, at a walking pace so the reaction allowance is small and
    # the window sits comfortably inside this one edge.
    got = at.attach_scenery(at.marks([mark(1000.0, speed=1.0)]), [(route, None)], edges)

    assert len(got) == 1
    row = got.iloc[0]
    assert row.sampled > 0, "the window should have snapped to the road it is on"
    assert row.model_score == pytest.approx(road.score, abs=0.6)
    assert row.highway == road.highway
    assert row.road == road["name"]


def test_a_mark_on_a_route_the_trace_never_recorded_is_left_unscored(edges):
    # A `mark` naming route 7 with no route-7 record is a truncated or
    # out-of-order trace. Unscored is right; scoring it against route 0's line
    # would attribute the verdict to a road on the other side of the state.
    road = edges[edges.length_m > 1200].iloc[0]
    got = at.attach_scenery(at.marks([mark(500.0, route=7)]),
                            [(_route_along(road, seq=0), None)], edges)
    assert got.model_score.isna().all()
    assert (got.sampled == 0).all()


def test_the_score_separates_marks_that_were_placed_to_agree_with_it(edges):
    # The instrument checked against a known answer. Marks are planted to agree
    # with the model — "nice" on the highest-scoring roads, "dull" on the
    # lowest — so the whole chain from `travelled` through the window and the
    # snap has to report near-perfect separation. If this comes back at 0.5 the
    # plumbing is wrong, and no real drive would ever reveal it: a real drive
    # has no known answer to check against.
    usable = edges[(edges.length_m > 1200) & edges.score.notna()]
    pretty = usable.nlargest(6, "score")
    plain = usable.nsmallest(6, "score")
    assert pretty.score.min() > plain.score.max()

    scored = []
    for i, (_, road) in enumerate([*pretty.iterrows(), *plain.iterrows()]):
        verdict = "nice" if i < len(pretty) else "dull"
        route = _route_along(road, seq=0)
        scored.append(at.attach_scenery(
            at.marks([mark(1000.0, verdict=verdict, speed=1.0)]),
            [(route, None)], edges))

    pooled = pd.concat(scored, ignore_index=True)
    auc = at.separation(pooled.loc[pooled.verdict == "nice", "model_score"],
                        pooled.loc[pooled.verdict == "dull", "model_score"])
    assert auc == pytest.approx(1.0), f"planted agreement should be recovered, got {auc}"
