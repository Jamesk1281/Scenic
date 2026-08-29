# New England terrain: built, and the relief fix verified

Run 2026-08-29 against `docs/new-england-terrain-brief.md`. Command, verbatim,
from the main checkout:

```bash
.venv/bin/python pipeline/elevation.py data/processed-ne 11
```

Exit 0. `data/processed` was not written to, and no constant was changed. The
first pass changed no source at all; `elevation.py` was then patched to fix the
corrupt-pixel defect it turned up (Finding 1), and New England rebuilt off the
warm cache. Test suite: **294 passed, 0 skipped**, before and after the patch.

## Verdict: the fix works, and the match is better than asked for

The brief asked whether the Massachusetts region of the New England raster
reproduces the live `data/processed/relief.tif` **to float32 rounding**. It does
better than that over the region that can be compared fairly:

| region | pixels | differing | max abs diff |
|---|---|---|---|
| **interior** (inset `win//2` = 6 px) | 20,074,640 | **0** | **0** — bit-identical |
| border ring (outer 6 px) | 110,448 | 35,332 | 145.96 m |
| full MA footprint | 20,185,088 | 35,332 (0.175%) | 145.96 m |

`win` printed as **13 px (~738 m)** at the New England `BBOX`, exactly as the
arithmetic predicted. `px_ground` came out 56.8 m.

**The border ring is not the fix failing.** It is a boundary artifact of the
*old* raster. `scipy.ndimage.maximum_filter`/`minimum_filter` default to
`mode='reflect'`, so in the MA-only build the outermost 6 px were filtered
against fabricated mirror-image neighbours. In the New England build those same
pixels have real neighbouring terrain, because MA's tile range (x 605–626,
y 753–766) sits strictly inside New England's (x 604–643, y 716–768) with at
least a full 256 px tile of margin on every side. Where both rasters had true
neighbours, the agreement is exact to the bit. The NE raster is the more correct
of the two at the rim.

The numbers the fix exists to protect held:

| | live MA | MA read out of NE | what `win=15` would have given |
|---|---|---|---|
| median relief | 24.79 m | 24.80 m | 28.00 m (+12.9%) |
| area ≥ `RELIEF_FULL` (100 m) | 7.157% | 7.166% | 9.15% |

The residual 0.008 m of median shift is entirely the border ring.

(Those figures are from the first pass, before `elevation.py` was patched. After
the fix the interior is bit-identical over 20,074,638 pixels rather than
20,074,640: the two remaining are the corrupt Cape Cod pixels, now nodata. See
"The fix".)

### One caveat that had to be cleared first

The live `relief.tif` is dated **Jun 20** and `elevation.py` has been edited four
times since, so a mismatch would have been ambiguous. Checked before trusting
the comparison: the Jun 20 version (`da11738`) computed
`win = round(1000.0 / px_m) | 1` = 13, and today's computes
`round(750.0 / px_ground) | 1` = 13. The `RELIEF_WINDOW_M` refactor from mercator
metres to ground metres was deliberately value-preserving, and the ocean clamp
(`np.clip(..., 0.0, None)`) was already present in June. So the baseline is
comparable, and the bit-identical interior confirms it empirically.

## What it actually cost

| | predicted | **measured** |
|---|---|---|
| tiles fetched (new) | 1,812 (~159 MB) | **1,812** (133 MB) |
| tile cache after | 2,120 | **2,120** (160 MB) |
| mosaic | 10240 × 13568, 1.11 GB float64 | **10240 × 13568, 1.111 GB** |
| peak RSS | ~5.6 GB (≈5× the array) | **5.19 GB / 4.83 GiB** (4.67× the array) |
| rasters written | ~406 MB | **389 MB** (elevation 207.2 + relief 182.0) |
| wall time | never estimated | **114.2 s** (122.8 user, 9.6 sys) |
| coverage | — | **100.0%**, zero tiles missing |

Measured with `/usr/bin/time -l`; `0 swaps`, so the peak is real resident memory,
not a swapped figure. Every structural prediction was exact; the two estimates
were both slightly conservative. Disk went 6.2 → 5.7 GiB free (~522 MB consumed),
against the 565 MB the brief budgeted.

`MIN_COVERAGE` did not trip. All 2,120 tiles fetched on the first attempt.

## Finding 1 — the Terrarium source data has corrupt pixels, and MA was just lucky

**This is the most important thing in the run.** The build reported:

```
elevation: -32768..32767 m (100.0% covered)
```

Those are not New England elevations. New England's true maximum is Mount
Washington at 1,917 m. `-32768.0` is `RGB(0,0,0)` and `32767.0` is
`RGB(255,255,0)` under the Terrarium decode — the ends of the encoding.

**320 pixels of 138.9 M are implausible** (154 above 2,000 m, 166 below
−12,000 m), scattered across 9 tiles. They are not whole failed tiles: they are
short *horizontal runs* of alternating black and yellow pixels sitting inside
otherwise perfect terrain. In `11_619_751` (inland New Hampshire), row 241
columns 87–92 read −32768, +32767, −32768, +32767 … while their immediate
neighbours read 76.8, 83.2, 86.7, 89.8, 95.6 m. That is a corrupted scanline.

**It is upstream, not ours.** Re-fetched all three worst tiles and compared:
bytes **identical** to the cache, valid PNG signature, valid `IEND`, and
`Content-Length` matching the body. The corruption is in the Mapzen/Terrarium
S3 objects themselves. Re-running will not fix it, and the retry logic in
`fetch_tile` cannot help — nothing failed.

**`MIN_COVERAGE` is structurally blind to this.** It tests `~np.isnan(elev)`,
which only catches tiles that could not be fetched. These tiles fetched
perfectly and decode to garbage, so coverage reads 100.0%.

**Why Massachusetts never saw it.** `land = np.clip(..., 0.0, None)` clamps
negatives, and MA's only two corrupt pixels are *negative* (−18,022.95, on Cape
Cod — present in the live `elevation.tif` too, which reads min −18022.95 / max
1063.38; the 1063.38 is Mount Greylock, correct). Nothing clamps the *positive*
side, and New England contains 154 positive spikes. The MA footprint of the new
raster is clean: max relief 502.64 m, zero pixels above 2,000 m — which is why
the interior comparison came out bit-identical.

**Damage to the New England relief raster: 2,281 pixels at ~32,767 m relief**
(0.0016% of valid pixels). Each corrupt source pixel poisons the full 13×13
window around it — roughly 990 m × 735 m of ground. Excluding them, the maximum
relief is 1,453 m, which is plausible for the White Mountains.

Downstream this is not cosmetic. `score.py:208` does
`np.clip(vals / RELIEF_FULL, 0, 1)`, so 32,767 m clips to **1.0 — a maximal
terrain score at weight 0.16** for any chunk midpoint landing in one of those
blocks. Exactly the failure mode the brief anticipated from missing tiles,
arriving instead through a door `MIN_COVERAGE` does not watch.

**Fixed** — see "The fix" below. It was reported rather than fixed on the first
pass because the brief scoped that run to build-and-verify; fixing it was asked
for afterwards.

## The fix

Two changes in `elevation.py`, no constant touched:

**1. A physical sanity band, folded into the existing hole mask.**
`ELEV_MAX_M = 9000.0` / `ELEV_MIN_M = -11000.0` — Everest is 8,849 m and
Challenger Deep is −10,935 m, so nothing real falls outside. Out-of-band pixels
become `NaN` *before* coverage is computed, so `MIN_COVERAGE` now guards this
failure mode too, and they are reported per tile the way missing tiles are:

```
WARNING: 321 pixel(s) across 9 tile(s) decode outside -11000..9000 m; the source
tiles are corrupt, not missing, so re-running will not fix them
    z11/631/734: 23 px
    z11/619/751: 210 px
    ...
elevation: -10722..1916 m (100.0% covered)
```

The band is unambiguous on the side that matters: the highest real pixel in New
England is Mount Washington at 1,915.7 m and the next value up is 32,767, with
nothing in between. It is deliberately *not* unambiguous on the negative side —
there is no gap there, so the surviving `−10722` above is garbage bathymetry the
band cannot distinguish from real sea floor. That costs nothing: the ocean clamp
flattens every negative to 0 before the filters, and `elevation.tif` has no
reader in the codebase (only `relief.tif` is consumed, by `score.py:335`).

**2. Gaps are now invisible to the filters rather than substituted with 0 m.**
This is the halo weakness listed as a minor note on the first pass, and fixing
it is what makes the sanity band actually sufficient. The old code substituted
`0.0` at every hole so the filters could run, which let a gap set the *minimum*
for every pixel within `win // 2` of it — inventing relief equal to the
surrounding ground, exactly the cliff the module exists to avoid. Now the
maximum filter sees `-inf` at gaps and the minimum filter sees `+inf`, so a gap
loses to every real neighbour in both passes and costs its own pixel and nothing
around it. `land` is reused across both passes to hold the peak down.

### Verified

| | before | after |
|---|---|---|
| elevation range | −32768 … 32767 m | **−10722 … 1916 m** |
| relief max | 32,767 m | **1,453 m** (White Mountains, plausible) |
| relief px > 2,000 m | 2,281 | **0** |
| NE relief median / p95 | 23.00 / 137.54 m | 23.00 / **137.51** m |

- **Changes are confined to the defect.** 2,312 of 138,936,320 pixels changed
  (0.0017%): 321 became nodata, 1,991 decreased, **none increased**, and
  **zero changed pixels lie outside `win // 2` of a dropped pixel** — checked
  with a dilation of the newly-nodata mask, which is the assertion that says the
  fix touched the corruption and nothing else.
- **Massachusetts is unmoved.** All **20,074,638** interior pixels that are
  finite in both rasters are **bit-identical** to the live `relief.tif`. The
  only difference in the whole MA footprint is the 2 corrupt Cape Cod pixels,
  which used to report 3.996 m and 3.824 m of relief and are now nodata — both
  in open water. Median stays 24.80 m and area ≥100 m stays 7.166%. Promoting
  this would not rescore a single road.
- **Cost.** Peak RSS 5.19 → **5.31 GB** (+2.4%, the extra boolean gap mask);
  peak memory *footprint* fell 5.23 → 4.79 GB. 97.9 s wall off the warm cache.

Verified against a copy of the pre-fix raster; the before/after comparison and
the MA check are both measured, not inferred.

## Finding 2 — `docs/new-england-rollout.md` §0a is stale and contradicts the code

The rollout doc, line 81, says **"Decided 2026-08-26: band the filter by
latitude"**, and then gives three reasons banding "beats pinning `win = 13`".
What actually shipped on 2026-08-29 (`6cd0620`) is the pinned latitude — the
option the doc argues against. The brief's own framing ("the rollout doc chose a
pinned latitude over latitude banding") describes the outcome, not the document.

Worth reconciling, because the doc's stated verification for Phase 0a is
"with banding, re-running elevation over the Massachusetts bbox must reproduce
today's `relief.tif` to within float32 rounding". That test has now been run
against the *pinned* implementation and passed, so the phase is satisfied — but
a reader of the doc would not know that.

The doc's remaining objection to pinning is still true and unaddressed: a pinned
13 px is 751 m of ground in Connecticut and 672 m in northern Maine. The window
no longer moves when the box widens, but it still is not a constant distance
within the box.

## Finding 3 — New England's relief distribution wants a different `RELIEF_FULL` (reported, not changed)

`RELIEF_FULL = 100.0` (`score.py:81`) was fitted to Massachusetts, whose comment
says MA "tops out" there. Measured, with the 2,281 corrupt spikes excluded:

| | median | p90 | p95 | p99 | max | ≥ 100 m |
|---|---|---|---|---|---|---|
| MA (live) | 24.8 | 86.4 | 115.1 | 185.3 | 502.6 | 7.16% |
| New England (all) | 23.0 | 102.0 | 137.5 | 223.0 | 1453.0 | 10.43% |
| NE north of MA | 32.2 | 115.0 | 152.0 | 238.9 | 1453.0 | **13.39%** |

At `RELIEF_FULL = 100`, 13.4% of northern New England already saturates, and a
1,453 m ravine in the White Mountains scores identically to a 100 m rise outside
Worcester. The top of the terrain scale carries no information north of MA.
Per the brief this is Phase 4's business — recorded, not acted on.

## Finding 4 — the whole-array filter has ~3× headroom, and it is thinner than it looks

Measured peak is **37.34 bytes per mosaic pixel** (4.67× the float64 array, where
`new-england-expansion.md` assumed ≈5×). Rescaling that doc's ladder by the
measured ratio:

| region | doc's estimate | measured-rate estimate |
|---|---|---|
| New England | 5.6 GB | **5.19 GB (actual)** |
| NE + NY | 10.9 GB | ~10.1 GB |
| Census Northeast | 14.1 GB | ~13.0 GB |
| East Coast | 51.5 GB | ~47.6 GB |

On this 24 GB machine the ceiling is ~460 M px (3.3× New England's mosaic) at a
16 GiB budget, ~575 M px (4.1×) at 20 GiB. So NE+NY is comfortable, the Census
Northeast is the last rung that fits, and the East Coast is out of reach.

**How urgent is banding? Not urgent for the next region, but the headroom is
overstated by the disk.** The machine is at 99% full with 5.7 GiB free, so
macOS has almost no room to swap. The current run showed `0 swaps` with 19 GB of
RAM to spare; a 13 GB Census-Northeast run has far less margin, and if it does
start swapping there is nowhere for it to go. Banding stays optional through
NE+NY and becomes the prerequisite at Census Northeast — and it is worth noting
that banding was already the *documented* decision (Finding 2), so this is
rediscovering a plan that exists rather than proposing a new one.

## Minor notes (not acted on)

- `elevation.py:126` never creates the output directory — `cache.mkdir()` makes
  `<out>/../raw/terrain` but nothing makes `<out>`, so `rasterio.open(out / ...)`
  would fail on a fresh path. Invisible in normal use because `data/processed`
  already exists. `mkdir -p data/processed-ne` was run first here.
- ~~`elevation.py:195` restores `NaN` only *at* a hole, not in the `win//2` halo
  around it.~~ **Fixed** as part of the corrupt-pixel work above — gaps now lose
  to every real neighbour in both filters instead of being substituted with 0 m.
- Nothing was found in `pipeline/extract.py` or `pipeline/score.py`; neither was
  opened for editing. `score.py` was read only, to confirm how relief is consumed.

## Artifacts left in place

`data/processed-ne/relief.tif` (182.0 MB) and `data/processed-ne/elevation.tif`
(207.2 MB), plus 2,120 tiles (160 MB) in the shared `data/raw/terrain` cache.
All gitignored. The expansion's slowest step is now done and cached; a rebuild
off the warm cache costs the filter time only, not the fetch.
