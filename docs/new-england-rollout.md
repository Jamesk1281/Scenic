# Rolling New England into the app

**Status: planned, nothing built.** The data is staged (see
[`new-england-expansion.md`](new-england-expansion.md)); this is the plan for
turning it into a region the app actually serves. Every number here was measured
on this machine on 2026-08-26 against the live Massachusetts build, which was
not modified. Where a measurement contradicts something previously assumed —
including two things this session assumed — it says so.

## The shape of the work

Merging the PBF was the easy half. The hard half is that **five of the app's
scoring constants were fitted on Massachusetts**, and one of them changes
meaning the moment the bounding box widens — including for Massachusetts itself.
So the plan is not "build it and look". It is: fix the two things that would
bake a defect into an expensive artifact, prove the fixes are neutral on
Massachusetts *before* spending the build, then build, then re-fit what needs
re-fitting against the only ground truth there is — 76 drive marks, all of them
in Massachusetts.

Nothing in the pipeline's *logic* is Massachusetts-specific. Every MA reference
in `graph.py`, `router.py` and `score.py` is a comment recording a measurement.
The two exceptions are `CRS_METERS` (fine at these latitudes — leave it) and
`graph.py:49`'s 40 km/h statutory default, which is close enough for the other
five states to not matter yet.

## What is already compatible, so nobody spends time on it

- **`SCENIC_DATA` is a complete seam.** `server/app.py:47` reads the graph
  directory from it, `tests/conftest.py:22` reads the same variable, and every
  pipeline stage and every tool in `tools/` takes the directory as an argument.
  A second region can be built, tested and served **without one line of path
  plumbing**, and the Massachusetts build can stay live the whole time.
- **Ingest is proven.** All ten layers populate for all six states; the only
  zero is Vermont's coastline, which is correct.
- **The green tagging split between northern and southern New England is
  harmless.** `extract.py` ORs the four tag families into one layer and
  `score.py:250` builds one tree from it. No code path reads a single green tag.
- **The wire protocol does not change.** An old build of the iOS app talks to a
  New England server correctly. That decouples the app release from the data
  switch entirely — the iOS work in Phase 7 is UX, not a dependency.
- **`SNAP_MAX_M = 5000`** already does the right thing for a wider region: a
  request from outside the covered area is still refused.

## Phase 0 — Two code changes that must land before anything is built

Both change what `extract.py` and `elevation.py` write, so doing them after the
build means doing the build twice.

### 0a. The relief window silently changes when the bbox widens

`elevation.py:170` sizes the relief window in pixels from **one latitude for the
whole mosaic** — the bbox midpoint:

```python
px_ground = px_m * math.cos(math.radians((s + n) / 2))
win = max(3, int(round(RELIEF_WINDOW_M / px_ground)) | 1)   # odd
```

Massachusetts spans 1.7 degrees, so one latitude is nearly right: `win` = 13 px,
meaning 748 m of ground at the Connecticut line and 728 m at the New Hampshire
line. New England spans 6.5 degrees, and the rounding to an odd integer tips
over: **`win` becomes 15 px.** Measured consequences, running both window sizes
over the *existing* Massachusetts `elevation.tif` — same terrain, same data,
only the window changed:

| | win=13 (today) | win=15 (after the bbox change) |
|---|---|---|
| MA relief median | 24.8 m | 28.0 m (**+12.9%**) |
| MA relief p99 | 185.3 m | 203.5 m |
| MA area at or above `RELIEF_FULL` (100 m) | 7.16% | **9.15%** |

And the ground width of that 15 px window still varies across the region, more
than before: **866 m at the Connecticut border, 775 m in northern Maine.**

So the one-line `BBOX` edit already committed is **not neutral**. It rescales
relief for Massachusetts by 13% before a single new state is considered, which
is exactly the region-dependence the `RELIEF_WINDOW_M` comment predicts. Any
attempt to re-fit `RELIEF_FULL` without fixing this is fitting to an artifact.

**Recommendation: band the filter by latitude.** Split the mosaic into latitude
bands, use the correct pixel window per band, and overlap the bands by a halo of
`win // 2 + 1` rows so the `maximum_filter`/`minimum_filter` results are
identical to a whole-array run. Then 750 m means 750 m everywhere.

Three reasons this beats pinning `win = 13`:

1. It removes the region-dependence permanently rather than re-centring it — a
   pinned 13 px is still 751 m in Connecticut and 672 m in northern Maine.
2. Banding is the same machinery the expansion note already says is needed to go
   past New England, where the whole-array filter hits a RAM wall (NE+NY
   ~10.9 GB, Census Northeast ~14.1 GB). Building it now buys that for free.
3. It cuts the current peak. The whole-array path allocates a 1.11 GB float64
   mosaic and peaks near 5.6 GB through the filters; banded, the peak is set by
   the band height, not the region.

Verification: with banding, re-running elevation over the **Massachusetts** bbox
must reproduce today's `relief.tif` to within float32 rounding, because 13 px is
the correct window for MA's latitudes. That is a pass/fail test, and it runs off
the 308 cached tiles in seconds.

### 0b. Scenic byways are in OSM as route relations, and the app is hand-coding three of them

`score.py:91` carries three Massachusetts byways matched by name substring, with
a comment promising the names are "distinctive enough to avoid false positives
statewide". Two measurements:

**The promise fails region-wide.** Drivable ways named "Mohawk Trail" outside
Massachusetts: **14 in Connecticut, 4 in Rhode Island, 2 in New Hampshire.**
Suburban streets, each collecting the full `scenic_tag` weight.

**And it is unnecessary, because OSM already has the whole regional network.**
Scanning the merged PBF for `type=route` relations mentioning scenic or byway:

- **54 relations across 13 networks, 53 distinct named routes**
- `US:MA:Scenic` 14 · `US:VT:byway` 9 · plus Maine, Rhode Island, Connecticut,
  New Hampshire and the multi-state Connecticut River Byway
- Kancamagus Scenic Byway, Acadia All-American Road, Green Mountain Byway,
  Molly Stark Byway, Scenic Route 100, Rangeley Lakes, Old Canada Road,
  Schoodic, Route 169, Mount Greylock, Route 112, Route 116, Jacob's Ladder…
- **5,550 drivable member ways, 4,026 km — 1.71% of the network**

Meanwhile the `scenic=yes` way tag that `extract.py` reads is effectively dead
data: **28 ways across all six states** (MA 0, VT 0, RI 0, NH 3, ME 3, CT 22).

So `scenic_tag` (weight 0.07) currently fires on ~146 name-matched MA ways and
almost nothing else, when 4,026 km of designated byway is sitting in the file.
This is the highest-leverage scoring change available and it is not a
New England problem — it is a Massachusetts problem that expansion exposed.

**Design:**

- Add a `relation()` method to `extract.py`'s `Handler` collecting member way ids
  for relations whose `network` is in an allowlist. Relations sort last in a PBF,
  so the set is complete before `main()` builds the frame; the set is idempotent
  across pyosmium's two passes.
- `roads.scenic` becomes `scenic=yes OR way_id in byway_ways`. `roads.parquet`
  already carries `way_id`, so the join is free.
- **The allowlist matters:** of the 13 networks matched, `nwn`, `lwn` and `lcn`
  are national/local *walking* and *cycling* networks that matched on the word
  "scenic". They must be excluded or footpath routes will flag roads. The 4,026 km
  is therefore an upper bound; vet the 12 no-network relations by name.
- Then **delete `BYWAY_NAMES`**, which removes the 20 false positives outright.
  One thing to confirm first: that the Mohawk Trail and Jacob's Ladder Trail are
  both among the 14 `US:MA:Scenic` relations. Jacob's Ladder is confirmed
  present. If the Mohawk Trail is not, keep a name fallback for it *with a
  bounding-box guard*.

## Phase 1 — The Massachusetts dry run. The gate before spending the build.

Do not point the pipeline at New England yet. Run it at **Massachusetts** with
the Phase 0 changes in, into a scratch directory, and diff against the live
build. One variable changed — the code — so anything that moves is attributable.

```bash
.venv/bin/python pipeline/extract.py   data/raw/massachusetts-latest.osm.pbf data/processed-ma-dryrun
.venv/bin/python pipeline/elevation.py data/processed-ma-dryrun 11
.venv/bin/python pipeline/score.py     data/processed-ma-dryrun
```

Use the **June** Massachusetts extract, not the same-day one, so the OSM
snapshot is held constant too. Terrain comes from the 308 cached tiles.

Gates, all of which can fail:

1. `relief.tif` matches the live one to float32 rounding. Anything else means
   the banding is not equivalent to the whole-array filter.
2. Every layer row count matches the live build exactly, except `roads.scenic`,
   which should rise from ~146 flagged ways to a few thousand.
3. `scored_chunks` differs **only** in `c_scenic_tag`, `raw` and `score`, and
   only on byway chunks. Any change to `c_relief` means gate 1 lied.
4. `tools/analyze_trace.py data/processed-ma-dryrun traces/*.ndjson`: the
   separation on the 76 marks must not fall below the live build's 0.71 against
   a 0.63 null ceiling. Byways are a precision signal, so it should rise.
5. `SCENIC_DATA=data/processed-ma-dryrun .venv/bin/pytest` passes.

This phase also produces the one number I could not measure: **how long
`extract.py` actually takes**, which is what makes the New England run
predictable rather than a gamble.

## Phase 2 — Build New England

```bash
.venv/bin/python pipeline/extract.py   data/raw/new-england-latest.osm.pbf data/processed-ne
.venv/bin/python pipeline/elevation.py data/processed-ne 11
.venv/bin/python pipeline/score.py     data/processed-ne
.venv/bin/python pipeline/graph.py     data/raw/new-england-latest.osm.pbf data/processed-ne
```

Note `elevation.py` derives its tile cache as `<out>/../raw/terrain`, so
`data/processed-ne` shares the existing cache: the 308 Massachusetts tiles are
reused and only **1,812 new tiles (~159 MB)** are fetched. The cache will then
hold 2,120 tiles and is no longer "the MA tiles" — that is correct behaviour,
not damage, but it is the one thing this work touches inside `data/raw`.

Resource budget, from measured per-unit costs:

| stage | RAM | disk added | basis |
|---|---|---|---|
| `extract.py` | ~5.2 GB | ~490 MB | 6.70 GB per GB of PBF × 0.782 GB |
| `elevation.py` (banded) | band-bounded, well under the 5.6 GB whole-array peak | 159 MB tiles + ~406 MB rasters | 2,120 tiles at 87.7 KB measured; mosaic 10240×13568 |
| `score.py` | moderate | ~250 MB | 313,791 MA chunks × 3.53 ≈ 1.11 M |
| `graph.py` | moderate | ~271 MB | 400,983 MA edges × 3.53 ≈ 1.42 M |
| **total** | peak ~5.2 GB | **~1.5 GB** | 11 GiB free today |

Disk is the constraint to watch, not RAM: the machine is at 98%. The two
`.tif`s (~406 MB) are only needed by `score.py` and can be deleted after.

## Phase 3 — The Massachusetts subset diff

The New England build contains every Massachusetts road. Compare those chunks to
the Phase 1 dry run. Constants are still frozen, so **the only legitimate
differences are geographic**:

- roads within the feature-distance thresholds of a state line now see features
  across it — a road on the CT border that previously saw no green area because
  the extract was clipped there;
- `c_relief` shifts wherever the banded window lands differently.

Anything else — a score change in central Massachusetts, a changed row count, a
component moving where no border is near — is a region-dependence bug, and this
is the phase that catches it while it is still cheap. Quantify the border band:
if more than a few percent of MA chunks change, understand why before Phase 4.

## Phase 4 — Re-fit the scoring constants

Only now is there a distribution to fit against. Three items, in order:

**`RELIEF_FULL = 100.0`.** Fit it on **road-sampled relief, not area.** The
expansion note's headline — "already saturates 7.2% of Massachusetts" — is an
*area* figure (measured: 7.16%). Road-weighted, `c_relief` pins on **1.64% of MA
chunks**, four times lower, because roads follow valleys and notches rather than
summits. Fitting to the area number would over-correct. The method: take the
road-km-weighted relief distribution over the whole region and set `RELIEF_FULL`
so the saturated share is comparable to what Massachusetts had, then check what
the White Mountains actually do rather than assuming they pin.

**`BYWAY_NAMES`** — deleted in Phase 0; confirm the byway coverage looks sane
per state here.

**`RAW_BASE = 0.143` / `STRETCH = 1.20`.** These were fitted so MA lands p50
≈ 4.5 and p99 ≈ 9.5. Rural Maine and Vermont systematically forfeit `c_urban`
(0.14) and `c_coast` (0.13) while gaining relief, curves and green, and the net
is not predictable from component counts. Re-check the percentiles region-wide.
**This is the decision with the widest blast radius:** re-fitting them changes
every Massachusetts score, and Massachusetts is the only place the model has
ever been validated. See "Decisions needed".

The gate for all of it is the same and it is not negotiable:
`tools/analyze_trace.py` on the 76 marks, separation at or above 0.71 against
the 0.63 null ceiling. A constant that improves New England and regresses that
number is not an improvement. `tests/test_calibration.py` is the second rail —
it exists precisely because "a scoring constant is a single number that silently
reshapes 66,000 km of road", and now it is 239,000 km.

**Curvature needs nothing.** Measured with `score.py`'s own function on 400 m
chunks: `CURVE_FULL` pins 19.0% of MA road-km and only 22.6% of New Hampshire's,
while the median doubles (MA 31 → VT 65 deg/km). It gains discrimination in the
north rather than losing it. I expected curvature to saturate like relief and it
does not — what pins it is junction corners, which exist everywhere.

## Phase 5 — Router latency: measured, and smaller than hoped in both directions

Baseline, measured on the live MA graph (310,162 nodes / 313,950 routing slots /
400,983 edges):

| | measured |
|---|---|
| `Router` load | 14.8 s, peak RSS **1.82 GB** |
| one `route()` call | 133–150 ms |
| **a full API request** (fastest + scenic = two Dijkstras) | **272–290 ms** |
| of which the bare `dijkstra` call | 109–123 ms, ~85% of each `route()` |
| score re-blend + CSR build | 10–19 ms |

Note the request is ~280 ms, not the 196 ms the expansion note quotes as
"MA is 196 ms/request" — that figure is close to a *single* `route()` call, and
`server/app.py:150` makes two. Scaling the measured request by the E^1.20 law
over a 3.53x edge count gives **~1.27 s per request**, not ~800 ms.

**Two candidate fixes were prototyped and both are small.** Reporting them so
nobody spends a week on either:

1. **`dijkstra(..., limit=cost)`**, which the expansion note calls "the known
   fix", is not a target early-exit — scipy prunes by distance and has no target
   argument. Handed a *perfect oracle* bound it gives 1.25–2.6x. Handed a
   realistic bound (the fastest route's cost re-evaluated under the scenic
   weights, 26–34% slack) it gives **1.21x, 0.86x and 1.61x** on three test
   pairs — one case is a regression, because rebuilding the cost vector over
   734,149 pairs costs about what the pruning saves.
2. **A dependency-free A***, as Dijkstra on reduced costs
   `w' = w - h(u) + h(v)` with `h` = straight-line distance over a 150 km/h
   ceiling, is **exactly correct** — it reproduced the true path cost to four
   decimals on all three pairs with no negative reduced cost — and is the same
   1.2x, for the same reason.
3. **Contracting degree-2 chains** at build time would need no new dependency,
   but the graph is already essentially junction-contracted: mean undirected
   degree 2.54, only 20.7% of slots at degree 2, 17.6% dead ends. Contraction
   buys 0.79x on node count. Not a lever.

Getting under ~500 ms means a genuinely target-terminating A* in compiled code
(numba or Cython) — a new binary dependency on a Windows serving box whose
deploy notes already flag build headaches — or contraction hierarchies.

**Recommendation: accept ~1.3 s and defer.** With usership low, throughput is
not the constraint, and 1.3 s to plan a scenic drive is tolerable. Revisit only
if mid-drive reroute latency measurably hurts on a real drive, which is a
question the traces can answer. Do not build the compiled router on spec.

## Phase 6 — Deploy

`SCENIC_DATA` makes the switch a one-line change and the rollback identical, so
the deploy risk is not the switch. It is two numbers.

**RAM on the serving box.** The router peaks at **1.82 GB on Massachusetts**,
with the optional access layers loaded. Scaling the arrays by 3.53x puts New
England near **6–6.5 GB resident**, or ~4.5 GB without the access layers.
`server/DEPLOY.md` prices those layers at "+10 s startup, +0.5 GB RAM and 59 MB
on disk" and marks them optional; at region scale that is more like +1.8 GB, and
dropping them reinstates the parking-lot snap defect that took one drive from 13
off-route reroutes to 3. **I do not know how much RAM the Windows laptop has,
and this is the one fact that could invalidate serving the region from it.**
Establish it before Phase 2, not after.

**Upload.** The serving payload goes from 77 MB (`graph_edges` 70 + `graph_nodes`
6.8 + `turn_restrictions` 0.1) to **~271 MB**, plus ~154 MB if the access layers
ship. Over a home upload link, behind a tunnel. `DEPLOY.md` already treats an
80 MB copy as a cost worth noting; this is five times that.

Also: load time scales with it — 14.8 s becomes ~52 s of restart downtime.

And the standing rule in `DEPLOY.md` applies with more force than usual: **the
code and the parquets must come from the same commit**, because `router.py`
re-blends scores live using `score.py`'s `WEIGHTS`. Phase 4 changes those
constants. A server left on old parquets with new code returns subtly wrong
routes and no error.
`test_neutral_weights_reproduce_the_precomputed_score` is the tripwire — run the
suite on the serving box after copying.

## Phase 7 — iOS, which is smaller than it looks

The expansion note lists four Massachusetts pins and says "search for a Vermont
town today and the completer biases against it". Reading the call sites, that is
true only at cold launch: `ContentView.swift:64` already replaces the search
region with the map camera on every pan (`onMapCameraChange`), and
`SearchCompleter.update(for:near:)` takes the region as a parameter. So
`.massachusetts` is a *fallback*, live in exactly two places — the initial
camera and `completer.region`'s initial value.

**Do not simply widen the box to New England.** `Region.swift`'s own comment
explains why: search ranks by distance from the region's centre, and the
statewide box centred near Oxford already made "main street" return a main
street four towns away. A six-state box centred in New Hampshire is worse for
everyone.

The right change is to make the opening state follow the user: initial camera
and initial search bias from the last known location when authorized, falling
back to a regional box only when there is nothing better. That is a real (small)
UX change rather than a constant edit — and because the wire protocol does not
change, it ships on its own schedule, before or after the server switch.

## Decisions needed

1. **Relief window** — band the filter (recommended: correct, unlocks the next
   expansion, cuts the peak), pin `win = 13` (preserves MA continuity, keeps a
   ±6% latitude bias), or accept 15 px and re-fit around it.
2. **How far to re-fit.** Minimal — fix relief and byways, leave `RAW_BASE`/
   `STRETCH` alone, accept that northern scores skew — or full, re-fitting the
   composite region-wide and re-validating Massachusetts against the 76 marks.
   Minimal is safer; full is the only way a 7/10 means the same thing in Stowe
   as in Sudbury.
3. **How much RAM the serving laptop has.** Gates Phase 6 and possibly the whole
   approach of serving the region from it.
4. **Whether the access layers ship.** ~154 MB more upload and ~1.8 GB more RAM,
   against reinstating a measured arrival defect.

## Risks, and what each one costs

| risk | cost if it bites | mitigation |
|---|---|---|
| Serving laptop cannot hold ~6 GB | region unservable from it; VPS or a slimmer router needed | measure RAM before Phase 2 |
| Disk fills mid-build (98% today, ~1.5 GB needed) | a partial build in a scratch dir | build into `data/processed-ne`, never over `data/processed`; delete the `.tif`s after scoring |
| Re-fitting the composite regresses Massachusetts | the only validated region gets worse | the 76 marks are the gate; keep the live build serving until it passes |
| Latency lands worse than 1.3 s | poor planning UX | Phase 5 says defer, not ignore — measure on a real drive |
| Byway relation allowlist admits a walking route | footpaths flagged as scenic road | vet all 13 networks; `nwn`/`lwn`/`lcn` are known-bad |

Throughout: **`data/processed/` is not written to by any phase.** The live
Massachusetts build keeps serving until Phase 6 chooses to switch, and switching
back is one environment variable.
