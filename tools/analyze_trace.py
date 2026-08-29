"""Read drive traces recorded by the iOS app and measure how wrong the clock is.

    .venv/bin/python tools/analyze_trace.py data/processed traces/*.ndjson

The *graph's* travel times are free-flow — `length / speed_limit`, with nothing
charged for lights, stop signs, turns or traffic (`SPEED_KMH` in graph.py). That
is known to be optimistic. This turns "known" into a number, and splits it into
the two separate defects it is made of, because they need different fixes:

  * **speed factor** — you do not average the posted limit between junctions.
    Measured as moving speed vs. the speed the graph assumed, per road class.
  * **junction cost** — the graph charges nothing for stopping. Measured as time
    spent stationary, per kilometre and per stop, and then split by *what* the
    car was stopped at: a mapped traffic signal, a stop sign, a turn, or nothing
    identifiable. That last split is the one that decides which fix is even
    possible — a signal is on that road every time you drive it and belongs in
    `graph.py`, while an unexplained stop is congestion that no static data will
    ever predict. Summing the two would bake one afternoon's traffic into the
    graph permanently.

Adding the speed factor and the junction cost together and calling it one fudge
factor would fit this drive and nothing else: they scale with different things
(distance vs. junction count), so a route with twice the intersections needs a
different correction, and only the split can tell you that.

Both corrections landed on 2026-08-15 and live in `router.py`, which applies
them when it loads the graph — so **the numbers here are the uncorrected
baseline, not what the app told the driver.** Nothing in this file reads
`SPEED_FACTOR` or `CONTROL_SECONDS`, deliberately: a measurement that already
had the correction folded in could not be used to re-fit it. For the corrected
error, and to re-fit either table from a new drive, run
`tools/fit_junction_cost.py` over the same traces.

There is a third, smaller question the trace can answer — whether the back roads
are slow because of the corners or because of the climb — reported as measured
speed by grade band.

Each trace is NDJSON, one record per line, written by `ios/Sources/DriveTrace.swift`:

    {"t":"drive","ts":..,"from":[lat,lon],"dest":[lat,lon],"pref":0.6,"weights":{..}}
    {"t":"route","ts":..,"seq":0,"reason":"start","km":..,"minutes":..,"coords":[[lon,lat],..]}
    {"t":"fix","ts":..,"lat":..,"lon":..,"alt":..,"travelled":..,"route":0,"joined":true,..}
    {"t":"phase","ts":..,"phase":"background"}
    {"t":"end","ts":..,"reason":"arrived"}

`travelled` is metres along the route line the fix was matched to, so the slope
of `travelled` against `ts` is real speed at a known place on a known road. That
is the whole measurement; everything below is bookkeeping around it.

Every exclusion this makes is reported, because the dangerous failure is silent:
a phone that stops reporting while the car is stationary turns every stop into a
gap, the junction cost reads zero, and that looks like good news. So the wall
clock is printed next to the measured time, and unaccounted minutes next to
both.

The per-class breakdown needs the built graph (`graph_edges.parquet`); the
signals-vs-traffic split additionally needs the OSM extract (found in data/raw,
or set SCENIC_PBF). Without either, the rest still works — pass `-` as the data
directory.
"""

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from common import CRS_METERS  # noqa: E402

# The fields this file actually reads, per record type.
#
# Asserted from both ends — `test_the_analysis_reads_only_fields_it_declares`
# here, and `test_the_recorded_fields_are_the_ones_the_analysis_reads` in
# ios/Tests/DriveTraceTests.swift. A field renamed on either side then fails a
# test, instead of producing traces that read as an empty drive on a laptop
# after the driving is already done.
REQUIRED_FIELDS = {
    "drive": {"t", "ts", "pref"},
    "route": {"t", "ts", "seq", "reason", "km", "minutes", "coords", "steps"},
    "fix": {"t", "ts", "lat", "lon", "acc", "alt", "off", "travelled", "joined", "route"},
    "mark": {"t", "ts", "verdict", "route", "travelled", "joined", "spd"},
    "phase": {"t", "ts", "phase"},
    "end": {"t", "ts", "reason"},
}

# A `route` record may also carry the request it answered — `req_lat`, `req_lon`,
# `req_heading`, `req_pref` — which is what makes "was the server right to send
# this?" answerable from a drive rather than a matter of opinion. Optional, and
# so deliberately not in REQUIRED_FIELDS: the opening route answers no request,
# `usableHeading` withholds a heading from a car too slow to have a trustworthy
# course, and every trace recorded before 2026-08-26 has none of them.

# The vocabulary of `mark.verdict`, and the trace's contract with the app —
# `SceneryVerdict` in ios/Sources/DriveTrace.swift asserts the same two strings
# from its own side. Anything else in a trace is reported and dropped rather
# than silently pooled into one of these.
VERDICTS = ("nice", "dull")

# --- what counts as noise ----------------------------------------------------
# A pair of fixes is only usable if the phone was confident about both, they are
# close together in time, and the match moved forwards. Every threshold here
# throws data away, so each one is a claim about what would otherwise be wrong.

# Worst horizontal accuracy to trust, in metres. LocationManager already drops
# anything above 65 m; this is tighter because a 40 m fix is fine for "which
# road" and useless for "how fast".
MAX_ACCURACY_M = 25.0

# Longest gap between fixes we'll treat as continuous driving, in seconds. A
# longer one means the app was backgrounded, the tunnel ate the signal, or the
# phone locked — and the distance across it is real but the *speed* across it is
# an average over unknown conditions.
MAX_GAP_S = 20.0

# Below this, the car is stopped rather than moving (m/s). 1.0 m/s is 2.2 mph.
#
# It was 0.5, on the reasoning that this is "faster than GPS drift at a red
# light". That premise is wrong: `travelled` is a per-fix map-matched position,
# and its along-track jitter at a standstill is metres, so a parked car produces
# apparent per-step speeds of several m/s. Measured against the smoothing window
# below, 0.5 recovered a 45 s stop as 3 runs totalling 24 s; 1.0 recovers it as
# 1 run of 40 s, while a genuine 3 m/s crawl still reads as moving throughout.
STOPPED_MS = 1.0

# A stop has to last this long to be one, in seconds. Shorter is a rolling stop
# or two jittery fixes, and counting those would inflate the junction cost.
MIN_STOP_S = 3.0

# Beyond this a stationary run is the driver parking, not the road stopping
# them, and it is excluded from the junction cost, in seconds.
#
# The junction cost this tool exists to fit is time the *road* takes: a signal,
# a stop sign, a queue. A driver who pulls over for lunch, for fuel, or to take
# a photograph is not being delayed by the junction they happen to be standing
# at, and `stops()` cannot tell the two apart from the speed trace alone —
# both are a car at 0 m/s.
#
# Measured on the 2026-08-22 batch, which is why this exists: two stationary
# runs, of 416 s and 761 s, carried 19.6 of the 34.2 minutes of "stopped" time
# across six drives. Both landed in the unexplained bucket — the one the README
# is emphatic must never reach graph.py — and between them they moved one
# drive's headline error from +32% to +59%. Left in, they would have been
# fitted as junction cost and baked into every route in the state.
#
# 300 s and not tighter because the cost of being wrong is asymmetric. Excluding
# a real 4-minute queue loses one sample from a fit that pools hundreds;
# including one 12-minute lunch stop poisons the fit outright. The longest
# genuinely road-imposed stop worth expecting — a freight crossing, a drawbridge,
# a lift bridge — runs to about five minutes, so this sits just past it. Every
# excluded run is printed with its duration and position, because the threshold
# is a judgement and the reader has to be able to overrule it.
PARKED_S = 300.0

# How many consecutive steps the stopped/moving decision is made over.
#
# Not per-step, because at a standstill the per-step delta IS the along-track
# noise: with 2 m of it, half the steps of a parked car read as moving and the
# run shatters into fragments that MIN_STOP_S then discards. Measured on a 45 s
# stop, the per-step test found 3 fragments totalling 10 s; over this window it
# is one stop of 40 s. The window measures net displacement over its own span,
# which is unbiased on a moving car (exact on a constant ramp) and averages the
# jitter down by the span at a standstill.
#
# 5 and not wider: the window cannot resolve a stop shorter than itself, and
# stop-and-go traffic is exactly the congestion signal this tool needs to see.
# At 9, the five 10-second stops of a queue smeared into a single 6-second one;
# at 5 all five are found. Grid-searched over four synthetic drives — one long
# stop, stop-and-go, free-flowing, and a 3 m/s crawl — 5 paired with
# STOPPED_MS = 1.0 is the only setting that gets the stop *count* right in both
# stop cases while inventing none in either moving case.
#
# Only the *classification* is smoothed. `dist_m` and `dt_s` stay raw, so
# distance and time totals are untouched — median-smoothing `travelled` itself
# was tried first and cost 8% of the drive's distance, clipping the ramp at
# every stop-start.
#
# The residual is a known under-count: the window straddles each stop's edges,
# so a stop reads roughly STOP_SMOOTH/2 seconds shorter than it was. Stop
# durations are therefore a floor. That is the right direction to be wrong in
# for a junction penalty, and far better than the 4 s the old clamped distance
# reported for the same 45 s light.
STOP_SMOOTH = 5

# How far a fix may be from a road before we refuse to say which road it was on.
MAX_SNAP_M = 30.0

# How far a fix may sit from the route line before its match is not to be
# trusted for distance. `progress()` in ios/Sources/Geo.swift bounds the match
# backwards (via notBefore) but not forwards, so on a route that runs parallel
# to itself — or shares a corridor with a highway — one fix can match far ahead
# of the driver. The recorded `off` says so plainly, in metres, and used to be
# printed and then ignored; the forward jump entered `dist_m` as genuine
# distance covered in one second, inflating that step's speed, its class's
# measured_kmh, and the factor prescribed for SPEED_KMH. Because such a step is
# *fast* rather than stopped, no stop-detection guard saw it either. 60 m
# mirrors NavigationModel.offRouteMeters: past that the app itself calls the
# driver off route and reroutes.
MAX_OFF_ROUTE_M = 60.0

# --- what a scenery mark refers to -------------------------------------------
# A `mark` says the driver tapped at a moment. It does not say which road they
# meant, and the two are not the same thing for three compounding reasons: the
# tap is later than what prompted it, the position it carries is the last GPS fix
# rather than that instant, and beauty is a property of a stretch rather than of
# a point. Every constant below is one of those, made explicit so it can be
# argued with — and `_mark_report` re-runs its own headline across a range of
# them, because a conclusion that only holds at one window size is not one.

# How long after seeing something a driver taps, in seconds.
#
# In seconds and not metres on purpose. Three seconds is 40 m through a village
# and 110 m on a highway, so a fixed metre offset is necessarily wrong at one end
# of the range; the mark carries `spd` precisely so this can be converted at the
# speed the car was actually doing.
MARK_REACTION_S = 3.0

# How much road a mark is taken to be about, in metres, ending at the
# reaction-corrected anchor.
#
# 400 m because that is the length of the chunks `score.py` scores, so this is
# one scored chunk's worth of road — the granularity of the thing being judged.
# Picking it to match the model rather than the driver is deliberate: a window
# finer than the score cannot disagree with it about anything, and a much coarser
# one averages a beautiful mile in with the strip mall at the end of it.
MARK_WINDOW_M = 400.0

# How far apart the window is sampled along the route line, in metres. Samples
# are spaced by distance rather than taken per GPS fix so that a minute spent at
# a red light does not weight that junction 60 times over.
MARK_SAMPLE_M = 25.0

# How stale the position behind a mark may be, in seconds.
#
# A mark carries the last GPS fix, not the instant of the tap, and `travelled` is
# that same fix's match — so if the stream had stalled, the anchor is wherever the
# car was when it stalled and the verdict gets charged to that road instead of the
# one the driver was looking at. At 1 Hz the normal age is under a second; five is
# already a stream that stopped, and at highway speed it is 140 m of error, which
# is a third of the window.
#
# Found by driving the simulator, where a parked phone produced marks aged 32 s
# and the analysis read them as verdicts on the route's first metre without a
# murmur — the silent failure this file's other thresholds all exist to prevent.
MARK_MAX_FIX_AGE_S = 5.0

# How close a stop has to be to a mapped signal or stop sign to be blamed on it.
# Generous, because the car stops at the back of a queue, not at the stop line.
CONTROL_NEAR_M = 45.0

# ...and to a maneuver, to be blamed on the turn instead. Tighter: a maneuver
# point is where the route changes road, which is the junction itself.
MANEUVER_NEAR_M = 35.0

# Which of router.py's steps are actually junctions a car can stop at. It also
# emits "Head <compass> on X" at the trip start, "Continue on X" where a leg
# seam is under 20 degrees (the road merely changes name), and "Arrive at your
# destination" at the end — none of which is one. Counting them blamed idling at
# the start point, whose coordinate *is* step 0, on "a turn", and _stop_report
# then folded that into the structural share it describes as predictable from
# OSM — corrupting the signals-vs-congestion split the exercise turns on.
#
# Read off the step's `type`, which router.py sends on every step and
# DriveTrace records, rather than off the English. Matching prose was already
# wrong once: rotaries, exits, merges and forks got their own wordings and
# stopped matching a prefix list written against the old ones, so every stop at
# a rotary silently became "unexplained — traffic" and inflated the congestion
# share that CONTROL_SECONDS is fitted around.
MANEUVER_TYPES = {"turn", "roundabout", "exit", "merge", "fork"}

# The fallback for traces recorded before the app logged `type`. Covers the
# current vocabulary as well as the old one — "Keep left", "Merge onto X",
# "Take the 2nd exit", "Make a U-turn" — because a trace can predate the type
# and still postdate the wording.
TURN_PREFIXES = ("Turn", "Slight", "Sharp", "Keep", "Merge", "Take", "Make")


def is_maneuver(step) -> bool:
    """Whether a route step is a junction, not a start, a name change or an end.

    `"unknown"` counts as *no* type, not as an unrecognised one. The app decodes
    a missing `type` to `ManeuverType.unknown`, whose rawValue is the string
    "unknown", and older builds wrote that into the trace — so treating any
    non-empty string as authoritative suppressed the fallback below for every
    step of such a drive, made `maneuvers` empty, and had `classify_stops` book
    every stop at every junction as "unexplained — traffic". That inflates the
    congestion share this file exists to separate out, which is the share
    CONTROL_SECONDS is fitted against. The app now emits "" instead; this keeps
    the traces already on disk readable.
    """
    kind = str(step.get("type", "") or "")
    if kind and kind != "unknown":
        return kind in MANEUVER_TYPES
    return str(step.get("instruction", "")).startswith(TURN_PREFIXES)

# Altitude noise on a phone is metres, and one step covers ~15 m of road. Rise
# over run with a run that short is almost entirely noise: ±1.5 m of residual
# error over 20 m reads as a 7% grade, so a dead-flat drive splits evenly into
# confident-looking uphill and downhill. This is exactly the trap curvature fell
# into (see "How scoring works" in the README), and the fix is the same one —
# measure between points far enough apart that the signal beats the error.
#
# So altitude is median-smoothed over ALTITUDE_SMOOTH samples, and then grade is
# rise over a GRADE_BASELINE_M run rather than over a single step. 200 m brings
# the same ±1.5 m down to 0.75%, comfortably inside the band below.
ALTITUDE_SMOOTH = 15
GRADE_BASELINE_M = 200.0
# Steepness (m per m) dividing downhill / level / uphill.
GRADE_BAND = 0.02

# Every column `steps` produces. Named once so the empty case can carry the same
# shape as the full one — see the early return in `steps`.
STEP_COLUMNS = ["ts", "dt_s", "dist_m", "grade", "lat", "lon", "acc", "off_m",
                "speed_ms", "stopped"]


# --- reading -----------------------------------------------------------------

def load(path):
    """Records from one trace file, skipping any line that got truncated.

    A drive can end in a jettison or a dead battery, so the last line is not
    guaranteed to be whole. That is exactly why the format is one JSON object
    per line — the loss is bounded to a single fix.
    """
    records = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"  ! {Path(path).name}: skipped a truncated final line")
    return records


def segments(records):
    """Split a drive into (route record, its fixes) pairs.

    One per route followed: the planned one, plus one per reroute. They have to
    stay apart because `travelled` restarts at zero on each new line — read as
    one series it looks like the car jumped backwards across the seam.
    """
    routes = {r["seq"]: r for r in records if r["t"] == "route"}
    fixes = [r for r in records if r["t"] == "fix"]
    out = []
    for seq, route in sorted(routes.items()):
        mine = [f for f in fixes if f.get("route") == seq]
        if mine:
            out.append((route, pd.DataFrame(mine)))
    return out


def steps(fixes):
    """Consecutive pairs of fixes, as one row each: how far, how long, how fast.

    Returns a frame with `dist_m`, `dt_s`, `speed_ms` and the position the step
    happened at, having dropped the pairs that cannot mean anything (see the
    thresholds above). `dist_m` comes from `travelled` — distance along the
    matched route line, not straight-line movement — so a hairpin is measured
    the length the car actually drove.
    """
    f = fixes.sort_values("ts").reset_index(drop=True)
    # Only fixes taken once the driver was on the line: before that the match
    # lands wherever the route happens to pass nearest, which is not where they
    # are, and the "distance" between two such matches is fiction.
    if "joined" in f:
        f = f[f["joined"].fillna(True).astype(bool)].reset_index(drop=True)
    if len(f) < 2:
        # Same columns as a full one, not a bare frame. A route followed for a
        # single fix is ordinary — a reroute a few seconds before arrival — and
        # everything downstream reads these by name, so a short-changed frame
        # takes the whole analysis down with it after the drive is over.
        return pd.DataFrame(columns=STEP_COLUMNS)

    grade_per_fix = _grade(f)

    out = pd.DataFrame({
        "ts": f["ts"].to_numpy()[:-1],
        "dt_s": np.diff(f["ts"].to_numpy()),
        # Signed, and deliberately NOT clamped at zero. The recorded match is
        # per-fix, so GPS jitter nudges it backwards a metre or two at a
        # standstill — and clamping that at zero *rectifies* zero-mean noise
        # into a positive drift: every backwards wobble becomes 0 while every
        # forwards one is kept as real distance, so a parked car accumulates
        # metres. Measured on a 45 s stop with 2.5 m of along-track noise, the
        # clamped form reported 4 s of stopped time instead of 45 and inflated
        # distance by 4.7% — with no gap, no inaccurate fix and no unaccounted
        # wall clock, so every guard in this file still called it a clean drive.
        # Left signed the wobbles cancel: a stop sums to ~0 m, its `speed_ms`
        # goes slightly negative (still below STOPPED_MS, so still "stopped"),
        # and the distance total stays honest.
        "dist_m": np.diff(f["travelled"].to_numpy()),
        "grade": grade_per_fix[:-1],
        "lat": f["lat"].to_numpy()[:-1],
        "lon": f["lon"].to_numpy()[:-1],
        "acc": np.maximum(f["acc"].to_numpy()[:-1], f["acc"].to_numpy()[1:]),
        # Both ends, for the same reason `acc` takes both: a step is only as
        # trustworthy as its worse end. Reading `off` from the first fix alone
        # kept the step *into* a bad match and dropped the step back out of it,
        # so a fix matched 240 m up a parallel corridor contributed its whole
        # forward jump and none of the correction — 340 m reported for 120 m
        # driven. One-sided inflation is worse than no filter at all.
        "off_m": np.maximum(f["off"].to_numpy()[:-1], f["off"].to_numpy()[1:]),
    })
    # Gaps are dropped but not forgotten. A dropout is the one exclusion that can
    # quietly delete the thing being measured: if the phone stops reporting while
    # the car is stationary, every stop becomes a gap, the junction cost reads
    # zero, and nothing in the output says so. Carried out as attributes so
    # `report` can put the discarded time on screen next to the answer.
    gaps = out[out.dt_s > MAX_GAP_S]
    kept = out[(out.dt_s > 0) & (out.dt_s <= MAX_GAP_S) & (out.acc <= MAX_ACCURACY_M)
               & (out.off_m <= MAX_OFF_ROUTE_M)]
    kept = kept.reset_index(drop=True)
    # Net displacement over a centred window, divided by that window's own
    # elapsed time — see STOP_SMOOTH. This is the column every stopped/moving
    # test reads (`stops`, `by_road_class`, `by_grade`), so they all agree; the
    # raw dist_m/dt_s beside it stay untouched and carry the totals.
    window = dict(window=STOP_SMOOTH, center=True, min_periods=1)
    kept["speed_ms"] = (kept.dist_m.rolling(**window).sum()
                        / kept.dt_s.rolling(**window).sum())
    # ...then widened by the window's own half-width, because that is exactly
    # how far the blur reaches. A step at the edge of a stop sees motion inside
    # its window and reads as moving, which both shortened the stop and let its
    # stationary seconds leak into the speed factor — measured, one 60 s stop
    # dragged a road's factor from 1.00 to 0.94, i.e. the junction cost bleeding
    # into the per-km number the split exists to keep it out of. Every consumer
    # reads this one column, so `stops`, `by_road_class` and `by_grade` cannot
    # disagree about what counted as stopped.
    edge = STOP_SMOOTH // 2
    kept["stopped"] = (kept.speed_ms < STOPPED_MS).rolling(
        window=2 * edge + 1, center=True, min_periods=1).max().astype(bool)
    kept = kept[STEP_COLUMNS]
    kept.attrs["gap_count"] = len(gaps)
    kept.attrs["gap_seconds"] = float(gaps.dt_s.sum())
    kept.attrs["gap_spans"] = list(zip(gaps.ts.to_numpy(),
                                       (gaps.ts + gaps.dt_s).to_numpy()))
    kept.attrs["dropped_accuracy"] = int((out.acc > MAX_ACCURACY_M).sum())
    kept.attrs["dropped_offroute"] = int((out.off_m > MAX_OFF_ROUTE_M).sum())
    # Wall clock from the first usable fix to the last, so `report` can say how
    # much of the drive the measured time actually accounts for. Without it a
    # drive with ten minutes of dropouts reports a shorter, tidier drive than
    # the one that happened, and the headline ratio looks fine.
    kept.attrs["elapsed_s"] = float(f["ts"].iloc[-1] - f["ts"].iloc[0])
    return kept


def _grade(f):
    """Road grade at each fix: rise over a GRADE_BASELINE_M run, or NaN.

    Measured forwards over a fixed *distance*, never between adjacent fixes —
    see the note on GRADE_BASELINE_M. The last stretch of the drive has no
    200 m of road ahead of it and comes back NaN rather than being measured over
    whatever happens to be left.
    """
    if "alt" not in f or f["alt"].isna().all():
        return np.full(len(f), np.nan)

    altitude = f["alt"].rolling(ALTITUDE_SMOOTH, center=True, min_periods=1).median().to_numpy()
    # Monotonic distance to look the baseline up against: the recorded match can
    # wobble backwards a metre or two, and searchsorted needs a sorted array.
    along = np.maximum.accumulate(f["travelled"].to_numpy())
    ahead = np.minimum(np.searchsorted(along, along + GRADE_BASELINE_M), len(along) - 1)
    run = along[ahead] - along
    with np.errstate(invalid="ignore", divide="ignore"):
        grade = (altitude[ahead] - altitude) / run
    return np.where(run >= GRADE_BASELINE_M, grade, np.nan)


# --- joining to the graph ----------------------------------------------------

def attach_road_class(steps_df, edges):
    """Label each step with the road it happened on, and that road's assumed speed.

    Snapped per *fix* rather than by walking the route's edge list, so it still
    works across reroutes and does not depend on the graph being byte-identical
    to the one that planned the drive — which it will not be after any rebuild.
    """
    import geopandas as gpd
    from shapely import STRtree

    if steps_df.empty:
        return steps_df.assign(highway=None, assumed_ms=np.nan)

    points = gpd.GeoSeries(
        gpd.points_from_xy(steps_df.lon, steps_df.lat), crs=4326
    ).to_crs(CRS_METERS)
    roads = edges.to_crs(CRS_METERS)
    tree = STRtree(roads.geometry.values)

    nearest = tree.nearest(points.values)
    distance = roads.geometry.values[nearest].distance(points.values)

    assumed_kmh = (roads["length_m"].to_numpy() / 1000.0) / (roads["minutes"].to_numpy() / 60.0)
    out = steps_df.copy()
    out["highway"] = roads["highway"].to_numpy()[nearest]
    out["assumed_ms"] = assumed_kmh[nearest] / 3.6
    # Too far from any road to say which one — a parking garage, a tunnel, a
    # ferry, or a road MA has and OSM does not.
    out.loc[distance > MAX_SNAP_M, ["highway", "assumed_ms"]] = [None, np.nan]
    return out


def marks(records, window_m=MARK_WINDOW_M, reaction_s=MARK_REACTION_S):
    """The driver's verdicts, each resolved to the stretch of road it is about.

    Returns a frame of one row per usable mark with `verdict` and the window
    `[lo_m, hi_m]` of metres along its route line that the verdict is taken to
    refer to, plus `seq` so the caller knows *which* line — `travelled` restarts
    at zero on every reroute, so a window without its sequence number points at
    the wrong road as soon as a drive re-routes once.

    The window ends short of where the tap happened, by the distance the car
    covered while the driver was reacting, and extends `window_m` back from
    there. Both ends move; a driver tapping at 100 km/h is describing road they
    passed 80 m ago, and charging their verdict to the road under the tap would
    credit the score for whatever came *next*.

    Exclusions are counted in `.attrs`, not dropped quietly — the same rule the
    rest of this file follows, and it matters more here than anywhere: marks are
    scarce (tens per drive, against thousands of fixes), so losing a third of
    them to something structural has to be visible rather than inferred from a
    number that looks a bit thin.
    """
    rows, unjoined, unknown_verdict, no_anchor, stale = [], 0, 0, 0, 0
    # Fixes, indexed by route, so a mark with no usable `spd` can fall back to
    # what the car was doing around it.
    fixes = pd.DataFrame([r for r in records if r["t"] == "fix"])

    for m in (r for r in records if r["t"] == "mark"):
        verdict = m.get("verdict")
        if verdict not in VERDICTS:
            unknown_verdict += 1
            continue
        # A tap before the driver reached the route line has no anchor: its
        # `travelled` is a match onto wherever the line happened to pass
        # nearest, which is not where they were. The app records these anyway
        # and flags them, leaving the decision here — and the decision is to
        # drop them, because there is no road to attribute them to.
        if not m.get("joined", False):
            unjoined += 1
            continue
        anchor = float(m.get("travelled", -1))
        if not np.isfinite(anchor) or anchor < 0:
            no_anchor += 1
            continue
        # The position, and therefore the anchor, is the last fix's. If that fix
        # is old the stream had stalled, and the verdict would be charged to
        # wherever the car was when it stopped reporting rather than to the road
        # the driver was looking at. A missing `fix_age` is an older trace, not a
        # stale one — those predate the field and are let through.
        age = float(m.get("fix_age", 0.0) or 0.0)
        if np.isfinite(age) and age > MARK_MAX_FIX_AGE_S:
            stale += 1
            continue

        speed = float(m.get("spd", -1))
        if not np.isfinite(speed) or speed < 0:
            # CoreLocation had no opinion. Fall back to what the fixes around
            # this moment were doing, and to a standstill if even that is
            # unavailable — a reaction distance of zero is the conservative
            # error here, since it attributes the verdict to road the driver
            # had definitely reached rather than to road they may not have.
            speed = _speed_near(fixes, m)

        hi = max(0.0, anchor - speed * reaction_s)
        rows.append({
            "ts": m["ts"],
            "verdict": verdict,
            "seq": int(m.get("route", 0)),
            "anchor_m": anchor,
            "speed_ms": speed,
            "lo_m": max(0.0, hi - window_m),
            "hi_m": hi,
            "lat": m.get("lat", np.nan),
            "lon": m.get("lon", np.nan),
            "fix_age": m.get("fix_age", np.nan),
        })

    out = pd.DataFrame(rows, columns=["ts", "verdict", "seq", "anchor_m", "speed_ms",
                                      "lo_m", "hi_m", "lat", "lon", "fix_age"])
    out.attrs["dropped_unjoined"] = unjoined
    out.attrs["dropped_unknown_verdict"] = unknown_verdict
    out.attrs["dropped_no_anchor"] = no_anchor
    out.attrs["dropped_stale_fix"] = stale
    return out


def _speed_near(fixes, mark, within_s=5.0):
    """Speed around a mark from the fixes either side of it, or 0 if there are none."""
    if fixes.empty or "travelled" not in fixes or "ts" not in fixes:
        return 0.0
    near = fixes[(fixes["route"] == mark.get("route", 0))
                 & (fixes["ts"] - mark["ts"]).abs().le(within_s)]
    if len(near) < 2:
        return 0.0
    near = near.sort_values("ts")
    span_s = float(near["ts"].iloc[-1] - near["ts"].iloc[0])
    span_m = float(near["travelled"].iloc[-1] - near["travelled"].iloc[0])
    if span_s <= 0 or span_m < 0:
        return 0.0
    return span_m / span_s


def road_index(edges):
    """The projected roads and a spatial index over them, ready to snap against.

    Split out of `attach_scenery` so it can be built once and reused. It is the
    expensive half by a wide margin — reprojecting 400k geometries and building an
    STRtree over them — and `report` snaps the same marks four times, once for the
    headline window and once per window in the sensitivity check. Rebuilding it
    each time made the answer no better and the run four times longer.

    Returns None when there is nothing to snap against, so callers can tell
    "no graph" from "a graph with no scores in it" and say which.
    """
    from shapely import STRtree

    if edges is None or "score" not in edges:
        return None
    roads = edges.to_crs(CRS_METERS)
    return {
        "geometry": roads.geometry.values,
        "tree": STRtree(roads.geometry.values),
        "score": roads["score"].to_numpy(),
        "highway": roads["highway"].to_numpy(),
        "name": (roads["name"].to_numpy() if "name" in roads
                 else np.full(len(roads), None, dtype=object)),
    }


def attach_scenery(marks_df, parts, edges, index=None):
    """What the model thought of each marked stretch: `model_score`, 0-10.

    The window is sampled *along the route line the drive was following*, every
    `MARK_SAMPLE_M`, and each sample snapped to the graph. Sampling the line
    rather than the driver's own fixes is what makes this distance-weighted: fixes
    arrive at 1 Hz, so a minute at a red light would otherwise weight that one
    junction sixty times over and a marked stretch driven briskly would count for
    less than the traffic light at the end of it.

    `travelled` is measured by the phone (`progress()` in ios/Sources/Geo.swift,
    in per-segment local frames) and re-measured here as distance along the same
    line projected into `CRS_METERS`. They are not the same computation, but
    `CRS_METERS` is conformal and its scale error across Massachusetts is a small
    fraction of a percent — metres over a 400 m window, against a window whose
    own length is a judgement call to within a factor of two.

    Snapping to the graph rather than reading the route's own edge scores is
    deliberate, and for the reason `attach_road_class` gives: the graph will not
    be byte-identical after a rebuild, and a mark should stay readable against
    whatever the score says *now* — that is the whole point of keeping it.
    """
    import geopandas as gpd
    from shapely.geometry import LineString

    blank = marks_df.assign(model_score=np.nan, highway=None, road=None,
                            sampled=0)
    if marks_df.empty:
        return blank
    # Why there is no score, when there is none. These three all used to return
    # the same all-NaN frame, and the report then blamed the *road* — "didn't
    # snap to a road" — for a graph that had simply been loaded without its
    # `score` column. A diagnostic that accuses the data for a bug in the loader
    # is worse than no diagnostic: it sends you out to re-drive a road that was
    # never the problem.
    if edges is None and index is None:
        blank.attrs["no_graph"] = len(marks_df)
        return blank
    if index is None:
        index = road_index(edges)
    if index is None:
        blank.attrs["no_score_column"] = len(marks_df)
        return blank

    lines = {}
    for route, _ in parts:
        coords = route.get("coords") or []
        if len(coords) >= 2:
            # The trace stores [lon, lat]; project to metres so distance along
            # the line is metres.
            lines[int(route["seq"])] = LineString(
                gpd.points_from_xy([c[0] for c in coords], [c[1] for c in coords],
                                   crs=4326).to_crs(CRS_METERS)
            )
    if not lines:
        blank.attrs["no_route_line"] = len(marks_df)
        return blank

    geometry, tree = index["geometry"], index["tree"]
    score, highway, name = index["score"], index["highway"], index["name"]

    scores, classes, names, counts = [], [], [], []
    for row in marks_df.itertuples():
        line = lines.get(row.seq)
        if line is None:
            scores.append(np.nan); classes.append(None); names.append(None)
            counts.append(0)
            continue
        lo = min(max(0.0, row.lo_m), line.length)
        hi = min(max(lo, row.hi_m), line.length)
        # At least one sample even where the window collapsed — a mark tapped in
        # the first few metres of a route, or while stopped at its very end.
        n = max(2, int(np.ceil((hi - lo) / MARK_SAMPLE_M)) + 1)
        points = [line.interpolate(d) for d in np.linspace(lo, hi, n)]
        near = tree.nearest(points)
        distance = np.array([geometry[i].distance(p)
                             for i, p in zip(near, points)])
        on_road = distance <= MAX_SNAP_M
        if not on_road.any():
            scores.append(np.nan); classes.append(None); names.append(None)
            counts.append(0)
            continue
        keep = near[on_road]
        scores.append(float(np.nanmean(score[keep])))
        counts.append(int(on_road.sum()))
        # The road the driver would say they were on: the class and name that
        # cover most of the samples, not the one under the tap.
        classes.append(pd.Series(highway[keep]).mode().iat[0])
        titles = pd.Series([n for n in name[keep] if n]).mode()
        names.append(titles.iat[0] if not titles.empty else None)

    out = marks_df.copy()
    out["model_score"] = scores
    out["highway"] = classes
    out["road"] = names
    out["sampled"] = counts
    return out


def traffic_control(pbf_path, cache_path):
    """Signal, stop-sign and give-way nodes from the OSM extract, cached.

    `graph.py` reads only ways, so none of this reaches the routing graph — which
    is exactly why every junction currently costs nothing. Read here so a stop
    can be blamed on a mapped control rather than on traffic, which is the whole
    difference between a fix that belongs in `graph.py` and one that needs a
    traffic feed. The node pass over a state-sized PBF takes about a minute, so
    the answer is cached beside the processed data.
    """
    import geopandas as gpd

    if cache_path and cache_path.exists():
        return gpd.read_parquet(cache_path)
    if not pbf_path or not Path(pbf_path).exists():
        return None

    import osmium

    print(f"reading traffic control nodes from {Path(pbf_path).name} (about a minute)...")
    wanted = {"traffic_signals", "stop", "give_way", "mini_roundabout"}
    rows = []

    class Handler(osmium.SimpleHandler):
        def node(self, n):
            kind = n.tags.get("highway")
            if kind in wanted:
                rows.append({"kind": kind, "lon": n.location.lon, "lat": n.location.lat})

    Handler().apply_file(str(pbf_path))
    control = gpd.GeoDataFrame(
        rows, geometry=gpd.points_from_xy([r["lon"] for r in rows],
                                          [r["lat"] for r in rows]), crs=4326)
    if cache_path:
        control.to_parquet(cache_path)
        print(f"  cached {len(control):,} nodes to {cache_path.name}")
    return control


def classify_stops(stops_df, control, maneuvers):
    """Blame each stop on a signal, a stop sign, a turn, or nothing.

    This is the split the whole exercise turns on. A stop at a mapped signal is a
    *structural* cost — it is on that road every time you drive it, it belongs in
    `graph.py`, and no traffic feed is needed to know about it. A stop at nothing
    in particular is congestion, or a junction OSM is missing. Adding those two
    together and calling the total "junction cost" would bake one afternoon's
    traffic into the graph permanently.
    """
    import geopandas as gpd

    out = stops_df.copy()
    out["cause"] = "unexplained"
    if out.empty:
        return out

    points = gpd.GeoSeries(gpd.points_from_xy(out.lon, out.lat),
                           crs=4326).to_crs(CRS_METERS)

    # Maneuvers first, so a mapped control can overwrite them: a signal you can
    # see in the data is a better explanation than "there was a turn here".
    if maneuvers is not None and len(maneuvers):
        turns = gpd.GeoSeries(
            gpd.points_from_xy([m["lon"] for m in maneuvers],
                               [m["lat"] for m in maneuvers]), crs=4326).to_crs(CRS_METERS)
        near, distance = _nearest(points, turns)
        out.loc[distance <= MANEUVER_NEAR_M, "cause"] = "maneuver"

    if control is not None and len(control):
        nodes = control.to_crs(CRS_METERS)
        near, distance = _nearest(points, nodes.geometry)
        kinds = nodes["kind"].to_numpy()[near]
        close = distance <= CONTROL_NEAR_M
        out.loc[close, "cause"] = kinds[close]
    return out


def _nearest(points, targets):
    """(index of nearest target, distance) for each point. Both projected."""
    from shapely import STRtree

    tree = STRtree(targets.values)
    near = tree.nearest(points.values)
    return near, targets.values[near].distance(points.values)


# --- the two measurements ----------------------------------------------------

def stops(steps_df):
    """Runs of stationary time, one row per stop.

    This is the junction cost the graph charges nothing for. Reported separately
    from the speed factor because it scales with the number of intersections on
    a route, not with its length — so the two cannot be folded into one number
    without making every route of a different shape wrong.
    """
    if steps_df.empty:
        return _stop_frame([])

    stopped = steps_df.stopped.to_numpy()
    # Adjacent *rows* are not adjacent *time*. `steps` has already deleted every
    # row over MAX_GAP_S or MAX_ACCURACY_M and `report` concatenates the route
    # segments, so two rows can be minutes apart — and a run scanned over rows
    # alone then merged a stop before a dropout with the stop after it into one
    # long stop, at the first one's coordinates. That halves the junction count,
    # which is the denominator of the per-junction penalty this whole tool
    # exists to produce, and hands the second stop to classify_stops at the
    # wrong junction. A row continues the previous one only if no time is
    # missing between them.
    ts = steps_df.ts.to_numpy()
    spans = steps_df.dt_s.to_numpy()
    continues = np.r_[False, np.isclose(ts[1:], ts[:-1] + spans[:-1], atol=0.5)]
    runs, start = [], None
    for i, is_stopped in enumerate(stopped):
        if is_stopped:
            if start is not None and not continues[i]:
                runs.append((start, i))       # a gap split one stop into two
                start = i
            elif start is None:
                start = i
        elif start is not None:
            runs.append((start, i)); start = None
    if start is not None:
        runs.append((start, len(stopped)))

    rows = []
    for a, b in runs:
        seconds = float(steps_df.dt_s.iloc[a:b].sum())
        if seconds >= MIN_STOP_S:
            rows.append({"start_ts": float(steps_df.ts.iloc[a]), "seconds": seconds,
                         "lat": float(steps_df.lat.iloc[a]), "lon": float(steps_df.lon.iloc[a]),
                         # Flagged rather than dropped here. Every consumer wants
                         # a different thing from a parked run — the junction fit
                         # wants it gone, the unmeasured-time audit wants it
                         # counted — and a row that never existed cannot be
                         # reported as an exclusion. See PARKED_S.
                         "parked": seconds >= PARKED_S})
    return _stop_frame(rows)


def _stop_frame(rows):
    """A stops table with `parked` guaranteed to be a real bool column.

    Worth a helper rather than a literal `DataFrame(...)` at each exit: `report`
    concatenates one frame per drive, and an object-dtype column from an empty
    frame makes `~stops.parked` arithmetic (-1, -2) instead of negation, which
    fails loudly here and would fail silently anywhere it indexed.
    """
    frame = pd.DataFrame(rows, columns=["start_ts", "seconds", "lat", "lon",
                                        "parked"])
    return frame.astype({"parked": bool})


def mark_parked(steps_df, stops_df):
    """Flag every step that falls inside a parked run, so the clock can drop it.

    By timestamp span rather than row index: `report` concatenates one frame per
    route segment before this is called, so row positions no longer line up with
    the runs `stops()` found, while `ts` is the same monotonic clock throughout.
    """
    parked = pd.Series(False, index=steps_df.index)
    if steps_df.empty or stops_df.empty or "parked" not in stops_df:
        return steps_df.assign(parked=parked)
    for _, stop in stops_df[stops_df.parked].iterrows():
        within = ((steps_df.ts >= stop.start_ts)
                  & (steps_df.ts <= stop.start_ts + stop.seconds))
        parked |= within
    return steps_df.assign(parked=parked)


def by_road_class(steps_df):
    """Assumed vs. measured speed per road class — the speed factor, per class.

    Moving steps only. Time spent stopped belongs to the junction cost, and
    leaving it in here would smear one defect across both numbers and let a fix
    for either look like it worked.
    """
    moving = steps_df[~steps_df.stopped & steps_df.highway.notna()
                      & (steps_df.assumed_ms > 0)]
    if moving.empty:
        return pd.DataFrame()

    grouped = moving.groupby("highway").apply(lambda d: pd.Series({
        "km": d.dist_m.sum() / 1000.0,
        "minutes": d.dt_s.sum() / 60.0,
        # Distance over time, not the mean of the per-step speeds: a hundred
        # crawling fixes and one fast one are a hundred metres and a kilometre,
        # and averaging the speeds would weight them equally.
        "measured_kmh": d.dist_m.sum() / d.dt_s.sum() * 3.6,
        # Total distance over total *assumed* time — the same harmonic form as
        # measured_kmh above. A distance-weighted arithmetic mean of speed is
        # not the speed that produces the assumed time, and arithmetic >=
        # harmonic always, so it biased assumed_kmh high and `factor` low:
        # measured 5.4% low on a single class carrying a mix of tagged and
        # default speeds, which pipeline/graph.py guarantees. `factor` is
        # prescribed for SPEED_KMH, so that bias landed in the routing weights.
        "assumed_kmh": d.dist_m.sum() / (d.dist_m / d.assumed_ms).sum() * 3.6,
    }), include_groups=False)
    grouped["factor"] = grouped.measured_kmh / grouped.assumed_kmh
    return grouped.sort_values("km", ascending=False)


def by_grade(steps_df):
    """Measured speed on the flat, uphill and downhill.

    Scenic routes are drawn to hilly roads, so "the graph is optimistic on back
    roads" has two candidate explanations — the corners or the climb — that want
    different fixes. Coarse bands on purpose: phone altitude cannot support
    anything finer, and a precise-looking grade number here would be the
    curvature mistake a second time.
    """
    moving = steps_df[~steps_df.stopped & steps_df.grade.notna()
                      & steps_df.highway.notna() & (steps_df.assumed_ms > 0)]
    if moving.empty:
        return pd.DataFrame()

    band = pd.cut(moving.grade, [-np.inf, -GRADE_BAND, GRADE_BAND, np.inf],
                  labels=["downhill", "level", "uphill"])
    grouped = moving.groupby(band, observed=True).apply(lambda d: pd.Series({
        "km": d.dist_m.sum() / 1000.0,
        "measured_kmh": d.dist_m.sum() / d.dt_s.sum() * 3.6,
        # Total distance over total *assumed* time — the same harmonic form as
        # measured_kmh above. A distance-weighted arithmetic mean of speed is
        # not the speed that produces the assumed time, and arithmetic >=
        # harmonic always, so it biased assumed_kmh high and `factor` low:
        # measured 5.4% low on a single class carrying a mix of tagged and
        # default speeds, which pipeline/graph.py guarantees. `factor` is
        # prescribed for SPEED_KMH, so that bias landed in the routing weights.
        "assumed_kmh": d.dist_m.sum() / (d.dist_m / d.assumed_ms).sum() * 3.6,
    }), include_groups=False)
    grouped["factor"] = grouped.measured_kmh / grouped.assumed_kmh
    return grouped


def headline(steps_df):
    """Actual vs. predicted time for the ground actually covered.

    Predicted per step from the road the step was on, rather than by scaling the
    route's total — so a drive abandoned halfway, or one that rerouted twice, is
    still compared against a prediction for the same ground.
    """
    known = steps_df[steps_df.assumed_ms.notna() & (steps_df.assumed_ms > 0)]
    return {
        "km": steps_df.dist_m.sum() / 1000.0,
        "actual_min": steps_df.dt_s.sum() / 60.0,
        # The clock over *matched* ground only, and the one to compare
        # `predicted_min` against. A prediction can only be made for steps that
        # snapped to a road, so dividing it into the whole drive's clock charges
        # every unmatched metre to the router as pure optimism — and unmatched
        # ground is routine, since DRIVABLE in pipeline/common.py excludes
        # `service`, so the parking aisle at each end of a trip never snaps.
        # Measured: with 20% of steps unmatched, a drive whose true optimism was
        # 0% reported +25%, the error tracking the unmatched fraction exactly.
        "matched_min": known.dt_s.sum() / 60.0,
        "predicted_min": (known.dist_m / known.assumed_ms).sum() / 60.0,
        "matched_km": known.dist_m.sum() / 1000.0,
    }


# --- report ------------------------------------------------------------------

def backgrounded(records, spans):
    """How many of these gaps happened while the app was in the background.

    A hole in the fixes has three very different causes — a tunnel, a suspended
    app, or a bug — and only the trace's own `phase` records can tell them apart.
    Guessing here would mean either shrugging off a real bug or re-driving to
    chase a tunnel.
    """
    away = [r["ts"] for r in records
            if r["t"] == "phase" and r.get("phase") in ("background", "inactive")]
    if not away:
        return 0
    return sum(1 for start, end in spans
               if any(start - 5 <= t <= end for t in away))


def report(paths, edges, control=None):
    all_steps, all_stops = [], []
    all_marks, mark_drops = [], Counter()
    # The same marks re-attributed over other window sizes, so `_mark_report` can
    # say whether its headline survives the one constant it had to invent.
    alternates = {w: [] for w in (200.0, 400.0, 800.0)}
    # Built once for all of them, and for every drive: reprojecting the graph and
    # indexing it is the expensive part, and it is identical every time.
    roads = road_index(edges)

    for path in paths:
        records = load(path)
        header = next((r for r in records if r["t"] == "drive"), {})
        ending = next((r for r in records if r["t"] == "end"), {})
        parts = segments(records)
        if not parts:
            print(f"{Path(path).name}: no usable fixes — skipped\n")
            continue

        rows = [steps(fixes) for _, fixes in parts]
        gap_count = sum(r.attrs.get("gap_count", 0) for r in rows)
        gap_minutes = sum(r.attrs.get("gap_seconds", 0.0) for r in rows) / 60.0
        gap_spans = [s for r in rows for s in r.attrs.get("gap_spans", [])]
        thrown_out = sum(r.attrs.get("dropped_accuracy", 0) for r in rows)
        mismatched = sum(r.attrs.get("dropped_offroute", 0) for r in rows)
        elapsed_min = sum(r.attrs.get("elapsed_s", 0.0) for r in rows) / 60.0
        # Empty segments are dropped before the concat, not after: pandas warns
        # that it will stop inferring dtypes past all-NA frames, and a route
        # followed for one fix is an all-NA frame.
        measured = [r for r in rows if not r.empty]
        drive_steps = (pd.concat(measured, ignore_index=True) if measured
                       else pd.DataFrame(columns=STEP_COLUMNS))
        if edges is not None:
            drive_steps = attach_road_class(drive_steps, edges)
        else:
            drive_steps = drive_steps.assign(highway=None, assumed_ms=np.nan)

        # Stops before the headline, because a parked run has to be found
        # before the clock it sits in can be quoted — see PARKED_S.
        drive_stops = stops(drive_steps)
        maneuvers = [s for route, _ in parts for s in route.get("steps", [])
                     if is_maneuver(s)]
        drive_stops = classify_stops(drive_stops, control, maneuvers)
        drive_steps = mark_parked(drive_steps, drive_stops)

        planned = parts[0][0]     # the route as first planned, before any reroute
        h = headline(drive_steps)
        driving = headline(drive_steps[~drive_steps.parked])
        print(f"{Path(path).name}  pref={header.get('pref', '?')}  "
              f"{ending.get('reason', 'unfinished')}  ({len(parts)} route(s))")
        print(f"  planned  {planned['minutes']:5.1f} min for {planned['km']:.1f} km")
        print(f"  drove    {h['actual_min']:5.1f} min for {h['km']:.1f} km"
              f"   (wall clock {elapsed_min:.1f} min)")
        if h["matched_km"] > 0 and h["predicted_min"] > 0:
            print(f"  predicted{h['predicted_min']:5.1f} min for the "
                  f"{h['matched_km']:.1f} km that snapped to a road"
                  f"  (drove {h['matched_min']:.1f} min of it)"
                  # error against *actual*, the one denominator
                  # docs/junction-timing-plan.md settles on — the same one the
                  # ALL DRIVES line below uses, so the two cannot disagree.
                  f"  →  {1 - h['predicted_min'] / h['matched_min']:+.0%}")

        parked = drive_stops[drive_stops.parked] if not drive_stops.empty \
            else drive_stops
        parked_min = parked.seconds.sum() / 60.0 if not parked.empty else 0.0
        # The line above charges the router for time the driver spent parked, so
        # when there is any, say what the number is without it. Not a correction
        # applied silently: both are printed, and the parked runs are listed at
        # the end of the report so the reader can disagree with the threshold.
        if parked_min > 0 and driving["matched_km"] > 0 and driving["predicted_min"] > 0:
            print(f"  ...without {parked_min:.1f} min parked over "
                  f"{len(parked)} stop(s) of {PARKED_S / 60:.0f}+ min: "
                  f"{driving['matched_min']:.1f} min actual vs "
                  f"{driving['predicted_min']:.1f} predicted"
                  f"  →  {driving['matched_min'] / driving['predicted_min'] - 1:+.0%}")

        junction_stops = drive_stops[~drive_stops.parked] if not drive_stops.empty \
            else drive_stops
        stopped_min = junction_stops.seconds.sum() / 60.0
        print(f"  stopped  {stopped_min:5.1f} min over {len(junction_stops)} stops"
              f"  ({stopped_min / max(h['km'], 0.01):.2f} min/km,"
              f" {100 * stopped_min / max(h['actual_min'], 0.01):.0f}% of the drive)"
              + (f"  [+{parked_min:.1f} min parked, excluded]" if parked_min > 0 else ""))
        off = drive_steps.off_m if "off_m" in drive_steps else pd.Series(dtype=float)
        if not off.empty:
            print(f"  off-route p50 {off.median():.0f} m, p95 {off.quantile(0.95):.0f} m"
                  "   (map-matching sanity, not a timing number)")
        # Time the measurement never saw. Printed against the wall clock rather
        # than on its own, because "12 minutes missing" means something quite
        # different on a 20-minute drive than on a three-hour one.
        unaccounted = elapsed_min - h["actual_min"]
        if gap_count or thrown_out or mismatched or unaccounted > 0.5:
            asleep = backgrounded(records, gap_spans)
            print(f"  ! {unaccounted:.1f} of {elapsed_min:.1f} wall-clock min are "
                  f"unmeasured: {gap_count} gaps over {MAX_GAP_S:.0f}s "
                  f"({asleep} while backgrounded), {thrown_out} inaccurate fixes, "
                  f"{mismatched} over {MAX_OFF_ROUTE_M:.0f} m off the route line.")
            if gap_minutes > stopped_min:
                print("    More time is missing than was measured stopped — the "
                      "stop numbers above are a floor, not a measurement.")
        print()

        drive_marks = marks(records)
        mark_drops.update(drive_marks.attrs)
        if not drive_marks.empty:
            scored_marks = attach_scenery(drive_marks, parts, edges, roads)
            # `attach_scenery` reports its *own* reasons for having no score —
            # a graph that was never loaded, one lacking the `score` column, a
            # trace whose route record is missing. Without carrying them up, all
            # three arrive at the report indistinguishable from a marked stretch
            # that genuinely wasn't near a road.
            # `.attrs` survives the `copy()`/`assign()` inside
            # `attach_scenery`, so re-adding the whole dict would count every
            # `dropped_*` reason from `marks()` twice — and `thrown` below
            # reads these same totals. Add only what `attach_scenery` itself
            # contributed.
            mark_drops.update({k: v for k, v in scored_marks.attrs.items()
                               if k not in drive_marks.attrs})
            all_marks.append(scored_marks)
            for width in alternates:
                alternates[width].append(
                    attach_scenery(marks(records, window_m=width), parts,
                                   edges, roads))

        all_steps.append(drive_steps)
        all_stops.append(drive_stops)

    if not all_steps:
        return
    combined = pd.concat(all_steps, ignore_index=True)
    pooled_stops = pd.concat(all_stops, ignore_index=True)
    if "parked" in pooled_stops:
        pooled_stops["parked"] = pooled_stops.parked.fillna(False).astype(bool)
    # `concat` does not carry `.attrs`, so the exclusion counts are re-attached
    # by hand. Losing them would turn "a third of your marks were unusable" into
    # a sample that merely looks small.
    pooled_marks = (pd.concat(all_marks, ignore_index=True) if all_marks
                    else pd.DataFrame(columns=["verdict", "model_score"]))
    pooled_marks.attrs.update(mark_drops)
    pooled_marks.attrs["auc_by_window"] = [
        (width, separation(
            (m := pd.concat(frames, ignore_index=True))
            .loc[m.verdict == "nice", "model_score"],
            m.loc[m.verdict == "dull", "model_score"]))
        for width, frames in sorted(alternates.items()) if frames
    ]

    print("=" * 72)
    total = headline(combined)
    if total["matched_km"] > 0 and total["matched_min"] > 0:
        # Like for like: matched ground on both sides of the ratio. See headline().
        print(f"ALL DRIVES  {total['km']:.1f} km driven, of which {total['matched_km']:.1f} km "
              f"snapped to a road")
        # "the graph", not "the router". Everything above is priced from each
        # edge's stored `minutes`, which is free-flow: router.py multiplies by
        # SPEED_FACTOR and adds CONTROL_SECONDS when it loads, and none of that
        # is in the parquet. So this is the *uncorrected* baseline — the number
        # the corrections exist to close, and the input to fit_junction_cost.py,
        # not a verdict on what the app told the driver. Labelled "the router"
        # until 2026-08-20, which reads as a regression report on a correction
        # that is working: the same two drives are 5.7% pooled once it is applied.
        print(f"            {total['matched_min']:.1f} min actual vs "
              f"{total['predicted_min']:.1f} min predicted for that ground "
              f"→ the graph's free-flow times are "
              f"{1 - total['predicted_min'] / total['matched_min']:.0%} optimistic")
        print("            (before SPEED_FACTOR and CONTROL_SECONDS, which "
              "router.py applies at load —")
        print("             run tools/fit_junction_cost.py for the corrected "
              "error, and to re-fit them)")
        driving_total = headline(combined[~combined.parked]) \
            if "parked" in combined else total
        if driving_total["matched_min"] < total["matched_min"] - 0.05:
            print(f"            {total['matched_min'] - driving_total['matched_min']:.1f} min "
                  f"of that is the car parked. Without it: "
                  f"{driving_total['matched_min']:.1f} vs "
                  f"{driving_total['predicted_min']:.1f} min → "
                  f"{1 - driving_total['predicted_min'] / driving_total['matched_min']:.0%} "
                  "optimistic,")
            print("            which is the figure to fit against. See PARKED_S "
                  "and the parked runs listed below.")
    print()

    # Before the timing chain, and never inside it: this is the only section that
    # measures the product rather than the clock, and it must not be skippable by
    # an early return further down when a graph is missing.
    _mark_report(pooled_marks, len(all_steps))

    _stop_report(pooled_stops, total)

    classes = by_road_class(combined)
    if classes.empty:
        print("\nNo road classes matched — pass a data directory holding "
              "graph_edges.parquet to get the per-class breakdown.")
        return

    print("\nMOVING SPEED BY ROAD CLASS  (stops excluded — they're priced above)")
    print(f"  {'class':<16}{'km':>7}{'assumed':>9}{'measured':>10}{'factor':>8}")
    for name, row in classes.iterrows():
        flag = "" if row.km >= 5 else "   (thin)"
        print(f"  {name:<16}{row.km:>7.1f}{row.assumed_kmh:>9.0f}"
              f"{row.measured_kmh:>10.0f}{row.factor:>8.2f}{flag}")
    print("\n  factor is measured speed over the speed the graph assumed. Rows")
    print("  marked (thin) have too little road behind them to be a measurement.")
    print("\n  Careful applying it: `assumed` comes from each edge's stored minutes,")
    print("  which graph.py takes from the OSM maxspeed tag wherever there is one")
    print("  and from SPEED_KMH only where there isn't. On the built MA graph the")
    print("  tagged share of km is 97% of motorway, 82% of trunk, 56% of primary,")
    print("  40% of secondary, 25% of tertiary, 11% of residential — 23% overall.")
    print("  So multiplying SPEED_KMH by a class's factor moves only its UNTAGGED")
    print("  remainder: on motorway that is 3% of the km, and the correction")
    print("  silently under-delivers by its own coverage ratio on exactly the")
    print("  classes whose factor looks most confident. To move tagged road you")
    print("  have to scale `minutes` in graph.py after the maxspeed lookup, not")
    print("  the fallback table.")

    grades = by_grade(combined)
    if not grades.empty and len(grades) > 1:
        print("\nAND BY GRADE  (does the climb explain the back roads, or the corners?)")
        print(f"  {'':<16}{'km':>7}{'assumed':>9}{'measured':>10}{'factor':>8}")
        for name, row in grades.iterrows():
            print(f"  {str(name):<16}{row.km:>7.1f}{row.assumed_kmh:>9.0f}"
                  f"{row.measured_kmh:>10.0f}{row.factor:>8.2f}")
        print(f"\n  Bands are ±{GRADE_BAND:.0%}, measured as rise over a "
              f"{GRADE_BASELINE_M:.0f} m run on altitude")
        print(f"  smoothed over {ALTITUDE_SMOOTH} fixes — a phone's altitude is "
              "metres-noisy, and rise over")
        print("  a single 20 m step is nearly all noise. Even so: a hint about "
              "where to")
        print("  look, not a coefficient to put in the graph.")


def separation(nice, dull):
    """P(a stretch the driver liked outscores one they didn't), ties as half.

    The rank statistic (Mann-Whitney / AUC) rather than a difference of means,
    for two reasons that both matter at this sample size. It needs no assumption
    about the score being linear in beauty — only that higher should mean nicer,
    which is the entire claim the score makes — and it cannot be moved by one
    outlier, where a difference of means can be carried by a single marked
    stretch that happened to snap to a 9.6.

    0.5 is a score that ranks roads no better than a coin. 1.0 is one that never
    got a pair the wrong way round.
    """
    nice = np.asarray([s for s in nice if np.isfinite(s)], dtype=float)
    dull = np.asarray([s for s in dull if np.isfinite(s)], dtype=float)
    if len(nice) == 0 or len(dull) == 0:
        return np.nan
    wins = sum(float(np.sum(a > dull)) + 0.5 * float(np.sum(a == dull)) for a in nice)
    return wins / (len(nice) * len(dull))


# Below this many marks of *either* verdict, the report adds a note telling the
# driver to tap more. It is advice, not the test — `null_ceiling` below is what
# actually decides whether a separation number means anything, and it works at
# every sample size. This is only here because "your sample is too small" is more
# useful to read as a sentence than as a threshold the number quietly failed.
MARKS_FOR_A_NUMBER = 12


def null_ceiling(n_nice, n_dull, z=1.645):
    """How high a score that knows nothing still reaches, one run in twenty.

    The separation number needs this beside it or it cannot be read at all. Off
    five marks each way a score that knows nothing reaches **0.82**; off thirty
    each way, 0.62. A reader with no yardstick sees 0.75 from a first drive and
    treats it as the premise confirmed — which is the single most likely way this
    whole exercise talks itself into a wrong answer.

    Standard error of the Mann-Whitney statistic under the null (Hanley-McNeil),
    which needs only the two sample sizes: sqrt((n1+n2+1) / (12*n1*n2)).
    """
    if n_nice < 1 or n_dull < 1:
        return np.nan
    se = np.sqrt((n_nice + n_dull + 1) / (12.0 * n_nice * n_dull))
    return 0.5 + z * se


def _mark_report(pooled_marks, drives):
    """Does the scenic score agree with the person who drove the road?

    The one question in this project that no amount of open geodata answers, and
    the reason `mark` records exist. Everything else `analyze_trace.py` prints is
    a check of the clock, which a laptop can do against itself; the score has only
    ever been calibrated against its own distribution and two byways named in
    `score.py`, which establishes that it is self-consistent, not that it is right.
    """
    print("=" * 72)
    # How many taps were thrown away before any of them could be scored. Printed
    # even when nothing survived, because "no marks" and "every mark you made was
    # unusable" call for completely different responses — the first says tap next
    # drive, the second says the tapping was fine and something else is broken.
    thrown = sum(n for key, n in pooled_marks.attrs.items()
                 if key.startswith("dropped_"))

    if pooled_marks.empty and not thrown:
        print("SCENERY  no marks. Nothing in these traces says whether the roads")
        print("  were actually nice, so the score is still only calibrated against")
        print("  itself. The two buttons above the trip bar are what records that;")
        print("  a drive that does not use them cannot test the premise.")
        print()
        return

    scored = pooled_marks[pooled_marks["model_score"].notna()] if not pooled_marks.empty \
        else pooled_marks
    nice = scored[scored.verdict == "nice"]["model_score"] if not scored.empty \
        else pd.Series(dtype=float)
    dull = scored[scored.verdict == "dull"]["model_score"] if not scored.empty \
        else pd.Series(dtype=float)

    if pooled_marks.empty:
        print(f"SCENERY  {thrown} marks over {drives} drive(s), none of them usable.")
        print("  You did the tapping; something else lost it. The reason is below,")
        print("  and every one of these marks is still in the trace — if the cause")
        print("  turns out to be fixable here, they can be re-read without driving")
        print("  anything again.")
    else:
        print(f"SCENERY  {len(pooled_marks)} marks over {drives} drive(s) — "
              f"{int((pooled_marks.verdict == 'nice').sum())} nice, "
              f"{int((pooled_marks.verdict == 'dull').sum())} dull")
    for label, why in (
        ("dropped_unjoined", "tapped before joining the route"),
        ("dropped_no_anchor", "no position on the route line"),
        ("dropped_stale_fix", f"the last GPS fix was over {MARK_MAX_FIX_AGE_S:.0f}s "
                              "old, so there is no telling which road it was"),
        ("dropped_unknown_verdict", "a verdict this tool doesn't know"),
        ("no_graph", "no graph was loaded — pass a data directory"),
        ("no_score_column", "the graph has no `score` column — rebuild it "
                            "with score.py, then graph.py"),
        ("no_route_line", "the trace has no route record to measure along"),
    ):
        n = pooled_marks.attrs.get(label, 0)
        if n:
            print(f"  ! {n} unusable: {why}")
    # Only what is left after those: a stretch that had every chance to score and
    # still didn't is a genuine "not near a road", and saying so has to mean that.
    unexplained = pooled_marks.attrs.get("no_graph", 0) \
        + pooled_marks.attrs.get("no_score_column", 0) \
        + pooled_marks.attrs.get("no_route_line", 0)
    unscored = len(pooled_marks) - len(scored) - unexplained
    if unscored > 0:
        print(f"  ! {unscored} marked stretches didn't snap to a road — "
              "no score to compare against")

    if scored.empty:
        print("  Nothing could be scored, so there is nothing to compare. The")
        print("  marks are still in the trace — fix the cause above and re-run;")
        print("  none of this needs the drive taken again.")
        print()
        return

    if nice.empty or dull.empty:
        print("  Only one verdict was used, so there is nothing to separate. The")
        print("  comparison needs both: marking only what you liked measures")
        print("  where you drove, not what the score gets wrong.")
        print()
        return

    print(f"  model score on the roads you liked   {nice.mean():.2f} "
          f"(median {nice.median():.2f}, n={len(nice)})")
    print(f"  model score on the roads you didn't  {dull.mean():.2f} "
          f"(median {dull.median():.2f}, n={len(dull)})")

    auc = separation(nice, dull)
    ceiling = null_ceiling(len(nice), len(dull))
    thin = min(len(nice), len(dull)) < MARKS_FOR_A_NUMBER
    beats_chance = np.isfinite(ceiling) and auc > ceiling

    print(f"\n  SEPARATION  {auc:.2f}    "
          + ("above chance" if beats_chance else "NOT above chance"))
    print("  The chance the score ranks a road you liked above one you didn't.")
    print(f"  0.50 is a coin — but with {len(nice)} nice and {len(dull)} dull, a score")
    print(f"  that knows nothing still reaches {ceiling:.2f} one run in twenty. That,")
    print("  not 0.50, is the number this has to beat to mean anything.")
    if not beats_chance:
        print("  It doesn't. Either the score isn't measuring what you see out of")
        print("  the window — in which case the weights in score.py are the thing")
        print("  to change — or there are too few marks yet to tell. More marks")
        print("  is much the cheaper of the two to rule out first.")

    # The window is a judgement call, so show whether the answer depends on it.
    # If it does, the marks are being charged to the wrong road and no amount of
    # sample size fixes that.
    if "auc_by_window" in pooled_marks.attrs:
        spread = pooled_marks.attrs["auc_by_window"]
        line = "   ".join(f"{int(w)} m: {a:.2f}" for w, a in spread)
        print(f"\n  same number over other windows —  {line}")
        values = [a for _, a in spread if np.isfinite(a)]
        # 0.15 across a 4x change in window width. Some drift is expected and
        # says nothing: a narrow window is a sharper instrument, so it *should*
        # separate slightly better when the marks are well placed. What this is
        # watching for is the answer being an artefact of the window rather than
        # a property of the score, and that shows up as a swing this size.
        if values and max(values) - min(values) > 0.15:
            print("  It moves with the window, which means these marks are not")
            print("  reliably landing on the road they were about. Trust the")
            print("  direction, not the value, and tap sooner next drive.")

    if thin:
        print(f"\n  ! Fewer than {MARKS_FOR_A_NUMBER} of one verdict. At this size a")
        print("  useless score still clears 0.70 about one run in six, so this is")
        print("  an indication and not a measurement. More marks per drive is the")
        print("  cheap fix — there is no cost to tapping often.")

    # Where the model and the driver disagree most. These are the addresses worth
    # driving back to: a specific road, a specific claim, and a score to check.
    #
    # Taken as the worst few in *each* direction rather than as one ranked list.
    # A single list cannot do it: "dull but scored 9" and "nice but scored 1"
    # are surprising by opposite comparisons, and any one number that mixes them
    # is on a different scale for each — ranking by it filled the whole table
    # with high-scoring dull roads and never showed the other kind at all.
    print("\n  WHERE IT DISAGREES WITH YOU  (worth driving back to)")
    print(f"  {'verdict':<9}{'score':>6}  {'class':<13}{'road':<24}where")
    for group, ascending in (("dull", False), ("nice", True)):
        rows = (scored[scored.verdict == group]
                .sort_values("model_score", ascending=ascending).head(3))
        for row in rows.itertuples():
            where = (f"{row.lat:.5f},{row.lon:.5f}"
                     if np.isfinite(row.lat) and np.isfinite(row.lon) else "—")
            print(f"  {row.verdict:<9}{row.model_score:>6.1f}  "
                  f"{str(row.highway or '?'):<13}{str(row.road or '?')[:23]:<24}{where}")
    print("  The dull rows are the expensive direction: a high score you called")
    print("  dull is scenery the model claims and the road does not have, and")
    print("  that claim is what the router spends a driver's extra minutes on.")
    print("  The nice rows cost nothing but are where the score is blind — a road")
    print("  worth driving that it will route around.")

    print("\n  One driver, on one day, in whatever weather it was. This measures")
    print("  agreement with you, which is what the product promises, and not")
    print("  beauty in general.")
    print()


def _stop_report(pooled_stops, total):
    """What the stops were, and which of them the graph could have known about."""
    if pooled_stops.empty:
        print("STOPS  none measured. If that seems wrong, check the unmeasured")
        print("  time above — a phone that stops reporting while the car is")
        print("  stationary turns every stop into a gap.")
        return

    # Parked runs are stopped time but not junction cost, and mixing them in
    # here is what makes the per-km figure unusable: on the 2026-08-22 batch two
    # of them carried 57% of all stopped time. Split first, report both.
    parked = pooled_stops[pooled_stops.parked] if "parked" in pooled_stops \
        else pooled_stops.iloc[:0]
    pooled_stops = pooled_stops[~pooled_stops.parked] if "parked" in pooled_stops \
        else pooled_stops
    if pooled_stops.empty:
        print("STOPS  every stationary run was longer than "
              f"{PARKED_S / 60:.0f} min, so none of them is junction cost.")
        _parked_report(parked)
        return

    seconds = pooled_stops.seconds.sum()
    print(f"STOPS  {len(pooled_stops)} of them, {seconds / 60:.1f} min total, "
          f"median {pooled_stops.seconds.median():.0f} s")
    print(f"  {seconds / max(total['km'], 0.01):.1f} s per km driven — this is the "
          "junction cost the graph charges nothing for.\n")

    # The split that decides which fix is even possible. A stop at a mapped
    # signal is on that road every time you drive it and belongs in graph.py;
    # an unexplained one is congestion, and no amount of static data will
    # predict it. Summing the two and calling it "junction cost" would bake one
    # afternoon's traffic into the graph for good.
    by_cause = pooled_stops.groupby("cause").agg(
        n=("seconds", "size"), minutes=("seconds", "sum")).sort_values("minutes",
                                                                       ascending=False)
    by_cause["minutes"] /= 60.0
    label = {"traffic_signals": "at a traffic signal", "stop": "at a stop sign",
             "give_way": "at a give-way", "mini_roundabout": "at a mini-roundabout",
             "maneuver": "at a turn (no control mapped)",
             "unexplained": "unexplained — traffic, or OSM is missing a junction"}
    for cause, row in by_cause.iterrows():
        print(f"  {label.get(cause, cause):<52}{int(row.n):>4}{row.minutes:>7.1f} min")

    structural = by_cause.drop(index="unexplained", errors="ignore").minutes.sum()
    if structural > 0:
        print(f"\n  {structural / (seconds / 60):.0%} of stopped time sits at something "
              "OSM already knows about,")
        print("  which is the part a per-junction penalty in graph.py can predict.")
        print("  The rest needs a traffic feed, or another drive at a different hour")
        print("  to see whether it moves.")
    _parked_report(parked)


def _parked_report(parked):
    """The stationary runs held out of the junction cost, one line each.

    Listed rather than summarised because the PARKED_S threshold is a judgement
    about what the driver was doing, and only the driver can check it. A run
    that was really a level crossing belongs back in the fit, and the way to
    notice is to recognise the place and the time of day.
    """
    if parked.empty:
        return
    print(f"\n  HELD OUT AS PARKED  {len(parked)} run(s) over "
          f"{PARKED_S / 60:.0f} min, {parked.seconds.sum() / 60:.1f} min in total,")
    print("  excluded from the junction cost above and from the clock it is "
          "fitted against.")
    for _, row in parked.sort_values("seconds", ascending=False).iterrows():
        when = dt.datetime.fromtimestamp(row.start_ts).strftime("%H:%M")
        print(f"    {row.seconds / 60:5.1f} min from {when} at "
              f"{row.lat:.5f},{row.lon:.5f}")
    print("  If one of these was the road stopping you — a freight crossing, a")
    print("  drawbridge — it belongs in the fit: raise PARKED_S and re-run.")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    data_dir, paths = argv[1], argv[2:]

    edges, control = None, None
    if data_dir != "-":
        edge_file = Path(data_dir) / "graph_edges.parquet"
        if not edge_file.exists():
            print(f"no graph at {edge_file} — road classes will be skipped "
                  "(pass '-' to silence this)")
        else:
            import geopandas as gpd
            # `score` and `name` are here for the scenery marks: the score is
            # what a driver's verdict is compared against, and the name is what
            # makes a disagreement worth driving back to — "Bay Road scored 7.1
            # and you called it dull" is actionable, a lat/lon is homework.
            edges = gpd.read_parquet(edge_file, columns=["highway", "length_m",
                                                         "minutes", "score",
                                                         "name", "geometry"])
        # Signals and stop signs, so a stop can be blamed on something the graph
        # could learn rather than on traffic it never can. Built from whichever
        # OSM extract is lying around and cached beside the processed data; the
        # analysis still runs, one section shorter, without it.
        control = traffic_control(_find_pbf(), Path(data_dir) / "traffic_control.parquet")
        if control is None:
            print("no OSM extract found — stops won't be split into signals vs "
                  "traffic. Put the .pbf in data/raw or set SCENIC_PBF.")
    report(paths, edges, control)
    return 0


def _find_pbf():
    """The OSM extract, from SCENIC_PBF or the usual place in data/raw."""
    import os

    override = os.environ.get("SCENIC_PBF")
    if override:
        return Path(override)
    root = Path(__file__).resolve().parent.parent
    return next(iter(sorted((root / "data" / "raw").glob("*.osm.pbf"))), None)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
