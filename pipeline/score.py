"""Score every drivable road chunk in the region for scenic quality.

Scoring components (the per-segment "beauty vector"):
  water   - proximity to lakes/reservoirs/rivers
  coast   - proximity to the ocean coastline
  forest  - OSM woods/forests/parks/reserves, blended half-and-half with
            measured tree cover from landcover.py
  curves  - heading change per km (twistiness)
  relief  - local terrain relief from elevation.py (hills, valleys, overlooks)
  farm    - adjacency to farmland/orchards/meadows
  views   - proximity to mapped viewpoints
  scenic  - designated scenic byway (OSM route relation, or scenic=yes)
  urban   - proximity to town/village centers and retail/commercial districts

The relief component is read from data/processed/relief.tif if present
(run elevation.py first); the tree-cover half of forest from
data/processed/tree_cover.parquet (run landcover.py first). Either one missing
degrades its component to zero and prints a notice.

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
DIST = {
    "water": 120, "water_mid": 350, "coast": 800, "green": 80, "farm": 80,
    "view": 400,
    # urban: full credit inside/near a retail-commercial district or close to a
    # town-center node; partial credit out to place_mid (the town's wider orbit).
    # Kept tight: Massachusetts is dense enough that a wider orbit tagged half
    # the state's road-km as "town", which made the label meaningless.
    "urban": 100, "place": 400, "place_mid": 900,
}
MIN_AREA = {"water": 20_000, "green": 30_000, "farm": 20_000}
WEIGHTS = {
    "water": 0.22, "coast": 0.13, "forest": 0.18, "curves": 0.13,
    "relief": 0.16, "farm": 0.06, "views": 0.05, "scenic_tag": 0.07,
    "urban": 0.14,
}
# Composite calibration: score = 10 * ((raw + RAW_BASE) * STRETCH + score_adj).
# The weights sum to 1.14, but no real road collects them all (a coastal road
# isn't farmland), so `raw` tops out near 0.65 in practice — leaving the old
# scale bunched into 0–6 with "8/10" unreachable. RAW_BASE is the baseline
# pleasantness of an ordinary road with no standout feature; STRETCH then opens
# the rest of the range. Fitted so p50 lands near 4.5 and p99 near 9.5 (see
# calibration_report). Roads that are actively unpleasant are driven back down
# by the negative score_adj below, not by the base.
RAW_BASE = 0.143
STRETCH = 1.20

# Twistiness, measured between chords CURVE_D apart rather than between raw
# vertices. OSM digitizes roads at ~20 m spacing, where a couple of metres of
# position error swings the heading several degrees; summing those swings
# measured mapping noise, not curves (the old metric peaked at 3,500 deg/km —
# ten full rotations per km — and rated a cul-de-sac above the Mohawk Trail).
CURVE_D = 60.0           # chord sampling distance (m)
CURVE_CAP = 25.0         # max heading change credited per step (deg)
CURVE_MIN_LEN = 300.0    # denominator floor (m), so short stubs can't explode
CURVE_FULL = 160.0       # deg/km that counts as maximally twisty

# Local relief (m within ~1 km) that counts as maximal. Massachusetts tops out
# well below alpine terrain: only 2.5% of the state reaches 160 m, so that
# threshold left the hills component — and the app's Hills slider — inert.
RELIEF_FULL = 100.0
CLASS_ADJ = {
    "motorway": -0.45, "motorway_link": -0.40, "trunk": -0.10, "trunk_link": -0.18,
    "primary": -0.04, "primary_link": -0.10, "secondary": 0.0, "secondary_link": -0.10,
    "tertiary": 0.0, "tertiary_link": -0.05, "unclassified": 0.0,
    "residential": -0.05, "living_street": -0.08,
}
UNPAVED = {"unpaved", "dirt", "gravel", "ground", "grass", "sand", "earth", "mud", "fine_gravel"}
UNPAVED_ADJ = -0.25


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
    # The length of the way this piece was cut from. `curvature_deg_per_km`
    # floors its denominator to protect short *roads*, and after chunking a
    # piece's own length no longer says whether its road was short.
    chunks["road_len_m"] = np.asarray(lengths)[out_idx]
    return chunks


def curvature_deg_per_km(geoms: np.ndarray,
                         road_lengths: np.ndarray | None = None) -> np.ndarray:
    """Sustained heading change per km — twistiness as a driver actually feels it.

    Samples each line every CURVE_D metres and sums the heading change between
    consecutive chords. Sampling at driving scale averages out vertex jitter
    (see CURVE_D above); capping each step at CURVE_CAP keeps a junction corner
    or cul-de-sac bulb from outweighing a real sweeping curve; and flooring the
    denominator at CURVE_MIN_LEN stops a 25 m stub with one bend from dividing
    its way to thousands of deg/km.
    """
    lengths = shapely.length(geoms)
    # Defaults to the geometry's own length, which is right whenever the caller
    # is passing whole roads.
    road_lengths = lengths if road_lengths is None else np.asarray(road_lengths)
    n_steps = int(np.ceil(lengths.max() / CURVE_D)) + 1

    # Point k sits CURVE_D * k along the line (clamped to its end).
    pts = np.empty((n_steps, len(geoms), 2))
    for k in range(n_steps):
        along = shapely.line_interpolate_point(geoms, np.minimum(k * CURVE_D, lengths))
        pts[k] = shapely.get_coordinates(along)
    # Samples past the end all clamp to the same endpoint, and those degenerate
    # chords must not count as turns. The *first* clamped sample is the genuine
    # end of the line, though, so it is kept — dropping it would discard the
    # last (partial) chord of every line, and zero out anything shorter than
    # two full steps.
    steps_before_end = (np.arange(n_steps)[:, None] - 1) * CURVE_D
    real = steps_before_end < lengths[None, :]

    chords = np.diff(pts, axis=0)
    chord_ok = real[1:] & real[:-1]
    headings = np.arctan2(chords[:, :, 1], chords[:, :, 0])
    dh = np.degrees((np.diff(headings, axis=0) + np.pi) % (2 * np.pi) - np.pi)
    turn = np.where(chord_ok[1:] & chord_ok[:-1],
                    np.minimum(np.abs(dh), CURVE_CAP), 0.0).sum(axis=0)

    # The floor exists to stop a 25 m stub dividing one bend into thousands of
    # deg/km, so it keys on the *road*: a 401 m way is cut into two 200.5 m
    # chunks, and dividing each of those by a floored 300 m understated a road
    # curving at CURVE_FULL as 0.53 (of 1.0) — a 0.53-point score swing decided
    # by whether OSM digitised the way at 400 m or 401 m. Short roads keep the
    # protection; chunks of long ones are measured over the length they have.
    denom = np.where(road_lengths >= CURVE_MIN_LEN,
                     lengths, np.maximum(lengths, CURVE_MIN_LEN))
    return turn / (denom / 1000.0)


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
    # Reachable again now that relief.tif carries nodata: NaN here means the
    # chunk sits outside the mosaic (a PBF wider than elevation.py's BBOX) or in
    # one of the holes MIN_COVERAGE tolerates. Scoring those as 0 is still the
    # only option, but it is worth one line rather than nothing at all.
    void = np.isnan(vals)
    if void.any():
        print(f"NOTE: {void.sum():,} of {len(vals):,} chunks "
              f"({100 * void.mean():.1f}%) have no elevation coverage; "
              f"relief = 0 for those (widen BBOX in elevation.py and re-run)")
    vals = np.nan_to_num(vals, nan=0.0)
    return np.clip(vals / RELIEF_FULL, 0, 1)


def sample_tree_cover(chunks: gpd.GeoDataFrame, path: Path) -> np.ndarray:
    """Per-chunk WorldCover tree-cover fraction, as written by landcover.py.

    Joined by position, so the file has to have been built from this same
    roads.parquet. It records the midpoint it sampled and every one of them is
    checked here, because a misaligned join has no symptom: it would credit one
    road with another's trees and still produce a plausible score.
    """
    if not path.exists():
        print(f"NOTE: {path.name} missing; c_forest falls back to OSM green at "
              f"half strength, which under-scores the whole network "
              f"(run landcover.py to enable measured tree cover)")
        return np.zeros(len(chunks))
    mids = chunks.geometry.interpolate(0.5, normalized=True).to_crs(4326)
    tc = pd.read_parquet(path)
    if not (len(tc) == len(chunks)
            and np.allclose(tc["lon"].to_numpy(), mids.x.to_numpy(), atol=1e-9)
            and np.allclose(tc["lat"].to_numpy(), mids.y.to_numpy(), atol=1e-9)):
        raise SystemExit(
            f"{path.name} holds {len(tc):,} chunks that do not line up with the "
            f"{len(chunks):,} being scored — it was built from a different "
            f"roads.parquet. Re-run landcover.py against this one.")
    return tc["tree"].to_numpy()


def components(df) -> list[str]:
    """The per-segment "beauty vector" columns present on a frame, in order."""
    return [c for c in df.columns if c.startswith("c_")]


def blend(df):
    """Weighted sum of every component column, using this file's WEIGHTS.

    The single definition of the blend: score.py builds the precomputed column
    with it, graph.py re-derives it after averaging components along an edge,
    and router.py's live re-blend leans on the same weights. Column `c_x` is
    weighted by `WEIGHTS["x"]`.
    """
    return sum(WEIGHTS[c[2:]] * df[c] for c in components(df))


def composite(raw, score_adj):
    """Blend the weighted component sum into the 0–10 scenic score.

    The single definition of the scale: router.py imports this so a live
    per-user re-blend lands on exactly the same numbers as the precomputed
    `score` column, and re-calibrating here moves both at once.
    """
    return 10.0 * np.clip((raw + RAW_BASE) * STRETCH + score_adj, 0.0, 1.0)


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
    curv = curvature_deg_per_km(geoms, chunks["road_len_m"].to_numpy())
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
    urban_tree = build_tree(load_layer(d, "urban_areas"))
    place_tree = build_tree(load_layer(d, "place_points"))

    near_water = near_flags(water_tree, geoms, DIST["water"])
    mid_water = near_flags(water_tree, geoms, DIST["water_mid"])
    chunks["c_water"] = np.where(near_water, 1.0, np.where(mid_water, 0.45, 0.0))
    chunks["c_coast"] = near_flags(coast_tree, geoms, DIST["coast"]).astype(float)
    # Forest: OSM's designated green polygons and WorldCover's measured tree
    # cover, half each. OSM green records land *designation*, not vegetation, so
    # on its own it is not comparable between states — its completeness against
    # WorldCover runs 0.24 in Maine to 0.80 in Rhode Island while the tree cover
    # actually beside those roads is flat at 82-91%, which leaves the most
    # forested state in the country with the least green credit. Half and half
    # rather than a straight swap because the two are only correlated at
    # rho=+0.3: a state park is worth crediting as a park even where the canopy
    # is thin, and 27% of Massachusetts road-km has no polygon at all on land
    # indistinguishable from the land OSM does call green. One blended column
    # rather than two weighted ones so the forest/park slider keeps its whole
    # 0.18 — a separate baseline column would leave half of forest-ness
    # permanently on for a user who set the slider to zero. See
    # docs/geodata-sources-findings.md.
    green = near_flags(green_tree, geoms, DIST["green"]).astype(float)
    tree = sample_tree_cover(chunks, d / "tree_cover.parquet")
    chunks["c_forest"] = 0.5 * green + 0.5 * tree
    chunks["c_farm"] = near_flags(farm_tree, geoms, DIST["farm"]).astype(float)
    chunks["c_views"] = near_flags(view_tree, geoms, DIST["view"]).astype(float)
    chunks["c_scenic_tag"] = chunks["scenic"].astype(float)
    chunks["c_relief"] = sample_relief(chunks, d / "relief.tif")

    # Townscape: full credit inside a retail/commercial district or close to a
    # town-center node, tapering to partial credit across the town's wider orbit.
    in_urban = near_flags(urban_tree, geoms, DIST["urban"])
    near_place = near_flags(place_tree, geoms, DIST["place"])
    mid_place = near_flags(place_tree, geoms, DIST["place_mid"])
    chunks["c_urban"] = np.where(in_urban | near_place, 1.0,
                                 np.where(mid_place, 0.5, 0.0))
    print(f"features computed in {time.time() - t0:.0f}s")

    # Composite score. Blended over whatever c_ columns exist rather than a
    # written-out sum, because graph.py re-derives the same blend from the same
    # WEIGHTS when it averages components along an edge — an explicit list here
    # is a list that can silently fall out of step with that one, and with the
    # router's live re-blend. A component with no WEIGHTS entry is a KeyError
    # rather than a term quietly missing from the score.
    raw = blend(chunks)
    class_adj = chunks["highway"].map(CLASS_ADJ).fillna(0.0)
    unpaved_adj = np.where(chunks["surface"].isin(UNPAVED), UNPAVED_ADJ, 0.0)
    # Store the road-class/surface adjustment separately from the composite. The
    # router needs it to re-blend a per-user score live: it recombines the raw
    # component vector with the user's beauty-type weights, then re-applies this
    # same adjustment so highways/unpaved roads stay penalized.
    chunks["score_adj"] = class_adj + unpaved_adj
    chunks["raw"] = raw
    chunks["score"] = composite(raw, chunks["score_adj"])

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

    calibration_report(chunks)


def calibration_report(chunks: gpd.GeoDataFrame):
    """Is the 0–10 scale actually used, and do known roads land in the right
    order? A regression here means a constant at the top of this file needs
    refitting — every number below is length-weighted, since a score is only as
    important as the kilometres it covers."""
    km = chunks["length_m"] / 1000.0
    total = km.sum()
    lw = lambda col, mask: float((chunks[col][mask] * km[mask]).sum()
                                 / max(km[mask].sum(), 1e-9))
    everything = np.ones(len(chunks), dtype=bool)

    pct = np.percentile(np.repeat(chunks["score"], np.maximum((km * 10).astype(int), 1)),
                        [10, 50, 90, 99])
    print(f"scale (len-weighted): p10 {pct[0]:.1f}  p50 {pct[1]:.1f}  "
          f"p90 {pct[2]:.1f}  p99 {pct[3]:.1f}  mean {lw('score', everything):.2f}")
    print(f"      raw blend: p50 {np.percentile(chunks['raw'], 50):.3f}  "
          f"p99 {np.percentile(chunks['raw'], 99):.3f}   "
          f"(RAW_BASE {RAW_BASE}, STRETCH {STRETCH})")
    pinned = 100 * km[chunks["score"] >= 9.99].sum() / total
    floored = 100 * km[chunks["score"] <= 0.01].sum() / total
    print(f"      clipped: {floored:.1f}% of km at 0, {pinned:.1f}% at 10")

    print("component coverage (% of network km at >= 0.5, and mean):")
    for c in sorted(col for col in chunks.columns if col.startswith("c_")):
        print(f"      {c:14s} {100 * km[chunks[c] >= 0.5].sum() / total:5.1f}%   "
              f"mean {chunks[c].mean():.3f}")

    name = chunks["name"].fillna("").str.lower()
    ref = chunks["ref"].fillna("")
    print("benchmarks (scenic roads should sit far above the interstates):")
    for label, mask in [
        ("Greylock Notch/Rockwell", name.str.contains("notch road|rockwell road")),
        # "jacob" alone matched 80 ways, 66 of them streets named after
        # people (Jacob Cobb Lane, Jacob Amsden Road) and only 14 the byway.
        # OSM names it "Jacobs Ladder Road", which is also why the old
        # BYWAY_NAMES entry "jacob's ladder trail" never matched a way.
        ("Jacob's Ladder Road", name.str.contains("jacob'?s ladder")),
        ("Mohawk Trail", name.str.contains("mohawk trail")),
        ("Route 6A (Old King's Hwy)", ref.str.contains("6A", na=False)),
        ("I-90 (Mass Pike)", ref.str.fullmatch("I 90", na=False)),
        ("I-95", ref.str.contains("I 95", na=False)),
    ]:
        m = mask.to_numpy()
        if m.any():
            print(f"      {label:26s} {lw('score', m):5.2f}   "
                  f"({km[m].sum():5.0f} km)")
        else:
            print(f"      {label:26s}  no match")


if __name__ == "__main__":
    main(sys.argv[1])
