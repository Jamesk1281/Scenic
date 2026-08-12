"""Scenic routing over the annotated road graph.

Loads graph_edges/graph_nodes, expands directed edges (honoring oneway), and
runs a Dijkstra whose edge weight blends travel time with an "unscenic" penalty:

    weight = minutes + pref**PREF_CURVE * BETA * km * (1 - score/10)

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
from collections import defaultdict
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

from common import CRS_METERS
from score import WEIGHTS, composite

BETA = 7.0  # minutes-equivalent penalty per km of fully-unscenic road at pref=1

# The scenery penalty saturates — past a few minutes-per-km the router has taken
# every detour worth taking — which used to leave the slider's top half handing
# back an identical route. Most of that was really the compressed score scale
# (see RAW_BASE in score.py): with the full 0-10 range in play the penalty
# discriminates enough that a near-linear slider already spreads well. This mild
# exponent evens out what remains. Measured over four routes, exponents above
# ~1.5 overcorrect, trading the dead top for a dead bottom.
PREF_CURVE = 1.3

ONEWAY_FWD = {"yes", "true", "1"}
ONEWAY_REV = {"-1", "reverse"}

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

# Baseline "quality" signals: always on, not user-tunable. Twistiness, mapped
# viewpoints, and explicit scenic tags form a floor of beauty under every road,
# so a fine road no one toggled on is never scored flat zero.
BASELINE = [
    ("c_curves",     WEIGHTS["curves"]),
    ("c_views",      WEIGHTS["views"]),
    ("c_scenic_tag", WEIGHTS["scenic_tag"]),
]

# The route-summary breakdown: km of road passing each beauty type. Derived from
# BEAUTY_TYPES so the two never drift; a stretch counts toward a type when its
# component is >= 0.5.
SCENERY_BREAKDOWN = [(label, col, 0.5) for _, label, col, _ in BEAUTY_TYPES]

_TO_M = Transformer.from_crs(4326, CRS_METERS, always_xy=True)


class Router:
    def __init__(self, processed_dir: str):
        d = Path(processed_dir)
        self.edges = gpd.read_parquet(d / "graph_edges.parquet")
        self.nodes = pd.read_parquet(d / "graph_nodes.parquet")

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

    def _build_directed(self):
        e = self.edges
        ui = e["u"].map(self.idx).to_numpy()
        vi = e["v"].map(self.idx).to_numpy()
        # Kept per undirected edge (not per directed slot) so snap() can pick
        # between the two ends of the road segment it landed on.
        self.edge_u_idx, self.edge_v_idx = ui, vi
        minutes = e["minutes"].to_numpy()
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
        self.d_minutes = minutes[self.eidx]

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
        # path back to edges. Built once here (eagerly) so the server is fully
        # warm after startup — no slow first request. Parallel roads mean a pair
        # can hold several slots.
        self._adj = defaultdict(list)
        for k in range(len(self.tail)):
            self._adj[(self.tail[k], self.head[k])].append(k)

    def _edge_scores(self, weights: dict) -> np.ndarray:
        """Per *undirected* edge 0-10 scenic score under the given beauty weights.

        `weights` maps a BEAUTY_TYPES api-name to a multiplier (1.0 = the
        calibrated default, >1 leans in, 0 ignores); missing types default to
        1.0. Blending the components is the only part done here — the 0-10
        transform is score.py's `composite`, imported rather than re-derived so
        the live score and the precomputed column cannot drift apart. At
        all-1.0 weights this equals the precomputed `score` column exactly.
        """
        w = np.array([weights.get(name, 1.0) for name, *_ in BEAUTY_TYPES])
        raw = self.base_score + self.pref_matrix @ w
        return composite(raw, self.score_adj)

    def _weights(self, pref: float, weights: dict) -> np.ndarray:
        """Directed-edge Dijkstra weights: travel time + a scenery detour cost."""
        score = self._edge_scores(weights)                  # per undirected edge
        penalty = self.km * (1.0 - score / 10.0)            # km of "unscenic" road
        strength = pref ** PREF_CURVE
        return self.d_minutes + strength * BETA * penalty[self.eidx]

    def snap(self, lat: float, lon: float) -> tuple[int, float]:
        """Routable node for a lat/lon: (node index, meters to the road).

        Finds the nearest road *segment* first, then takes that segment's nearer
        end — rather than going straight to the nearest junction. The difference
        is not cosmetic. Graph nodes are junctions, and a house mid-block is
        routinely closer, in a straight line, to a junction on the street behind
        it than to either end of its own street. Snapping to the nearest
        junction outright therefore started 23% of sampled residential addresses
        on a road the driver was not on (measured over 400 blocks; the drivers'
        complaint was "it starts me on a different road than I live on"). Going
        via the segment makes that 0%: both ends of the nearest segment are, by
        construction, on the road you are standing on.

        The distance returned is to the road itself, so callers can reject
        points that aren't on the network at all — e.g. a request from outside
        Massachusetts would otherwise silently snap to a border town and return
        a nonsense route.
        """
        x, y = _TO_M.transform(lon, lat)
        point = shapely.Point(x, y)
        e = int(self._edge_tree.nearest(point))
        u, v = int(self.edge_u_idx[e]), int(self.edge_v_idx[e])
        du = (self._nx[u] - x) ** 2 + (self._ny[u] - y) ** 2
        dv = (self._nx[v] - x) ** 2 + (self._ny[v] - y) ** 2
        return (u if du <= dv else v), float(self._edge_geom_m[e].distance(point))

    def route(self, src_idx: int, dst_idx: int, pref: float, weights: dict = None):
        w = self._weights(pref, weights or {})
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
        return self._collect(path, w)

    def _collect(self, path, w):
        """Turn a Dijkstra node path into the chosen edges, in travel order.

        Dijkstra hands back a sequence of node indices. For each hop (a -> b) we
        look up the directed edge(s) joining them and keep the one the cost
        actually used — the cheapest under the same weights `w` Dijkstra saw, so
        the drawn geometry and scenery match the chosen parallel road — then
        gather that edge's row and geometry (reversed if we drove it backwards).
        The edges come out in travel order, which is exactly what the
        turn-by-turn step list walks over to emit "turn onto X" maneuvers.
        """
        chosen = []
        for a, b in zip(path[:-1], path[1:]):
            slots = self._adj.get((a, b))
            if not slots:
                continue
            chosen.append(min(slots, key=lambda s: w[s]))

        # The undirected edge rows (stats, names, geometry) in travel order.
        rows = self.edges.iloc[[self.eidx[k] for k in chosen]]

        # Stitch the per-edge geometries into one line, flipping any edge we
        # traversed against its stored direction so the points run start -> end.
        coords = []
        for k in chosen:
            c = shapely.get_coordinates(self.edges.geometry.values[self.eidx[k]])
            if self.flip[k]:
                c = c[::-1]
            coords.append(c)
        # `coords` is the per-edge geometry in travel order; the steps generator
        # uses it (with the edge names) to build maneuvers.
        return RouteResult(rows, stitch(coords), coords)


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


def _turn_phrase(bearing_in, bearing_out):
    """Describe the turn from one heading to the next, e.g. 'Turn left'."""
    delta = _turn_delta(bearing_in, bearing_out)
    magnitude = abs(delta)
    if magnitude < 20:
        return "Continue"
    side = "right" if delta > 0 else "left"
    if magnitude < 45:
        return f"Slight {side}"
    if magnitude < 120:
        return f"Turn {side}"
    return f"Sharp {side}"


class RouteResult:
    def __init__(self, edge_rows: gpd.GeoDataFrame, line, edge_coords=None):
        self.edges = edge_rows
        self.line = line
        # Per-edge [lon, lat] arrays in travel order (parallel to edge_rows),
        # used to build turn-by-turn steps. None for callers that don't need them.
        self.edge_coords = edge_coords or []

    @cached_property
    def km(self):
        return self.edges["length_m"].sum() / 1000.0

    @cached_property
    def minutes(self):
        return self.edges["minutes"].sum()

    @cached_property
    def mean_score(self):
        L = self.edges["length_m"]
        return float((self.edges["score"] * L).sum() / max(L.sum(), 1))

    def scenery_km(self):
        """Kilometers of this route that pass each kind of scenery (the labels
        in SCENERY_BREAKDOWN), for the breakdown bars in the app."""
        length_km = self.edges["length_m"] / 1000.0
        out = {}
        for label, column, threshold in SCENERY_BREAKDOWN:
            out[label] = float(length_km[self.edges[column] >= threshold].sum())
        return out

    def steps(self):
        """Turn-by-turn maneuvers for the client to follow.

        Consecutive edges on the same road are merged into one "leg", then we
        emit a step at the start of each: the first tells you which way to set
        off, the rest are turns onto the next road, and a final step announces
        arrival. Each step carries the coordinate of its maneuver and the
        distance that instruction then carries you (the leg's length).
        """
        if not self.edge_coords:
            return []
        names = self.edges["name"].to_numpy()
        refs = self.edges["ref"].to_numpy()
        lengths = self.edges["length_m"].to_numpy()
        label = lambda i: names[i] or refs[i] or "the road"

        # Merge consecutive same-road edges into legs (label, coords, length).
        # Same label alone isn't enough to merge: a road can turn sharply at a
        # junction while keeping its name (and two different unnamed roads both
        # label as "the road"), and a silent merge there would swallow a real
        # turn. So a sharp heading change at the seam always starts a new leg.
        legs = []
        for i, pts in enumerate(self.edge_coords):
            if legs and label(i) == legs[-1]["label"]:
                prev = legs[-1]["coords"]
                seam_turn = _turn_delta(_bearing(prev[-2], prev[-1]),
                                        _bearing(pts[0], pts[1]))
                if abs(seam_turn) < 45:
                    legs[-1]["coords"].extend(pts[1:].tolist())   # drop shared vertex
                    legs[-1]["length_m"] += lengths[i]
                    continue
            legs.append({"label": label(i), "coords": pts.tolist(),
                         "length_m": float(lengths[i])})

        steps = []
        for i, leg in enumerate(legs):
            pts = leg["coords"]
            if i == 0:
                instruction = f"Head {_compass(_bearing(pts[0], pts[1]))} on {leg['label']}"
            else:
                prev = legs[i - 1]
                phrase = _turn_phrase(_bearing(prev["coords"][-2], prev["coords"][-1]),
                                      _bearing(pts[0], pts[1]))
                if phrase == "Continue":
                    instruction = f"Continue on {leg['label']}"
                elif leg["label"] == "the road":
                    instruction = phrase                       # "Turn left" — no useful name
                elif leg["label"] == prev["label"]:
                    instruction = f"{phrase} to stay on {leg['label']}"
                else:
                    instruction = f"{phrase} onto {leg['label']}"
            steps.append({"instruction": instruction,
                          "lat": round(pts[0][1], 6), "lon": round(pts[0][0], 6),
                          "distance_m": round(leg["length_m"])})

        end = legs[-1]["coords"][-1]
        steps.append({"instruction": "Arrive at your destination",
                      "lat": round(end[1], 6), "lon": round(end[0], 6), "distance_m": 0})
        return steps

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
