"""Read drive traces recorded by the iOS app and measure how wrong the clock is.

    .venv/bin/python tools/analyze_trace.py data/processed traces/*.ndjson

The router's travel times are free-flow — `length / speed_limit`, with nothing
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

import json
import sys
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
    "phase": {"t", "ts", "phase"},
    "end": {"t", "ts", "reason"},
}

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

# Below this, the car is stopped rather than moving (m/s). ~1.1 mph: slower than
# a crawl in traffic, faster than GPS drift at a red light.
STOPPED_MS = 0.5

# A stop has to last this long to be one, in seconds. Shorter is a rolling stop
# or two jittery fixes, and counting those would inflate the junction cost.
MIN_STOP_S = 3.0

# How far a fix may be from a road before we refuse to say which road it was on.
MAX_SNAP_M = 30.0

# How close a stop has to be to a mapped signal or stop sign to be blamed on it.
# Generous, because the car stops at the back of a queue, not at the stop line.
CONTROL_NEAR_M = 45.0

# ...and to a maneuver, to be blamed on the turn instead. Tighter: a maneuver
# point is where the route changes road, which is the junction itself.
MANEUVER_NEAR_M = 35.0

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
                "speed_ms"]


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
        # The recorded match is per-fix, so GPS jitter can nudge it backwards a
        # metre or two. Clamped rather than dropped: a car at a light produces a
        # long run of these, and dropping them would delete the stop.
        "dist_m": np.maximum(np.diff(f["travelled"].to_numpy()), 0.0),
        "grade": grade_per_fix[:-1],
        "lat": f["lat"].to_numpy()[:-1],
        "lon": f["lon"].to_numpy()[:-1],
        "acc": np.maximum(f["acc"].to_numpy()[:-1], f["acc"].to_numpy()[1:]),
        "off_m": f["off"].to_numpy()[:-1],
    })
    # Gaps are dropped but not forgotten. A dropout is the one exclusion that can
    # quietly delete the thing being measured: if the phone stops reporting while
    # the car is stationary, every stop becomes a gap, the junction cost reads
    # zero, and nothing in the output says so. Carried out as attributes so
    # `report` can put the discarded time on screen next to the answer.
    gaps = out[out.dt_s > MAX_GAP_S]
    kept = out[(out.dt_s > 0) & (out.dt_s <= MAX_GAP_S) & (out.acc <= MAX_ACCURACY_M)]
    kept = kept.reset_index(drop=True)
    kept["speed_ms"] = kept.dist_m / kept.dt_s
    kept = kept[STEP_COLUMNS]
    kept.attrs["gap_count"] = len(gaps)
    kept.attrs["gap_seconds"] = float(gaps.dt_s.sum())
    kept.attrs["gap_spans"] = list(zip(gaps.ts.to_numpy(),
                                       (gaps.ts + gaps.dt_s).to_numpy()))
    kept.attrs["dropped_accuracy"] = int((out.acc > MAX_ACCURACY_M).sum())
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
        return pd.DataFrame(columns=["start_ts", "seconds", "lat", "lon"])

    stopped = (steps_df.speed_ms < STOPPED_MS).to_numpy()
    runs, start = [], None
    for i, is_stopped in enumerate(stopped):
        if is_stopped and start is None:
            start = i
        elif not is_stopped and start is not None:
            runs.append((start, i)); start = None
    if start is not None:
        runs.append((start, len(stopped)))

    rows = []
    for a, b in runs:
        seconds = float(steps_df.dt_s.iloc[a:b].sum())
        if seconds >= MIN_STOP_S:
            rows.append({"start_ts": float(steps_df.ts.iloc[a]), "seconds": seconds,
                         "lat": float(steps_df.lat.iloc[a]), "lon": float(steps_df.lon.iloc[a])})
    return pd.DataFrame(rows, columns=["start_ts", "seconds", "lat", "lon"])


def by_road_class(steps_df):
    """Assumed vs. measured speed per road class — the speed factor, per class.

    Moving steps only. Time spent stopped belongs to the junction cost, and
    leaving it in here would smear one defect across both numbers and let a fix
    for either look like it worked.
    """
    moving = steps_df[(steps_df.speed_ms >= STOPPED_MS) & steps_df.highway.notna()]
    if moving.empty:
        return pd.DataFrame()

    grouped = moving.groupby("highway").apply(lambda d: pd.Series({
        "km": d.dist_m.sum() / 1000.0,
        "minutes": d.dt_s.sum() / 60.0,
        # Distance over time, not the mean of the per-step speeds: a hundred
        # crawling fixes and one fast one are a hundred metres and a kilometre,
        # and averaging the speeds would weight them equally.
        "measured_kmh": d.dist_m.sum() / d.dt_s.sum() * 3.6,
        "assumed_kmh": (d.assumed_ms * d.dist_m).sum() / d.dist_m.sum() * 3.6,
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
    moving = steps_df[(steps_df.speed_ms >= STOPPED_MS) & steps_df.grade.notna()
                      & steps_df.highway.notna()]
    if moving.empty:
        return pd.DataFrame()

    band = pd.cut(moving.grade, [-np.inf, -GRADE_BAND, GRADE_BAND, np.inf],
                  labels=["downhill", "level", "uphill"])
    grouped = moving.groupby(band, observed=True).apply(lambda d: pd.Series({
        "km": d.dist_m.sum() / 1000.0,
        "measured_kmh": d.dist_m.sum() / d.dt_s.sum() * 3.6,
        "assumed_kmh": (d.assumed_ms * d.dist_m).sum() / d.dist_m.sum() * 3.6,
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

        planned = parts[0][0]     # the route as first planned, before any reroute
        h = headline(drive_steps)
        print(f"{Path(path).name}  pref={header.get('pref', '?')}  "
              f"{ending.get('reason', 'unfinished')}  ({len(parts)} route(s))")
        print(f"  planned  {planned['minutes']:5.1f} min for {planned['km']:.1f} km")
        print(f"  drove    {h['actual_min']:5.1f} min for {h['km']:.1f} km"
              f"   (wall clock {elapsed_min:.1f} min)")
        if h["matched_km"] > 0:
            print(f"  predicted{h['predicted_min']:5.1f} min for the same ground"
                  f"  →  {h['actual_min'] / h['predicted_min'] - 1:+.0%}")

        drive_stops = stops(drive_steps)
        maneuvers = [s for route, _ in parts for s in route.get("steps", [])]
        drive_stops = classify_stops(drive_stops, control, maneuvers)
        stopped_min = drive_stops.seconds.sum() / 60.0
        print(f"  stopped  {stopped_min:5.1f} min over {len(drive_stops)} stops"
              f"  ({stopped_min / max(h['km'], 0.01):.2f} min/km,"
              f" {100 * stopped_min / max(h['actual_min'], 0.01):.0f}% of the drive)")
        off = drive_steps.off_m if "off_m" in drive_steps else pd.Series(dtype=float)
        if not off.empty:
            print(f"  off-route p50 {off.median():.0f} m, p95 {off.quantile(0.95):.0f} m"
                  "   (map-matching sanity, not a timing number)")
        # Time the measurement never saw. Printed against the wall clock rather
        # than on its own, because "12 minutes missing" means something quite
        # different on a 20-minute drive than on a three-hour one.
        unaccounted = elapsed_min - h["actual_min"]
        if gap_count or thrown_out or unaccounted > 0.5:
            asleep = backgrounded(records, gap_spans)
            print(f"  ! {unaccounted:.1f} of {elapsed_min:.1f} wall-clock min are "
                  f"unmeasured: {gap_count} gaps over {MAX_GAP_S:.0f}s "
                  f"({asleep} while backgrounded), {thrown_out} inaccurate fixes.")
            if gap_minutes > stopped_min:
                print("    More time is missing than was measured stopped — the "
                      "stop numbers above are a floor, not a measurement.")
        print()

        all_steps.append(drive_steps)
        all_stops.append(drive_stops)

    if not all_steps:
        return
    combined = pd.concat(all_steps, ignore_index=True)
    pooled_stops = pd.concat(all_stops, ignore_index=True)

    print("=" * 72)
    total = headline(combined)
    if total["matched_km"] > 0:
        print(f"ALL DRIVES  {total['km']:.1f} km, {total['actual_min']:.1f} min actual "
              f"vs {total['predicted_min']:.1f} min predicted "
              f"→ the router is {1 - total['predicted_min'] / total['actual_min']:.0%} optimistic")
    print()

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
    print("\n  factor is what SPEED_KMH in pipeline/graph.py should be multiplied")
    print("  by for that class. Rows marked (thin) have too little road behind")
    print("  them to be a measurement — drive more of that class or ignore them.")

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


def _stop_report(pooled_stops, total):
    """What the stops were, and which of them the graph could have known about."""
    if pooled_stops.empty:
        print("STOPS  none measured. If that seems wrong, check the unmeasured")
        print("  time above — a phone that stops reporting while the car is")
        print("  stationary turns every stop into a gap.")
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
            edges = gpd.read_parquet(edge_file, columns=["highway", "length_m",
                                                         "minutes", "geometry"])
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
