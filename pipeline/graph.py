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
                                       dest_ref, dest_name, geometry (WGS84)

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

from common import CRS_METERS, DRIVABLE, ONEWAY_FWD, ONEWAY_REV, PRIVATE_ACCESS
from score import blend, components, composite

# Assumed driving speed (km/h) per road class, used to turn edge length into
# travel time when OSM has no maxspeed tag.
SPEED_KMH = {
    "motorway": 105, "motorway_link": 60, "trunk": 85, "trunk_link": 50,
    "primary": 65, "primary_link": 45, "secondary": 55, "secondary_link": 40,
    "tertiary": 50, "tertiary_link": 38, "unclassified": 45, "residential": 30,
    "living_street": 12,
}


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
        self.errors = 0

    def node(self, n):
        """Pick up exit numbers.

        They live on the *mainline* node where the ramp diverges — an OSM
        `highway=motorway_junction` carrying `ref=26` — and not on the ramp
        itself. That distinction is the whole reason this handler exists: 74%
        of Massachusetts' 1,363 junction nodes carry an exit number, while only
        4% of ramp ways carry any `ref` at all, and the ones that do hold the
        road number they lead to ("MA 3") rather than the exit. Reading the
        ramp would produce "Take exit MA 3".
        """
        if n.tags.get("highway") == "motorway_junction":
            ref = n.tags.get("ref", "")
            if ref:
                self.exit_refs[n.id] = ref

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


def build_edges(ways, node_count, to_m):
    """Split each way at junction nodes (degree >= 2) into edges."""
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
                "length_m": length,
                "minutes": length / 1000.0 / meta["speed"] * 60.0,
                "oneway": meta["oneway"],
                "name": meta["name"], "ref": meta["ref"], "highway": meta["highway"],
                "junction": meta["junction"],
                "dest_ref": meta["dest_ref"], "dest_name": meta["dest_name"],
                "geometry": shapely.LineString(seg),
            })
    return rows


def main(pbf_path: str, processed_dir: str):
    d = Path(processed_dir)
    t0 = time.time()

    h = GraphHandler()
    h.apply_file(pbf_path, locations=True, idx="flex_mem")
    print(f"parsed {len(h.ways):,} drivable ways in {time.time() - t0:.0f}s "
          f"({h.errors} skipped)")

    fwd = Transformer.from_crs(4326, CRS_METERS, always_xy=True)
    to_m = lambda lons, lats: fwd.transform(lons, lats)

    rows = build_edges(h.ways, h.node_count, to_m)
    edges = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
    print(f"built {len(edges):,} edges in {time.time() - t0:.0f}s")

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

    edges.to_parquet(d / "graph_edges.parquet")
    nodes.to_parquet(d / "graph_nodes.parquet")
    print(f"wrote graph_edges.parquet + graph_nodes.parquet in {time.time() - t0:.0f}s")
    print(f"edge score: mean {edges['score'].mean():.2f}, "
          f"length {edges['length_m'].sum()/1000:.0f} km total")


# How finely an edge is sampled when averaging the chunks it runs over. A
# quarter of score.py's CHUNK_LEN, so every chunk an edge crosses is hit.
SAMPLE_STEP_M = 100.0


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
