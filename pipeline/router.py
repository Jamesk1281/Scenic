"""Scenic routing over the annotated road graph.

Loads graph_edges/graph_nodes, expands directed edges (honoring oneway), and
runs a Dijkstra whose edge weight blends travel time with an "unscenic" penalty:

    weight = minutes + pref * BETA * km * (1 - score/10)

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
from scipy.spatial import cKDTree
from pyproj import Transformer

from common import CRS_METERS
from score import STRETCH, WEIGHTS

BETA = 7.0  # minutes-equivalent penalty per km of fully-unscenic road at pref=1
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
        nx, ny = _TO_M.transform(self.nodes["lon"].values, self.nodes["lat"].values)
        self._kdt = cKDTree(np.column_stack([nx, ny]))

        # Lazily-built (tail, head) -> directed-edge-slot lookup, used to map a
        # node path back to edges in _collect. Built on first route, then reused.
        self._adj = None

        self._build_directed()

    def _build_directed(self):
        e = self.edges
        ui = e["u"].map(self.idx).to_numpy()
        vi = e["v"].map(self.idx).to_numpy()
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

    def _edge_scores(self, weights: dict) -> np.ndarray:
        """Per *undirected* edge 0-10 scenic score under the given beauty weights.

        `weights` maps a BEAUTY_TYPES api-name to a multiplier (1.0 = the
        calibrated default, >1 leans in, 0 ignores); missing types default to
        1.0. The math mirrors score.py: blend the components, stretch, add the
        road-class/surface adjustment, clamp to 0-10. At all-1.0 weights this
        equals the precomputed `score` column exactly.
        """
        w = np.array([weights.get(name, 1.0) for name, *_ in BEAUTY_TYPES])
        raw = self.base_score + self.pref_matrix @ w
        return 10.0 * np.clip(raw * STRETCH + self.score_adj, 0.0, 1.0)

    def _weights(self, pref: float, weights: dict) -> np.ndarray:
        """Directed-edge Dijkstra weights: travel time + a scenery detour cost."""
        score = self._edge_scores(weights)                  # per undirected edge
        penalty = self.km * (1.0 - score / 10.0)            # km of "unscenic" road
        return self.d_minutes + pref * BETA * penalty[self.eidx]

    def snap(self, lat: float, lon: float) -> int:
        x, y = _TO_M.transform(lon, lat)
        return int(self._kdt.query([x, y])[1])

    def route(self, src_idx: int, dst_idx: int, pref: float, weights: dict = None):
        w = self._weights(pref, weights or {})
        g = csr_matrix((w, (self.tail, self.head)), shape=(self.n, self.n))
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
        return self._collect(path)

    def _collect(self, path):
        """Turn a Dijkstra node path into the chosen edges, in travel order.

        Dijkstra hands back a sequence of node indices. For each hop (a -> b) we
        look up the directed edge(s) joining them and keep the fastest, then
        gather that edge's row and geometry (reversed if we drove it backwards).
        The edges come out in travel order, which is exactly what a future
        turn-by-turn step list would walk over to emit "turn onto X" maneuvers.
        """
        # Build the (tail, head) -> directed-edge-slot lookup once, then cache
        # it. Two nodes can be joined by more than one edge (parallel roads), so
        # each key holds a list of slots and we pick the fastest per hop below.
        if self._adj is None:
            adj = defaultdict(list)
            for k in range(len(self.tail)):
                adj[(self.tail[k], self.head[k])].append(k)
            self._adj = adj

        chosen = []
        for a, b in zip(path[:-1], path[1:]):
            slots = self._adj.get((a, b))
            if not slots:
                continue
            chosen.append(min(slots, key=lambda s: self.d_minutes[s]))

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
        return RouteResult(rows, stitch(coords))


def stitch(coord_arrays):
    if not coord_arrays:
        return None
    out = [coord_arrays[0]]
    for c in coord_arrays[1:]:
        out.append(c[1:] if len(c) > 1 else c)
    return shapely.LineString(np.vstack(out))


class RouteResult:
    def __init__(self, edge_rows: gpd.GeoDataFrame, line):
        self.edges = edge_rows
        self.line = line

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

    def geojson(self):
        return {
            "type": "Feature",
            "geometry": json.loads(shapely.to_geojson(self.line)) if self.line else None,
            "properties": {
                "km": round(self.km, 1), "minutes": round(self.minutes, 1),
                "mean_score": round(self.mean_score, 2),
                "scenery_km": {k: round(v, 1) for k, v in self.scenery_km().items()},
            },
        }


def main(processed_dir, a, b, pref=1.0):
    r = Router(processed_dir)
    (lat1, lon1), (lat2, lon2) = parse_ll(a), parse_ll(b)
    s, t = r.snap(lat1, lon1), r.snap(lat2, lon2)
    print(f"snapped to node idx {s} -> {t}")

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
