"""Build a routable road graph from the OSM PBF, annotated with scenic scores.

Splits drivable ways at shared intersection nodes into directed edges, computes
per-edge travel time, and tags each edge with the scenic score (and component
vector) of the nearest scored chunk from score.py. Keeps the largest *strongly*
connected component — oneway-aware, so every node is mutually reachable by car
rather than merely joined to the network by some road running the wrong way.

Outputs:
  data/processed/graph_nodes.parquet  node_id, lon, lat, exit_ref
  data/processed/graph_edges.parquet  u, v, length_m, minutes, score,
                                       c_water/c_coast/c_green/c_relief/...,
                                       name, ref, highway, junction,
                                       dest_ref, dest_name, n_<control>_fwd/rev,
                                       geometry (WGS84)
  data/processed/turn_restrictions.parquet
                                       via_node, from_edge, to_edge, kind

Usage: python graph.py <input.osm.pbf> <processed_dir>
"""

import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd
import shapely
from pyproj import Transformer
from shapely.strtree import STRtree

from common import (CONTROL_COLUMNS, CONTROL_KINDS, CRS_METERS, DRIVABLE,
                    ONEWAY_FWD, ONEWAY_REV, PRIVATE_ACCESS)
from score import blend, components, composite

# Assumed speed limit (km/h) per road class, used where OSM has no maxspeed tag
# — which is 77% of the network's kilometres, so these numbers matter more than
# the tagged ones do.
#
# They used to be guesses. They are now read off the roads of the same class
# that *are* tagged, as the harmonic mean weighted by length — harmonic because
# it is the speed that reproduces the right total travel time, which is the only
# thing this table is for. Re-derive with the query in
# docs/junction-timing-plan.md §13.
#
# The one that mattered: `residential` was 30, and 4,477 tagged kilometres say
# 40. Massachusetts posts 25 mph (40 km/h) as its statutory default in a thickly
# settled district and does not post 19 mph anywhere — the old number was not a
# speed limit at all. It governs 36,871 km of road, 56% of the state's network.
#
# `trunk` was 85 against a measured 64, and `unclassified` 45 against 37.
# The `_link` classes have single-digit tagged kilometres behind them and are
# left as they were rather than fitted to noise; likewise `living_street`, where
# the tag's own meaning (a street pedestrians share) is better evidence than
# 7.5 km of it.
#
# Note these are *posted limits*, not driving speeds. How much faster or slower
# a class is actually driven is SPEED_FACTOR in router.py, measured separately —
# and separating them is the point. Folded together, `residential` looked like a
# class people drive at 1.51x the limit; it is really a class whose limit the
# graph had wrong by a third.
SPEED_KMH = {
    "motorway": 97, "motorway_link": 58, "trunk": 64, "trunk_link": 50,
    "primary": 59, "primary_link": 45, "secondary": 53, "secondary_link": 40,
    "tertiary": 47, "tertiary_link": 38, "unclassified": 37, "residential": 40,
    "living_street": 12,
}

# OSM turn-restriction relations worth reading: the ones that forbid a movement
# outright, and the ones that permit only one and so forbid the rest.
#
# `no_right_turn_on_red` is deliberately absent — it restricts *when* you may
# turn, not whether, and treating it as a ban would route drivers around 40
# junctions they are allowed to use.
NO_TURN = {"no_left_turn", "no_right_turn", "no_straight_on", "no_u_turn",
           "no_entry", "no_exit"}
ONLY_TURN = {"only_straight_on", "only_left_turn", "only_right_turn",
             "only_u_turn"}


def parse_maxspeed(v: str) -> float | None:
    if not v:
        return None
    v = v.strip().lower()
    try:
        if "mph" in v:
            return float(v.replace("mph", "").strip()) * 1.60934
        return float(v.split()[0])
    except (ValueError, IndexError):
        return None


class GraphHandler(osmium.SimpleHandler):
    """Collect drivable ways with node ids + coordinates."""

    def __init__(self):
        super().__init__()
        self.ways = []           # (tags-dict, [node_ids], [(lon,lat)])
        self.node_count = {}     # node_id -> times referenced (for junctions)
        self.exit_refs = {}      # node_id -> exit number ("26", "13A")
        self.controls = {}       # node_id -> (kind, direction)
        self.restrictions = []   # (kind, from_way, via_node, to_way)
        self.skipped_restrictions = {}
        self.errors = 0

    def relation(self, r):
        """Collect turn restrictions: "no left here", "only straight on there".

        A restriction is a property of a *pair* of roads meeting at a junction,
        which is the one thing a graph keyed by nodes cannot say — so these are
        carried out to router.py, which splits the junction to say it. Reading
        them at all is new: measured on 2026-08-15, 7 of 40 random long
        Massachusetts routes told the driver to make a turn the map forbids.

        Only the `via`-node form is taken. The 604 `via`-way restrictions —
        "no U-turn via the crossover", where the forbidden movement spans a
        whole little road rather than a point — need the search to remember more
        than one junction back, and are left for later rather than approximated.
        """
        if r.tags.get("type") not in ("restriction", "restriction:motorcar"):
            return
        kind = (r.tags.get("restriction")
                or r.tags.get("restriction:motorcar") or "")
        # `no_left_turn @ (Mo-Fr 07:00-15:00)` — a conditional restriction, of
        # which Massachusetts has two. Enforced unconditionally: routing a
        # driver around a turn they could have made costs them a minute, and
        # the other way costs them a ticket.
        kind = kind.split("@")[0].strip()
        if kind not in NO_TURN and kind not in ONLY_TURN:
            self.skipped_restrictions[kind or "(untagged)"] = \
                self.skipped_restrictions.get(kind or "(untagged)", 0) + 1
            return
        frm = to = via = None
        for m in r.members:
            if m.role == "from" and m.type == "w":
                frm = m.ref
            elif m.role == "to" and m.type == "w":
                to = m.ref
            elif m.role == "via" and m.type == "n":
                via = m.ref
            elif m.role == "via" and m.type == "w":
                via = None
                break
        if frm and to and via:
            self.restrictions.append((kind, frm, via, to))
        else:
            self.skipped_restrictions["via-way or incomplete"] = \
                self.skipped_restrictions.get("via-way or incomplete", 0) + 1

    def node(self, n):
        """Pick up exit numbers and traffic controls.

        Exit numbers live on the *mainline* node where the ramp diverges — an
        OSM `highway=motorway_junction` carrying `ref=26` — and not on the ramp
        itself. That distinction is the whole reason this handler exists: 74%
        of Massachusetts' 1,363 junction nodes carry an exit number, while only
        4% of ramp ways carry any `ref` at all, and the ones that do hold the
        road number they lead to ("MA 3") rather than the exit. Reading the
        ramp would produce "Take exit MA 3".

        Traffic controls ride along in the same pass, and are read here rather
        than matched by proximity afterwards *because* they are nodes. A signal
        or a stop sign is mapped as a node of the road it governs, so the way
        that lists it is the road you stop on — no radius, no ambiguity. 87.5%
        of Massachusetts' controls are a node of a drivable way (94.9% of
        signals, 82.6% of stop signs); the remainder are on ways this pipeline
        does not route over, and are correctly ignored rather than dragged onto
        a nearby road. Matching by distance instead would be a guess with no
        good answer: measured on the built graph, 81.7% of controls have more
        than one candidate road within 15 m and 72.4% have three or more — a
        stop sign at a crossroads is within a few metres of all four approaches
        and governs one of them.

        `direction` says which way the traffic it stops is travelling, in the
        way's own node order, and 81% of Massachusetts' stop signs carry it. It
        is what makes the cost per direction rather than per road: a stop sign
        facing northbound traffic must not delay the driver heading south past
        its back.
        """
        tags = n.tags
        if tags.get("highway") == "motorway_junction":
            ref = tags.get("ref", "")
            if ref:
                self.exit_refs[n.id] = ref
        kind = CONTROL_KINDS.get(tags.get("highway"))
        if kind:
            # Signals spell it `traffic_signals:direction`; everything else uses
            # the bare tag. Anything other than a plain forward/backward —
            # "both", a missing tag, `direction=45` on a stray compass bearing —
            # falls through to charging both directions, which is the safe way
            # to be wrong about a control that is definitely there.
            direction = (tags.get("direction")
                         or tags.get("traffic_signals:direction") or "")
            self.controls[n.id] = (kind, direction.strip().lower())

    def way(self, w):
        hw = w.tags.get("highway")
        if hw not in DRIVABLE:
            return
        if w.tags.get("access") in PRIVATE_ACCESS and w.tags.get("motor_vehicle") != "yes":
            return
        ids, coords = [], []
        try:
            for n in w.nodes:
                if not n.location.valid():
                    continue
                ids.append(n.ref)
                coords.append((n.location.lon, n.location.lat))
        except osmium.InvalidLocationError:
            self.errors += 1
            return
        if len(ids) < 2:
            return
        oneway = w.tags.get("oneway", "")
        # OSM convention: roundabouts and motorways are one-way even when no
        # oneway tag is present (mappers rely on the implication). MA has ~160
        # untagged rotary ways; without this rule the router would happily send
        # a driver the wrong way around one.
        if not oneway and (
            w.tags.get("junction") in ("roundabout", "circular") or hw == "motorway"
        ):
            oneway = "yes"
        meta = {
            "highway": hw,
            # Kept only long enough to resolve turn restrictions, which name
            # their roads by way id, and dropped before the parquet is written.
            "way_id": w.id,
            "name": w.tags.get("name", ""),
            "ref": w.tags.get("ref", ""),
            # Kept rather than merely consulted. `junction` was already read
            # just above to infer oneway and then discarded, which is why a
            # rotary arrived at the driver as a run of unexplained slight
            # rights instead of "take the 2nd exit". MA has 1,234 of them, 693
            # named.
            "junction": w.tags.get("junction", ""),
            # Where a ramp leads, as it appears on the sign. `destination:ref`
            # is the road ("I 93 North"), `destination` the places it serves
            # ("Cambridgeport;Brookline"), semicolon-separated in OSM and left
            # that way here — splitting them is a rendering decision, and the
            # wording lives in router.py with the rest of the labels. 46% of MA
            # ramps carry one or the other.
            "dest_ref": w.tags.get("destination:ref", ""),
            "dest_name": (w.tags.get("destination", "")
                          or w.tags.get("destination:street", "")),
            "oneway": oneway,
            "speed": parse_maxspeed(w.tags.get("maxspeed", "")) or SPEED_KMH[hw],
        }
        self.ways.append((meta, ids, coords))
        for nid in ids:
            self.node_count[nid] = self.node_count.get(nid, 0) + 1


def count_controls(ids, a, b, controls, node_count=None):
    """The traffic controls a driver meets crossing `ids[a:b+1]`, per direction.

    Returns a dict of the CONTROL_COLUMNS. A control is charged to the
    traversal that *approaches* it, which is why this is not the simple
    membership test it looks like it should be.

    A junction node belongs to two edges at once — it ends one and begins the
    next — so a signal sitting on it appears in both node lists. Charging both
    would price every set of lights twice. Forward travel therefore pays for
    everything from just past the segment's first node through to its last
    (`a < i <= b`), and reverse travel for everything from the last node back
    through to its first (`a <= i < b`). Each direction excludes the end it
    departs from, so a control on a shared junction is paid for exactly once:
    by the edge you arrive on, never by the one you leave on.

    `direction` then narrows it further. A stop sign tagged `forward` faces
    traffic moving along the way's node order and does not exist for anyone
    going the other way — and because the two rules compose, such a sign at a
    segment's first node is charged to neither traversal of *this* edge and to
    the forward traversal of the edge that arrives there, which is exactly the
    driver who has to stop.

    That last rule only means anything on a node one way owns. `direction` is
    written in the node order of the single way the control governs, and this
    function runs once per way listing the node — so where several ways meet,
    "forward" is read as each of their node orders in turn and the delay lands
    on whichever approaches OSM happened to digitize in that direction.
    Measured, 5,360 of Massachusetts' 29,772 controls sit exactly on a graph
    junction node, so a main road can pay for a sign facing the side street
    one way and nothing the other, and reversing its digitization swaps them.
    `node_count` identifies those nodes; on them the tag is dropped and the
    arrival rule above is left to do the work, which charges each approach
    exactly once — the four-way signal every one of whose approaches faces a
    red light being the case the tag was getting wrong in both directions.
    """
    counts = dict.fromkeys(CONTROL_COLUMNS, 0)
    node_count = node_count or {}
    for i in range(a, b + 1):
        found = controls.get(ids[i])
        if not found:
            continue
        kind, direction = found
        if node_count.get(ids[i], 1) >= 2:
            direction = ""
        if i > a and direction != "backward":
            counts[f"n_{kind}_fwd"] += 1
        if i < b and direction != "forward":
            counts[f"n_{kind}_rev"] += 1
    return counts


def build_edges(ways, node_count, to_m, controls=None):
    """Split each way at junction nodes (degree >= 2) into edges."""
    controls = controls or {}
    rows = []
    for meta, ids, coords in ways:
        n = len(ids)
        # split indices: endpoints + any interior junction node
        breaks = [0]
        for i in range(1, n - 1):
            if node_count[ids[i]] >= 2:
                breaks.append(i)
        breaks.append(n - 1)
        for a, b in zip(breaks[:-1], breaks[1:]):
            seg = coords[a:b + 1]
            if len(seg) < 2:
                continue
            xs, ys = to_m([c[0] for c in seg], [c[1] for c in seg])
            length = float(np.hypot(np.diff(xs), np.diff(ys)).sum())
            if length < 1.0:
                continue
            rows.append({
                "u": ids[a], "v": ids[b],
                "way_id": meta["way_id"],
                "length_m": length,
                # Free-flow, and it stays that way. The two corrections that
                # make this a real travel time — the per-class speed factor and
                # the cost of the controls counted beside it — are applied by
                # router.py when it loads. Baking them in here would also
                # destroy the baseline `tools/analyze_trace.py` measures them
                # against: it reads `length_m / minutes` as the speed the graph
                # assumed, so a corrected column would have the next drive
                # report a factor of 1.00 and no way to tell whether the
                # correction was right.
                "minutes": length / 1000.0 / meta["speed"] * 60.0,
                "oneway": meta["oneway"],
                "name": meta["name"], "ref": meta["ref"], "highway": meta["highway"],
                "junction": meta["junction"],
                "dest_ref": meta["dest_ref"], "dest_name": meta["dest_name"],
                **count_controls(ids, a, b, controls, node_count),
                "geometry": shapely.LineString(seg),
            })
    return rows


def resolve_restrictions(edges, restrictions):
    """Turn (kind, from-way, via-node, to-way) into forbidden edge-row pairs.

    A way becomes several edges — `build_edges` splits it at every junction — so
    "you may not turn from way 123 into way 456" has to be pinned to the two
    *segments* that actually touch the junction. Resolved here, against the
    final edge table, because `largest_component` renumbers the rows and a
    restriction that names the wrong row silently bans an unrelated turn
    somewhere else in the state.

    `only_*` is expanded into its complement while the whole junction is in
    hand: "only straight on" is every other exit forbidden, and saying it that
    way means router.py needs one rule instead of two. The U-turn back onto the
    road you came in on is left out of that complement — a shortest path cannot
    make one anyway, and banning it would cost a node copy to say so.

    Returns a frame of (via_node, from_edge, to_edge, kind) and a count of what
    could not be resolved, which is expected to be non-zero: a restriction whose
    roads were dropped as unroutable, or which never reached the largest
    component, has nothing left to forbid.
    """
    by_way = {}
    for row, way in enumerate(edges["way_id"].to_numpy()):
        by_way.setdefault(way, []).append(row)
    u, v = edges["u"].to_numpy(), edges["v"].to_numpy()
    at_node = {}
    for row in range(len(edges)):
        at_node.setdefault(u[row], []).append(row)
        if v[row] != u[row]:
            at_node.setdefault(v[row], []).append(row)

    def touching(way, via):
        return [r for r in by_way.get(way, ()) if u[r] == via or v[r] == via]

    out, unresolved = [], 0
    for kind, frm, via, to in restrictions:
        from_rows, to_rows = touching(frm, via), touching(to, via)
        if not from_rows or not to_rows:
            unresolved += 1
            continue
        # A no-U-turn is the one kind that routinely names the same way on both
        # sides, and `build_edges` has already split that way at this junction —
        # so `to_rows` holds the segment *beyond* the node as well as the one
        # the driver came in on. The movement being forbidden is leaving by the
        # segment you arrived on; the other segment is driving straight through.
        # Banning by way alone here bans the straight-through and never the
        # U-turn, which is the mainline movement, and a shortest path would
        # never have made the U-turn anyway.
        if kind == "no_u_turn" and frm == to:
            continue
        # OSM's convention is that the from-way *ends* at the via node. When it
        # does not, `build_edges` has split it and `touching` returns both
        # halves — two opposite approaches, only one of which the mapper meant.
        # Banning from both forbids a legal movement (a no-left-turn applied to
        # the driver coming the other way, for whom it is a right), and the
        # router detours them around the block. Under-banning is the safer of
        # the two errors, so leave it unresolved where it can be counted.
        # Measured at 0 of 4,218 resolvable MA restrictions; other regions
        # follow the convention less reliably.
        if len(from_rows) > 1:
            unresolved += 1
            continue
        for f in from_rows:
            if kind in NO_TURN:
                # A pure backtrack is unreachable by a shortest path, so saying
                # so would cost a junction copy and forbid nothing.
                banned = [t for t in to_rows if t != f]
            else:
                allowed = set(to_rows) | {f}
                banned = [t for t in at_node.get(via, ()) if t not in allowed]
            for t in banned:
                out.append((via, f, t, kind))

    frame = pd.DataFrame(out, columns=["via_node", "from_edge", "to_edge", "kind"])
    return frame.drop_duplicates(subset=["via_node", "from_edge", "to_edge"]), unresolved


def main(pbf_path: str, processed_dir: str):
    d = Path(processed_dir)
    t0 = time.time()

    h = GraphHandler()
    h.apply_file(pbf_path, locations=True, idx="flex_mem")
    print(f"parsed {len(h.ways):,} drivable ways in {time.time() - t0:.0f}s "
          f"({h.errors} skipped)")

    fwd = Transformer.from_crs(4326, CRS_METERS, always_xy=True)
    to_m = lambda lons, lats: fwd.transform(lons, lats)

    rows = build_edges(h.ways, h.node_count, to_m, h.controls)
    edges = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
    counts = edges[CONTROL_COLUMNS]
    # A segment runs junction to junction, so its control count is a handful at
    # most and uint8 is enormous headroom. Checked anyway because numpy wraps
    # silently: 256 signals would become 0, and a road that costs nothing is
    # exactly the bug this column exists to fix.
    if counts.to_numpy().max() > np.iinfo("uint8").max:
        raise RuntimeError("a single edge carries more than 255 controls — "
                           "widen the dtype rather than wrapping it to zero")
    edges[CONTROL_COLUMNS] = counts.astype("uint8")
    print(f"built {len(edges):,} edges in {time.time() - t0:.0f}s")
    charged = edges[CONTROL_COLUMNS].to_numpy().sum()
    print(f"{len(h.controls):,} traffic controls, charged {charged:,} times "
          f"across both directions")

    # --- attach scenic score from the scored chunks the edge covers ---
    chunks = gpd.read_parquet(d / "scored_chunks.parquet").to_crs(CRS_METERS)
    component_cols = attach_scores(edges, chunks)
    print(f"scored edges ({len(component_cols)} components) in {time.time() - t0:.0f}s")

    # --- keep largest connected component (undirected reachability) ---
    edges = largest_component(edges)
    print(f"largest component: {len(edges):,} edges, "
          f"{len(set(edges['u']) | set(edges['v'])):,} nodes")

    # node table (coords from edge endpoints, plus any exit number)
    nodes = node_table(edges, h.exit_refs)
    print(f"{(nodes['exit_ref'] != '').sum():,} nodes carry an exit number")

    # --- turn restrictions, resolved against the final row numbering ---
    banned, unresolved = resolve_restrictions(edges, h.restrictions)
    print(f"{len(h.restrictions):,} via-node restrictions read "
          f"({unresolved:,} name roads this graph does not carry, "
          f"{dict(h.skipped_restrictions)} skipped) -> "
          f"{len(banned):,} forbidden turns at "
          f"{banned['via_node'].nunique():,} junctions")
    # Internal only: the way id exists to resolve the restrictions above, and a
    # column nothing reads is a column that goes stale.
    edges = edges.drop(columns=["way_id"])

    edges.to_parquet(d / "graph_edges.parquet")
    nodes.to_parquet(d / "graph_nodes.parquet")
    banned.to_parquet(d / "turn_restrictions.parquet")
    print(f"wrote graph_edges + graph_nodes + turn_restrictions in "
          f"{time.time() - t0:.0f}s")
    print(f"edge score: mean {edges['score'].mean():.2f}, "
          f"length {edges['length_m'].sum()/1000:.0f} km total")


# How finely an edge is sampled when averaging the chunks it runs over. A
# quarter of score.py's CHUNK_LEN, so every chunk an edge crosses is hit.
SAMPLE_STEP_M = 100.0

# How far a sample may sit from the chunk it takes its score from. A chunk is at
# most CHUNK_LEN long and sampling runs along the edge itself, so a correct join
# is metres; 150 m is slack for geometry that moved between extract and score.
MAX_CHUNK_SNAP_M = 150.0
# Above this share of strays the two files are not describing the same roads.
MAX_CHUNK_SNAP_SHARE = 0.005


def sample_offsets(lengths: np.ndarray, step: float):
    """Split each length into equal pieces of <= `step` and return, per piece,
    its parent's row index, the distance to its midpoint, and its length.

    The midpoints are where the chunk is read; the lengths are how much of the
    edge each reading speaks for. One piece for anything shorter than `step`,
    so a short edge behaves exactly as a single midpoint sample did.
    """
    counts = np.maximum(1, np.ceil(lengths / step).astype(np.int64))
    row = np.repeat(np.arange(len(lengths)), counts)
    # position of each piece within its own parent: 0, 1, ... counts[row]-1
    k = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    piece = lengths[row] / counts[row]
    return row, (k + 0.5) * piece, piece


def attach_scores(edges: gpd.GeoDataFrame, chunks: gpd.GeoDataFrame,
                  step: float = SAMPLE_STEP_M) -> list[str]:
    """Tag each edge with the scenic score of the chunks it runs over.

    Edges are split at junctions and chunks every 400 m, so the two grids do not
    line up: 7.7% of edges are longer than a chunk and they carry 35% of the
    state's road-km (rural roads run kilometres between junctions — exactly what
    a scenic route picks). Reading a single chunk at the edge's midpoint threw
    the rest of that away; measured against the true length-weighted value on
    edges over 800 m, the midpoint was off by a mean of 0.65 points and by up to
    3.9 on a 0-10 scale.

    So sample every SAMPLE_STEP_M instead and average, weighting each reading by
    the length it stands for. The composite `score` is *recomputed* from the
    averaged components rather than averaged itself, so it stays exactly
    `composite(sum(WEIGHTS * components), score_adj)` — the identity
    test_score_matches_components checks and the router's live re-blend needs.

    Returns the component column names it wrote.
    """
    tree = STRtree(chunks.geometry.values)
    geoms_m = np.asarray(edges.geometry.to_crs(CRS_METERS).values)
    lengths = shapely.length(geoms_m)

    row, along, piece = sample_offsets(lengths, step)
    pts = shapely.line_interpolate_point(geoms_m[row], along)
    nearest = tree.query_nearest(pts, all_matches=False)
    # query_nearest returns indices aligned to input order
    idx = nearest if nearest.ndim == 1 else nearest[1]

    # How far each sample actually had to reach for its chunk. Unbounded, this
    # join is silent when it is wrong: an edge with no chunk of its own (a way
    # `extract.py` dropped for a missing node but `graph.py` kept) takes the
    # nearest chunk at any distance, so a residential street beside I-90 can
    # inherit the interstate's components and its score_adj with nothing said.
    # A handful of strays is normal; a large share means these chunks were not
    # built from this PBF, which mis-scores the whole graph.
    gap = shapely.distance(pts, np.asarray(chunks.geometry.values)[idx])
    far = gap > MAX_CHUNK_SNAP_M
    if far.any():
        share = far.mean()
        detail = (f"{far.sum():,} of {len(pts):,} samples ({100 * share:.2f}%) "
                  f"are more than {MAX_CHUNK_SNAP_M:.0f} m from any scored chunk "
                  f"(worst {gap.max():,.0f} m)")
        if share > MAX_CHUNK_SNAP_SHARE:
            raise RuntimeError(
                f"{detail}. scored_chunks.parquet does not match this graph — "
                f"re-run score.py against the same PBF before attaching scores."
            )
        print(f"NOTE: {detail}; those edges take their nearest chunk's score.")

    n = len(edges)
    total = np.bincount(row, weights=piece, minlength=n)
    mean_of = lambda values: np.bincount(row, weights=values[idx] * piece,
                                         minlength=n) / total

    # Carry every per-segment "beauty vector" column (c_water, c_coast, ...).
    # Auto-detecting the c_-prefixed columns (rather than a hardcoded list)
    # means a new component added in score.py flows through with no change here.
    component_cols = components(chunks)
    for c in component_cols:
        edges[c] = mean_of(chunks[c].to_numpy())
    edges["score_adj"] = mean_of(chunks["score_adj"].to_numpy())

    # Share of this edge's length that is unpaved, 0..1. Averaged like a
    # component rather than folded into `score_adj`, because it is not part of
    # the beauty claim: `router.py` prices it in minutes, outside the scenery
    # term. A graph built before 2026-08-29 has no such column and the router
    # recovers the same number from `score_adj` instead — see
    # `Router._load_unpaved`.
    edges["unpaved_frac"] = mean_of(chunks["unpaved"].to_numpy())

    edges["score"] = composite(blend(edges[component_cols]).to_numpy(),
                               edges["score_adj"].to_numpy())
    return component_cols


def largest_component(edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep the edges whose endpoints are mutually reachable *by car*.

    Strongly connected on the directed graph, not weakly connected on an
    undirected one. The router traverses oneway-aware, so an undirected check
    answers a question nobody asks: measured on the MA graph it kept 645 nodes
    (199 km of road) that Dijkstra can never route out of, 167 of them with no
    outgoing edge at all. Those nodes sit within SNAP_MAX_M of a real address,
    so server/app.py accepted the request as in-region and then answered 404
    "no route found" for two perfectly ordinary Massachusetts points.

    The directed graph is built with the same oneway rules as router.py's
    `_build_directed` (shared via common.py), so the component kept here is
    exactly the one the router can traverse.
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    node_ids = pd.unique(pd.concat([edges["u"], edges["v"]]))
    idx = {nid: i for i, nid in enumerate(node_ids)}
    ui = edges["u"].map(idx).to_numpy()
    vi = edges["v"].map(idx).to_numpy()
    n = len(node_ids)
    ow = edges["oneway"].astype(str).str.lower()
    fwd_ok = ~ow.isin(ONEWAY_REV).to_numpy()
    rev_ok = ~ow.isin(ONEWAY_FWD).to_numpy()
    tails = np.concatenate([ui[fwd_ok], vi[rev_ok]])
    heads = np.concatenate([vi[fwd_ok], ui[rev_ok]])
    g = csr_matrix((np.ones(len(tails)), (tails, heads)), shape=(n, n))
    ncomp, labels = connected_components(g, directed=True, connection="strong")
    if ncomp == 1:
        return edges.reset_index(drop=True)
    biggest = np.bincount(labels).argmax()
    keep_nodes = set(node_ids[labels == biggest])
    mask = edges["u"].isin(keep_nodes) & edges["v"].isin(keep_nodes)
    return edges[mask].reset_index(drop=True)


def node_table(edges: gpd.GeoDataFrame, exit_refs: dict | None = None) -> pd.DataFrame:
    coords = shapely.get_coordinates(edges.geometry.values)
    counts = shapely.get_num_coordinates(edges.geometry.values)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    ends = np.cumsum(counts) - 1
    u_xy = coords[starts]
    v_xy = coords[ends]
    nid = np.r_[edges["u"].to_numpy(), edges["v"].to_numpy()]
    xy = np.vstack([u_xy, v_xy])
    _, first = np.unique(nid, return_index=True)
    table = pd.DataFrame({
        "node_id": nid[first],
        "lon": xy[first, 0],
        "lat": xy[first, 1],
    })
    # Empty for all but a handful of nodes — MA has ~1,000 numbered exits
    # against 310,000 nodes — but it has to ride on the node rather than the
    # edge, because an exit number describes the *point* where the ramp leaves
    # the mainline. That is exactly the seam the maneuver generator looks at.
    table["exit_ref"] = table["node_id"].map(exit_refs or {}).fillna("")
    return table


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
