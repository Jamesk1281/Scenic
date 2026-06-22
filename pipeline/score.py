"""Score every drivable road chunk in the region for scenic quality.

Scoring components (the per-segment "beauty vector"):
  water   - proximity to lakes/reservoirs/rivers
  coast   - proximity to the ocean coastline
  green   - adjacency to woods, forests, parks, reserves
  curves  - heading change per km (twistiness)
  relief  - local terrain relief from elevation.py (hills, valleys, overlooks)
  farm    - adjacency to farmland/orchards/meadows
  views   - proximity to mapped viewpoints
  scenic  - explicit scenic=yes tag

The relief component is read from data/processed/relief.tif if present
(run elevation.py first); otherwise it is zero and a notice is printed.

Each chunk keeps its component vector (the per-segment "beauty vector")
plus a composite 0-10 score. Output: scored_chunks.parquet.

Usage: python score.py <processed_dir>
"""

import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.ops import substring
from shapely.strtree import STRtree

from common import CRS_METERS

CHUNK_LEN = 400.0  # max road chunk length in meters

# --- TUNABLE: distances (m), minimum polygon sizes (m^2), weights ---
DIST = {"water": 120, "water_mid": 350, "coast": 800, "green": 80, "farm": 80, "view": 400}
MIN_AREA = {"water": 20_000, "green": 30_000, "farm": 20_000}
WEIGHTS = {
    "water": 0.22, "coast": 0.13, "green": 0.18, "curves": 0.13,
    "relief": 0.16, "farm": 0.06, "views": 0.05, "scenic_tag": 0.07,
}
STRETCH = 1.35           # expands the composite so great roads land near 10
CURVE_FULL = 120.0       # deg/km that counts as maximally twisty
RELIEF_FULL = 160.0      # local relief (m within ~1 km) that counts as maximal
CLASS_ADJ = {
    "motorway": -0.45, "motorway_link": -0.40, "trunk": -0.10, "trunk_link": -0.18,
    "primary": -0.04, "primary_link": -0.10, "secondary": 0.0, "secondary_link": -0.10,
    "tertiary": 0.0, "unclassified": 0.0, "residential": -0.05, "living_street": -0.08,
}
UNPAVED = {"unpaved", "dirt", "gravel", "ground", "grass", "sand", "earth", "mud", "fine_gravel"}
UNPAVED_ADJ = -0.25

# Official MA scenic byways that OSM doesn't tag scenic=yes — matched by road
# name (case-insensitive substring). Treated like an OSM scenic designation.
# Names are distinctive enough to avoid false positives statewide.
BYWAY_NAMES = ["mohawk trail", "jacob's ladder trail", "jacobs ladder trail"]


def load_layer(d: Path, name: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_parquet(d / f"{name}.parquet")
    if len(gdf) == 0:
        return gdf
    return gdf.to_crs(CRS_METERS)


def chunk_roads(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Split ways into <=CHUNK_LEN pieces so scores have spatial resolution."""
    lengths = roads.geometry.length.values
    geoms = roads.geometry.values
    out_geoms, out_idx = [], []
    for i in range(len(roads)):
        L = lengths[i]
        if L <= CHUNK_LEN:
            out_geoms.append(geoms[i])
            out_idx.append(i)
        else:
            n = int(np.ceil(L / CHUNK_LEN))
            step = L / n
            for k in range(n):
                out_geoms.append(substring(geoms[i], k * step, (k + 1) * step))
                out_idx.append(i)
    chunks = roads.iloc[out_idx].drop(columns="geometry").reset_index(drop=True)
    chunks = gpd.GeoDataFrame(chunks, geometry=gpd.GeoSeries(out_geoms, crs=CRS_METERS))
    return chunks


def curvature_deg_per_km(geoms: np.ndarray) -> np.ndarray:
    """Total absolute heading change per km, vectorized over all geometries."""
    coords = shapely.get_coordinates(geoms)
    counts = shapely.get_num_coordinates(geoms)
    gidx = np.repeat(np.arange(len(geoms)), counts)

    d = np.diff(coords, axis=0)
    seg_same = gidx[1:] == gidx[:-1]  # segment stays within one geometry
    headings = np.arctan2(d[:, 1], d[:, 0])
    dh = np.diff(headings)
    dh = (dh + np.pi) % (2 * np.pi) - np.pi
    pair_same = seg_same[1:] & seg_same[:-1]  # both segments in same geometry

    turn = np.zeros(len(geoms))
    np.add.at(turn, gidx[1:-1][pair_same], np.abs(dh[pair_same]))
    lengths_km = shapely.length(geoms) / 1000.0
    return np.degrees(turn) / np.maximum(lengths_km, 1e-6)


def near_flags(tree: STRtree | None, geoms: np.ndarray, dist: float) -> np.ndarray:
    flags = np.zeros(len(geoms), dtype=bool)
    if tree is not None:
        hits = tree.query(geoms, predicate="dwithin", distance=dist)
        flags[np.unique(hits[0])] = True
    return flags


def sample_relief(chunks: gpd.GeoDataFrame, relief_path: Path) -> np.ndarray:
    """Sample local relief (m) at each chunk midpoint, normalized to 0..1."""
    if not relief_path.exists():
        print(f"NOTE: {relief_path.name} missing; relief component = 0 "
              f"(run elevation.py to enable terrain scoring)")
        return np.zeros(len(chunks))
    import rasterio
    mids = chunks.geometry.interpolate(0.5, normalized=True).to_crs(3857)
    coords = np.column_stack([mids.x.values, mids.y.values])
    with rasterio.open(relief_path) as src:
        vals = np.fromiter(
            (v[0] for v in src.sample(coords)), dtype=float, count=len(coords)
        )
    vals = np.nan_to_num(vals, nan=0.0)
    return np.clip(vals / RELIEF_FULL, 0, 1)


def build_tree(gdf: gpd.GeoDataFrame, min_area: float | None = None) -> STRtree | None:
    if len(gdf) == 0:
        return None
    geoms = gdf.geometry.values
    if min_area is not None:
        geoms = geoms[shapely.area(geoms) >= min_area]
    if len(geoms) == 0:
        return None
    return STRtree(geoms)


def main(processed_dir: str):
    d = Path(processed_dir)
    t0 = time.time()

    roads = load_layer(d, "roads")
    print(f"roads: {len(roads):,} ways")

    chunks = chunk_roads(roads)
    chunks["length_m"] = chunks.geometry.length
    print(f"chunked into {len(chunks):,} pieces (<= {CHUNK_LEN:.0f} m) in {time.time() - t0:.0f}s")

    geoms = chunks.geometry.values

    # Component: curvature
    curv = curvature_deg_per_km(geoms)
    chunks["c_curves"] = np.clip(curv / CURVE_FULL, 0, 1)

    # Components: proximity to scenic features
    # area filter only applies to polygons; rivers are lines with area 0, keep them
    wa = load_layer(d, "water_areas")
    wl = load_layer(d, "water_lines")
    water_geoms = []
    if len(wa):
        g = wa.geometry.values
        water_geoms.append(g[shapely.area(g) >= MIN_AREA["water"]])
    if len(wl):
        water_geoms.append(wl.geometry.values)
    water_tree = STRtree(np.concatenate(water_geoms)) if water_geoms else None

    coast_tree = build_tree(load_layer(d, "coastline"))
    green_tree = build_tree(load_layer(d, "green_areas"), MIN_AREA["green"])
    farm_tree = build_tree(load_layer(d, "farm_areas"), MIN_AREA["farm"])
    view_tree = build_tree(load_layer(d, "viewpoints"))

    near_water = near_flags(water_tree, geoms, DIST["water"])
    mid_water = near_flags(water_tree, geoms, DIST["water_mid"])
    chunks["c_water"] = np.where(near_water, 1.0, np.where(mid_water, 0.45, 0.0))
    chunks["c_coast"] = near_flags(coast_tree, geoms, DIST["coast"]).astype(float)
    chunks["c_green"] = near_flags(green_tree, geoms, DIST["green"]).astype(float)
    chunks["c_farm"] = near_flags(farm_tree, geoms, DIST["farm"]).astype(float)
    chunks["c_views"] = near_flags(view_tree, geoms, DIST["view"]).astype(float)
    name_l = chunks["name"].str.lower()
    is_byway = name_l.apply(lambda s: any(b in s for b in BYWAY_NAMES))
    chunks["c_scenic_tag"] = (chunks["scenic"] | is_byway).astype(float)
    chunks["c_relief"] = sample_relief(chunks, d / "relief.tif")
    print(f"features computed in {time.time() - t0:.0f}s")

    # Composite score
    raw = (
        WEIGHTS["water"] * chunks["c_water"]
        + WEIGHTS["coast"] * chunks["c_coast"]
        + WEIGHTS["green"] * chunks["c_green"]
        + WEIGHTS["curves"] * chunks["c_curves"]
        + WEIGHTS["relief"] * chunks["c_relief"]
        + WEIGHTS["farm"] * chunks["c_farm"]
        + WEIGHTS["views"] * chunks["c_views"]
        + WEIGHTS["scenic_tag"] * chunks["c_scenic_tag"]
    )
    class_adj = chunks["highway"].map(CLASS_ADJ).fillna(0.0)
    unpaved_adj = np.where(chunks["surface"].isin(UNPAVED), UNPAVED_ADJ, 0.0)
    chunks["score"] = 10 * np.clip(raw * STRETCH + class_adj + unpaved_adj, 0, 1)

    out_path = d / "scored_chunks.parquet"
    chunks.to_parquet(out_path)
    print(f"wrote {out_path} ({len(chunks):,} chunks) in {time.time() - t0:.0f}s total\n")

    # --- Validation report ---
    pd.set_option("display.width", 140)
    by_class = chunks.groupby("highway").agg(
        chunks_n=("score", "size"), mean_score=("score", "mean")
    ).sort_values("mean_score", ascending=False)
    print("score by road class:\n", by_class.round(2), "\n")

    named = chunks[chunks["name"] != ""].copy()
    named["wscore"] = named["score"] * named["length_m"]
    top = (
        named.groupby("name")
        .agg(km=("length_m", lambda s: s.sum() / 1000), wscore=("wscore", "sum"), L=("length_m", "sum"))
        .assign(score=lambda df: df["wscore"] / df["L"])
        .query("km >= 3")
        .sort_values("score", ascending=False)
        .head(25)[["km", "score"]]
    )
    print("top named roads (>=3 km):\n", top.round(2), "\n")

    for label, mask in [
        ("Mohawk Trail", named["name"].str.contains("Mohawk Trail", case=False)),
        ("Route 6A (Old King's Hwy)", chunks["ref"].str.contains("6A", na=False)),
        ("I-90 (Mass Pike)", chunks["ref"].str.fullmatch("I 90", na=False)),
        ("I-95", chunks["ref"].str.contains("I 95", na=False)),
    ]:
        sel = named[mask] if label == "Mohawk Trail" else chunks[mask]
        if len(sel):
            print(f"benchmark {label}: mean {sel['score'].mean():.2f} over {sel['length_m'].sum()/1000:.0f} km")
        else:
            print(f"benchmark {label}: no match")


if __name__ == "__main__":
    main(sys.argv[1])
