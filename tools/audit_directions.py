"""How wrong can the turn-by-turn directions be? Measure every way at once.

    .venv/bin/python tools/audit_directions.py data/processed [routes]

"Accurate directions" is not one property, so this checks the separable ways a
route can mislead a driver and reports each on its own. A single pass/fail
number would hide which of them is actually costing anything.

  * **illegal turn** — the route makes a movement OSM explicitly forbids. Checked
    against the raw `type=restriction` relations in the PBF rather than against
    the graph's own `turn_restrictions.parquet`, deliberately: the graph resolves
    those relations to edge rows and drops the ones it cannot place, so checking
    against them would only prove the router agrees with itself, and would score
    a dropped restriction as a pass.

  * **silent fork** — the route passes a junction where another road leaves at
    nearly the same angle as the one taken, and the driver is told nothing. This
    is the failure that does not look like one in a test: every instruction is
    correct, and the driver still ends up on the wrong road.

  * **start and end offset** — routes begin and end at junctions, so the driver
    is told to set off from the corner rather than from where they are.

The PBF pass takes about two minutes and is cached beside the processed data.
"""

import math
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from graph import NO_TURN, ONLY_TURN  # noqa: E402
from router import (Router, _bearing, _bearing_in, _bearing_out,  # noqa: E402
                    _turn_delta)

# Two coordinates are the same OSM node within this many degrees. They come from
# the same source, so this is float rounding and nothing more.
SAME = 1e-7

# How close another road's departure bearing has to be to the one the route
# takes before a driver could plausibly follow it by mistake. 45 degrees is the
# same window `_turn_modifier` calls "slight", i.e. exactly the range where the
# geometry alone does not tell you which road is which.
FORK_DEGREES = 45.0

# How near a junction a step has to be to count as describing it.
STEP_NEAR_M = 12.0


def _dist_m(p, q):
    mid = math.radians((p[1] + q[1]) / 2.0)
    return math.hypot((q[0] - p[0]) * 111320.0 * math.cos(mid),
                      (q[1] - p[1]) * 110540.0)


# Enough road behind a junction to take an honest heading off, in metres. Longer
# than the chord it feeds so the chord is never the whole of it.
APPROACH_M = 60.0


def _approach(edge_coords, i):
    """The road leading into junction `i`, back far enough to have a bearing."""
    coords = list(edge_coords[i - 1])
    k = i - 2
    while k >= 0 and _dist_m(coords[0], coords[-1]) < APPROACH_M:
        coords = list(edge_coords[k])[:-1] + coords
        k -= 1
    return coords


# --- the forbidden movements, from the PBF ----------------------------------

def forbidden_movements(pbf, cache):
    """(via node, coord before it, coord after it) for every banned movement.

    Two passes: the relations name their roads by way id, and the ways come
    first in the file, so the second pass is the only way to learn where those
    roads actually are. Cached because it is two minutes either way.
    """
    if cache.exists():
        return pd.read_parquet(cache)

    import osmium

    class Relations(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.rows = []

        def relation(self, r):
            if r.tags.get("type") not in ("restriction", "restriction:motorcar"):
                return
            kind = (r.tags.get("restriction")
                    or r.tags.get("restriction:motorcar") or "").split("@")[0].strip()
            if kind not in NO_TURN and kind not in ONLY_TURN:
                return
            frm = to = via = None
            for m in r.members:
                if m.role == "from" and m.type == "w":
                    frm = m.ref
                elif m.role == "to" and m.type == "w":
                    to = m.ref
                elif m.role == "via":
                    if m.type == "n":
                        via = m.ref
                    else:
                        via = None
                        break
            if frm and to and via:
                self.rows.append((kind, frm, via, to))

    print("reading turn restrictions from the PBF (about two minutes)...")
    rel = Relations()
    rel.apply_file(str(pbf))
    wanted = {w for _, f, _, t in rel.rows for w in (f, t)}

    class Ways(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.nodes = {}

        def way(self, w):
            if w.id in wanted:
                self.nodes[w.id] = [(n.ref, n.location.lon, n.location.lat)
                                    for n in w.nodes if n.location.valid()]

    ways = Ways()
    ways.apply_file(str(pbf), locations=True, idx="flex_mem")

    def neighbours(way, via):
        seq = ways.nodes.get(way, ())
        out = []
        for i, (nid, lon, lat) in enumerate(seq):
            if nid != via:
                continue
            if i:
                out.append((seq[i - 1][1], seq[i - 1][2]))
            if i < len(seq) - 1:
                out.append((seq[i + 1][1], seq[i + 1][2]))
        return out

    rows = []
    for kind, frm, via, to in rel.rows:
        befores, afters = neighbours(frm, via), neighbours(to, via)
        if not befores or not afters:
            continue
        for bx, by in befores:
            if kind in NO_TURN:
                for ax, ay in afters:
                    rows.append((via, bx, by, ax, ay, kind, True))
            else:
                for ax, ay in afters:
                    rows.append((via, bx, by, ax, ay, kind, False))
    frame = pd.DataFrame(rows, columns=["via", "bx", "by", "ax", "ay",
                                        "kind", "banned"])
    frame.to_parquet(cache)
    print(f"  cached {len(frame):,} movements to {cache.name}")
    return frame


def index_movements(frame):
    """via node -> (banned pairs, allowed-only pairs) of coordinates."""
    banned, only = {}, {}
    for row in frame.itertuples(index=False):
        target = banned if row.banned else only
        target.setdefault(row.via, []).append(((row.bx, row.by), (row.ax, row.ay)))
    return banned, only


# --- the checks --------------------------------------------------------------

def illegal_turns(result, node_osm, banned, only):
    """Movements on this route that OSM forbids."""
    bad = []
    for i in range(1, len(result.edge_coords)):
        via = int(node_osm[result.nodes[i]])
        arrive = tuple(result.edge_coords[i - 1][-2])
        leave = tuple(result.edge_coords[i][1])
        same = lambda a, b: abs(a[0] - b[0]) < SAME and abs(a[1] - b[1]) < SAME
        for before, after in banned.get(via, ()):
            if same(before, arrive) and same(after, leave):
                bad.append(via)
        allowed = [a for b, a in only.get(via, ()) if same(b, arrive)]
        if allowed and not any(same(a, leave) for a in allowed):
            bad.append(via)
    return bad


def silent_forks(router, result, steps):
    """Junctions the driver can get wrong with no instruction to stop them.

    Two grades, because they are worth very different amounts.

    **misleading** — a road leaves the junction *straighter* than the route
    does, and nothing is said. This is the one that actually strands people: the
    driver holds the wheel, the road they are on carries them onto the rival,
    and every instruction the app gave was correct. It needs no judgement call
    about what a driver would find confusing — carrying straight on is simply
    the wrong move here, and the app did not say so.

    **ambiguous** — a rival leaves within `FORK_DEGREES` of the route but not
    straighter than it. Following the road works; the driver just cannot be sure
    from the screen. Reported separately because the fix is different (a name,
    not a maneuver) and because counting the two together makes a real problem
    look like an enormous one.
    """
    told = [(s["lat"], s["lon"]) for s in steps]
    # A rotary is described once, for the whole circle — "take the 2nd exit onto
    # Elm Street" names both the junctions you pass and the road you leave by.
    # So the junctions inside one, and the two where you join and leave it, are
    # covered by an instruction that is nowhere near them.
    rotary = [str(j).lower() in ("roundabout", "circular")
              for j in result.edges["junction"]]
    misleading, ambiguous = [], []
    for i in range(1, len(result.edge_coords)):
        if rotary[i] or rotary[i - 1]:
            continue
        junction = result.edge_coords[i][0]
        # Over a chord, not the last vertex pair. OSM packs vertices tightly
        # through a junction to shape the corner, so a bearing taken across the
        # final 4 m of an approach already points round the bend — which made
        # this tool disagree with the router about which road is the straight
        # one and report five forks that were not there.
        #
        # Walking back across edge boundaries to find that chord, because the
        # last edge before a junction is often shorter than one: OSM splits a
        # way at every junction, and a 10 m block between two of them would
        # otherwise put the whole measurement back on the noise it is avoiding.
        arrival = _bearing_in(_approach(result.edge_coords, i))
        taken = abs(_turn_delta(arrival, _bearing_out(result.edge_coords[i])))
        node = result.nodes[i]

        straighter, close = False, False
        for slot in router.outgoing_slots(node):
            coords = router.slot_coords(slot)
            if len(coords) < 2:
                continue
            rival = abs(_turn_delta(arrival, _bearing_out(coords)))
            # The road you came in on is not a rival: leaving by it is a U-turn,
            # which no driver makes by carrying straight on.
            if rival > 150:
                continue
            if abs(rival - taken) < 1e-6:
                continue        # this is the road the route takes
            if rival < taken:
                straighter = True
            if abs(rival - taken) < FORK_DEGREES:
                close = True

        if not (straighter or close):
            continue
        if any(_dist_m((junction[0], junction[1]), (lon, lat)) < STEP_NEAR_M
               for lat, lon in told):
            continue
        (misleading if straighter else ambiguous).append(node)
    return misleading, ambiguous


def audit(router, banned, only, n_routes, seed=5):
    node_osm = router.nodes["node_id"].to_numpy()
    rng = random.Random(seed)
    stats = Counter()
    routes = 0
    start_off, end_off = [], []

    while routes < n_routes:
        a = (rng.uniform(41.9, 42.6), rng.uniform(-72.0, -70.9))
        b = (rng.uniform(41.9, 42.6), rng.uniform(-72.0, -70.9))
        s, s_off = router.snap(*a)
        t, t_off = router.snap(*b)
        if max(s_off, t_off) > 2000 or s == t:
            continue
        for pref in (0.0, 1.0):
            result = router.route(s, t, pref)
            if result is None:
                stats["no route"] += 1
                continue
            routes += 1
            steps = result.steps()
            bad = illegal_turns(result, node_osm, banned, only)
            misleading, ambiguous = silent_forks(router, result, steps)
            stats["illegal turns"] += len(bad)
            stats["routes with an illegal turn"] += bool(bad)
            stats["misleading forks"] += len(misleading)
            stats["routes with a misleading fork"] += bool(misleading)
            stats["ambiguous forks"] += len(ambiguous)
            stats["routes with an ambiguous fork"] += bool(ambiguous)
            stats["junctions"] += max(len(result.edge_coords) - 1, 0)
            stats["steps"] += len(steps)
            head = result.line.coords[0]
            tail = result.line.coords[-1]
            start_off.append(_dist_m((a[1], a[0]), head))
            end_off.append(_dist_m((b[1], b[0]), tail))

    return routes, stats, np.array(start_off), np.array(end_off)


def _find_pbf():
    """The OSM extract, from SCENIC_PBF or the usual place in data/raw.

    Same rule as `tools/analyze_trace.py`, and needed for the same reason: run
    from a git worktree, `data/` lives only in the main checkout.
    """
    import os

    override = os.environ.get("SCENIC_PBF")
    if override:
        return Path(override)
    return next(iter(sorted((ROOT / "data" / "raw").glob("*.osm.pbf"))), None)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    data = Path(argv[1])
    n = int(argv[2]) if len(argv) > 2 else 60

    cache = data / "forbidden_movements.parquet"
    pbf = _find_pbf()
    if not cache.exists() and pbf is None:
        print("no OSM extract found — put the .pbf in data/raw or set SCENIC_PBF. "
              "It is only needed once; the movements are cached afterwards.")
        return 1
    frame = forbidden_movements(pbf, cache)
    banned, only = index_movements(frame)
    print(f"{len(frame):,} forbidden movements at {frame.via.nunique():,} junctions\n")

    router = Router(str(data))
    print(f"router: {router.n:,} node slots ({len(router.nodes):,} junctions, "
          f"{len(router.node_copies):,} split for restrictions)\n")

    routes, stats, start_off, end_off = audit(router, banned, only, n)

    print(f"AUDITED {routes} routes, {stats['junctions']:,} junctions, "
          f"{stats['steps']:,} instructions\n")
    print(f"  {'failure':<38}{'count':>8}{'routes':>9}{'per route':>12}")
    for label, total, per_route in (
        ("illegal turn", stats["illegal turns"], stats["routes with an illegal turn"]),
        ("misleading fork (straight is wrong)", stats["misleading forks"],
         stats["routes with a misleading fork"]),
        ("ambiguous fork (unnamed choice)", stats["ambiguous forks"],
         stats["routes with an ambiguous fork"]),
    ):
        print(f"  {label:<38}{total:>8}{per_route:>9}"
              f"{100 * per_route / max(routes, 1):>11.0f}%")

    print(f"\n  {'start offset':<38}median {np.median(start_off):>5.0f} m   "
          f"p90 {np.percentile(start_off, 90):>5.0f} m")
    print(f"  {'end offset':<38}median {np.median(end_off):>5.0f} m   "
          f"p90 {np.percentile(end_off, 90):>5.0f} m")
    print("\n  offsets are from the point asked for to where the route actually")
    print("  starts and ends — routes begin and end at junctions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
