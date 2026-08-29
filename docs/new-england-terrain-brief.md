# Build the New England terrain, and prove the relief fix holds

**Status: decided, not started.** Nothing built, no source file to touch. This is
the first executable step of `docs/new-england-rollout.md`, scoped deliberately
narrow — see "Why only terrain".

## The goal

Two things, in one run:

1. **Build the New England relief raster** into a scratch directory. It is the
   long pole of the whole expansion (1,812 terrain tiles to fetch) and it is the
   first time `elevation.py` runs at regional scale.
2. **Prove the relief fix works**, by checking that the Massachusetts region of
   the New England raster reproduces the live Massachusetts one.

Then report what it actually cost, against predictions that were never measured.

## Why only terrain, and not the whole build

Two other jobs are live and this must not collide with either:

- **`pipeline/extract.py` and `pipeline/score.py` are being edited right now** by
  the byway-relations task (`docs/byway-relations-brief.md`). Running the OSM
  extract before that lands would produce a `roads.parquet` that has to be
  thrown away, and editing those files would conflict outright.
- **`data/processed` is the live promoted Massachusetts build**, validated
  2026-08-29 and already copied to the serving box. It must not be written to.

Terrain is the one stage that depends on neither. `relief.tif` is built from
elevation tiles alone — no roads, no scenic tags, no scoring constants — so it
is unaffected by whatever the byway job does, and it is needed by Phase 2
regardless. Build it now and the expansion's slowest step is already done when
the rest is ready.

## The fix being verified, and why it needs verifying

`pipeline/elevation.py:169` sizes the relief window as one integer for the whole
mosaic:

```python
px_ground = px_m * math.cos(math.radians(RELIEF_REF_LAT))
win = max(3, int(round(RELIEF_WINDOW_M / px_ground)) | 1)  # odd
```

That `RELIEF_REF_LAT = 42.05` (`elevation.py:58`) was merged 2026-08-29 to fix a
real defect: `win` used to come from the *bounding box's* mid-latitude, so
widening `BBOX` to New England moved it from 13 px to 15 px and silently
rescored every Massachusetts road. Measured on the existing MA raster:

| | win=13 | win=15 |
|---|---|---|
| MA relief median | 24.79 m | 28.00 m (**+12.9%**) |
| MA area at or above `RELIEF_FULL` (100 m) | 7.16% | **9.15%** |

`c_relief` carries weight 0.16, so that is not cosmetic.

The arithmetic says the fix works — at the New England `BBOX`, `px_ground` is
56.8 m and `win` comes out **13 px (~738 m)**, unchanged. **That has been
checked on paper and never against a built raster.** This task closes that.

## What to do

```bash
# from the MAIN CHECKOUT, never a worktree
.venv/bin/python pipeline/elevation.py data/processed-ne 11
```

`elevation.py` derives its tile cache as `<out>/../raw/terrain`, so
`data/processed-ne` shares the existing cache: the 308 Massachusetts tiles are
reused and only the new ones are fetched.

Then compare. The MA raster and the NE raster are both EPSG:3857 at zoom 11, so
their pixel grids are aligned and the Massachusetts window can be read straight
out of the bigger one with `rasterio`'s windowed read against
`data/processed/relief.tif`'s bounds. **They should agree to float32 rounding.**
Disagreement means the pinned latitude is not doing what the arithmetic says,
and that is a finding worth more than the raster.

## Predictions to check, none of them measured

From `docs/new-england-rollout.md` Phase 2 and
`docs/new-england-expansion.md`. Report the real figures beside them:

| | predicted | actual |
|---|---|---|
| tiles fetched (new) | 1,812 (~159 MB) | ? |
| tile cache after | 2,120 | ? |
| mosaic | 10240 x 13568 px, 1.11 GB float64 | ? |
| peak RSS | ~5.6 GB (≈5x the array, through the filters) | ? |
| rasters written | ~406 MB | ? |
| wall time | never estimated | ? |

## Traps

- **Do not write into `data/processed`.** Its `relief.tif` is the live
  Massachusetts one and is the comparison baseline. Build into
  `data/processed-ne`.
- **Do not edit `pipeline/extract.py` or `pipeline/score.py`.** Another session
  is editing both. If you find something wrong in them, write it down and leave
  it.
- **Do not change `RELIEF_FULL`, `RELIEF_WINDOW_M` or `RELIEF_REF_LAT`.**
  Re-fitting is rollout Phase 4 and is explicitly not this job. If the New
  England relief distribution looks like it wants a different `RELIEF_FULL`,
  that is a finding to report, not a change to make.
- **Disk is at 99% with roughly 6 GiB free.** The run needs about 565 MB
  (tiles plus rasters). Check before starting and stop if it is tighter than
  that. `data/processed_backup/` is 260 MB of a June 22 build that no current
  `Router` can load, if space is needed — but confirm before deleting it.
- **`MIN_COVERAGE = 0.98` (`elevation.py:49`) will abort the run** rather than
  write a raster with holes. That is correct behaviour, not a bug — New England
  includes a lot of ocean, and the check is about *missing tiles*, not water. If
  it trips, report the coverage figure rather than lowering the constant.
- **A tile fetch over 1,812 tiles will hit transient failures.** Check how
  `elevation.py` handles a failed tile before assuming a clean run; a tile
  silently filled as sea level reads downstream as flat ground ringed by a cliff
  of maximal relief, which `score.py` turns into scenery.

## Done looks like

1. `data/processed-ne/relief.tif` and `elevation.tif` built, with the tile count
   and coverage percentage reported.
2. A stated verdict on whether the Massachusetts region of the New England
   raster matches `data/processed/relief.tif` to float32 rounding — **or**, if
   it does not, the measured difference and where it comes from.
3. The prediction table above filled in with real numbers, including peak RSS
   measured rather than estimated.
4. A note on whether the whole-array filter is going to be a problem past New
   England, given what the peak actually was. The rollout doc chose a pinned
   latitude over latitude banding partly on the grounds that banding could come
   later; this is the measurement that says how urgent that is.
5. No constant changed, no other pipeline file touched.
