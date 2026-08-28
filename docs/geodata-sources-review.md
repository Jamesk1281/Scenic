# Are we using the right geographic data?

**Status: diagnosed, nothing changed.** No source file has been touched. This is
a review task with a measurement already done: the size of the gap is known, the
cause is known, and what is *not* known is whether better open data exists to
close it. **"Everything is fine as it is" is an acceptable and expected
verdict** — say so plainly if that is what the evidence supports.

## The goal

Scenic's geographic inputs were chosen once, early, and never revisited. There
are exactly two: an **OSM PBF** from Geofabrik, and **Terrarium elevation
tiles**. Everything the app knows about beauty comes from those.

Two constraints from the product owner, both hard:

1. **Any source considered must cover at least all six New England states.** A
   Massachusetts-only dataset is not interesting at any quality, because the
   next build is regional (`docs/new-england-rollout.md`).
2. **Preferably it covers a much larger part of the United States.** Given two
   comparable sources, the one with national coverage wins, because the
   architecture ceiling is the Census Northeast and the ambition is past it.

Everything must stay open-license. The README's first line commits to "open
geodata only (no Google/Apple data)", and that is a product claim, not a
preference.

## The measurement that makes this worth asking

Run on the shipped Massachusetts graph (`data/processed/graph_edges.parquet`,
400,983 edges, 66,195 km), 2026-08-28:

```
road-km with NO mapped scenery polygon at all:  22.6%  (14,979 km)
  their mean score: 2.42        vs 4.68 for the rest
```

"No mapped scenery polygon" means all six of `c_water`, `c_coast`, `c_green`,
`c_farm`, `c_views`, `c_urban` are exactly 0.0. On those 14,979 km the score is
produced entirely by road geometry (`c_curves`), terrain (`c_relief`) and the
road-class penalty. **Nearly a quarter of the state is scored without the model
seeing a single mapped feature.**

Per-component share of network km with any credit at all:

| component | source | % of km |
|---|---|---|
| `c_urban` | OSM landuse + place nodes | 41.1% |
| `c_water` | OSM water areas/lines | 36.8% |
| `c_green` | OSM wood/forest/park/reserve | 35.9% |
| `c_coast` | OSM coastline | 7.7% |
| `c_farm` | OSM farmland/orchard/meadow | 4.4% |
| `c_views` | OSM `tourism=viewpoint` | 1.9% |
| `c_curves` | **road geometry** | 97.7% |
| `c_relief` | **Terrarium raster** | 100.0% |
| `c_scenic_tag` | OSM `scenic=yes` + 3 hardcoded names | **~0%** |

Weighted by `WEIGHTS` and by road length, the raw blend is **59% mapped
polygons, 41% geometry/terrain, and ~0% scenic tag.**

### Why that 2.42 population is the interesting one

From the only ground truth this project has — 76 driver marks over three drives
on 2026-08-25, recorded in `traces/` and analysed by `tools/analyze_trace.py`:

> Blind spots are all "no polygon": **Armstrong Rd 2.45, Linden St 2.48, West
> Bare Hill Rd 2.78**, all marked *nice*.

Those three scores sit inside the 2.42 mean of the no-polygon population, to two
decimals. The roads the driver liked and the model missed are not scattered —
**they are that 22.6%.** That is the hypothesis this task exists to test against
better data, and it is a hypothesis: three roads is three roads.

Two more results from the same 76 marks, which bound what a data fix can buy:

- `c_curves` — pure road geometry, no external data — is the **best single
  predictor at 0.81 separation**, beating the whole composite (0.71), on a
  weight of only 0.13.
- `c_urban` is the **only component pointing the wrong way** (0.35). It is
  deliberate townscape credit at weight 0.14 and it produces the expensive
  errors: Hudson Road scored 6.42 with `c_green` 1.0 *and* `c_urban` 1.0, and
  the driver called it dull. OSM `landuse=retail|commercial` cannot tell a
  village green from a strip mall. If a data source can, that is in scope.

## What is used today, exactly

**OSM, via `pipeline/extract.py`** — ten layers, from these tag sets:

- `extract.py:32-35` green: `natural=wood`, `landuse=forest`,
  `leisure=park|nature_reserve`, `boundary=national_park|protected_area`
- `extract.py:36-38` water: `natural=water|bay`, `landuse=reservoir|basin`,
  `waterway=river|canal`
- `extract.py:39` farm: `landuse=farmland|orchard|vineyard|meadow`
- `extract.py:46-47` townscape: `place=city|town|village|hamlet|square` nodes,
  `landuse=retail|commercial`
- viewpoints: `tourism=viewpoint`; coastline: `natural=coastline`

Thresholds and minimum polygon areas are `score.py:39-48`; the blend is
`score.py:49-53`.

**Terrarium elevation**, `elevation.py:32` —
`elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png` at zoom 11
(~57 m/px at MA latitudes). Feeds one thing: local relief over a 750 m window
(`elevation.py:43`), normalised by `RELIEF_FULL = 100.0` (`score.py:78`).

**Nothing else.** The README's one unchecked scoring item is
`README.md:34` — "Land cover (NLCD/ESA WorldCover) feature for better score
accuracy" — proposed long ago, never evaluated.

## The questions to answer

1. **Is OSM feature coverage uniform across the six states?** This is the
   load-bearing one. `docs/new-england-rollout.md` Phase 4 commits to a
   region-wide re-fit so "a 7/10 means the same thing in Stowe as in Sudbury" —
   which is only achievable if the *inputs* are comparably mapped. If Maine's
   `landuse=farmland` is a third as complete as Massachusetts', the re-fit
   launders a mapping artifact into a scenery score. Measure it per state,
   normalised by road-km or by land area, not by raw feature count.
2. **Does better open data exist for any of the six polygon components?**
   Candidates worth checking (not exhaustive, not endorsed): ESA WorldCover,
   NLCD and its Tree Canopy product, USGS PAD-US for protected land, NHD for
   hydrography, USGS 3DEP for elevation, FHWA's national byway inventory,
   USGS GAP. For each, the answer needed is: coverage extent, license,
   **retrievability**, resolution, size on disk, and which pipeline stage it
   would enter.
3. **Is Terrarium at zoom 11 good enough for relief?** It has never been
   compared against anything. 3DEP is far higher resolution over CONUS; whether
   that changes a 750 m-window range statistic is an open question and may well
   be no.
4. **Can anything fix `c_urban`?** The one component measured to point the wrong
   way.
5. **What would each candidate cost?** Disk, RAM, build time, and whether it
   tiles.

## Traps

- **Do not propose anything that does not cover all of New England.** Stated
  twice above because it is the constraint most likely to be quietly relaxed for
  an attractive Massachusetts dataset — MassGIS in particular is excellent, and
  is out of bounds on its own. It is in bounds only as a *yardstick* for
  measuring how complete OSM is in the one state where a reference exists.
- **Verify retrievability, not existence.** This project has been bitten:
  `scenic-traffic-data-sources` records a MassDOT report whose text search
  engines still index and whose **URL 404s**, and an FHWA report whose numbers
  are **images with no text layer**. A document appearing in search results with
  quotable methodology is not evidence it can be fetched. **Download it. Use a
  control from the same host to tell a 404 from a bot block.** Say which of your
  claims are from a retrieved file and which are from a search summary.
- **Do not touch `pipeline/`.** Two other pieces of work are queued on those
  exact files: the `elevation.py` latitude-banding fix, and the byway-relation
  change to `extract.py` + `score.py`. This task writes a document. Editing a
  pipeline file here is a merge conflict, not progress.
- **Do not re-derive the scoring calibration.** `RAW_BASE`, `STRETCH`,
  `CURVE_FULL`, `RELIEF_FULL` and `WEIGHTS` are all fitted and defended in
  comments. This task asks whether the *inputs* are right, not the constants.
- **Adding a component is not free, and the cost is not the column.** `score.py`
  writes any `c_<name>` column, `graph.py` auto-detects it, `router.py`
  re-blends it live — so the plumbing genuinely is one `WEIGHTS` entry plus a
  rebuild. What is *not* free: `router.py:164` `BEAUTY_TYPES` vs
  `router.py:181` `BASELINE` decides whether users can tune it (and
  `BEAUTY_TYPES` is mirrored in `ios/Sources/BeautyType.swift`, with tests both
  sides asserting the lists match); and changing the composite forces the
  `BETA`/`PREF_CURVE` sweep to be re-run, because they were co-fitted against
  the current scale. Cost any proposal with that chain included.
- **Any raster proposal must say how it tiles.** `elevation.py` holds one
  in-RAM float64 mosaic of the whole bbox and runs `maximum_filter` /
  `minimum_filter` over it — peak ≈ 5× the array. New England is already ~5.6 GB
  by that path, and it is the known wall past the Northeast. A 10 m global
  product is ~32× the pixels of Terrarium zoom 11. "It's higher resolution" is
  not an argument until the memory is costed.
- **`CRS_METERS = 26986` is NAD83 / Massachusetts Mainland** (`common.py:58`).
  Scale error is ≤0.31% anywhere in New England and NY/PA, 1.0% at Atlanta,
  3.9% at Miami. It does not block New England. It does bear on
  "a larger portion of the country", so mention it; do not fix it here.
- **Do not re-survey traffic or congestion data.** Ruled out at source and
  decided against — `docs/traffic-schedule-plan.md`.
- **`c_scenic_tag` carrying ~0% is already diagnosed** — 54 OSM byway route
  relations, 5,550 ways, 4,026 km, sitting unused. That fix is written up in
  `docs/new-england-rollout.md` Phase 0b and is somebody else's. Do not
  re-propose it; the ~0% above is context, not a finding to rediscover.

## Out of scope

Writing code. Changing any pipeline file. Building or downloading a large
dataset — a sample sufficient to answer a question is fine; a full CONUS
download is not. Choosing between options: present them with costs and a
recommendation. The iOS app.

## Done looks like

1. **A per-state coverage table** for the six OSM polygon components across all
   six New England states, normalised so the states are comparable, with a
   verdict on whether a region-wide scoring re-fit is safe on this data.
2. **A candidate table** — every source seriously considered, with coverage
   extent, license, whether it was actually retrieved, resolution, disk/RAM
   cost, and which pipeline stage it enters. Include the ones you rejected and
   why, so nobody re-surveys them.
3. **A recommendation**, ranked, with the single highest-value change named
   first — **or a clear statement that the current sources are adequate and
   nothing should change.** That is a genuinely acceptable answer and should not
   be padded into a change proposal.
4. **An answer on the 22.6% hypothesis**: would any candidate source actually
   give those 14,979 km a signal, or are roads with no mapped feature simply
   roads with nothing to map? Name what would settle it.
5. **Anything that cannot be determined without building a prototype**, named as
   such, with what it would take.
