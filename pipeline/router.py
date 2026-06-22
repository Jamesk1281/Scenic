"""Scenic routing over the annotated road graph.

Loads graph_edges/graph_nodes, expands directed edges (honoring oneway), and
runs a Dijkstra whose edge weight blends travel time with an "unscenic" penalty:

    weight = minutes + pref * BETA * km * (1 - score/10)

The penalty is a minutes-equivalent cost charged per kilometer of *unscenic*
road (BETA min/km at full ugliness), so the router trades extra distance for
beauty instead of only shaving seconds. pref = 0 gives the fastest route;
pref = 1 leans hard into scenery. The same graph answers both, so we return
fastest vs scenic side by side with deltas and a scenery breakdown.

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

BETA = 7.0  # minutes-equivalent penalty per km of fully-unscenic road at pref=1
ONEWAY_FWD = {"yes", "true", "1"}
ONEWAY_REV = {"-1", "reverse"}

# The user-facing "scenery breakdown": how many km of a route pass each kind of
# landscape. This is a curated *subset* of the eight scoring components — only
# the ones a driver would recognize as scenery (not curves/viewpoints/scenic
# tags, which are quality signals rather than places you pass through). Each
# entry is (label, edge column, threshold): a stretch of road counts toward a
# label when that component is >= the threshold.
#
# The label strings are mirrored on the client in ios/Sources/Models.swift
# (RouteProps.sceneryBreakdown), which also decides their display order — keep
# the two label sets in sync.
SCENERY_BREAKDOWN = [
    ("coast", "c_coast", 0.5),
    ("forest/park", "c_green", 0.5),
    ("water", "c_water", 0.5),
    ("hills", "c_relief", 0.5),
    ("farmland", "c_farm", 0.5),
    ("town", "c_urban", 0.5),
]

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
        score = e["score"].to_numpy()
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
        km = e["length_m"].to_numpy() / 1000.0
        self.d_minutes = minutes[self.eidx]

        # Penalty term, per directed edge: kilometers of road weighted by how
        # *unscenic* it is (1 - score/10). _weights multiplies this by pref*BETA
        # so a higher preference charges more "minutes" per km of ugly road.
        #
        # SEAM — future per-beauty-type preferences: this bakes the penalty from
        # the single precomputed composite `score`. To let a user weight beauty
        # types differently (more coast, less farmland), compute the score per
        # edge here from the component columns (c_water, c_coast, ... already
        # carried on every edge by graph.py) dotted with the user's weights,
        # then derive d_penalty from that blended score instead.
        self.d_penalty = km[self.eidx] * (1.0 - np.clip(score[self.eidx], 0, 10) / 10.0)

    def _weights(self, pref: float) -> np.ndarray:
        return self.d_minutes + pref * BETA * self.d_penalty

    def snap(self, lat: float, lon: float) -> int:
        x, y = _TO_M.transform(lon, lat)
        return int(self._kdt.query([x, y])[1])

    def route(self, src_idx: int, dst_idx: int, pref: float):
        w = self._weights(pref)
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
