"""Measure what a traffic control actually costs, from drive traces.

    .venv/bin/python tools/fit_junction_cost.py data/processed traces/*.ndjson

`tools/analyze_trace.py` says how much time the router loses to stopping and
splits it by what the car stopped at. It cannot say what a *control* costs,
because it never counts the controls that were passed without stopping — and
that denominator is the whole number `pipeline/router.py` needs:

    cost of a control of kind k  =  stopped seconds at k  ÷  times k was met

which folds P(stop) and the mean delay into the one quantity a static graph can
charge, without having to estimate either separately.

The denominator is the hard part and it is why this is a separate tool. Counting
controls within a buffer of the driven line over-counts badly: 81.7% of
Massachusetts' controls have more than one road within 15 m of them and 72.4%
have three or more, so a buffer sweeps up the stop signs on every cross street
the driver went straight past. Doing that gives an implied stop rate of 15% at
signals, which is nonsense.

So this counts encounters the way `graph.py` charges them — per traversed edge,
per direction, from the `n_<kind>_<dir>` columns, which come from OSM way
membership rather than from any radius. To do that it has to recover which edges
the drive actually covered:

  * the route polyline recorded in the trace is the graph's own edge geometries
    stitched together, so its vertices include every junction node on the route;
  * matching those vertices back to the node table recovers the node path
    exactly, and consecutive nodes name the edge and the direction;
  * `travelled` says how far along that line the driver actually got before
    arriving or rerouting, which trims the tail of every abandoned route.

Stops are then attributed positionally: a stop is at the nearest control that
lies *on the driven line* and is charged in the driven direction. A cross-street
stop sign is not on the line, so it cannot be blamed for anything.
"""

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))

from common import CONTROL_COLUMNS, CRS_METERS  # noqa: E402
from router import CONTROL_SECONDS, SPEED_FACTOR  # noqa: E402
from analyze_trace import (attach_road_class, load, segments,  # noqa: E402
                           steps, stops)

# How close a route vertex has to be to a node to *be* that node, in metres.
# The two come from the same coordinates via the API's GeoJSON, so this is a
# float-rounding tolerance and nothing more; anything looser starts matching a
# junction on the road alongside.
NODE_MATCH_M = 0.5

# How close a control has to be to the driven line to count as on it. Also a
# rounding tolerance: the control is a *vertex* of that line, not merely near
# it. 86.6% of controls sit within 1 m of an edge; the rest are on roads this
# pipeline does not route over.
ON_LINE_M = 2.0

# How far back from a control the car may have stopped and still be stopped *at*
# it. Generous, because a queue forms behind the stop line and the tail of it
# can reach back past the previous junction.
STOP_REACH_M = 60.0

# Which control explains a stop when a junction has more than one kind on it.
# A signal beats a stop sign for the same reason `analyze_trace.classify_stops`
# lets a control beat a maneuver: it is the more specific explanation.
KIND_PRECEDENCE = ["signal", "stop", "giveway"]


def node_lookup(nodes):
    """(STRtree of node points in metres, node_id array) for matching vertices."""
    import geopandas as gpd
    from shapely.strtree import STRtree

    pts = gpd.GeoSeries(gpd.points_from_xy(nodes.lon, nodes.lat),
                        crs=4326).to_crs(CRS_METERS)
    return STRtree(pts.values), nodes.node_id.to_numpy(), pts.values


def node_path(coords, tree, node_ids, node_pts):
    """The graph nodes a route polyline passes through, in travel order.

    Returns (indices into `coords`, node ids). The polyline is a stitch of edge
    geometries, so every junction it crosses is one of its own vertices — this
    is recovery, not matching, and a vertex either is a node or is not.
    """
    import geopandas as gpd

    lon = [c[0] for c in coords]
    lat = [c[1] for c in coords]
    pts = gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs=4326).to_crs(CRS_METERS)
    near = tree.nearest(pts.values)
    dist = node_pts[near].distance(pts.values)
    hit = np.where(dist <= NODE_MATCH_M)[0]
    return hit, node_ids[near[hit]]


def cumulative_m(coords):
    """Distance along a lon/lat polyline, in metres, per vertex."""
    from pyproj import Transformer

    to_m = Transformer.from_crs(4326, CRS_METERS, always_xy=True)
    x, y = to_m.transform([c[0] for c in coords], [c[1] for c in coords])
    return np.r_[0.0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]


def driven_edges(route, fixes, edges, pairs, tree, node_ids, node_pts):
    """The (edge row, reversed?, fraction covered) this route segment covered.

    Trimmed to the ground the driver crossed. A reroute abandons the rest of the
    line, and charging its controls to the drive would count junctions nobody
    ever reached — drive 2 rerouted twelve times, so this is most of that drive.

    The fraction matters at exactly those seams. An edge the driver was halfway
    along when the route changed is half of a traversal, and counting it whole
    inflated drive 2's covered distance by 5% over the distance its own fixes
    report. Charging a fraction of that edge's controls is right in expectation,
    which is the most a partial traversal can tell you.
    """
    coords = route["coords"]
    if len(coords) < 2:
        return []
    at, ids = node_path(coords, tree, node_ids, node_pts)
    if len(ids) < 2:
        return []
    along = cumulative_m(coords)

    travelled = fixes["travelled"].to_numpy()
    lo, hi = float(np.nanmin(travelled)), float(np.nanmax(travelled))

    out = []
    for k in range(len(ids) - 1):
        start, end = along[at[k]], along[at[k + 1]]
        if end <= lo or start >= hi:          # never reached, or already passed
            continue
        span = end - start
        covered = (min(end, hi) - max(start, lo)) / span if span > 0 else 0.0
        rows = pairs.get((ids[k], ids[k + 1]))
        if rows is None:
            rows = pairs.get((ids[k + 1], ids[k]))
            if rows is None:
                continue
            flip = True
        else:
            flip = False
        # Parallel roads join the same two junctions; the one the driver was on
        # is the one whose length matches the line between those vertices.
        row = min(rows, key=lambda r: abs(edges.length_m.iloc[r] - span))
        out.append((row, flip, min(1.0, max(0.0, covered))))
    return out


def encounters(driven, edges):
    """How many controls of each kind those traversals meet."""
    total = Counter()
    for row, flip, covered in driven:
        for kind in KIND_PRECEDENCE:
            column = f"n_{kind}_{'rev' if flip else 'fwd'}"
            total[kind] += covered * int(edges[column].iloc[row])
    return total


def control_minutes(driven, edges, cost):
    """Minutes those traversals lose to controls, under a cost table."""
    met = encounters(driven, edges)
    return sum(cost[kind] * met[kind] for kind in KIND_PRECEDENCE) / 60.0


def controls_on_line(driven, edges, control):
    """The control points sitting on the driven edges, with their kind.

    Only those the traversed direction is actually charged for: a stop sign
    facing the other way is a vertex of the same road and costs this driver
    nothing.
    """
    from shapely.strtree import STRtree

    if not driven:
        return np.empty((0, 2)), []
    geoms = edges.geometry.values
    tree = STRtree(control.geometry.values)
    kinds = control.kind.to_numpy()

    found_xy, found_kind = [], []
    seen = set()
    for row, flip, _ in driven:
        geom = geoms[row]
        charged = {kind for kind in KIND_PRECEDENCE
                   if edges[f"n_{kind}_{'rev' if flip else 'fwd'}"].iloc[row]}
        if not charged:
            continue
        for c in tree.query(geom.buffer(ON_LINE_M), predicate="intersects"):
            if kinds[c] not in charged or c in seen:
                continue
            seen.add(c)
            found_xy.append((control.geometry.values[c].x,
                             control.geometry.values[c].y))
            found_kind.append(kinds[c])
    return np.asarray(found_xy).reshape(-1, 2), found_kind


def attribute(drive_stops, xy, kinds):
    """Blame each stop on the nearest control of the driven line, or nothing."""
    import geopandas as gpd

    out = drive_stops.copy()
    out["cause"] = "unexplained"
    if out.empty or not len(xy):
        return out
    pts = gpd.GeoSeries(gpd.points_from_xy(out.lon, out.lat),
                        crs=4326).to_crs(CRS_METERS)
    px = np.array([p.x for p in pts.values])
    py = np.array([p.y for p in pts.values])
    d = np.hypot(px[:, None] - xy[None, :, 0], py[:, None] - xy[None, :, 1])
    nearest = d.argmin(axis=1)
    close = d[np.arange(len(px)), nearest] <= STOP_REACH_M
    out.loc[close, "cause"] = np.asarray(kinds)[nearest[close]]
    return out


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    import geopandas as gpd

    data = Path(argv[1])
    edges = gpd.read_parquet(data / "graph_edges.parquet",
                             columns=["u", "v", "length_m", "minutes", "highway",
                                      "geometry"] + CONTROL_COLUMNS).to_crs(CRS_METERS)
    nodes = pd.read_parquet(data / "graph_nodes.parquet",
                            columns=["node_id", "lon", "lat"])
    control = gpd.read_parquet(data / "traffic_control.parquet").to_crs(CRS_METERS)
    # analyze_trace caches the raw OSM tag; graph.py's three kinds are what the
    # columns are named after, so map once here rather than at every comparison.
    control["kind"] = control["kind"].map(
        {"traffic_signals": "signal", "stop": "stop",
         "give_way": "giveway", "mini_roundabout": "giveway"})

    pairs = {}
    for i, (u, v) in enumerate(zip(edges.u.to_numpy(), edges.v.to_numpy())):
        pairs.setdefault((u, v), []).append(i)

    tree, node_ids, node_pts = node_lookup(nodes)

    drives = []
    for path in argv[2:]:
        records = load(path)
        parts = segments(records)
        drive_driven, drive_steps = [], []
        for route, fixes in parts:
            joined = fixes[fixes.get("joined", True).astype(bool)] \
                if "joined" in fixes else fixes
            if joined.empty:
                continue
            drive_driven += driven_edges(route, joined, edges, pairs,
                                         tree, node_ids, node_pts)
            drive_steps.append(steps(fixes))

        measured = [s for s in drive_steps if not s.empty]
        frame = (pd.concat(measured, ignore_index=True) if measured
                 else pd.DataFrame())
        frame = attach_road_class(frame, edges)
        drive_stops = stops(frame) if not frame.empty else pd.DataFrame()
        xy, kinds = controls_on_line(drive_driven, edges, control)
        drive_stops = attribute(drive_stops, xy, kinds)

        met_here = encounters(drive_driven, edges)
        km = sum(edges.length_m.iloc[r] * c for r, _, c in drive_driven) / 1000.0
        print(f"{Path(path).name}")
        print(f"  {len(drive_driven):,} edge traversals, {km:.1f} km of graph covered")
        print("  met: " + ", ".join(f"{k} {met_here[k]:.0f}" for k in KIND_PRECEDENCE))
        by = (drive_stops.groupby("cause").seconds.agg(["size", "sum"])
              if not drive_stops.empty else pd.DataFrame())
        for cause, row in by.iterrows():
            print(f"    stopped at {cause:<12}{int(row['size']):>3} times, "
                  f"{row['sum'] / 60:>5.1f} min")
        print()
        drives.append({"name": Path(path).name, "driven": drive_driven,
                       "steps": frame, "met": met_here,
                       "stopped_s": Counter({c: float(r["sum"]) for c, r in by.iterrows()}),
                       "stops": Counter({c: int(r["size"]) for c, r in by.iterrows()})})

    print("=" * 68)
    print("COST PER CONTROL MET  (this is what router.py charges)")
    cost = report_cost(drives)

    print("\n" + "=" * 68)
    print("DOES IT PREDICT THE DRIVE?  error = |predicted - actual| / actual")
    for d in drives:
        row = evaluate(d, edges, cost)
        print(f"  {d['name']}")
        print(f"    actual        {row['actual']:>6.1f} min")
        print(f"    free-flow     {row['free']:>6.1f} min   "
              f"{row['free_err']:>5.1%}   (what the graph used to say)")
        print(f"    + speed       {row['speed']:>6.1f} min   {row['speed_err']:>5.1%}")
        print(f"    + controls    {row['full']:>6.1f} min   {row['full_err']:>5.1%}")

    print("\n" + "=" * 68)
    print("HELD OUT  (fit on one drive, predict the other — §9)")
    if len(drives) < 2:
        print("  needs two drives")
        return 0
    for held in drives:
        others = [d for d in drives if d is not held]
        fitted = fit(others)
        row = evaluate(held, edges, fitted)
        source = ", ".join(d["name"].split("-")[-1] for d in others)
        print(f"  {held['name']} predicted from {source}: "
              f"{row['full']:.1f} vs {row['actual']:.1f} min "
              f"→ {row['full_err']:.1%}   (was {row['free_err']:.1%})")
        print("    " + ", ".join(f"{k} {fitted[k]:.1f}s" for k in KIND_PRECEDENCE))
    return 0


def fit(drives):
    """Seconds per control met, per kind, pooled over these drives."""
    met, stopped = Counter(), Counter()
    for d in drives:
        met.update(d["met"])
        stopped.update(d["stopped_s"])
    # A kind nobody met keeps the shipped constant rather than becoming zero:
    # "we have no evidence" and "it is free" are different claims.
    return {k: (stopped[k] / met[k] if met[k] else CONTROL_SECONDS[k])
            for k in KIND_PRECEDENCE}


def report_cost(drives):
    cost = fit(drives)
    met, stopped, count = Counter(), Counter(), Counter()
    for d in drives:
        met.update(d["met"])
        stopped.update(d["stopped_s"])
        count.update(d["stops"])
    print(f"  {'kind':<12}{'met':>7}{'stops':>7}{'P(stop)':>9}"
          f"{'s/stop':>8}{'s/control':>11}")
    for kind in KIND_PRECEDENCE:
        n = met[kind]
        if not n:
            print(f"  {kind:<12}{'0':>7}{'-':>7}{'-':>9}{'-':>8}"
                  f"{CONTROL_SECONDS[kind]:>11.1f}   (assumed)")
            continue
        print(f"  {kind:<12}{n:>7.0f}{count[kind]:>7}{count[kind] / n:>9.2f}"
              f"{stopped[kind] / max(count[kind], 1):>8.1f}{cost[kind]:>11.1f}")
    print(f"\n  unexplained: {count['unexplained']} stops, "
          f"{stopped['unexplained'] / 60:.1f} min — left out of the graph on "
          "purpose (§7)")
    return cost


def evaluate(drive, edges, cost):
    """Actual vs predicted minutes for one drive, at each stage of the model.

    The driving part is predicted per *step* — the ground the fixes actually
    covered, at the speed its road class is really driven — rather than by
    summing whole edges, so a drive abandoned halfway or rerouted twelve times
    is still compared against a prediction for the same ground. The control part
    is per traversal, because that is the only place the encounters are known.
    """
    s = drive["steps"]
    known = s[s.assumed_ms.notna() & (s.assumed_ms > 0)]
    actual = s.dt_s.sum() / 60.0
    free = (known.dist_m / known.assumed_ms).sum() / 60.0
    factor = known.highway.map(SPEED_FACTOR).fillna(1.0).to_numpy()
    speed = (known.dist_m.to_numpy() / (known.assumed_ms.to_numpy() * factor)).sum() / 60.0
    full = speed + control_minutes(drive["driven"], edges, cost)
    err = lambda p: abs(p - actual) / max(actual, 1e-9)
    return {"actual": actual, "free": free, "speed": speed, "full": full,
            "free_err": err(free), "speed_err": err(speed), "full_err": err(full)}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
