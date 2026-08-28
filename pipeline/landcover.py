"""Sample ESA WorldCover tree cover at every road chunk midpoint.

OSM's green polygons record land *designation*, not vegetation, and that makes
`c_green` wildly non-uniform across New England: the OSM/WorldCover completeness
ratio runs 0.24 in Maine to 0.80 in Rhode Island while the tree cover actually
beside those roads is nearly flat at 82-91%. Maine is the most forested state in
the country and gets the least green credit. This stage adds the missing half —
a land-cover signal produced by one global process that has never heard of a
state line, so a 7/10 can mean the same thing in Stowe as in Sudbury. See
docs/geodata-sources-findings.md and docs/geodata-peer-review-verdict.md.

Reads ESA WorldCover v200 2021 (10 m, CC-BY 4.0, global) and writes:

  data/processed/tree_cover.parquet   lon, lat, tree fraction per chunk

score.py joins it into `c_forest = 0.5 * c_green + 0.5 * tree`. The join is
positional, so this stage chunks the roads with score.py's own chunk_roads() and
stores the midpoint it sampled; score.py refuses a file whose midpoints do not
match the ones it is about to score.

Memory: **the raster side is bounded by one tile and does not grow with the
region.** Each tile is read at most once, as the single window bounding the
sample boxes that fall in it, and those pixels are freed before the next tile is
opened -- 1.3 GB for a tile covered corner to corner, and measured 255 MB for
Massachusetts' largest, 757 MB for Maine's. Peak RSS was 1.9 GB for
Massachusetts, most of it the chunked road frame rather than the raster. That is
the opposite cost shape to elevation.py, whose in-RAM float64 mosaic plus max/min
filters is already 5.6 GB for New England and grows with every state added.
There is no mosaic, no filter pass and no coverage guard here.

Usage: python landcover.py <processed_dir> [--stream]

  --stream reads the windows straight off the COGs over HTTP range requests and
  writes nothing to disk. Verified to return bit-identical values to the cached
  path. Worth it because the eight tiles New England needs are 343 MB and this
  project's build box has run at 99% full.
"""

import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.windows import Window

from score import chunk_roads, load_layer

TILE_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
            "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")

# WorldCover's classification. 10 is tree cover; the rest (shrub, grass, crop,
# built, bare, snow, water, wetland, mangrove, moss) are not what this component
# measures. Kept as a named constant because "== 10" reads like an index.
TREE_CLASS = 10

# The tile grid: 3-degree cells on a 1/12000-degree pixel, named for their
# SOUTH-WEST corner. That naming is the trap in this file. Deriving the tile
# with ceil() instead of floor() sends every point three degrees east, where it
# lands outside the tile's bounds, gets clipped to an edge column, and comes
# back as plausible-looking garbage instead of an error -- it has already
# produced one wrong results table on this project. The arithmetic below never
# names a tile directly: it converts lon/lat to a global pixel index first and
# divides, which is total and exact, and then asserts that the tile it opened is
# georeferenced where its name says it is.
TILE_DEG = 3
PX_PER_DEG = 12000
TILE_PX = TILE_DEG * PX_PER_DEG  # 36000

# Ground size of the sampling box, matching score.py's 80 m green threshold
# closely enough to be the same question asked of a different source.
#
# Sized in *ground* metres, not pixels. A pixel is 1/12000 degree in both axes,
# which up here is 9.3 m north-south but only 6.9 m east-west -- a fixed 9x9
# window is 83 m x 62 m, not the 90 m square it reads as. The anisotropy does
# not change the uniformity finding, but a smaller box raises the share of road
# pinned at tree = 1.0, and fixed 9x9 puts Maine at 0.335 against the < 0.35
# ceiling guard in tests/test_calibration.py. The ground-correct box drops it to
# 0.258. This is the one place the correction is load-bearing.
SAMPLE_BOX_M = 100.0
M_PER_DEG_LAT = 111_320.0


def tile_name(tile_row: int, tile_col: int) -> str:
    """Tile id from its position on the global 3-degree grid, e.g. N42W072."""
    lat0 = 90 - (tile_row + 1) * TILE_DEG   # south edge
    lon0 = tile_col * TILE_DEG - 180        # west edge
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return f"{ns}{abs(lat0):02d}{ew}{abs(lon0):03d}"


def tile_bounds(tile_row: int, tile_col: int):
    """(west, south, east, north) of a tile, in degrees."""
    lon0 = tile_col * TILE_DEG - 180
    lat0 = 90 - (tile_row + 1) * TILE_DEG
    return lon0, lat0, lon0 + TILE_DEG, lat0 + TILE_DEG


def window_px(lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Odd pixel dimensions of a ~SAMPLE_BOX_M ground box at each latitude.

    Rows are constant (a degree of latitude is a degree of latitude); columns
    widen towards the pole as a degree of longitude shortens.
    """
    m_per_px_lat = M_PER_DEG_LAT / PX_PER_DEG
    m_per_px_lon = M_PER_DEG_LAT * np.cos(np.radians(lat)) / PX_PER_DEG
    rows = np.full(len(lat), max(3, int(round(SAMPLE_BOX_M / m_per_px_lat)) | 1))
    cols = np.maximum(3, np.rint(SAMPLE_BOX_M / m_per_px_lon).astype(np.int64) | 1)
    return rows, cols


def fetch_tile(tile: str, cache: Path) -> Path:
    """Download one tile to the cache if it is not already there.

    Retries back off, for the same reason elevation.py's do: the failure worth
    retrying is a rate limit or a blip, and three requests in a row rides out
    neither. A tile that cannot be fetched raises -- unlike a missing elevation
    tile there is no plausible fill value, and a silent zero here would read as
    "no trees" on land that is nothing but.
    """
    p = cache / f"{tile}.tif"
    if p.exists() and p.stat().st_size > 0:
        return p
    tmp = p.with_suffix(".tif.part")
    reason = "no attempt made"
    for attempt in range(3):
        try:
            with requests.get(TILE_URL.format(tile=tile), timeout=120,
                              stream=True) as r:
                if r.status_code == 200:
                    with open(tmp, "wb") as f:
                        for block in r.iter_content(1 << 20):
                            f.write(block)
                    tmp.replace(p)
                    return p
                reason = f"HTTP {r.status_code}"
                if r.status_code == 404:
                    break  # a genuinely absent cell; retrying cannot help
        except requests.RequestException as e:
            reason = type(e).__name__
        if attempt < 2:
            time.sleep(0.5 * 2 ** attempt)
    tmp.unlink(missing_ok=True)
    raise SystemExit(
        f"could not fetch WorldCover tile {tile}: {reason}\n"
        f"  {TILE_URL.format(tile=tile)}\n"
        f"WorldCover's grid omits cells that are entirely ocean, so a 404 on a "
        f"tile a coastal sample box reaches into is possible and would need "
        f"handling here; anything else is a network or bucket problem."
    )


def open_tile(tile: str, cache: Path, stream: bool):
    """A rasterio dataset for one tile, from the cache or over HTTP."""
    if stream:
        return rasterio.open("/vsicurl/" + TILE_URL.format(tile=tile))
    return rasterio.open(fetch_tile(tile, cache))


def check_georeferencing(src, tile_row: int, tile_col: int, tile: str):
    """Refuse a tile that is not where its name says it is.

    The whole point of the check: a mis-derived tile id still opens, still
    reads, and still returns numbers. This is what turns that into a stop.
    """
    named = tile_bounds(tile_row, tile_col)
    west, south, east, north = named
    b = src.bounds
    if src.width != TILE_PX or src.height != TILE_PX:
        raise SystemExit(f"{tile}: expected {TILE_PX}x{TILE_PX} px, "
                         f"got {src.width}x{src.height}")
    if not all(math.isclose(actual, expect, abs_tol=1e-6) for actual, expect in
               zip((b.left, b.bottom, b.right, b.top), named)):
        raise SystemExit(
            f"{tile}: name says {west},{south} to {east},{north} but the raster "
            f"covers {b.left},{b.bottom} to {b.right},{b.top}. WorldCover tiles "
            f"are named for their south-west corner -- the tile index is "
            f"floor(lon/3)*3, not ceil.")


def sample_tree_fraction(lon: np.ndarray, lat: np.ndarray, cache: Path,
                         stream: bool) -> np.ndarray:
    """Fraction of each point's ~100 m ground box classified as tree cover.

    Points are converted to a global pixel index before anything else, so a
    sample box that straddles a tile edge is counted from both tiles rather than
    silently truncated -- Massachusetts' southern border sits on the lat 42
    tile line, so that is real road, not a corner case.
    """
    n = len(lon)
    # Global pixel grid. Every WorldCover tile is a 36000-pixel cell of this one
    # grid, so a tile index is a division rather than a name lookup, and a point
    # exactly on a tile edge (Maine has road at exactly lat 45.0) resolves into
    # the tile below it instead of off the end of the tile above.
    gcol = np.floor((lon + 180.0) * PX_PER_DEG).astype(np.int64)
    grow = np.floor((90.0 - lat) * PX_PER_DEG).astype(np.int64)

    rows_px, cols_px = window_px(lat)
    expected = rows_px * cols_px
    r0, r1 = grow - rows_px // 2, grow + rows_px // 2
    c0, c1 = gcol - cols_px // 2, gcol + cols_px // 2

    # Which tile(s) each box touches. Almost always one; two or four at a seam.
    boxes: dict[tuple[int, int], list] = {}
    lr0, lr1, lc0, lc1 = r0.tolist(), r1.tolist(), c0.tolist(), c1.tolist()
    ltr0, ltr1 = (r0 // TILE_PX).tolist(), (r1 // TILE_PX).tolist()
    ltc0, ltc1 = (c0 // TILE_PX).tolist(), (c1 // TILE_PX).tolist()
    for i in range(n):
        for tr in range(ltr0[i], ltr1[i] + 1):
            row_lo, row_hi = tr * TILE_PX, tr * TILE_PX + TILE_PX - 1
            for tc in range(ltc0[i], ltc1[i] + 1):
                col_lo, col_hi = tc * TILE_PX, tc * TILE_PX + TILE_PX - 1
                boxes.setdefault((tr, tc), []).append((
                    i,
                    max(lr0[i], row_lo) - row_lo, min(lr1[i], row_hi) - row_lo,
                    max(lc0[i], col_lo) - col_lo, min(lc1[i], col_hi) - col_lo,
                ))

    tree_n = np.zeros(n, dtype=np.int64)
    total_n = np.zeros(n, dtype=np.int64)
    order = sorted(boxes)
    print(f"{n:,} midpoints across {len(order)} WorldCover tile(s): "
          f"{', '.join(tile_name(*k) for k in order)}")

    for tr, tc in order:
        items = boxes[(tr, tc)]
        tile = tile_name(tr, tc)
        t0 = time.time()
        # One read per tile: the window bounding every box that lands in it.
        rlo = min(it[1] for it in items)
        rhi = max(it[2] for it in items)
        clo = min(it[3] for it in items)
        chi = max(it[4] for it in items)
        with open_tile(tile, cache, stream) as src:
            check_georeferencing(src, tr, tc, tile)
            arr = src.read(1, window=Window(clo, rlo, chi - clo + 1, rhi - rlo + 1))
        print(f"  {tile}: {len(items):,} boxes, read {arr.shape[1]}x{arr.shape[0]} px "
              f"({arr.nbytes / 1e6:.0f} MB) in {time.time() - t0:.0f}s")

        for i, br0, br1, bc0, bc1 in items:
            w = arr[br0 - rlo:br1 - rlo + 1, bc0 - clo:bc1 - clo + 1]
            tree_n[i] += int((w == TREE_CLASS).sum())
            total_n[i] += w.size
        del arr

    # Every pixel of every box has to have been read from somewhere. This is the
    # assertion that fires if a tile is mis-indexed, mis-clipped, or missing --
    # the failure mode this source has is a plausible number, not an exception.
    short = np.flatnonzero(total_n != expected)
    if len(short):
        i = short[0]
        raise SystemExit(
            f"{len(short):,} sample boxes did not read their full pixel count "
            f"(first: point {i} at {lon[i]:.5f},{lat[i]:.5f} got {total_n[i]} of "
            f"{expected[i]} px). A box reaching into a tile that was never "
            f"opened, or a tile index off by one cell.")

    return (tree_n / total_n).astype(np.float32)


def main(processed_dir: str, stream: bool = False):
    d = Path(processed_dir)
    cache = d.parent / "raw" / "worldcover"
    if not stream:
        cache.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    roads = load_layer(d, "roads")
    chunks = chunk_roads(roads)
    length_m = chunks.geometry.length.to_numpy()
    mids = chunks.geometry.interpolate(0.5, normalized=True).to_crs(4326)
    lon, lat = mids.x.to_numpy(), mids.y.to_numpy()
    print(f"{len(roads):,} ways -> {len(chunks):,} chunks in {time.time() - t0:.0f}s")

    tree = sample_tree_fraction(lon, lat, cache, stream)

    out_path = d / "tree_cover.parquet"
    pd.DataFrame({"lon": lon, "lat": lat, "tree": tree}).to_parquet(out_path)
    print(f"wrote {out_path} ({len(tree):,} chunks) in {time.time() - t0:.0f}s total")

    # Length-weighted, because a value is only as important as the kilometres it
    # covers -- and because the two numbers this component can go wrong on are
    # both distributional: no spread left (a flag rather than a signal), or too
    # much road pinned at the ceiling (test_calibration.py's < 0.35 guard).
    km = length_m / 1000.0
    order = np.argsort(tree)
    cw = np.cumsum(km[order]) / km.sum()
    p25, p50, p75 = (np.interp(q, cw, tree[order]) for q in (0.25, 0.5, 0.75))
    print(f"tree fraction (len-weighted): p25 {p25:.2f}  p50 {p50:.2f}  "
          f"p75 {p75:.2f}  mean {(tree * km).sum() / km.sum():.3f}")
    print(f"      {100 * km[tree >= 0.999].sum() / km.sum():.1f}% of km at 1.0, "
          f"{100 * km[tree <= 0.001].sum() / km.sum():.1f}% at 0.0")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], stream="--stream" in sys.argv[1:])
