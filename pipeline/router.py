"""Scenic routing over the annotated road graph.

Loads graph_edges/graph_nodes, expands directed edges (honoring oneway), and
runs a Dijkstra whose edge weight blends travel time with an "unscenic" penalty:

    weight = minutes + pref**PREF_CURVE * BETA * km * (1 - score/10)

where `minutes` is driving time at the class's *measured* speed plus the time
lost to the traffic signals and stop signs on that road in that direction — not
the free-flow number stored in the graph. See SPEED_FACTOR and CONTROL_SECONDS.

The penalty is a minutes-equivalent cost charged per kilometer of *unscenic*
road (BETA min/km at full ugliness), so the router trades extra distance for
beauty instead of only shaving seconds. `pref` (0..1) is the overall scenery
strength: 0 gives the fastest route, 1 leans hard into scenery.

The per-edge `score` is computed *live* from the road's beauty vector so the
user can weight beauty types differently (see BEAUTY_TYPES) — e.g. favor coast
and town centers, ignore farmland. At neutral weights (all 1.0) the live score
exactly reproduces the precomputed composite from score.py. The same graph
answers fastest vs scenic, so we return them side by side with a breakdown.

CLI:
    python router.py <processed_dir> "lat,lon" "lat,lon" [pref]
writes out/route_fastest.geojson and out/route_scenic.geojson.
"""

import json
import math
import sys
from functools import cached_property
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.strtree import STRtree
from pyproj import Transformer

from common import CONTROL_COLUMNS, CRS_METERS, ONEWAY_FWD, ONEWAY_REV
from score import WEIGHTS, composite

# Minutes-equivalent penalty per km of fully-unscenic road at pref=1.
#
# Raised from 7.0 when travel time stopped being free-flow. This is a cost in
# *minutes*, competing against a `minutes` term that grew — scenic back roads
# got slower and picked up the stop signs on them, while motorways got 16%
# faster and carry almost none — so the same 7.0 bought measurably less detour
# than it used to. Measured over 20 routes, the share of a pref-0.25 route that
# leaves the fastest road fell from 70% to 50% and the scenery it found fell
# from 4.62 to 3.39 on the 0-10 scale: the slider's bottom half had quietly gone
# soft. 10.0 restores it (71%, 4.39) and leaves the top half where it was — the
# penalty saturates up there, so pref 1.0 moves from 5.49 to 5.58 and the routes
# barely change.
#
# Read that as calibration, not preference: `pref` means the same thing to a
# driver as it did before, and it takes a bigger number to mean it now.
BETA = 10.0

# --- Travel time --------------------------------------------------------------
# `graph_edges.minutes` is free-flow — length over the speed limit, with nothing
# charged for stopping. Measured against two recorded drives it ran 22% short of
# the clock. The two corrections below close that, and are applied here at load
# rather than baked into the parquet for two reasons: `tools/analyze_trace.py`
# reads `length_m / minutes` as the speed the graph assumed, so a pre-corrected
# column would have every future drive report a factor of 1.00 whether or not
# the correction was any good; and re-fitting these as drives accumulate is then
# a constant and a restart, not a 135 s rebuild plus copying 80 MB to the
# serving box. See docs/junction-timing-plan.md §5.

# Measured moving speed over the speed the graph assumed, per road class, with
# stopped time excluded (that is priced separately below). From 63 km of trace
# on 2026-08-14; only classes with at least 5 km behind them are listed, and
# everything else — including `residential`, which is 62% of the network's km
# and has 0.9 km of measurement — stays at 1.0 rather than being guessed.
#
# Motorway is above 1.0 because drivers exceed the posted limit by 16%, and 97%
# of motorway km carry a real `maxspeed` tag, so that is measured against the
# sign rather than against a fallback. An ETA predicts what the driver will do.
SPEED_FACTOR = {
    "motorway": 1.16,
    "secondary": 0.93,
    "tertiary": 0.89,
    "primary": 0.86,
}

# Seconds lost per traffic control *met* — P(stop) and the delay when you do
# stop, folded into the one number a static graph can charge. Fitted by
# `tools/fit_junction_cost.py`: signals were met 53 times for 16 stops averaging
# 31.7 s, stop signs 9 times for 6 stops averaging 14.0 s.
#
# These are an average over a quiet hour and a busy one, and that is the most a
# static graph can be. The two drives met almost the same number of signals — 26
# and 27 — and stopped at 4 and 12 of them, so fitting either drive alone gives
# 2.7 s or 16.1 s per signal. The location of a signal is structural; the wait
# at it is not. See docs/junction-timing-plan.md §10.
#
# Give-ways are the one number here that is a judgement rather than a
# measurement — the two drives met none. Half a stop sign, on the grounds that
# yielding is cheaper than stopping and that charging zero is a known error in a
# known direction. Massachusetts has 839 of them against 17,567 stop signs, so
# the choice moves an ETA by well under a tenth of a percent either way.
CONTROL_SECONDS = {"signal": 9.5, "stop": 9.3, "giveway": 4.7}

# The scenery penalty saturates — past a few minutes-per-km the router has taken
# every detour worth taking — which used to leave the slider's top half handing
# back an identical route. Most of that was really the compressed score scale
# (see RAW_BASE in score.py): with the full 0-10 range in play the penalty
# discriminates enough that a near-linear slider already spreads well. This mild
# exponent evens out what remains. Measured over four routes, exponents above
# ~1.5 overcorrect, trading the dead top for a dead bottom.
PREF_CURVE = 1.3

# --- Beauty types -----------------------------------------------------------
# The six *tunable* beauty types — the kinds of scenery a driver would actually
# choose between. One row per type:
#     (api name, display label, edge column, default weight)
# The default weight is the calibrated value from score.py's WEIGHTS, so a user
# weight of 1.0 reproduces the original composite score exactly; >1 leans into
# that type, 0 ignores it.
#
# The display labels are mirrored on the client in ios/Sources/Models.swift
# (RouteProps.sceneryBreakdown) — keep the label set in sync.
BEAUTY_TYPES = [
    ("water",  "water",       "c_water",  WEIGHTS["water"]),
    ("coast",  "coast",       "c_coast",  WEIGHTS["coast"]),
    ("forest", "forest/park", "c_green",  WEIGHTS["green"]),
    ("hills",  "hills",       "c_relief", WEIGHTS["relief"]),
    ("farm",   "farmland",    "c_farm",   WEIGHTS["farm"]),
    ("town",   "town",        "c_urban",  WEIGHTS["urban"]),
]

# The calibrated weight of each tunable type, in BEAUTY_TYPES order. Its sum is
# the "weight mass" a user's slider settings are renormalized back onto — see
# Router._edge_scores.
DEFAULT_WEIGHTS = np.array([w for *_, w in BEAUTY_TYPES])

# Baseline "quality" signals: always on, not user-tunable. Twistiness, mapped
# viewpoints, and explicit scenic tags form a floor of beauty under every road,
# so a fine road no one toggled on is never scored flat zero.
BASELINE = [
    ("c_curves",     WEIGHTS["curves"]),
    ("c_views",      WEIGHTS["views"]),
    ("c_scenic_tag", WEIGHTS["scenic_tag"]),
]

# Minimum component value for a stretch of road to count toward a beauty type in
# the route summary. It has to sit *below* the smallest partial-credit band any
# component awards, or that band is silently invisible: score.py gives water 0.45
# at 120-350 m and towns 0.5 in a settlement's wider orbit, and the old 0.5
# threshold dropped every metre of the water band — 18% of the network's km — so
# a route hugging a river 200 m away reported "water: 0 mi".
#
# 0.4 rather than exactly 0.45, because graph.py averages components over an
# edge's length: a stretch that is mostly-but-not-entirely in the band lands
# just under it. The cost of the margin is that `hills`, the one component
# that is continuous rather than banded, now counts 40 m of local relief
# instead of 50 m (25.9% of the network's km rather than 16.0%). That is a
# taste call either way; the water band being invisible was not.
# `test_breakdown_threshold_admits_every_partial_credit_band` is the tripwire if
# a DIST band in score.py is ever retuned below this.
BREAKDOWN_MIN = 0.4

# The route-summary breakdown: km of road passing each beauty type. Derived from
# BEAUTY_TYPES so the two never drift.
SCENERY_BREAKDOWN = [(label, col, BREAKDOWN_MIN) for _, label, col, _ in BEAUTY_TYPES]

_TO_M = Transformer.from_crs(4326, CRS_METERS, always_xy=True)


class Router:
    # Columns the maneuver generator needs, and the graph build that writes
    # them. Checked at load and refused loudly, rather than degraded silently:
    # a graph built before the maneuver rework still *loads* and still routes,
    # so the failure would be a server quietly answering every rotary with a
    # slight right and every exit with nothing — which is the exact
    # silent-disagreement case server/DEPLOY.md exists to warn about.
    REQUIRED_EDGE_COLUMNS = ("junction", "dest_ref", "dest_name", *CONTROL_COLUMNS)
    REQUIRED_NODE_COLUMNS = ("exit_ref",)

    def __init__(self, processed_dir: str):
        d = Path(processed_dir)
        self.edges = gpd.read_parquet(d / "graph_edges.parquet")
        self.nodes = pd.read_parquet(d / "graph_nodes.parquet")
        self._require_columns()

        ids = self.nodes["node_id"].to_numpy()
        self.idx = {nid: i for i, nid in enumerate(ids)}
        self.n = len(ids)
        self._nx, self._ny = _TO_M.transform(
            self.nodes["lon"].values, self.nodes["lat"].values
        )

        # Spatial index over the road *geometry*, not just its junctions, so
        # snap() can find the road a point actually sits on — see snap(). Costs
        # ~1 s to project and ~0.1 s to index, once, at startup.
        self._edge_geom_m = self.edges.geometry.to_crs(CRS_METERS).values
        self._edge_tree = STRtree(self._edge_geom_m)

        self._build_directed()

    def _require_columns(self):
        missing = [c for c in self.REQUIRED_EDGE_COLUMNS
                   if c not in self.edges.columns]
        missing += [c for c in self.REQUIRED_NODE_COLUMNS
                    if c not in self.nodes.columns]
        if missing:
            raise RuntimeError(
                f"the graph is missing {', '.join(missing)} — it was built "
                "before turn-by-turn maneuvers and junction timing needed those "
                "tags. Rerun pipeline/graph.py and copy BOTH parquets over; see "
                "server/DEPLOY.md."
            )

    @cached_property
    def is_roundabout(self):
        """Per undirected edge, whether it is part of a rotary."""
        j = self.edges["junction"].astype(str).str.lower()
        return j.isin(("roundabout", "circular")).to_numpy()

    @cached_property
    def exits_at_node(self):
        """Per node, how many roads leave it that are not part of a rotary.

        Counted over *outgoing* directed edges, not attached ones: a one-way
        street pointing into a rotary is an entrance, and counting it would put
        "take the 2nd exit" one exit early for every driver who passed one.
        """
        leaves = ~self.is_roundabout[self.eidx]
        return np.bincount(self.tail[leaves], minlength=self.n)

    @cached_property
    def maneuver_context(self):
        exit_refs = {}
        refs = self.nodes["exit_ref"].astype(str).to_numpy()
        for i, ref in enumerate(refs):
            if ref and ref != "nan":
                exit_refs[i] = ref
        return ManeuverContext(exit_refs, self.exits_at_node)

    def _driving_minutes(self) -> np.ndarray:
        """Per undirected edge, free-flow time corrected to real moving speed.

        Divided rather than multiplied: `SPEED_FACTOR` is measured speed over
        assumed speed, and a road driven at 0.93 of its limit takes 1/0.93 as
        long to cover.

        Applied to the computed `minutes` and not to `graph.py`'s `SPEED_KMH`
        fallback table, which is the trap this whole correction is arranged
        around. The table is consulted only where OSM has no `maxspeed` tag, so
        scaling it would move 3% of motorway km and 60% of secondary — landing
        least on the classes whose factor is best measured, and saying nothing
        about it.
        """
        factor = self.edges["highway"].map(SPEED_FACTOR).fillna(1.0).to_numpy()
        return self.edges["minutes"].to_numpy() / factor

    def _control_minutes(self) -> tuple[np.ndarray, np.ndarray]:
        """Per undirected edge, minutes lost to traffic controls each way.

        `graph.py` charges each control to the edge whose node list contains it
        and to the direction it faces, so the forward and reverse counts differ:
        a stop sign facing northbound traffic is on the southbound driver's road
        and costs them nothing.
        """
        out = []
        for direction in ("fwd", "rev"):
            seconds = sum(cost * self.edges[f"n_{kind}_{direction}"].to_numpy()
                          for kind, cost in CONTROL_SECONDS.items())
            out.append(seconds / 60.0)
        return out[0], out[1]

    def _build_directed(self):
        e = self.edges
        ui = e["u"].map(self.idx).to_numpy()
        vi = e["v"].map(self.idx).to_numpy()
        # Kept per undirected edge (not per directed slot) so snap() can pick
        # between the two ends of the road segment it landed on.
        self.edge_u_idx, self.edge_v_idx = ui, vi
        minutes = self._driving_minutes()
        control_fwd, control_rev = self._control_minutes()
        ow = e["oneway"].astype(str).str.lower()

        fwd_ok = ~ow.isin(ONEWAY_REV).to_numpy()
        rev_ok = ~ow.isin(ONEWAY_FWD).to_numpy()

        tails, heads, eidx, flip = [], [], [], []
        for ok, t, hh, f in [(fwd_ok, ui, vi, False), (rev_ok, vi, ui, True)]:
            sel = np.where(ok)[0]
            tails.append(t[sel]); heads.append(hh[sel])
            eidx.append(sel); flip.append(np.full(len(sel), f))
        self.tail = np.concatenate(tails)
        self.head = np.concatenate(heads)
        self.eidx = np.concatenate(eidx)        # back-reference to undirected edge row
        self.flip = np.concatenate(flip)
        self.km = e["length_m"].to_numpy() / 1000.0   # per undirected edge
        # The travel time Dijkstra optimises and RouteResult reports: driving
        # time, plus whatever stopping the direction of travel is charged for.
        # One array, used for both, so the ETA cannot describe a different
        # journey from the one that was chosen.
        self.d_minutes = minutes[self.eidx] + np.where(
            self.flip, control_rev[self.eidx], control_fwd[self.eidx])

        # Parallel edges: more than one directed edge can join the same
        # (tail, head) — parallel roads between the same two junctions. scipy's
        # csr_matrix *sums* duplicate coordinates, which would over-charge those
        # hops, so route() collapses each node-pair to its single cheapest edge.
        # Precompute the unique pairs and a slot -> pair-index map once here.
        pairs = np.stack([self.tail, self.head], axis=1)
        unique_pairs, slot_pair = np.unique(pairs, axis=0, return_inverse=True)
        self.u_tail, self.u_head = unique_pairs[:, 0], unique_pairs[:, 1]
        self.slot_pair = slot_pair.ravel()
        self.n_pairs = len(unique_pairs)

        # Inputs for re-scoring each edge live under a user's beauty weights (see
        # _edge_scores). We split the score.py formula into pieces that let us
        # recompute it as one matrix-vector product per request:
        #   base_score   : the always-on baseline blend (curves/views/scenic tag)
        #   pref_matrix  : column k = default_weight_k * component_k, so scaling
        #                  column k by the user's weight leans into that type
        #   score_adj    : the road-class/surface penalty, re-added after stretch
        self.score_adj = e["score_adj"].to_numpy()
        self.base_score = sum(w * e[col].to_numpy() for col, w in BASELINE)
        self.pref_matrix = np.column_stack(
            [w * e[col].to_numpy() for _, _, col, w in BEAUTY_TYPES]
        )

        # (tail, head) -> directed-edge slots, used in _collect to map a node
        # path back to edges. Parallel roads mean a pair can hold several slots,
        # so this is a CSR-style grouping: the slots for pair p live in
        # _pair_slots[_pair_start[p]:_pair_start[p + 1]], and _pair_key holds
        # each pair's (tail, head) folded into one sorted integer to bisect on.
        #
        # A dict keyed by (tail, head) tuples is the obvious way to write this
        # and cost ~200 MB of the server's ~1 GB — three quarters of a million
        # boxed tuples, boxed ints and one-element lists — plus the eager Python
        # loop that built them at every startup. These arrays are ~18 MB.
        order = np.argsort(self.slot_pair, kind="stable")
        self._pair_slots = order
        self._pair_start = np.searchsorted(self.slot_pair[order],
                                           np.arange(self.n_pairs + 1))
        self._pair_key = self.u_tail.astype(np.int64) * self.n + self.u_head

    def _edge_scores(self, weights: dict) -> np.ndarray:
        """Per *undirected* edge 0-10 scenic score under the given beauty weights.

        `weights` maps a BEAUTY_TYPES api-name to a multiplier (1.0 = the
        calibrated default, >1 leans in, 0 ignores); missing types default to
        1.0. Blending the components is the only part done here — the 0-10
        transform is score.py's `composite`, imported rather than re-derived so
        the live score and the precomputed column cannot drift apart. At
        all-1.0 weights this equals the precomputed `score` column exactly.

        The weight vector is renormalized to hold the *total* tunable weight
        constant, so these knobs change the scenery mix and `pref` alone sets
        the strength — which is what the tune screen tells the user they do.
        Without it the sliders quietly destroy the scale they feed: pushing all
        six to the app's maximum pinned 17% of the state's road-km at exactly
        10.0, and the router's penalty is `km * (1 - score/10)`, so every pinned
        road became free and indistinguishable from every other. Cranking
        everything then returned a route no more scenic than neutral and 5 km
        longer — the "more of everything" request making the result worse.
        Renormalizing maps that request back to "no preference", which is what
        it means. All-1.0 is a fixed point, so the precomputed column is
        untouched.
        """
        w = np.array([weights.get(name, 1.0) for name, *_ in BEAUTY_TYPES], float)
        mass = float(w @ DEFAULT_WEIGHTS)
        if mass > 0:
            w = w * (DEFAULT_WEIGHTS.sum() / mass)
        raw = self.base_score + self.pref_matrix @ w
        return composite(raw, self.score_adj)

    def _weights(self, pref: float, scores: np.ndarray) -> np.ndarray:
        """Directed-edge Dijkstra weights: travel time + a scenery detour cost.

        `scores` is the per-undirected-edge 0-10 score from `_edge_scores`,
        passed in rather than recomputed so the route is reported on exactly the
        scale it was optimized against (see `route`).
        """
        penalty = self.km * (1.0 - scores / 10.0)           # km of "unscenic" road
        # Clamped because a negative pref raised to a fractional power is a
        # *complex* number in Python ((-0.5) ** 1.3), which would silently poison
        # the whole cost matrix. The API clamps too; this keeps the class safe
        # for its other caller, the CLI.
        strength = max(0.0, min(1.0, pref)) ** PREF_CURVE
        return self.d_minutes + strength * BETA * penalty[self.eidx]

    def snap(self, lat: float, lon: float,
             heading: float | None = None) -> tuple[int, float]:
        """Routable node for a lat/lon: (node index, meters to the road).

        Finds the nearest road *segment* first, then takes one of that segment's
        two ends — rather than going straight to the nearest junction. The
        difference is not cosmetic. Graph nodes are junctions, and a house
        mid-block is routinely closer, in a straight line, to a junction on the
        street behind it than to either end of its own street. Snapping to the
        nearest junction outright therefore started 23% of sampled residential
        addresses on a road the driver was not on (measured over 400 blocks; the
        drivers' complaint was "it starts me on a different road than I live
        on"). Going via the segment makes that 0%: both ends of the nearest
        segment are, by construction, on the road you are standing on.

        Which end depends on whether the caller knows where the driver is
        pointing. Planning a trip from a parked car, there is no travel
        direction and the *nearer* end is right. Mid-drive there is, and the
        nearer end is as often as not the junction just passed — so a reroute
        computed from it can legitimately open by sending the driver back the
        way they came, which the first test drive did in fact do. Given
        `heading` (course over ground in degrees, 0=N, clockwise) the end that
        lies more nearly *ahead* is chosen instead.

        Pass `heading` only while actually moving. CoreLocation reports a course
        of -1 when it has no opinion and its course is noise at walking pace; a
        confidently wrong heading is worse here than none, because it points the
        route at the wrong end of the road with no distance check to catch it.
        Out-of-range values are therefore ignored rather than trusted.

        The distance returned is to the road itself, so callers can reject
        points that aren't on the network at all — e.g. a request from outside
        Massachusetts would otherwise silently snap to a border town and return
        a nonsense route.
        """
        x, y = _TO_M.transform(lon, lat)
        point = shapely.Point(x, y)
        e = int(self._edge_tree.nearest(point))
        u, v = int(self.edge_u_idx[e]), int(self.edge_v_idx[e])
        if heading is None or not 0.0 <= heading < 360.0:
            du = (self._nx[u] - x) ** 2 + (self._ny[u] - y) ** 2
            dv = (self._nx[v] - x) ** 2 + (self._ny[v] - y) ** 2
            node = u if du <= dv else v
        else:
            node = self._forward_end(x, y, u, v, heading)
        return node, float(self._edge_geom_m[e].distance(point))

    def _forward_end(self, x: float, y: float, u: int, v: int,
                     heading: float) -> int:
        """Whichever of a segment's two ends lies more nearly ahead of a driver
        at (x, y) travelling on `heading`.

        Bearings are taken in projected metres rather than on the sphere.
        `CRS_METERS` is a conformal conic, so a grid bearing differs from a true
        one by the convergence angle — under 1.5 degrees anywhere in
        Massachusetts, against a decision that is almost always ~180 degrees
        apart. The approximation is nowhere near the margin.
        """
        best, best_delta = None, None
        for node in (u, v):
            dx, dy = self._nx[node] - x, self._ny[node] - y
            # The driver standing exactly on a junction gives no direction to
            # it; the other end still does.
            if dx == 0.0 and dy == 0.0:
                continue
            # Easting/northing, so a compass bearing is atan2(east, north).
            bearing = math.degrees(math.atan2(dx, dy)) % 360.0
            delta = abs((bearing - heading + 180.0) % 360.0 - 180.0)
            if best_delta is None or delta < best_delta:
                best, best_delta = node, delta
        # Both ends degenerate to the driver's own position: nothing to choose
        # between, and returning a valid node matters more than which.
        return u if best is None else best

    def route(self, src_idx: int, dst_idx: int, pref: float, weights: dict = None):
        # Scored once, then used for both jobs: choosing the route and reporting
        # it. They used to disagree — the router optimized the live re-blend
        # while RouteResult.mean_score read the stored neutral column, so a user
        # who asked for coast was shown a number computed as though they hadn't
        # (measured 1.9 points apart on a 0-10 scale). That number is the whole
        # output of the tune screen.
        scores = self._edge_scores(weights or {})
        w = self._weights(pref, scores)
        # Collapse parallel edges to the cheapest weight per node-pair, so the
        # cost matrix has one entry per pair (no summed duplicates).
        pair_w = np.full(self.n_pairs, np.inf)
        np.minimum.at(pair_w, self.slot_pair, w)
        g = csr_matrix((pair_w, (self.u_tail, self.u_head)), shape=(self.n, self.n))
        dist, pred = dijkstra(g, directed=True, indices=src_idx,
                              return_predecessors=True)
        if not np.isfinite(dist[dst_idx]):
            return None
        # reconstruct node path
        path = []
        cur = dst_idx
        while cur != src_idx and cur >= 0:
            path.append(cur)
            cur = pred[cur]
        if cur < 0:
            return None
        path.append(src_idx)
        path.reverse()
        return self._collect(path, w, scores)

    def _collect(self, path, w, scores):
        """Turn a Dijkstra node path into the chosen edges, in travel order.

        Dijkstra hands back a sequence of node indices. For each hop (a -> b) we
        look up the directed edge(s) joining them and keep the one the cost
        actually used — the cheapest under the same weights `w` Dijkstra saw, so
        the drawn geometry and scenery match the chosen parallel road — then
        gather that edge's row and geometry (reversed if we drove it backwards).
        The edges come out in travel order, which is exactly what the
        turn-by-turn step list walks over to emit "turn onto X" maneuvers.
        """
        hops = np.asarray(path, dtype=np.int64)
        keys = hops[:-1] * self.n + hops[1:]
        pairs = np.searchsorted(self._pair_key, keys)

        chosen = []
        for hop, pair in enumerate(pairs):
            if pair >= self.n_pairs or self._pair_key[pair] != keys[hop]:
                # Every (tail, head) Dijkstra can traverse was indexed from the
                # same arrays, so this is unreachable. Skipping the hop silently
                # would be the worst way to be wrong about that: the drawn line
                # would jump the gap and the distance would be under-reported,
                # with nothing anywhere saying so.
                raise RuntimeError(f"no directed edge for hop {hops[hop]} -> "
                                   f"{hops[hop + 1]}; graph index is inconsistent")
            slots = self._pair_slots[self._pair_start[pair]:self._pair_start[pair + 1]]
            chosen.append(int(slots[np.argmin(w[slots])]))

        # The undirected edge rows (stats, names, geometry) in travel order.
        edge_rows = [self.eidx[k] for k in chosen]
        rows = self.edges.iloc[edge_rows]

        # Stitch the per-edge geometries into one line, flipping any edge we
        # traversed against its stored direction so the points run start -> end.
        coords = []
        for k in chosen:
            c = shapely.get_coordinates(self.edges.geometry.values[self.eidx[k]])
            if self.flip[k]:
                c = c[::-1]
            coords.append(c)
        # `coords` is the per-edge geometry in travel order; the steps generator
        # uses it (with the edge names) to build maneuvers. `path` goes with it
        # because two of those maneuvers are properties of the *junctions*
        # rather than the roads: which numbered exit this is, and how many roads
        # you pass going round a rotary.
        #
        # The per-hop travel time Dijkstra actually weighted, carried rather
        # than re-derived. Summing the edge table's `minutes` instead would
        # report a free-flow, direction-blind number for a route chosen on a
        # corrected, directional one — the same silent disagreement that once
        # had `mean_score` reporting the neutral score for a route optimised
        # under the user's beauty weights.
        return RouteResult(rows, stitch(coords), coords, scores[edge_rows],
                           nodes=path, context=self.maneuver_context,
                           edge_minutes=self.d_minutes[chosen])


def stitch(coord_arrays):
    if not coord_arrays:
        return None
    out = [coord_arrays[0]]
    for c in coord_arrays[1:]:
        out.append(c[1:] if len(c) > 1 else c)
    return shapely.LineString(np.vstack(out))


# --- Turn-by-turn maneuver helpers ------------------------------------------

_COMPASS = ["north", "northeast", "east", "southeast",
            "south", "southwest", "west", "northwest"]


def _bearing(p, q):
    """Compass bearing in degrees (0=N, 90=E) along the ground from lon/lat
    point p to point q."""
    lon1, lat1, lon2, lat2 = map(math.radians, [p[0], p[1], q[0], q[1]])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _compass(bearing):
    """Nearest of the 8 compass directions for a bearing."""
    return _COMPASS[int((bearing + 22.5) % 360 // 45)]


def _turn_delta(bearing_in, bearing_out):
    """Signed heading change in degrees, -180..180. Positive = right turn
    (bearings increase clockwise)."""
    return (bearing_out - bearing_in + 180) % 360 - 180


# How far back from a junction the approach heading is measured, and how far
# past it the departure heading is.
#
# Not the adjacent vertex pair, which is what this used to use. OSM carries a
# vertex roughly every 19 m but packs them far tighter through a junction, to
# shape the corner: measured across the MA graph, 29% of edge ends have their
# last two vertices under 10 m apart and 10% under 5 m. A bearing taken over
# 4 m of geometry is mostly digitizing noise — the same defect that had
# curvature rating cul-de-sacs above the Mohawk Trail, in a function that never
# got the fix.
#
# And the noise is *biased*, which is what made it a user-visible bug rather
# than jitter: the approach geometry curves into the turn, so the final few
# metres already point round the corner, the measured heading change comes out
# too small, and a real turn was announced as "Continue". A chord long enough
# to clear the corner rounding, short enough not to swallow the turn itself.
TURN_CHORD_M = 25.0


def _dist_m(p, q):
    """Metres between two [lon, lat] points, on the flat.

    Called per vertex over a few tens of metres, where a spherical formula buys
    millimetres and costs a trig call per candidate.
    """
    mid_lat = math.radians((p[1] + q[1]) / 2.0)
    return math.hypot((q[0] - p[0]) * 111320.0 * math.cos(mid_lat),
                      (q[1] - p[1]) * 110540.0)


def _bearing_in(coords):
    """Heading on arrival at the end of `coords`, over a `TURN_CHORD_M` chord."""
    end = coords[-1]
    for p in reversed(list(coords[:-1])):
        if _dist_m(p, end) >= TURN_CHORD_M:
            return _bearing(p, end)
    # Shorter than the chord: the whole leg is the best baseline there is.
    return _bearing(coords[0], end)


def _bearing_out(coords):
    """Heading on departure from the start of `coords`, over the same chord."""
    start = coords[0]
    for q in list(coords[1:]):
        if _dist_m(start, q) >= TURN_CHORD_M:
            return _bearing(start, q)
    return _bearing(start, coords[-1])


def _turn_modifier(bearing_in, bearing_out):
    """Which way the road turns, as a maneuver modifier.

    The vocabulary is OSRM's and Valhalla's, not prose — see `RouteResult.steps`
    for why the wire format is a type plus a modifier rather than a sentence.
    """
    delta = _turn_delta(bearing_in, bearing_out)
    magnitude = abs(delta)
    if magnitude < 20:
        return "straight"
    side = "right" if delta > 0 else "left"
    if magnitude < 45:
        return f"slight {side}"
    if magnitude < 120:
        return side
    if magnitude < 160:
        return f"sharp {side}"
    return "uturn"


_MODIFIER_PHRASE = {
    "straight": "Continue",
    "slight left": "Slight left", "left": "Turn left", "sharp left": "Sharp left",
    "slight right": "Slight right", "right": "Turn right",
    "sharp right": "Sharp right", "uturn": "Make a U-turn",
}


def _leg_kind(highway: str, junction: str) -> str:
    """How a stretch of road behaves for the purpose of describing it.

    A rotary and a slip road are not turns onto a differently-named street, and
    describing them as though they were is what produced "Slight right" for a
    motorway exit and nothing at all for a rotary.
    """
    if junction in ("roundabout", "circular"):
        return "roundabout"
    if highway.endswith("_link"):
        return "ramp"
    return "road"


# The classes you "exit" rather than "turn off", and "merge" onto rather than
# "continue" onto.
_GRADE_SEPARATED = ("motorway", "trunk")

_ORDINALS = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"]


def _ordinal(n: int) -> str:
    """'2nd', for counting rotary exits. Beyond the table, a rotary with nine
    exits is a roundabout interchange and the number is more use than the word.
    """
    return _ORDINALS[n] if 0 <= n < len(_ORDINALS) else f"{n}th"


class ManeuverContext:
    """What the step generator needs to know about the graph beyond the route.

    Two questions the route alone cannot answer. Which numbered exit a junction
    is — that lives on the mainline node, not on any edge of the route. And how
    many roads leave each node of a rotary — which needs the *whole* graph's
    adjacency, since the roads you pass without taking are by definition not on
    your route, and they are exactly what "take the 2nd exit" counts.
    """

    def __init__(self, exit_ref: dict, exits_at_node):
        self.exit_ref = exit_ref                # node index -> "26", "13A"
        self.exits_at_node = exits_at_node      # node index -> outgoing non-rotary roads

    def numbered_exit(self, node_idx):
        return self.exit_ref.get(node_idx, "")

    def leaves_the_rotary(self, node_idx):
        return bool(self.exits_at_node[node_idx])


class RouteResult:
    def __init__(self, edge_rows: gpd.GeoDataFrame, line, edge_coords=None,
                 scores=None, nodes=None, context: "ManeuverContext" = None,
                 edge_minutes=None):
        self.edges = edge_rows
        self.line = line
        # Per-edge travel time in travel order, as weighted. None falls back to
        # the stored free-flow column, which is right only for a result built by
        # hand — see `minutes`.
        self.edge_minutes = edge_minutes
        # Per-edge [lon, lat] arrays in travel order (parallel to edge_rows),
        # used to build turn-by-turn steps. None for callers that don't need them.
        self.edge_coords = edge_coords or []
        # Per-edge 0-10 score under the beauty weights this route was chosen
        # with, aligned to edge_rows. None falls back to the stored neutral
        # column, which is right only when no weights were applied.
        self.scores = scores
        # Node indices in travel order, one longer than `edges`: the junction
        # each edge starts at, plus the destination. Exit numbers hang off
        # these, so without them a route still describes its turns but never
        # names an exit.
        self.nodes = list(nodes) if nodes is not None else []
        self.context = context

    def _column(self, name):
        """A route column, or blanks if this result was built without it.

        The Router refuses to load a graph missing any of these (see
        `REQUIRED_EDGE_COLUMNS`), so in production they are always present.
        Tests build a `RouteResult` straight from a handful of geometries to
        exercise the turn logic, and should not have to carry every tag to do
        it.
        """
        if name in self.edges.columns:
            return [("" if v is None or (isinstance(v, float) and math.isnan(v))
                     else str(v)) for v in self.edges[name]]
        return [""] * len(self.edges)

    @cached_property
    def km(self):
        return self.edges["length_m"].sum() / 1000.0

    @cached_property
    def minutes(self):
        """How long the drive takes — the number Dijkstra minimised.

        Driving time at the class's measured speed plus the controls the
        traversed direction is charged for, summed over the route. The edge
        table's own `minutes` column is free-flow and direction-blind, and is
        used only by a `RouteResult` built directly in a test.
        """
        if self.edge_minutes is None:
            return self.edges["minutes"].sum()
        return float(np.sum(self.edge_minutes))

    @cached_property
    def mean_score(self):
        L = self.edges["length_m"].to_numpy()
        s = self.edges["score"].to_numpy() if self.scores is None else self.scores
        return float((s * L).sum() / max(L.sum(), 1))

    def scenery_km(self):
        """Kilometers of this route that pass each kind of scenery (the labels
        in SCENERY_BREAKDOWN), for the breakdown bars in the app."""
        length_km = self.edges["length_m"] / 1000.0
        out = {}
        for label, column, threshold in SCENERY_BREAKDOWN:
            out[label] = float(length_km[self.edges[column] >= threshold].sum())
        return out

    def _legs(self):
        """Group the route's edges into stretches worth one instruction each.

        Consecutive edges merge when they are the same *kind* of road, carry the
        same label, and do not turn sharply at the seam. Each of those three
        conditions is load-bearing:

        - Kind, because a rotary and a slip road are not streets. Merging a
          rotary into the road that fed it is how a rotary produced no
          instruction at all, and merging the segments of a rotary *together*
          is what makes counting its exits possible.
        - Label, because a turn onto a differently-named road is a maneuver.
        - The seam, because a road can turn hard at a junction while keeping its
          name, and a silent merge there swallows a real turn.

        The rotary is the exception to the seam rule: it is a circle, so of
        course it turns, and breaking it at every few degrees would emit a
        stream of slight rights — which is exactly what the first test drive
        got.
        """
        names, refs = self._column("name"), self._column("ref")
        highways, junctions = self._column("highway"), self._column("junction")
        dest_refs, dest_names = self._column("dest_ref"), self._column("dest_name")
        lengths = self.edges["length_m"].to_numpy()
        label = lambda i: names[i] or refs[i] or ""

        legs = []
        for i, pts in enumerate(self.edge_coords):
            kind = _leg_kind(highways[i], junctions[i])
            coords = [list(p) for p in pts]
            if legs:
                prev = legs[-1]
                mergeable = prev["kind"] == kind and (
                    kind == "roundabout" or prev["label"] == label(i))
                if mergeable and (kind == "roundabout" or abs(_turn_delta(
                        _bearing_in(prev["coords"]), _bearing_out(coords))) < 45):
                    prev["coords"].extend(coords[1:])      # drop the shared vertex
                    prev["length_m"] += float(lengths[i])
                    prev["end"] = i + 1
                    prev["dest_ref"] = prev["dest_ref"] or dest_refs[i]
                    prev["dest_name"] = prev["dest_name"] or dest_names[i]
                    continue
            legs.append({
                "kind": kind, "label": label(i), "highway": highways[i],
                # The OSM `name` on its own, without the `ref` fallback. A
                # rotary carrying only a route number is not a rotary *called*
                # that: "Take the 1st exit at MA 122" reads as though the
                # roundabout were named after the highway crossing it.
                "name": names[i],
                "coords": coords, "length_m": float(lengths[i]),
                "start": i, "end": i + 1,
                "dest_ref": dest_refs[i], "dest_name": dest_names[i],
            })
        return legs

    def _roundabout_exit(self, leg):
        """Which exit of a rotary this leg leaves by, or 0 if it can't be known.

        Counted over the nodes the route actually traverses around the circle.
        `build_edges` splits a way at every node two ways share, so every road
        meeting the rotary is a node on it and none can be skipped — which is
        what makes the count trustworthy rather than an estimate.

        The entry node is deliberately excluded: the road you arrived on is not
        one of the exits you pass, and counting it would put every instruction
        one exit late.
        """
        if not self.context or not self.nodes:
            return 0
        passed = 0
        for node_idx in self.nodes[leg["start"] + 1:leg["end"] + 1]:
            if self.context.leaves_the_rotary(node_idx):
                passed += 1
        return passed

    def _exit_number(self, leg):
        """The signed exit number for a ramp leg, if the junction carries one."""
        if not self.context or not self.nodes:
            return ""
        return self.context.numbered_exit(self.nodes[leg["start"]])

    @staticmethod
    def _destination(leg):
        """Where a ramp says it goes, as it would read on the sign.

        OSM separates multiple destinations with semicolons and splits the road
        number (`destination:ref`, "I 93 North") from the places it serves
        (`destination`, "Cambridgeport;Brookline"). Rendered here rather than in
        graph.py so the wording stays with the other labels, and so re-wording
        it never costs a graph rebuild.

        Truncated, deliberately. A big interchange lists everything it serves —
        Massachusetts' worst reads "I 93: South Station / Concord New Hampshire
        / Quincy" — which is a sign you read at 60 mph, not a sentence anyone
        can follow spoken aloud. The road number is the part a driver matches
        against the overhead gantry, so it survives at the expense of the
        places.
        """
        split = lambda s: [p.strip() for p in s.split(";") if p.strip()]
        refs = split(leg["dest_ref"])[:2]
        # One place alongside a road number, two when the number is all we have
        # to go on.
        names = split(leg["dest_name"])[:1 if refs else 2]
        parts = [" / ".join(p) for p in (refs, names) if p]
        return ": ".join(parts)

    def steps(self):
        """Turn-by-turn maneuvers for the client to follow.

        Each step is a *structured* maneuver — a `type`, a `modifier`, and
        whichever of `exit_ref` / `destination` / `roundabout_exit` that type
        needs — with `instruction` as the rendered English alongside it, not
        instead of it. Three reasons the wire format is not just the sentence:
        the app can style an exit differently from a turn, voice guidance needs
        the parts separately ("in 500 feet, take exit 26"), and the vocabulary
        is deliberately OSRM's and Valhalla's, so swapping the routing engine
        later would not move the client.

        The first step sets off, the last announces arrival, and each carries
        the coordinate of its maneuver plus how far that instruction then
        carries you.
        """
        if not self.edge_coords:
            return []
        legs = self._legs()
        steps = []

        for i, leg in enumerate(legs):
            pts = leg["coords"]
            previous = legs[i - 1] if i else None
            step = {"type": "continue", "modifier": "straight",
                    "name": leg["label"], "exit_ref": "", "destination": "",
                    "roundabout_exit": 0}

            if previous is None:
                step["type"] = "depart"
                step["modifier"] = "straight"
                where = f" on {leg['label']}" if leg["label"] else ""
                step["instruction"] = f"Head {_compass(_bearing_out(pts))}{where}"
            else:
                modifier = _turn_modifier(_bearing_in(previous["coords"]),
                                          _bearing_out(pts))
                step["modifier"] = modifier
                if leg["kind"] == "roundabout":
                    self._describe_roundabout(step, leg, legs[i + 1:])
                elif leg["kind"] == "ramp":
                    self._describe_ramp(step, leg, legs[i + 1:], modifier)
                elif (previous["kind"] == "ramp"
                        and leg["highway"] in _GRADE_SEPARATED):
                    step["type"] = "merge"
                    onto = f" onto {leg['label']}" if leg["label"] else ""
                    step["instruction"] = f"Merge{onto}"
                else:
                    self._describe_turn(step, leg, previous, modifier)

            # A rotary instruction already names the road you leave on, and an
            # exit already names where the ramp goes. Emitting "Continue on X"
            # a few metres later says nothing and arrives while the driver is
            # still in the manoeuvre.
            if (previous is not None and previous["kind"] in ("roundabout", "ramp")
                    and step["type"] == "continue"):
                steps[-1]["distance_m"] += round(leg["length_m"])
                continue

            step["lat"] = round(pts[0][1], 6)
            step["lon"] = round(pts[0][0], 6)
            step["distance_m"] = round(leg["length_m"])
            steps.append(step)

        end = legs[-1]["coords"][-1]
        steps.append({"instruction": "Arrive at your destination",
                      "type": "arrive", "modifier": "straight", "name": "",
                      "exit_ref": "", "destination": "", "roundabout_exit": 0,
                      "lat": round(end[1], 6), "lon": round(end[0], 6),
                      "distance_m": 0})
        return steps

    def _describe_roundabout(self, step, leg, following):
        """'Take the 2nd exit at Reid Rotary onto Elm Street'."""
        step["type"] = "roundabout"
        # Going round is not a turn; the exit number is the instruction, and a
        # modifier here would fight it.
        step["modifier"] = "straight"
        nth = self._roundabout_exit(leg)
        step["roundabout_exit"] = nth
        onto = next((f["label"] for f in following if f["kind"] != "roundabout"), "")
        step["name"] = onto or leg["label"]

        where = f" at {leg['name']}" if leg["name"] else ""
        # 693 of MA's 1,234 rotaries are named, so the fallback is common
        # enough to have to read well on its own.
        if nth:
            instruction = f"Take the {_ordinal(nth)} exit{where}"
        else:
            instruction = f"At the roundabout{where}, take your exit"
        if onto:
            instruction += f" onto {onto}"
        step["instruction"] = instruction

    def _describe_ramp(self, step, leg, following, modifier):
        """'Take exit 26 toward I 93 North: Boston'.

        The fallback order is the one that never invents anything: the signed
        exit number if the junction carries one (74% of Massachusetts' numbered
        junctions do), then where the ramp says it goes, then the road it
        actually joins, and only then a bare instruction. A ramp described as
        "Slight right" — which is what every exit used to get, because 96% of
        ramps carry no name and the label fell through to nothing — is
        geometrically true and useless.
        """
        step["type"] = "exit"
        number = self._exit_number(leg)
        destination = self._destination(leg)
        joins = next((f["label"] for f in following if f["kind"] != "ramp"), "")
        step["exit_ref"] = number
        step["destination"] = destination
        step["name"] = leg["label"] or joins

        side = "right" if "right" in modifier else "left" if "left" in modifier else ""
        if number:
            step["instruction"] = f"Take exit {number}"
            if destination:
                step["instruction"] += f" toward {destination}"
        elif destination:
            step["instruction"] = f"Take the exit toward {destination}"
        elif joins:
            step["instruction"] = f"Take the exit onto {joins}"
        else:
            step["instruction"] = f"Take the exit on the {side}" if side else "Take the exit"

    def _describe_turn(self, step, leg, previous, modifier):
        phrase = _MODIFIER_PHRASE[modifier]
        if modifier == "straight":
            step["type"] = "continue"
            step["instruction"] = (f"Continue onto {leg['label']}" if leg["label"]
                                   else "Continue")
        else:
            step["type"] = "turn"
            if not leg["label"]:
                step["instruction"] = phrase           # no useful name to offer
            elif leg["label"] == previous["label"]:
                step["instruction"] = f"{phrase} to stay on {leg['label']}"
            else:
                step["instruction"] = f"{phrase} onto {leg['label']}"

    def geojson(self):
        return {
            "type": "Feature",
            "geometry": json.loads(shapely.to_geojson(self.line)) if self.line else None,
            "properties": {
                "km": round(self.km, 1), "minutes": round(self.minutes, 1),
                "mean_score": round(self.mean_score, 2),
                "scenery_km": {k: round(v, 1) for k, v in self.scenery_km().items()},
                "steps": self.steps(),
            },
        }


def main(processed_dir, a, b, pref=1.0):
    r = Router(processed_dir)
    (lat1, lon1), (lat2, lon2) = parse_ll(a), parse_ll(b)
    (s, s_off), (t, t_off) = r.snap(lat1, lon1), r.snap(lat2, lon2)
    print(f"snapped to node idx {s} ({s_off:.0f} m off) -> {t} ({t_off:.0f} m off)")

    fast = r.route(s, t, 0.0)
    scenic = r.route(s, t, float(pref))
    if fast is None or scenic is None:
        print("no route found (disconnected)"); return

    out = Path("out"); out.mkdir(exist_ok=True)
    for name, res in [("fastest", fast), ("scenic", scenic)]:
        (out / f"route_{name}.geojson").write_text(json.dumps(res.geojson()))

    fj, sj = fast.geojson()["properties"], scenic.geojson()["properties"]
    print(f"\nFASTEST:  {fj['km']} km, {fj['minutes']} min, "
          f"mean scenic {fj['mean_score']}")
    print(f"SCENIC :  {sj['km']} km, {sj['minutes']} min, "
          f"mean scenic {sj['mean_score']}")
    dt = sj["minutes"] - fj["minutes"]
    print(f"\n+{dt:.0f} min ({100*dt/max(fj['minutes'],1):.0f}% longer), "
          f"scenic {fj['mean_score']} -> {sj['mean_score']}")
    print(f"{'feature':12s} {'fastest':>8s} {'scenic':>8s}   (km near feature)")
    for k in sj["scenery_km"]:
        print(f"   {k:12s} {fj['scenery_km'][k]:8.1f} {sj['scenery_km'][k]:8.1f}")
    print("\nwrote out/route_fastest.geojson, out/route_scenic.geojson")


def parse_ll(s):
    lat, lon = s.split(",")
    return float(lat), float(lon)


if __name__ == "__main__":
    pref = sys.argv[4] if len(sys.argv) > 4 else 1.0
    main(sys.argv[1], sys.argv[2], sys.argv[3], pref)
