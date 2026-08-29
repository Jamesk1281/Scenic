"""Build a terrain-relief raster for the region from free Terrarium elevation tiles.

Downloads Mapzen/Terrarium terrain-RGB tiles (free, no auth) covering the MA
bounding box, decodes them to meters, computes local relief (the elevation
range within a ~1 km window — a robust proxy for hills, valleys, and overlook
potential), and writes:

  data/processed/relief.tif     local relief in meters (EPSG:3857)
  data/processed/elevation.tif  raw elevation in meters (EPSG:3857)

score.py samples relief.tif to add a terrain component to the scenic score.

Usage: python elevation.py <output_dir> [zoom]
"""

import concurrent.futures as cf
import io
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.transform import from_bounds
from scipy.ndimage import maximum_filter, minimum_filter

# New England bounding box (lon/lat) with a small margin: the envelope of the
# six merged Geofabrik extracts (MA, CT, RI, VT, NH, ME). At zoom 11 this is
# 40x53 = 2120 tiles, a 1.11 GB float64 mosaic peaking near 5.6 GB through the
# relief filters. The MA-only box was (-73.55, 41.18, -69.85, 42.92).
BBOX = (-73.76, 40.93, -66.87, 47.47)
TILE_URL = "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png"

# Neighborhood for local relief, in *ground* meters. Web Mercator stretches
# distance by 1/cos(latitude), so a window sized in raw mercator units is
# narrower on the ground than it reads — at Massachusetts' latitude, 26%
# narrower. That went unnoticed because it is self-consistent within one
# region: score.py's RELIEF_FULL was fitted against whatever window this
# actually produced. It stops being self-consistent the moment the pipeline is
# pointed at a second region, where the same constant would silently mean a
# different distance. 750 m is the window MA has been using all along
# (13 px either way), now stated in the units it is measured in.
RELIEF_WINDOW_M = 750.0

# The latitude the window is sized at. Fixed, rather than the mid-point of
# whatever BBOX happens to be, because `win` is one integer for the whole
# mosaic: derive it from the extent and widening the box silently rescores
# every region already inside it. Pointing BBOX at New England moved the
# mid-latitude from 42.05 to 44.20 and the window from 13 px to 15 px, which
# back at Massachusetts' own latitude is 851 m of ground instead of 738 m —
# 15% wider than the window score.py's RELIEF_FULL was fitted against, applied
# to every MA road, with nothing in the output saying so. 42.05 is that fitted
# latitude, so MA's numbers stay put and a second region is measured with the
# same constant meaning the same distance.
RELIEF_REF_LAT = 42.05

# Below this share of the mosaic covered by real data, stop rather than write a
# raster. Missing tiles are filled as sea level, so a hole does not read as
# "no data" downstream — it reads as a flat plain ringed by a cliff of maximal
# relief, which score.py then happily turns into scenery.
MIN_COVERAGE = 0.98

TILE_PX = 256
R = 6378137.0  # web mercator radius


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_to_mercator(x, y, z):
    """Top-left corner of tile (x, y) at zoom z, in EPSG:3857 meters."""
    n = 2 ** z
    mx = x / n * 2 * math.pi * R - math.pi * R
    my = math.pi * R - y / n * 2 * math.pi * R
    return mx, my


def fetch_tile(z, x, y, cache: Path):
    """Fetch one terrain tile. Returns (x, y, rgb or None, reason or None).

    A tile that cannot be fetched is not a harmless gap — main() fills it as
    sea level — so the reason travels back with it and gets reported rather
    than swallowed. Retries back off, because the failure worth retrying is a
    rate limit or a blip, and hammering S3 three times in a row rides out
    neither.
    """
    p = cache / f"{z}_{x}_{y}.png"
    if p.exists():
        try:
            return x, y, np.asarray(Image.open(p).convert("RGB")), None
        except Exception as e:
            # A truncated write from an interrupted earlier run. Drop it and
            # re-fetch rather than failing forever on a poisoned cache.
            p.unlink(missing_ok=True)
            print(f"  re-fetching corrupt cache entry {p.name}: {e}")

    reason = "no attempt made"
    for attempt in range(3):
        try:
            r = requests.get(TILE_URL.format(z=z, x=x, y=y), timeout=30)
            if r.status_code == 200:
                p.write_bytes(r.content)
                return x, y, np.asarray(
                    Image.open(io.BytesIO(r.content)).convert("RGB")), None
            reason = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            reason = type(e).__name__
        if attempt < 2:
            time.sleep(0.5 * 2 ** attempt)
    return x, y, None, reason


def decode_terrarium(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.float64)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


def main(out_dir: str, zoom: int = 11):
    out = Path(out_dir)
    cache = out.parent / "raw" / "terrain"
    cache.mkdir(parents=True, exist_ok=True)

    w, s, e, n = BBOX
    x0f, y0f = lonlat_to_tile(w, n, zoom)  # north-west
    x1f, y1f = lonlat_to_tile(e, s, zoom)  # south-east
    x0, y0 = int(math.floor(x0f)), int(math.floor(y0f))
    x1, y1 = int(math.floor(x1f)), int(math.floor(y1f))
    tx = list(range(x0, x1 + 1))
    ty = list(range(y0, y1 + 1))
    print(f"zoom {zoom}: {len(tx)}x{len(ty)} = {len(tx) * len(ty)} tiles")

    H, W = len(ty) * TILE_PX, len(tx) * TILE_PX
    elev = np.full((H, W), np.nan, dtype=np.float64)

    jobs = [(zoom, x, y) for x in tx for y in ty]
    done, missing = 0, []
    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        for x, y, rgb, reason in ex.map(lambda j: fetch_tile(*j, cache), jobs):
            done += 1
            if rgb is None:
                missing.append((x, y, reason))
                continue
            col = (x - x0) * TILE_PX
            row = (y - y0) * TILE_PX
            elev[row:row + TILE_PX, col:col + TILE_PX] = decode_terrarium(rgb)
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)} tiles")

    valid = ~np.isnan(elev)
    coverage = float(valid.mean())
    if missing:
        print(f"WARNING: {len(missing)} of {len(jobs)} tiles could not be fetched; "
              f"each becomes a ~{TILE_PX * 40:.0f} km^2 patch of fake sea level")
        for x, y, reason in missing[:10]:
            print(f"    z{zoom}/{x}/{y}: {reason}")
        if len(missing) > 10:
            print(f"    ... and {len(missing) - 10} more")
    if coverage < MIN_COVERAGE:
        raise SystemExit(
            f"only {100 * coverage:.1f}% of the mosaic has real elevation data "
            f"(need {100 * MIN_COVERAGE:.0f}%). Refusing to write a relief raster "
            f"whose holes would score as terrain. Re-run to retry the failed "
            f"tiles — successful ones are cached in {cache}."
        )

    # Mercator bounds + transform for the assembled mosaic
    left, top = tile_to_mercator(x0, y0, zoom)
    right, bottom = tile_to_mercator(x1 + 1, y1 + 1, zoom)
    transform = from_bounds(left, bottom, right, top, W, H)

    # Local relief via a moving window (range = max - min). The window is sized
    # in ground meters, so the mercator scale factor at this region's latitude
    # has to come out first — see RELIEF_WINDOW_M.
    px_m = (right - left) / W                       # mercator meters per pixel
    px_ground = px_m * math.cos(math.radians(RELIEF_REF_LAT))
    win = max(3, int(round(RELIEF_WINDOW_M / px_ground)) | 1)  # odd
    print(f"pixel ~{px_m:.0f} mercator m (~{px_ground:.0f} m on the ground at "
          f"{RELIEF_REF_LAT:.2f}N); relief window {win}px (~{win * px_ground:.0f} m)")
    # Clamp ocean bathymetry (Terrarium encodes sea floor as deep negatives) to
    # sea level so coastal roads don't get spuriously huge land relief.
    land = np.clip(np.where(np.isnan(elev), 0.0, elev), 0.0, None)
    relief = (maximum_filter(land, size=win) - minimum_filter(land, size=win))
    # A hole is not flat ground. `elev` is NaN where no tile was fetched, and
    # the substitution above turned that into 0 m so the filters would run;
    # putting the NaN back is what lets a reader tell "no terrain here" from
    # "terrain, and it is level" — see the nodata= on the write below.
    relief = np.where(np.isnan(elev), np.nan, relief).astype(np.float32)

    prof = dict(
        driver="GTiff", height=H, width=W, count=1, dtype="float32",
        crs="EPSG:3857", transform=transform, compress="deflate", predictor=2,
    )
    # nodata, like elevation.tif below. Without it rasterio's `sample` hands a
    # point outside the raster back as 0.0 — genuine flat terrain — so a run
    # against a PBF this mosaic does not cover scored every road's relief as
    # zero and said nothing, and `score.py`'s np.nan_to_num had no NaN to catch.
    with rasterio.open(out / "relief.tif", "w", nodata=float("nan"), **prof) as dst:
        dst.write(relief, 1)
    with rasterio.open(out / "elevation.tif", "w", nodata=float("nan"), **prof) as dst:
        dst.write(elev.astype(np.float32), 1)

    print(f"elevation: {np.nanmin(elev):.0f}..{np.nanmax(elev):.0f} m "
          f"({100 * coverage:.1f}% covered)")
    print(f"relief: median {np.median(relief[valid]):.0f} m, "
          f"p95 {np.percentile(relief[valid], 95):.0f} m")
    print(f"wrote {out / 'relief.tif'} and {out / 'elevation.tif'}")


if __name__ == "__main__":
    z = int(sys.argv[2]) if len(sys.argv) > 2 else 11
    main(sys.argv[1], z)
