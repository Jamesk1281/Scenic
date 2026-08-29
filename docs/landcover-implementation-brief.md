# Add a land-cover component to the scenic score — stages 0–3

**Status: fully diagnosed, peer-reviewed, and decided. Nothing in `pipeline/`
has been touched yet — that is this task.** Four documents on branch
`claude/stoic-goldstine-d6b9d5` establish the case; read them in this order and
do not re-derive them:

1. `docs/geodata-sources-review.md` — the original question
2. `docs/geodata-sources-findings.md` — the measured answer
3. `docs/geodata-peer-review-verdict.md` — an adversarial review that reproduced
   the substrate exactly, failed to break the core argument four ways, and found
   four real defects in the findings
4. this file — what to actually build

## The goal

Scenic scores road beauty from an OSM PBF and Terrarium elevation tiles.
**OSM's `c_green` layer measures land *designation*, not vegetation**, and that
makes it wildly non-uniform across New England: the OSM ÷ WorldCover
completeness ratio runs **0.24 in Maine to 0.80 in Rhode Island (3.3×)**, while
WorldCover's tree cover on the same road-km is nearly flat (82–91%). Maine is the
most forested state in the country and gets the *least* green credit. 26.3% of
Massachusetts road-km is scored with no mapped polygon at all, and that
population is nearly indistinguishable from the roads OSM *does* call green
(WorldCover tree 0.678 vs 0.720).

**The New England rollout is confirmed real, with further US expansion intended.**
That settles it: `docs/new-england-rollout.md` Phase 4 promises "a 7/10 means the
same thing in Stowe as in Sudbury", and on the current inputs it cannot deliver
that — a region-wide re-fit would average the bias, not remove it.

This task adds a uniformly-produced land-cover signal so that promise becomes
reachable. **It is the first of at least three changes** — `c_urban` (weight
0.14, measured to point the wrong way, 2.3× spread) and `c_farm` (0.06, 5.9×
spread — the worst on the board) are out of scope here and remain non-uniform.

## Decisions already made — do not relitigate

- **Source: ESA WorldCover v200 2021.** CC-BY 4.0, 10 m, global (which matters
  for expansion past CONUS — NLCD stops at the border). Eight tiles cover New
  England at 343 MB; they are COGs with HTTP range requests. Retrievability was
  verified with control URLs. Vintage was tested and closed: one epoch *plus* a
  full algorithm revision perturbs the score ~40× less than this change itself
  (ρ 0.9990 vs 0.956).
- **Weight split: 0.09 `c_green` + 0.09 tree cover**, holding the total at
  `WEIGHTS["green"] = 0.18`. The peer review is right that this is a *choice*,
  not a derivation — uniformity alone is monotone and would prefer full
  replacement. It is chosen because `c_green` retains independent signal
  (ρ only +0.31 with tree cover) and because the review's own benchmark test
  shows this split moves the named scenic roads the right way.
- **Wiring: one blended column, reusing the existing `BEAUTY_TYPES` row.**
  See §Stage 2. This differs from what the findings doc proposes and supersedes it.

## Stage 0 — correct the two documents (no code)

The peer review found four defects and closed three open questions. Apply to
`docs/geodata-sources-findings.md`:

1. **§1 closing / §7.4**: replace "buys uniformity by construction" with
   **"takes the non-uniform share of composite weight from 37.7% to 29.8%"**.
   Name `c_urban` and `c_farm` as the remainder and this change as the first of
   at least three. This is the single most important correction — the current
   wording reads as though the problem is solved.
2. **§2**: the bolded claim that blind roads are *"slightly more wooded than the
   population the model does credit (0.66 vs 0.62)"* compares against a pool that
   includes water/coast/farm/urban credit. Like-for-like the sign reverses:
   blind **0.678** vs green-credited **0.720** (*less* wooded), against **0.584**
   for credited-but-not-green. The conclusion — under-mapped, not featureless —
   is unaffected; the sentence is wrong and must go.
3. **§1**: add the **water negative control**. Running the identical
   OSM÷WorldCover estimator on `c_water` — the layer the doc calls uniform on
   reasoning alone — gives a **1.52× spread against green's 3.21×**. This is the
   strongest single piece of evidence that the estimator does not manufacture
   spread, and it currently appears in neither document's argument. Lead §1 with it.
4. **§1**: add the **threshold table** — ≥10% tree → 3.21×, ≥25% → 3.47×,
   ≥50% → 4.11×, ≥75% → 5.11×, continuous mean → 3.93×. The doc picked the
   denominator that *minimises* its own headline number. The "that bar is too
   low" objection inverts.
5. **§6/§7.2**: move **variant E onto the calibration-breaking list** with B, C
   and F. E raises road-km pinned at 10.0 from 0.32% to **1.03%** and p99 to 10.0,
   because it adds weight without removing any (`WEIGHTS` sum 1.14 → 1.23).
6. **§7.4**: add and explicitly reject the **per-state `RAW_BASE`/`STRETCH`
   alternative** — the cheapest competing option, currently unrejected on the
   page. It removes the between-state level shift at zero pipeline cost, and §1
   alone does not rule it out. §1 *and* §2 together do: per-state calibration
   cannot touch the within-state distortion, which §2 measures at 27% of
   Massachusetts road-km scored blind on land that looks like the land OSM does
   credit. Join them.

Also record in §6 that the wiring decision has changed to the blended column
below, and why.

## Stage 1 — `pipeline/landcover.py` (new file)

Model on `elevation.py`'s shape — a standalone stage run before `score.py` —
but it is materially simpler: **no mosaic, no `maximum_filter`, no coverage
guard.** Peak RSS is one tile and does not grow with the region, which is the
opposite cost shape to `elevation.py`'s in-RAM float64 mosaic (already 5.6 GB for
New England and the known wall past the Northeast).

Tiles: `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{TILE}_Map.tif`
New England needs exactly eight: `N39W072 N39W075 N42W069 N42W072 N42W075
N45W069 N45W072 N45W075`. Class 10 is tree cover.

For each road chunk midpoint, compute the **fraction of class 10 in a
latitude-corrected ~100 m ground box**. Output a per-chunk tree fraction that
`score.py` can consume — either a raster like `relief.tif` or a parquet keyed the
same way `score.py` already joins; match whatever fits the existing stage
boundary most naturally.

**It must be continuous, not a flag.** A ≥10% tree flag is true on 88.7% of
Massachusetts road-km and would carry almost no ranking signal. The fraction has
real spread (MA p25 0.25 / p50 0.62 / p75 0.94).

## Stage 2 — wiring, via one blended column

`score.py` writes **`c_forest = 0.5 * c_green + 0.5 * tree_fraction`** and keeps
`WEIGHTS["green"] = 0.18` — renamed to match the column. `router.py`'s existing
row becomes:

```python
("forest", "forest/park", "c_forest", WEIGHTS["forest"]),
```

Arithmetically this is identical to weighting the two at 0.09 each, but it is
strictly better than a separate `c_treecover` column in `BASELINE`, which is what
the findings doc proposes. Measured differences:

| | separate column in `BASELINE` | **blended `c_forest`** |
|---|---|---|
| tunable weight mass | 0.89 → 0.80 | **unchanged at 0.89** |
| does `forest=0` still ignore forest? | **no** — half becomes permanently on | **yes** |
| iOS changes | none in code, but the slider silently loses 44% of its pull | **none, product included** |
| `WEIGHTS` sum | 1.14 | **1.14** |
| Maine road-km pinned at 1.0 | **0.255** (vs a `< 0.35` guard) | **0.068** |
| `SCENERY_BREAKDOWN` | follows automatically | follows automatically |

The last row is the one that matters most operationally: pinning now requires
*both* designation and full canopy, so the ceiling guard that was 4% from
tripping in Maine gains an order of magnitude of headroom.

**One thing to check, not a blocker.** `BREAKDOWN_MIN = 0.4` (`router.py`) means
a road with no OSM green and 0.7 tree cover scores `c_forest = 0.35` and will
*not* appear in the route summary's "forest/park" kilometres, where today a
designated-but-bare road does. Verify against
`test_breakdown_threshold_admits_every_partial_credit_band`. If it bites, come
back rather than silently retuning `BREAKDOWN_MIN` — that constant has a long
comment defending its value.

## Stage 3 — tests

`tests/test_calibration.py` carries **two** hardcoded component lists, and the
findings doc's cost table only found one:

- `COMPONENTS`, line 18 — feeds `test_not_pinned_at_the_ceiling`
- a second `key` dict at **lines 82–85** inside `test_score_matches_components`,
  mapping every `c_` column to its `WEIGHTS` name

In both, `c_green` is **replaced** by `c_forest`, not joined by it. Miss the
second and the test recomputes `raw` short and fails with a confusing message.

`tests/test_graph.py` derives `ALL_COMPONENTS` from `WEIGHTS` and needs nothing.
`tests/test_scoring.py:91` asserts on `sum(WEIGHTS.values())`, which this change
leaves at 1.14 exactly.

## Acceptance — reproduce these

Rebuild Massachusetts and check against the shipped `scored_chunks.parquet`:

| quantity | expected |
|---|---|
| length-weighted p50 | **4.49** (target in `score.py` is ~4.5; shipped is 4.03) |
| length-weighted p99 | 9.12 |
| road-km pinned at 0.0 / 10.0 | 2.98% / 0.24% |
| Spearman vs shipped score | **0.957** |
| road-km moving > 1 point | 18.1% |
| Maine `c_forest ≥ 0.999` share | **0.068** (guard is `< 0.35`) |
| full suite | 294 pass, 0 skipped |

Benchmark direction, from the peer review — every scenic road up, both
interstates down: Jacob's Ladder Trail 4.60 → 5.16, Mohawk Trail 5.12 → 5.31,
Route 6A 5.24 → 5.56, I-90 0.63 → 0.57, I-95 0.67 → 0.49. The
scenic−interstate gap widens 4.73 → 5.13. `TestBenchmarkRoads` and
`TestScoreScale` must pass with *more* margin than the shipped scoring, not less.

## Traps

**1. Do not touch `pipeline/elevation.py` or `pipeline/extract.py`.** A
latitude-banding fix to `elevation.py` is live and unmerged on
`claude/brave-tharp-91ffea`, and a byway-relation change to `extract.py` is
queued. `score.py`, `router.py`, `tests/test_calibration.py` and the new
`landcover.py` are verified clear of other branches — those four are yours.

**2. WorldCover tiles are named for their SOUTH-WEST corner.** The tile index is
`floor(lon/3)*3`, **not `ceil`**. With `ceil` every point resolves to the tile 3°
east, lands outside its bounds, gets clipped to an edge column, and returns
*plausible-looking garbage* rather than raising. This already produced one wrong
results table in this project. **Keep a hard bounds assertion**, allowing one
pixel of slack for points exactly on a tile edge (a real case: Maine has points
at exactly lat 45.0).

**3. Use a latitude-corrected ground box, not a fixed 9×9 pixels.** WorldCover
pixels are 1/12000°, so at these latitudes a 9×9 window is about **83 m × 62 m**,
not 90 m square — a degree of longitude is short up here. The anisotropy does not
change the uniformity finding, but it *does* change the ceiling guard: fixed 9×9
puts Maine at 0.335 against a `< 0.35` assertion.

**4. A `c_` column in `WEIGHTS` but in neither `BASELINE` nor `BEAUTY_TYPES` is
silently dropped from the live re-blend** while still counting in the precomputed
`score`, breaking the invariant that all-1.0 weights reproduce the stored column.
Stage 2's design avoids this by reusing the existing row — but if you deviate
from it, this is the trap. `test_routing.py:1013` is the tripwire.

**5. `graph_edges.parquet` and `scored_chunks.parquet` give different
no-polygon rates and both are right.** Edges are 22.6%, chunks 27.0%, because
`graph.py` averages components along an edge. Work chunk-level. Comparing across
the two will make you "find" a bug that is a unit mismatch.

**6. Do not re-survey the closed questions.** 3DEP (rejected, Spearman 0.97 vs
Terrarium), NLCD/GAP/Copernicus (coarser), PAD-US and Overture (re-import the same
designation bias), NOAA C-CAP (has no NH or VT), Google Dynamic World (excluded by
the README's "no Google/Apple data" product claim), MassGIS (out of bounds),
traffic data, and the OSM byway relations (Phase 0b, someone else's).

## Done looks like

1. `docs/geodata-sources-findings.md` corrected per Stage 0, all six items.
2. `pipeline/landcover.py` exists, is runnable standalone, and documents its
   memory profile the way `elevation.py` documents its own.
3. `c_forest` flows end to end — `score.py` → `graph.py` (auto-detects) →
   `router.py` live re-blend — with the acceptance numbers above reproduced.
4. Full suite green: 294 pass, 0 skipped.
5. A short note in the findings doc recording what the rebuild actually produced
   against the expected table, including any number that came out different.
6. **Or**: a clear statement of which acceptance number could not be reproduced
   and why. If the rebuild disagrees with the table, that is a finding — say so
   rather than tuning until it matches.

## Environment

- Branch from **`claude/stoic-goldstine-d6b9d5`**, which carries all four
  documents. Never commit to `main`. Commit the brief with your change if it is
  still untracked.
- `data/`, `.venv/` and `traces/` live **only in the main checkout**, never in a
  worktree. Run the pipeline and the tests from the main checkout.
- The project path contains spaces, so venv console scripts are broken. Use
  `.venv/bin/python -m pip`, never `.venv/bin/pip`.
- Tests: `SCENIC_DATA=data/processed .venv/bin/python -m pytest tests/` — 294
  pass, 0 skipped, ~2 min.
- `data/raw/` already holds the merged 782 MB New England PBF and all six state
  extracts. Nothing needs downloading except WorldCover tiles.
- **Disk is at ~99%, ~6.5 GiB free.** The eight NE tiles are 343 MB. They are
  COGs with range requests, so windowed `/vsicurl` reads need no download at all
  if you would rather not spend the space. Clean up anything you do download.
- `docs/new-england-rollout.md` is referenced throughout but lives on the
  unmerged branch `claude/brave-tharp-91ffea` — do not go hunting for it on main.
