# Are we using the right geographic data? — findings

Answers to `docs/geodata-sources-review.md`, measured 2026-08-28. No pipeline
file was touched; everything below came out of standalone scripts reading
`data/raw/`, `data/processed/` and `traces/`.

**Verdict.** The elevation source is fine and should be left alone. The OSM
polygon layers are **not uniform enough across New England for the Phase 4
re-fit**, and that is a structural fact, not a tuning problem. The fix is small:
**add a WorldCover tree-cover component alongside `c_green` rather than
replacing it** — roughly one pipeline stage plus four lines, no iOS change, and
no recalibration. **Whether it is worth doing at all depends on whether the
regional rollout is real** (§7.4); if Scenic stays Massachusetts-only the case is
weak.

---

## 0. What is a retrieved claim and what is not

Every number below is from a file downloaded and read in this session, unless it
appears in the "search summary only" list.

**Retrieved and read** (bytes on disk, pixels or records parsed):

- ESA WorldCover v200 2021 — 8 tiles, 343 MB, pixels read and classified
- ESA WorldCover tile grid (`esa_worldcover_grid.geojson`, 544 KB, 2,651 tiles)
- USGS 3DEP 1/3-arcsec `n43w073` / `n43w072` — read over HTTP range requests,
  real elevations returned (10.31 m, float32, 483 MB/tile, COG w/ overviews)
- Terrarium z11 tile — 131,008 B PNG, HTTP 200
- NLCD 2021 CONUS metadata XML — 50,437 B (gave 30 m, 1,012 MB, ERDAS)
- PAD-US 4 ScienceBase item JSON — 18,135 B (gave "Public Domain")
- NOAA C-CAP high-res bulk-download listing — 3,204 B (gave the state list)
- AWS Open Data Registry YAML for WorldCover / Copernicus DEM / Terrain Tiles
- `data/processed/graph_edges.parquet`, `scored_chunks.parquet`, `relief.tif`
- All six Geofabrik state PBFs; the 12 `traces/*.ndjson`

**HTTP 200 on a listing, contents not downloaded:** USDA CDL 2024 (504,387,158 B
`Content-Length`), NHDPlus HR and NHD-by-state prefixes on `prd-tnm`,
Copernicus DEM GLO-30 bucket, Overture release prefix, USGS GAP ScienceBase item.

**Could not be retrieved — bot block, not a 404:** the FHWA byway inventory.
`highways.dot.gov` and `www.fhwa.dot.gov` return **HTTP 403 on their own root
URL**, so a 403 on a byway page proves nothing about whether the page exists. I
make no claim about FHWA's data either way. This is the second time this project
has hit an FHWA wall (`scenic-traffic-data-sources` records the first).

**Search summary only, never fetched:** nothing load-bearing. Where a search
suggested a URL I fetched it; where the fetch failed I said so.

**Control-URL discipline.** Every host was probed with a deliberately bogus URL
alongside the real one. `esa-worldcover.s3.eu-central-1.amazonaws.com`,
`prd-tnm.s3.amazonaws.com`, `copernicus-dem-30m.s3.amazonaws.com`,
`elevation-tiles-prod.s3.amazonaws.com`, `www.mrlc.gov`, `coast.noaa.gov` and
`raw.githubusercontent.com` all returned a clean **404 for the bogus key and 200
for the real one** — so absence there is real absence. Two hosts failed the
control and are reported as unverifiable rather than absent:
**`s3-us-west-2.amazonaws.com/mrlc`** (403 for real *and* bogus — bucket listing
denied; the `www.mrlc.gov/downloads/` path works instead) and **FHWA**.

### One reconciliation before the tables

The review's headline "22.6% / 14,979 km" is measured on **`graph_edges.parquet`**.
I reproduced it exactly (22.63%, 14,979 km, mean score 2.42 vs 4.68). The same
question asked of **`scored_chunks.parquet`** gives **26.98% / 18,311 km**,
because `graph.py` averages components along an edge and an edge spanning one
credited chunk and one uncredited one is no longer all-zero. Both are right.
**Everything below is chunk-level**, the unit `score.py` produces and the unit
that compares cleanly across states. Read my Massachusetts 26.3% against the
review's 27.0%, not against its 22.6%.

---

## 1. Is OSM feature coverage uniform across the six states? **No.**

Replicating `score.py`'s exact `DIST` / `MIN_AREA` thresholds and 400 m chunking
on each Geofabrik state extract (951,182 chunks, 239,204 road-km):

| state | road-km | **no polygon** | green | water | farm | urban | coast | views |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Maine | 60,080 | **44.3%** | 21.9 | 32.5 | 1.9 | 10.3 | 10.2 | 0.4 |
| Vermont | 28,562 | **32.5%** | 39.3 | 31.7 | 3.9 | 23.6 | 0.0 | 0.9 |
| New Hampshire | 32,157 | **31.5%** | 35.0 | 38.4 | 2.9 | 24.6 | 0.3 | 0.6 |
| Connecticut | 39,712 | **30.6%** | 35.2 | 29.3 | 3.3 | 32.1 | 5.5 | 1.4 |
| Massachusetts | 67,806 | **26.3%** | 32.2 | 32.4 | 3.1 | 38.2 | 8.4 | 1.6 |
| Rhode Island | 10,886 | **6.8%** | 65.7 | 32.6 | 9.7 | 38.8 | 23.1 | 0.8 |

A 6.5× spread in the blind rate — but this table alone cannot tell a mapping gap
from a real landscape difference. That is what the control is for.

### The control: ESA WorldCover, which has never heard of a state line

WorldCover is a 10 m global land-cover classification produced by one uniform
process. Sampling it in a 90 m box around **the same chunk midpoints**
(≈ `score.py`'s 80 m green/farm threshold) gives an independent read of what is
on the ground, so *OSM ÷ WorldCover* is a completeness index comparable between
states.

| state | OSM green | WC tree | **ratio** | OSM farm | WC crop+grass | **ratio** | OSM urban | WC built | **ratio** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Rhode Island | 65.7 | 82.1 | **0.80** | 9.7 | 35.3 | **0.27** | 38.8 | 61.1 | **0.64** |
| Vermont | 39.3 | 86.5 | **0.45** | 3.9 | 48.6 | **0.08** | 23.6 | 26.2 | **0.90** |
| Connecticut | 35.2 | 91.1 | **0.39** | 3.3 | 38.4 | **0.09** | 32.1 | 37.5 | **0.86** |
| New Hampshire | 35.0 | 91.4 | **0.38** | 2.9 | 37.0 | **0.08** | 24.6 | 35.0 | **0.70** |
| Massachusetts | 32.2 | 88.7 | **0.36** | 3.1 | 30.5 | **0.10** | 38.2 | 49.6 | **0.77** |
| Maine | 21.9 | 89.8 | **0.24** | 1.9 | 41.2 | **0.05** | 10.3 | 26.0 | **0.39** |

**Completeness spread: green 3.3×, farmland 5.9×, townscape 2.3×.**

The load-bearing column is the second. **WorldCover's tree cover is nearly flat
across all six states (82–91% of road-km) while OSM's green credit ranges 21.9%
to 65.7%.** The land is similar; the mapping is not.

The direction is perverse. Maine is the most forested state in the country —
WorldCover's median tree fraction on its road-km is **0.83**, the highest of the
six — and gets **the least** green credit, 21.9%. Rhode Island has the lowest
median tree fraction (**0.42**) and gets **the most**, 65.7%. Across the six the
rank correlation between real forest and OSM green credit is **negative**
(Spearman −0.60), though at n=6 that is not significant on its own (p=0.21) — the
per-state ratio column is the stronger evidence.

The mechanism is not mysterious: OSM green polygons are drawn around *designated*
land. Rhode Island's woods are nearly all inside state management areas and so
are nearly all drawn; Maine's are private working timberland and nobody has drawn
them. **`c_green` measures land designation, not vegetation.**

### Which components are actually affected

| component | weight | spread across 6 states | |
|---|---:|---:|---|
| `c_water` | 0.22 | **1.3×** | **uniform** — a lake is a lake everywhere |
| `c_green` | 0.18 | 3.0× | not uniform |
| `c_urban` | 0.14 | 3.8× | not uniform |
| `c_farm` | 0.06 | 5.1× | not uniform |
| `c_views` | 0.05 | 3.8× | not uniform |
| `c_coast` | 0.13 | — | **legitimately** varies; Vermont is landlocked |
| `c_curves`, `c_relief` | 0.29 | — | uniform by construction |
| `c_scenic_tag` | 0.07 | — | inert either way (~0%) |

**0.43 of 1.14 weight — 38% — rides on inputs that vary 3–5× by state for
mapping reasons.** This is the precise size of the problem, and note what it is
*not*: OSM is not uniformly bad. Water is fine at 1.3×. The failure is confined
to the layers where OSM records designation instead of substance.

### Verdict on the re-fit

**A region-wide re-fit on the current polygon inputs is not safe.**
`docs/new-england-rollout.md` Phase 4 wants "a 7/10 to mean the same thing in
Stowe as in Sudbury". On these inputs it cannot: an identical wooded road gets
green credit at 0.24 of its due in Maine and 0.80 in Rhode Island. Re-fitting
would not remove that — it would average it, making every state wrong by a
different fixed amount and calling it calibrated.

---

## 2. The 22.6% hypothesis: **confirmed — those roads are under-mapped, not empty**

For every chunk with all six polygon components at exactly 0.0, what does
WorldCover see?

| state | no-polygon km | ≥10% tree in 90 m box | mean tree fraction, **no polygon** | mean tree fraction, **has polygon** | diff |
|---|---:|---:|---:|---:|---:|
| Massachusetts | 17,856 | **91.2%** | **0.66** | 0.62 | **+0.03** |
| Connecticut | 12,154 | 93.3% | 0.70 | 0.65 | +0.05 |
| New Hampshire | 10,114 | 93.4% | 0.74 | 0.68 | +0.06 |
| Vermont | 9,274 | 84.2% | 0.64 | 0.65 | −0.01 |
| Maine | 26,595 | 89.0% | 0.69 | 0.71 | −0.02 |
| Rhode Island | 744 | 74.6% | 0.38 | 0.54 | −0.15 |

**In Massachusetts, 91.2% of the road-km the model scores blind has real tree
cover beside it, and that population is on average *slightly more wooded* than
the population the model does credit (0.66 vs 0.62).** They are not roads with
nothing to map. They are roads with something to map that nobody has mapped.

That holds in five of six states. Rhode Island is the exception and the exception
makes sense: with only 6.8% uncredited, what is left there really is the
genuinely featureless remainder.

I could not check the three named roads individually — the review uses short
forms ("Armstrong Rd"), OSM spells them out ("Armstrong Road"), and "Linden
Street" alone has 288 edges statewide with no way to identify the driver's from
the review text. The population result settles the hypothesis on 17,856 km rather
than on three roads.

---

## 3. Does a better source score better? Yes — but not provably so

The only ground truth is the 76 marks of 2026-08-25. I sampled WorldCover at each
mark's position stepped back by `MARK_REACTION_S`, and scored candidates with the
project's own `separation()` and `null_ceiling()` imported from
`tools/analyze_trace.py`. All 76 recovered, 59 nice / 17 dull, none skipped.
**Null ceiling 0.632.**

| signal | separation | |
|---|---:|---|
| WorldCover built-up, **inverted** | **0.728** | above null |
| `c_curves` | 0.705 | above null |
| WorldCover tree fraction | 0.705 | above null |
| composite `score` | 0.694 | above null |
| `c_relief` | 0.652 | above null |
| `c_urban` **inverted** | 0.644 | above null |
| `c_green` | 0.631 | at null |
| `c_water` | 0.590 | below |
| road-class adjustment alone | 0.565 | below |
| `c_urban` **as used** | 0.356 | points the wrong way |

**Read only the comparisons within this table.** To put raster values and model
components on identical rows I snapped each mark to its nearest graph edge
(median snap 2.4 m, max 7.1 m). That is a *different estimator* from
`analyze_trace.py`'s 400 m along-route window — which is why `c_curves` reads
0.705 here against the 0.81 the review quotes. My absolute numbers are not
comparable to the review's; the ordering within my own table is.

`c_urban` as used scores 0.356, confirming the review's finding at a second
estimator. WorldCover built-up entered negatively (0.728) beats `c_urban`-inverted
(0.644) as a measure of the same "townness" concept.

### The ceiling: the ground truth binds, not the data source

**Standard error of separation at 59/17 marks is 0.080.** The gap from the
shipped 0.694 to the best hand-specified variant (0.750) is **0.70 SE** —
statistically indistinguishable.

Fitting makes it worse, which is the more useful result:

| feature set | leave-one-out | in-sample |
|---|---:|---:|
| geometry + terrain + WorldCover (4) | **0.678** | 0.762 |
| WorldCover only (2) | 0.668 | 0.722 |
| geometry + terrain only (2) | 0.665 | 0.731 |
| model components only (9) | 0.602 | 0.769 |
| everything (11) | **0.586** | 0.787 |

The 11-feature fit lands **below the 0.632 null** out-of-sample while scoring
best in-sample. Every hand-specified variant (0.709–0.750) beats every fitted
one, because the weights defended in `score.py`'s comments carry real prior
knowledge that fitting throws away. **The 9-component vector is already at or
past the complexity 76 marks can support.** No data source purchase changes that.

**Consequence for how to decide:** the accuracy case cannot be settled with the
validation data that exists. The uniformity case in §1 can, and does not need
ground truth at all. Decide on §1.

### Three caveats on this section

1. **Collinearity.** WC built and WC tree are two sides of one coin
   (Spearman −0.859). WC built vs `c_urban` is +0.449, vs road-class adjustment
   −0.441. WC tree vs `c_green` is only **+0.31** — they measure different things,
   which is the whole basis for keeping both (§6).
2. **The within-road-class test is underpowered and I will not claim it.** Split
   by class, WC built inverted separates at 0.61–0.67, but every one sits *below*
   its own null ceiling (0.74–0.84): dull marks concentrate on big roads, and 12
   residential and 13 tertiary marks are all "nice", zero dull.
3. **All 76 marks are one driver, three drives, one corner of eastern
   Massachusetts.** §1 establishes that these states are not interchangeable.
   Neither is this validation.

---

## 4. Is Terrarium at zoom 11 good enough? **Yes. Close 3DEP.**

3DEP 1/3-arcsec (10.31 m measured) read over HTTP range requests, relief
recomputed the way `elevation.py` does it — max-minus-min over a 750 m **ground**
window, anisotropy handled — compared with the shipped `relief.tif` at real
Massachusetts chunk midpoints.

| area | n chunks | Terrarium mean | 3DEP mean | ratio | **Spearman** | mean abs Δ`c_relief` | chunks moving >0.10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| western MA hills | 3,115 | 78.9 m | 87.0 m | 1.10 | **0.973** | 0.052 | 16.1% |
| eastern MA flat | 12,156 | 29.1 m | 32.3 m | 1.11 | **0.968** | 0.034 | 4.0% |

3DEP reads ~10% more relief — finer pixels catch more extreme local maxima and
minima — but reads it almost perfectly monotonically with Terrarium in both
terrains. A uniform +10% at rank correlation 0.97 is not new information; it is
`RELIEF_FULL = 100.0` wanting to be 110, and that constant is already fitted and
explicitly out of scope.

**Do not adopt 3DEP.** 483 MB per 1°×1° tile (~14 tiles, ~6.8 GB for New England)
and a much heavier mosaic, to move `c_relief` by 0.03–0.05.

One observation in passing, not a proposal: 22.9% of western-MA chunks already
saturate at `c_relief = 1.0` under Terrarium (30.8% under 3DEP). In the hills the
component is at its ceiling. That is a `RELIEF_FULL` question and belongs to
whoever owns the calibration.

---

## 5. Candidate table

"Retrieved" = downloaded and parsed in this session. NE = covers all six states.

| source | extent | NE? | license | **retrieved** | resolution | cost | pipeline stage | verdict |
|---|---|---|---|---|---|---|---|---|
| **ESA WorldCover v200** | global | ✅ | CC-BY 4.0 *(registry YAML)* | ✅ **8 tiles, 343 MB, pixels read** | 10 m | NE 343 MB; CONUS 153 tiles ≈6.4 GB; sampling 314k chunks 9.3 s, peak RSS 2.83 GB | new raster stage beside `elevation.py`; new `c_` column | **ADOPT — §6** |
| Terrarium (incumbent) | global | ✅ | per-source attribution *(registry gives only a link to tilezen's attribution doc — a blend of SRTM/NED/others, each with its own terms; not verified further)* | ✅ tile, 131 KB | z11 ≈57 m | already paid | `elevation.py` | **KEEP** |
| USGS 3DEP 1/3″ | US | ✅ | public domain | ✅ **pixels read remotely** | 10.31 m | 483 MB/tile, ~6.8 GB NE | would replace `elevation.py` input | **REJECT — Spearman 0.97, §4** |
| NLCD 2021 | CONUS+ | ✅ | public domain | metadata only (50 KB XML) | **30 m** | 1,012 MB CONUS | same as WorldCover | **REJECT — 9× coarser by area for the same job. The README's open item is answered by choosing WorldCover over NLCD.** |
| USDA Cropland Data Layer | CONUS | ✅ | public domain | listing only (504 MB) | 30 m, annual | 504 MB/yr | could sharpen `c_farm` | **HOLD — `c_farm` is 1.9–9.7% of road-km at weight 0.06; smallest prize on the board** |
| USGS PAD-US 4 | US | ✅ | **Public Domain** *(item JSON)* | item JSON only | vector | not costed | `extract.py` polygon layer | **HOLD — it maps *designation*, the exact failure mode of `c_green`. Would deepen the bias, not fix it.** |
| NHD / NHDPlus HR | US | ✅ | public domain | listing only | vector | not costed | `extract.py` water layer | **HOLD — `c_water` is the one uniform polygon layer (1.3×); no gap demonstrated** |
| Copernicus DEM GLO-30 | global | ✅ | "free basis for the general public" under COP-DEM terms *(registry, verbatim)* | listing only | 30 m | not costed | elevation | **REJECT — §4 says elevation is not the problem; a 30 m DEM cannot beat 3DEP's 10 m, which already failed to move the score** |
| USGS GAP land cover | US | ✅ | public domain | item JSON 200 | 30 m | not costed | land cover | **REJECT — superseded by WorldCover on resolution, NLCD on currency** |
| Overture Maps | global | ✅ | ODbL / CDLA | listing only | vector | not costed | `extract.py` | **HOLD — buildings could inform `c_urban`, but its places/landuse are substantially OSM-derived, so it would re-import the same bias** |
| **NOAA C-CAP hi-res** | coastal states | ❌ | public domain | ✅ **listing read** | 1 m | — | — | **REJECT ON CONSTRAINT — listing has `ct, ma, me, ri` and **no `nh`, no `vt`**.** |
| FHWA byway inventory | US | ? | ? | ❌ **403 on root — bot block** | ? | ? | ? | **UNVERIFIABLE.** Also largely redundant: the 54 OSM byway relations are Phase 0b's job. |
| Google Dynamic World | global | ✅ | CC-BY | not attempted | 10 m | — | — | **EXCLUDED BY PRODUCT CLAIM** — Google-produced; README commits to "no Google/Apple data". Flagged because it is the obvious 10 m near-real-time alternative and someone will suggest it. |
| MassGIS | MA only | ❌ | open | not attempted | — | — | — | **OUT OF BOUNDS** by the review's own rule. Not needed: WorldCover served as the yardstick and works in all six states. |

---

## 6. Add tree cover *alongside* `c_green` — do not replace it

An earlier draft of this document recommended replacing `c_green`. That was
wrong, and measuring the alternatives is what showed it. Two axes matter: does it
score better, and how much of the map moves.

| variant | separation | Spearman vs shipped | km moving >1 pt | blind-pop mean |
|---|---:|---:|---:|---:|
| current (shipped) | 0.694 | 1.000 | 0% | 2.40 |
| A — replace: `c_green` → tree @0.18 | 0.726 | 0.804 | 51% | 3.76 |
| **D — both: green 0.09 + tree 0.09** | **0.712** | **0.953** | **20%** | **3.08** |
| E — keep green 0.18, add tree 0.09 | 0.709 | 0.979 | 30% | 3.08 |
| F — D + built-up in `BASELINE` | 0.722 | 0.937 | 74% | 3.90 |

**D gets ~56% of the separation gain for ~40% of the disruption.** And since
every separation here sits inside one standard error (0.080) of every other
(§3), they are statistically indistinguishable — **so the choice must be made on
structural grounds, not on the score.**

Structurally D is right and A is wrong. `c_green` is not uniformly bad: in Rhode
Island its completeness is 0.80 and it carries genuine information. Replacing it
discards that to fix Maine's problem. Keeping both means the model sees
designation *and* substance, and they are only correlated at ρ=+0.31 — they are
not redundant.

**Continuous, not binary.** A ≥10% tree flag is true on 88.7% of Massachusetts
road-km and would carry almost no ranking signal. The fraction keeps real spread
(p25 0.25, p50 0.62, p75 0.94) and that is what scored 0.705.

**How it tiles** — the review demands this, and it is where WorldCover wins.
WorldCover needs **no mosaic at all**. The kernel is a 9×9 box, so a chunk is
sampled directly from the COG containing it. Measured peak RSS 2.83 GB is *one
36000×36000 tile* — **it does not grow with the region.** Compare `elevation.py`'s
in-RAM float64 mosaic with `maximum_filter`/`minimum_filter` over it, ~5× the
array, already 5.6 GB for New England and the known wall past the Northeast.
This is the opposite cost shape. The tiles are COGs with overviews and range
requests, so even the 343 MB download is optional.

### `c_urban` is confirmed backwards — handed over, not applied

WorldCover built-up entered negatively is the best single signal measured
(0.728 vs `c_urban`-inverted's 0.644). But variant F, which adds it, moves **74%**
of road-km — the most disruptive option on the board — and the review puts
`WEIGHTS` out of scope. **This is a finding, not a proposal.** Note also that WC
built and WC tree are ρ=−0.859, so F is largely one change wearing two hats.

---

## 7. What it would cost

`BEAUTY_TYPES` maps an API name to an edge *column*:
`("forest", "forest/park", "c_green", WEIGHTS["green"])`. The iOS mirror and its
two tests (`test_routing.py:36`, `:44`) assert the **apiName set and the display
labels — not the columns.** That decoupling is what makes this cheap.

| item | cost |
|---|---|
| new `landcover.py` stage | **the only real engineering.** Simpler than `elevation.py`: no mosaic, no filters, no coverage guard. The sampling loop is written and measured (9.3 s for Massachusetts) |
| `score.py` — new `c_treecover` column, 2 `WEIGHTS` entries | ~3 lines |
| `router.py` — add `("c_treecover", WEIGHTS["treecover"])` to `BASELINE` | **1 line** |
| **iOS app** | **zero changes** — `forest` apiName and `BEAUTY_TYPES` are untouched |
| `tests/test_calibration.py` `COMPONENTS` list (line 18) | ~2 lines |
| rebuild + test run | one pipeline run |

### 7.1 The trap

`score.py`'s `blend()` sums every `c_` column with a `WEIGHTS` entry, but
`router.py` builds its live score from two explicit lists:

```
self.base_score  = sum(w * e[col] for col, w in BASELINE)
self.pref_matrix = column_stack([w * e[col] for _, _, col, w in BEAUTY_TYPES])
```

A new column in `WEIGHTS` but in **neither** list is included in the precomputed
`score` and silently dropped from the live re-blend, breaking the documented
invariant that all-1.0 weights reproduce the precomputed column exactly
(`test_routing.py:1016` is the tripwire). **`BASELINE` is the right home** — the
existing "always on, not user-tunable" list where `c_curves`, `c_views` and
`c_scenic_tag` already live. Putting it there also means no iOS work.

### 7.2 Two costs the review warned about that do not bite

**`BETA` / `PREF_CURVE` re-sweep.** `docs/scenery-cap-options.md` already
establishes BETA is a **dead lever** — swept across a 64-fold range at pref=1.0,
routes barely move — and that unclamping `pref` is the same lever and equally
dead. Re-running it after a composite change is a *check against existing routes*,
not a re-fit.

**`RAW_BASE` / `STRETCH` recalibration.** Measured, and D needs none:

| | p05 | p25 | **p50** | p75 | p95 | p99 | mean |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 0.80 | 2.81 | **4.03** | 5.51 | 7.74 | 9.21 | 4.18 |
| variant D | 1.11 | 3.35 | **4.46** | 5.71 | 7.75 | 9.12 | 4.51 |

Length-weighted, against `score.py`'s stated targets of p50 ≈ 4.5 and p99 ≈ 9.5.
**D lands closer to the p50 target than the shipped scoring does.** Road-km pinned
at 10.0 goes 0.31% → 0.24%; pinned at 0.0, 3.31% → 3.10%.

(Variants B, C and F *do* break the calibration — their means run to 5.65 and
6.52 — which is a further reason to prefer D. Those numbers are an artifact of
not re-fitting, not an improvement.)

### 7.3 How big the change is, in plain terms

Under D, 20% of Massachusetts road-km moves by more than a point and rank
correlation with the shipped score stays at **0.953**. Under A it is 51% and
0.804. D is a correction; A is a re-ranking.

### 7.4 Is it worth it? Conditional on the rollout

**If Scenic stays Massachusetts-only, the case is weak.** The uniformity argument
is about crossing state lines and largely evaporates inside one state, where a
roughly constant mapping bias is absorbed by calibration. What remains is the 26%
blind population and an accuracy gain that §3 shows cannot be proven at 76 marks.
On that alone, the pipeline stage is not obviously worth it.

**If New England is happening, the case is strong and structural** — and it is
not really about accuracy. 38% of the composite's weight rides on inputs varying
3–5× by state for mapping reasons. Phase 4 promises a 7/10 means the same in
Stowe as in Sudbury; on these inputs it cannot, and re-fitting averages the bias
rather than removing it. Variant D is roughly one pipeline stage plus four lines,
needs no iOS work and no recalibration, and buys uniformity by construction.

**So the decision is not "is the score good enough". It is "is the rollout real".**

---

## 8. What cannot be settled without a prototype

1. **Whether D actually improves the composite.** §3 measures signals
   individually and §6 measures hand-weighted variants; neither is a fitted
   composite. Needs the column built region-wide and separation re-measured. The
   `BETA` sweep is a check (§7.2), not a re-fit.
2. **Whether any of this transfers outside eastern Massachusetts — the real
   blocker.** All 76 marks are one driver, three drives, one corner of one state,
   and §1's whole finding is that these states are not interchangeable. **The
   cheapest thing that would settle the most is marks from a second state** —
   ideally Maine, the 0.24-completeness extreme. Twenty marks each way there would
   test the uniformity claim and the WorldCover claim in one drive, and roughly
   halve the 0.080 error bar. Nothing here is worth shipping region-wide first.
3. **Whether WorldCover built-up beats road class, or is road class.** §3 caveat
   2: underpowered at 17 dull marks, which concentrate on big roads.
4. **WorldCover's 2021 vintage against a 2026 road network.** Forest and water
   move slowly; built-up does not. Un-assessed. NLCD Annual (1985–2024, 30 m) is
   the currency-vs-resolution trade if it matters.
5. **`CRS_METERS = 26986`** is NAD83 / Massachusetts Mainland. Per the review,
   ≤0.31% scale error anywhere in New England — it does not block this. It does
   bear on "a much larger part of the United States", and WorldCover being global
   makes that ambition more concrete. Not fixed here.

---

## 9. Reproducing this

Scripts are standalone and read only `data/raw/`, `data/processed/` and
`traces/`. None import or modify anything in `pipeline/`; the constants they need
(`DIST`, `MIN_AREA`, `DRIVABLE`, `CHUNK_LEN`, `RELIEF_WINDOW_M`, `RELIEF_FULL`,
`WEIGHTS`, `RAW_BASE`, `STRETCH`) are copied literally from `score.py`,
`common.py` and `elevation.py` at commit `415e0ac`.

| script | produces | runtime |
|---|---|---|
| `state_coverage.py` | §1 per-state table, from a state PBF | 24–136 s/state |
| `wc_check.py` | §1 ratio table, §2 no-polygon table | ~3 min, six states |
| `relief_check.py` | §4, via 3DEP HTTP range reads | ~40 s |
| `marks_wc.py`, `marks_compare.py` | §3 table, imports `analyze_trace.py` | seconds |
| `ceiling.py` | §3 leave-one-out table | ~1 min |
| `sim_scores.py`, `both.py` | §6 variants, §7.2 calibration | ~1 min |

They live in this session's scratch directory rather than in `tools/`, because
the review scoped this task to a document. If §6 goes ahead, `wc_check.py` is the
thing to promote — its sampling loop *is* the proposed pipeline stage.

**One bug worth recording**, since it silently produced a wrong answer:
WorldCover tiles are named for their **south-west** corner, so the tile index is
`floor(lon/3)*3`, **not `ceil`**. With `ceil` every point resolves to the tile 3°
east, lands outside its bounds, gets clipped to an edge column, and returns
*plausible-looking garbage* rather than an error. The first run of §1's ratio
table was wrong this way and read as merely surprising. A bounds assertion caught
it; the fix was verified against five corner points of the region and against the
published `esa_worldcover_grid.geojson`. Any promoted version must keep that
assertion.
