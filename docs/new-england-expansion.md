# Staging the other five New England extracts

**Status: staged. All six extracts downloaded and merged, format identity
proven per state, `BBOX` updated.** `data/processed/` was not touched. The
results, including two predictions in this file that turned out wrong, are in
"Results" at the bottom. Everything above that section is the original scoping
and still reads as it did before the work; the numbers it predicted are checked
against reality there.

## The goal

Massachusetts is the only region the project has ever ingested
(`data/raw/massachusetts-latest.osm.pbf`, 307 MB, Geofabrik). Add Connecticut,
Rhode Island, Vermont, New Hampshire and Maine, prove they are the same format
and will flow through the existing pipeline, and document exactly what breaks
downstream — without breaking the working Massachusetts build.

## What is already known, so do not re-derive it

Measured on this machine (M2, 24 GB, macOS) on 2026-08-13 and re-verified
2026-08-25. **Quote these; do not re-measure.**

| constant | value |
|---|---|
| MA baseline graph | 401,695 edges, 310,807 nodes, 66,394 km road |
| Build RAM | **6.70 GB per GB of PBF** (osmium `locations=True, idx="flex_mem"` + the Python ways list) |
| Router latency | scales as **E^1.20** (log-log R² = 0.9992); MA is 196 ms/request |
| New England vs MA | **3.20x** by road miles (FHWA HM-20 2024) |
| PBF bytes per road-mile | MA 8,372 vs 2,824 nationally — **MA is a density outlier** |

That last row is the one that trips people: **road miles scale ~3x faster than
PBF size**, so sizing the graph off PBF ratios under-provisions by about 3x.
Size download and import RAM by PBF bytes; size anything graph-shaped by road
miles.

Expected after a merged New England build: ~3.2x MA road miles, so router
latency ≈ 196 ms × 3.2^1.20 ≈ **800 ms per request**. Degraded but working. The
known fix is `dijkstra(..., limit=cost)` for target early-exit — out of scope
here, and noted only so nobody treats the slowdown as a surprise.

## The elevation question, settled

`pipeline/elevation.py:31` hardcodes Massachusetts:

```python
BBOX = (-73.55, 41.18, -69.85, 42.92)
```

`main()` then allocates **one in-RAM float64 mosaic of the whole bbox**
(`elevation.py:116-128`) and later runs `maximum_filter`/`minimum_filter` over
it, peaking at roughly 5x the array. Whether that fits was the open question.
It does:

| region | tiles @ z11 | float64 array | filter peak |
|---|---|---|---|
| Massachusetts | 22 x 14 = 308 | 0.16 GB | ~0.8 GB |
| New England | 40 x 53 = 2,120 | 1.11 GB | **~5.6 GB** |

The MA row reproduces the 308 tiles actually sitting in `data/raw/terrain`, so
the arithmetic is calibrated against reality, not assumed. **5.6 GB peak on a
24 GB machine is tight but fine — elevation is not a blocker for New England.**
It becomes one further out (NE+NY ~10.9 GB, Census Northeast ~14.1 GB, East
Coast ~51.5 GB), where the answer is to tile the filter with an overlap halo,
since relief is a local 750 m operator. Not needed now.

`BBOX` appears in exactly one file and is read in exactly one place — there is
no second copy to keep in step.

## The approach

**Merge to one PBF, then run the existing pipeline unchanged.** `extract.py`
takes a single path (`extract.py:230`, `main(pbf_path, out_dir)`), so six
regions means either a code change or one merged file. Merging with
`osmium merge` needs no code change at all, and it is the only option that gives
cross-border routing — which for a New England scenic app is the entire point,
since every interesting trip from Needham crosses a state line.

1. Download the five missing extracts from Geofabrik
   (`https://download.geofabrik.de/north-america/us/<state>-latest.osm.pbf`) into
   `data/raw/`. Keep them; do not delete after merging.
2. `osmium merge` all six into `data/raw/new-england-latest.osm.pbf`. Verify
   with `osmium fileinfo -e` that the result is one valid PBF. **Do not use its
   bbox as a coverage check — that does not work, see Results.** The checks that
   do work are `Objects ordered: yes`, `Multiple versions of same object: no`,
   and the per-state header bboxes.
3. Prove format identity: run `extract.py`'s handler over each state
   individually and confirm the same tag families populate the same buckets
   (`extract.py:150-170` — `GREEN_NATURAL` / `GREEN_LANDUSE` / `GREEN_LEISURE` /
   `GREEN_BOUNDARY`, water, farm, urban). A state that yields zero green areas
   or zero urban areas is a red flag worth reporting, not silently accepting.
4. Update `BBOX` in `elevation.py:31` to the New England envelope
   (`(-73.76, 40.93, -66.87, 47.47)` is the measured figure behind the table
   above) and confirm the tile grid prints `40x53 = 2120 tiles`. Do not download
   them.
5. Write down, in this file, what the numbers actually came out as.

## Known downstream defects — document, do not fix

These are real and they are all out of scope. Record them so the next session
starts from them:

- **`RELIEF_FULL = 100.0`** (`score.py:78`) already saturates 7.2% of
  Massachusetts. The White and Green Mountains would pin at 1.0 across the
  board, making the hills component — and the app's Hills slider — useless
  exactly where the terrain is most interesting. This is the most important
  downstream problem and it needs a decision, not a nudge.
- **`BYWAY_NAMES`** (`score.py:91`) is three Massachusetts byways. Vermont,
  New Hampshire and Maine all designate their own; none are listed.
- **iOS is Massachusetts-only** in four places: `Region.swift:9`
  (`.massachusetts`), `ContentView.swift:16` (initial camera),
  `SearchCompleter.swift:23` (search bias), `RouteModel.swift:37`
  (`searchRegion`). Search for a Vermont town today and the completer biases
  against it.
- **`CRS_METERS = 26986`** (`common.py:58`, NAD83 / MA Mainland) **is fine and
  should be left alone.** It is a conformal conic whose scale error depends on
  latitude only: ≤0.31% anywhere in New England. It needs replacing to go south
  (1.0% at Atlanta, 3.9% at Miami), not to go north. Changing it now is churn.

## Traps

- **Do not overwrite `data/processed/`.** It holds the working Massachusetts
  build. Every analysis tool in `tools/` reads it, today's drive-mark analysis
  was run against it, and re-deriving it costs hours. Build anything new into a
  separate directory.
- **The disk is at 98% (12 GB free).** Budget roughly 500 MB of new PBFs, a
  further ~800 MB for the merged file (keep both), ~190 MB of terrain tiles at
  z11, and — if anyone later rebuilds — on the order of 1.2 GB of processed
  parquet. Check `df -h` before starting and report the number.
- **Do not size the graph off PBF ratios.** See the density-outlier row above.
  Off PBF bytes New England looks like ~2.6x MA; by road miles it is 3.20x, and
  the graph follows road miles.
- **Do not "fix" `CRS_METERS`.** It looks Massachusetts-specific and is not a
  problem at these latitudes.
- **`data/`, `traces/` and `.venv/` live only in the main checkout**, not in
  worktrees — `.gitignore` carries both `data/` and `/data` for exactly this
  reason. Run anything that touches data from the main checkout.

## Out of scope, deliberately

Running `score.py` / `graph.py` over the merged region, downloading the 2,120
terrain tiles, rebuilding `data/processed`, and any router or iOS change. Those
are a bigger job with a real disk cost and a destructive failure mode, and they
should start from this file's measured results rather than run blind.

## Done looks like

1. Five extracts downloaded, sizes recorded, all six merged into one verified
   PBF.
2. A per-state table of what `extract.py`'s handler found — road ways, and each
   area bucket — with any state that looks anomalous called out.
3. `BBOX` updated and the tile grid confirmed as 40x53, with no tiles fetched.
4. This file updated with the actual numbers, replacing the predicted ones where
   they differ — including a plain statement if any prediction was wrong.
5. `data/processed/` provably untouched (`git status` plus an mtime check).


---

## Results, 2026-08-25

Everything above this line is the original scoping, left as written. This
section is what actually happened. Two of its predictions were wrong and both
are stated as such below.

Disk before starting: **12 GiB free of 460 GiB, 98% capacity** (`df -h`), as the
brief said. After all downloads and the merge: **11 GiB free**. Total cost of
this work on disk was 1.16 GB.

`osmium` was not installed on this machine; `brew install osmium-tool` put
1.19.1 in place. `pyosmium` was already in `.venv`.

### 1. Downloads

Five extracts from Geofabrik, plus one more explained in section 3. Sizes and
snapshot times as reported by `osmium fileinfo -e`:

| extract | bytes | snapshot | header bbox |
|---|---|---|---|
| massachusetts (pre-existing, 2026-06-12) | 307,403,589 | 2026-06-11 | -73.51, 40.83, -68.72, 42.89 |
| connecticut | 216,392,430 | 2026-08-25 | -73.73, 40.96, -71.79, 42.05 |
| maine | 90,637,397 | 2026-08-25 | -71.45, 42.85, -65.86, 48.16 |
| new-hampshire | 71,122,668 | 2026-08-25 | -72.56, 42.70, -70.49, 45.45 |
| rhode-island | 52,039,030 | 2026-08-25 | -71.91, 41.00, -71.06, 42.02 |
| vermont | 45,823,743 | 2026-08-25 | -73.44, 42.72, -71.46, 45.18 |
| massachusetts (re-pulled, see section 3) | 309,648,509 | 2026-08-25 | — |

The five new ones total **476 MB**, against the brief's budget of "roughly
500 MB". Nothing deleted: all seven `.osm.pbf` files are still in `data/raw/`.

Connecticut being 216 MB — larger than Maine, New Hampshire and Vermont put
together — is not a bad download. Its header bbox is correct and tight to the
state. It is the same density effect the brief warns about, showing up a second
time: southern New England is mapped far more finely than northern.

### 2. The merge

```
osmium merge massachusetts-20260825 connecticut rhode-island vermont \
             new-hampshire maine -o new-england-latest.osm.pbf
```

35 s wall (342% CPU), streaming, negligible RAM. Result:

| | value |
|---|---|
| `new-england-latest.osm.pbf` | **782,015,620 bytes (782 MB)** |
| nodes / ways / relations | 95,231,100 / 9,345,651 / 86,844 |
| objects ordered by type and id | yes |
| multiple versions of same object | **no** |

782 MB against a predicted ~800 MB. Merge dedup is real but small: the six
inputs sum to 785 MB of separate files and 9,382,410 ways, so 36,759 ways were
dropped as exact duplicates in the border overlaps.

**Prediction wrong: `osmium fileinfo -e`'s bbox cannot be used to check that
the merge covers all six states.** The brief's step 2 says to verify coverage
that way. The merged file's data bbox comes out as

```
(-76.0144098, 20.1063872, -20.000017, 47.7801404)
```

— 56 degrees of longitude, reaching 20°N and 20°W, i.e. the mid-Atlantic. That
is not a merge fault; it is what the Geofabrik extracts contain. A handful of
stray objects (long boundary and ferry relations, plus a few misplaced nodes)
drag the extent out, and one outlier is enough to make the bbox meaningless as a
coverage test. It says nothing about whether Vermont made it in.

Coverage was checked two other ways instead, and both pass:

- the six per-state **header** bboxes in the table above, which are tight and
  correct, and whose union is the region;
- a direct count of how many nodes in the merged file fall outside the New
  England envelope: **28,777 of 95,231,100, or 0.03%**. Spot-checking them, they
  are ordinary border spillover — just over the New York line near -73.99/41.04,
  just into New Brunswick near -66.1/43.8 — not junk and not a gap.

That 0.03% is also the answer to whether the elevation `BBOX` clips anything
real: it does not.

### 3. Merging extracts of different dates silently duplicates border objects

Not predicted anywhere in this file, and worth writing down because the symptom
is quiet.

The first merge used the existing 2026-06-11 Massachusetts extract with five
2026-08-25 ones. It succeeded, and `osmium fileinfo -e` reported:

```
Multiple versions of same object: yes
  WARNING! This is different from the setting in the header.
```

`osmium merge` drops duplicates only when type, id **and version** all match.
An object in the MA/CT or MA/NH border overlap that was edited between June 11
and August 25 appears in both inputs at different versions, so both survive. A
streaming same-id pass counted what got through: **564 duplicate nodes, 165
duplicate ways — 99 of them drivable — and 191 duplicate relations.**

Tiny in absolute terms, and every one of them sits on a state border, which is
precisely where cross-border routing has to work. Duplicated drivable ways would
become parallel duplicate edges in `graph.py`.

Fixed by re-pulling Massachusetts the same day
(`massachusetts-20260825.osm.pbf`, 309,648,509 bytes) and merging from that, so
all six inputs are one snapshot. The merged file now reports
`Multiple versions of same object: no`. **The June extract was left in place
untouched** — it is the input the working `data/processed/` build was made from,
and overwriting it would have thrown away that build's reproducibility.

Standing rule for next time: **merge only extracts pulled on the same day**, and
treat `Multiple versions of same object` in `fileinfo -e` as the check that
proves it.

### 4. Format identity, per state

A counting pass over each state's PBF, importing the bucket predicates
(`GREEN_NATURAL` / `GREEN_LANDUSE` / `GREEN_LEISURE` / `GREEN_BOUNDARY`, water,
farm, urban, place, viewpoint) directly from `extract.py` rather than restating
them, so a disagreement here would be a disagreement with the real pipeline.
Geometry is not built, which is why it runs in minutes.

The harness validates against a known answer: Massachusetts came out at
**227,251 drivable ways and 433,969 service ways**, matching to the digit the
two counts asserted in `extract.py`'s own comments.

| state | roads | access | coast | wtr line | wtr area | green | farm | urban | views | places | secs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| massachusetts | 227,251 | 433,969 | 1,553 | 2,677 | 21,051 | 35,123 | 8,435 | 2,036 | 757 | 1,905 | 149 |
| connecticut | 106,717 | 314,729 | 617 | 1,653 | 23,339 | 19,040 | 5,364 | 2,072 | 680 | 945 | 91 |
| maine | 86,863 | 125,837 | 4,342 | 5,041 | 26,913 | 16,388 | 4,030 | 322 | 407 | 748 | 41 |
| new-hampshire | 66,556 | 181,632 | 16 | 2,177 | 15,782 | 13,983 | 3,803 | 1,084 | 408 | 867 | 31 |
| vermont | 44,293 | 72,068 | **0** | 1,634 | 9,442 | 12,926 | 5,255 | 498 | 322 | 871 | 22 |
| rhode-island | 36,180 | 63,774 | 2,093 | 308 | 3,132 | 19,265 | 5,045 | 1,876 | 38 | **110** | 37 |

**Every bucket is populated for every state, with one zero, and that zero is
correct**: Vermont has no coastline because Vermont is landlocked. New
Hampshire's 16 coastline ways are its 18-mile shore. No state yields zero green
areas or zero urban areas, so the format-identity check passes and the merged
file can go through `extract.py` unchanged.

Two anomalies worth carrying forward:

- **The green buckets split north/south by tagging convention.** Breaking
  `green_areas` down by which tag matched:

  | state | natural=wood | landuse=forest | leisure | boundary |
  |---|---|---|---|---|
  | rhode-island | 18,295 | **33** | 631 | 458 |
  | connecticut | 10,992 | **49** | 8,034 | 129 |
  | massachusetts | 17,987 | 1,428 | 15,718 | 8,684 |
  | vermont | 11,586 | 492 | 787 | 347 |
  | new-hampshire | 8,664 | **2,856** | 3,078 | 381 |
  | maine | 10,037 | **2,901** | 3,573 | 301 |

  Southern New England maps woodland as `natural=wood` and barely uses
  `landuse=forest` at all; northern New England uses both. This is harmless
  *because* `extract.py` ORs all four tag families — but it means no single one
  of them is a usable green signal across the region, and anything downstream
  that reaches for one specific tag will read as a state boundary. Also note
  Massachusetts' `boundary` count (8,684) is an order of magnitude above every
  other state's; that is MA-specific conservation-land tagging, not real
  difference in protected land.

- **Rhode Island under-maps `place=*` centers.** 110 place points against
  Vermont's 871, on 36,180 roads against Vermont's 44,293 — about six times
  fewer per road. Its 38 viewpoints against Vermont's 322 tell the same story.
  Rhode Island is not six times less towned than Vermont. The "townscape"
  signal that `place_points` feeds will be near-silent in Rhode Island, and the
  Hills/scenery mix there will lean on whatever else is available. Worth a look
  before anyone trusts a Rhode Island score.

### 5. Elevation `BBOX`

`pipeline/elevation.py:31` updated from the MA box to the measured New England
envelope:

```python
BBOX = (-73.76, 40.93, -66.87, 47.47)
```

Running the file's own `lonlat_to_tile` grid arithmetic over it prints exactly
what the brief predicted, to the digit:

```
zoom 11: 40x53 = 2120 tiles; mosaic 10240x13568 px, float64 1.11 GB, ~5x peak 5.56 GB
```

**No tiles were fetched.** `data/raw/terrain` still holds the same 308 MA tiles.
The prediction in "The elevation question, settled" is confirmed unchanged.

### 6. Scaling ratios: the density warning holds, with a third under-predictor

The brief warns not to size the graph off PBF ratios, because road miles scale
about 3x faster than PBF size. Both halves of that check out, and way counts
turn out to be a third thing that under-predicts:

| basis | New England / Massachusetts |
|---|---|
| PBF bytes (785 MB / 307 MB) | **2.55x** |
| drivable way count (567,860 / 227,251) | **2.50x** |
| road miles (FHWA HM-20 2024) | **3.20x** |

So way count is no better than bytes — it under-provisions by the same ~25%.
Northern rural ways are simply longer, and neither count sees length. The rule
stands and now has one more clause: **size the graph off road miles only.**
Bytes and way counts are both about 2.5x; the graph will be 3.2x.

The predicted ~800 ms/request router latency after a full build is untouched by
any of this — nothing here re-measured it, and nothing here contradicts it.

### 7. What was deliberately not done

Unchanged from "Out of scope": no `score.py` or `graph.py` run over the merged
region, no terrain tiles fetched, no `data/processed/` rebuild, no router or iOS
change. `CRS_METERS = 26986` was left exactly as it is, for the reason this file
already gives.

`data/processed/` verified untouched: all 18 files carry their original mtimes
(oldest 2026-06-20, newest 2026-08-24 01:00), 384 MB, and `git status` is clean
apart from this document and the one-line `BBOX` change.

### 8. Where the next session should start

The staging is done, so the next decision is the one this file already names as
the most important: **`RELIEF_FULL = 100.0` in `score.py:78`**. It saturates
7.2% of Massachusetts today. The merged region adds the White and Green
Mountains, which will pin at 1.0 across the board and flatten the Hills slider
exactly where the terrain is best. Nothing downstream of the merge is worth
running until that has an answer.

The per-state counting pass is kept as `tools/bucket_census.py` so the next
region can be checked the same way without rewriting it.
