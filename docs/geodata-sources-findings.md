# Are we using the right geographic data? — findings

Answers to `docs/geodata-sources-review.md`, measured 2026-08-28. No pipeline
file was touched; everything below came out of standalone scripts reading
`data/raw/` and `data/processed/`.

**Verdict in one line:** the elevation source is fine and should be left alone,
but **OSM's scenery polygons are not uniform enough across New England for the
region-wide re-fit**, and the one source that fixes that — ESA WorldCover — is
retrievable, open, global, and measurably better on the only ground truth this
project has. This is a change proposal, not a "leave it alone".

---

## 0. What is a retrieved claim and what is not

Every number in this document is from a file that was downloaded and read in
this session, unless it appears in the "search summary only" list below.

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
URL**, so a 403 on a byway page proves nothing about whether the page exists.
I make no claim about FHWA's data either way. This is the second time this
project has hit an FHWA wall (`scenic-traffic-data-sources` records the first).

**Search summary only, never fetched:** nothing load-bearing. Where a search
suggested a URL I fetched it; where the fetch failed I said so.

**Control-URL discipline.** Every host was probed with a deliberately bogus URL
alongside the real one. `esa-worldcover.s3`, `prd-tnm.s3`, `copernicus-dem-30m.s3`,
`elevation-tiles-prod.s3`, `www.mrlc.gov`, `coast.noaa.gov` and
`raw.githubusercontent.com` all returned a clean **404 for the bogus key and 200
for the real one** — so absence there is real absence. Two hosts failed the
control and are reported as unverifiable rather than as absent:
**`s3-us-west-2.amazonaws.com/mrlc`** (403 for real *and* bogus — bucket listing
is denied) and **FHWA** (403 for everything including root).

### One reconciliation before the tables

The review's headline "22.6% / 14,979 km" is measured on **`graph_edges.parquet`**.
I reproduced it exactly (22.63%, 14,979 km, mean score 2.42 vs 4.68). The same
question asked of **`scored_chunks.parquet`** gives **26.98% / 18,311 km**,
because `graph.py` averages components along an edge and an edge spanning one
credited chunk and one uncredited one is no longer all-zero. Both are right.
**Everything below is chunk-level**, because that is the unit `score.py`
produces and the unit that compares cleanly across states. Read my Massachusetts
26.3% against the review's 27.0%, not against its 22.6%.

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

(% of road-km receiving any credit for that component. Massachusetts here is the
2026-08-25 PBF; the shipped June layers give 27.0%.)

A 6.5× spread in the blind-spot rate. But this table alone cannot tell a mapping
gap from a real landscape difference — Rhode Island is small and coastal, Maine
is empty. That is what the next section is for.

### The control: ESA WorldCover, which has never heard of a state line

WorldCover is a 10 m global land-cover classification produced by one uniform
process. Sampling it in a 90 m box around **the same chunk midpoints**
(≈ `score.py`'s 80 m green/farm threshold) gives an independent read of what is
actually on the ground, and the ratio *OSM ÷ WorldCover* is a mapping-completeness
index that is comparable between states.

| state | OSM green | WC tree | **ratio** | OSM farm | WC crop+grass | **ratio** | OSM urban | WC built | **ratio** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Rhode Island | 65.7 | 82.1 | **0.80** | 9.7 | 35.3 | **0.27** | 38.8 | 61.1 | **0.64** |
| Connecticut | 35.2 | 91.1 | **0.39** | 3.3 | 38.4 | **0.09** | 32.1 | 37.5 | **0.86** |
| New Hampshire | 35.0 | 91.4 | **0.38** | 2.9 | 37.0 | **0.08** | 24.6 | 35.0 | **0.70** |
| Vermont | 39.3 | 86.5 | **0.45** | 3.9 | 48.6 | **0.08** | 23.6 | 26.2 | **0.90** |
| Massachusetts | 32.2 | 88.7 | **0.36** | 3.1 | 30.5 | **0.10** | 38.2 | 49.6 | **0.77** |
| Maine | 21.9 | 89.8 | **0.24** | 1.9 | 41.2 | **0.05** | 10.3 | 26.0 | **0.39** |

**Completeness spread: green 3.3×, farmland 5.9×, townscape 2.3×.**

The load-bearing observation is the second column. **WorldCover's tree cover is
nearly flat across all six states (82–91% of road-km), while OSM's green credit
ranges from 21.9% to 65.7%.** The land is similar; the mapping is not.

And the direction is perverse. Maine is the most forested state in the country —
WorldCover's median tree fraction on its road-km is **0.83**, the highest of the
six — and it gets **the least** green credit, 21.9%. Rhode Island has the lowest
median tree fraction (**0.42**) and gets **the most**, 65.7%. Across the six
states the rank correlation between real forest and OSM green credit is
**negative** (Spearman −0.60), though at n=6 that is not significant on its own
(p=0.21) — the per-state ratio column above is the stronger evidence.

The mechanism is not mysterious: OSM green polygons are drawn around *designated*
land — parks, reserves, named state forests. Rhode Island's woods are nearly all
inside state management areas and so are nearly all drawn. Maine's woods are
private working timberland, and nobody has drawn them. **`c_green` is measuring
land designation, not vegetation.**

### Verdict on the re-fit

**A region-wide scoring re-fit on the current polygon inputs is not safe.**
`docs/new-england-rollout.md` Phase 4 wants "a 7/10 to mean the same thing in
Stowe as in Sudbury". On these inputs it cannot: an identical wooded road gets
green credit at 0.24 of its due in Maine and 0.80 in Rhode Island. Re-fitting
constants over the region would not remove that — it would average it, making
every state's score wrong by a different fixed amount and calling it calibrated.

This is a statement about the six polygon components. `c_curves` (road geometry)
and `c_relief` (Terrain raster) are produced identically everywhere and are not
implicated.

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
the population the model does credit (0.66 vs 0.62).** The review asked whether
these are "roads with nothing to map". They are not. They are roads with
something to map that nobody has mapped.

That holds in five of six states. Rhode Island is the exception and the exception
makes sense: with only 6.8% of its road-km uncredited, what is left over there
really is the genuinely featureless remainder.

This is the mechanism behind Armstrong Rd 2.45, Linden St 2.48 and West Bare Hill
Rd 2.78. I could not check those three roads individually — the review names them
in short form ("Armstrong Rd"), OSM spells them out ("Armstrong Road"), and
"Linden Street" alone has 288 edges across Massachusetts with no way to identify
the driver's one from the review text. The population-level result above is what
settles the hypothesis, and it settles it on 17,856 km rather than on three roads.

---

## 3. Does a better source actually score better? The 76 marks say yes

The only ground truth is the 76 marks of 2026-08-25. I sampled WorldCover at each
mark's position, stepped back by `MARK_REACTION_S`, and scored candidates with the
project's own `separation()` and `null_ceiling()` imported from
`tools/analyze_trace.py`. All 76 marks were recovered, 59 nice / 17 dull, none
skipped. **Null ceiling 0.632** — a signal that knows nothing reaches that one run
in twenty.

| signal | separation | |
|---|---:|---|
| **WorldCover built-up, inverted** | **0.728** | above null |
| `c_curves` | 0.705 | above null |
| **WorldCover tree fraction** | **0.705** | above null |
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
0.705 here against the 0.81 the review quotes, and the composite 0.694 against
0.71. My absolute numbers are not comparable to the review's; the ordering within
my own table is.

Two results matter:

**WorldCover tree fraction (0.705) matches the entire current composite (0.694)
on its own.** One raster column, no polygons.

**WorldCover built-up entered *negatively* (0.728) is the best single signal
here**, and it beats `c_urban` inverted (0.644) — a better measure of the same
"townness" concept. `c_urban` as currently used scores 0.356, i.e. it is
actively anti-predictive, confirming the review's finding at a second estimator.

Mean values by verdict make the mechanism plain:

| | WC tree | WC built |
|---|---:|---:|
| nice | 0.645 | 0.233 |
| dull | 0.388 | **0.460** |

### Three honest caveats on this section

1. **Collinearity.** WC built and WC tree are two sides of one coin
   (Spearman −0.859) — adding both is close to adding one. WC built vs `c_urban`
   is +0.449, vs the road-class adjustment −0.441: related to what the model has,
   but not a restatement of it.
2. **The within-road-class test is underpowered and I will not claim it.** Split
   by class, WC built inverted separates at 0.61–0.67 — but every one of those
   sits *below* its own null ceiling (0.74–0.84) because dull marks concentrate on
   big roads (12 residential and 13 tertiary marks are all "nice", zero dull). At
   n=17 dull I cannot show WC built adds anything beyond road class. I also cannot
   show it doesn't.
3. **All 76 marks are one driver, three drives, one corner of eastern
   Massachusetts.** Section 1 just established that scores do not transfer across
   these states. Neither does this validation.

---

## 4. Is Terrarium at zoom 11 good enough? **Yes. Close 3DEP.**

3DEP 1/3-arcsec (10.31 m measured) read over HTTP range requests, relief
recomputed the way `elevation.py` does it — max-minus-min over a 750 m **ground**
window, anisotropy handled — and compared with the shipped `relief.tif` at real
Massachusetts chunk midpoints.

| area | n chunks | Terrarium mean | 3DEP mean | ratio | **Spearman** | mean abs Δ`c_relief` | chunks moving >0.10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| western MA hills | 3,115 | 78.9 m | 87.0 m | 1.10 | **0.973** | 0.052 | 16.1% |
| eastern MA flat | 12,156 | 29.1 m | 32.3 m | 1.11 | **0.968** | 0.034 | 4.0% |

3DEP reads ~10% more relief — finer pixels catch more extreme local maxima and
minima, exactly as expected — but it reads it **almost perfectly monotonically**
with Terrarium in both hilly and flat terrain. A uniform +10% at rank correlation
0.97 is not new information; it is `RELIEF_FULL = 100.0` wanting to be 110, and
that constant is already fitted and explicitly out of scope.

**Recommendation: do not adopt 3DEP.** It would cost 483 MB per 1°×1° tile
(~14 tiles for New England, ~6.8 GB) and a much heavier mosaic to move
`c_relief` by 0.03–0.05. Terrarium is the right call and was the right call.

One observation in passing, not a proposal: 22.9% of western-MA chunks already
saturate at `c_relief = 1.0` under Terrarium (30.8% under 3DEP). In the hills the
component is at its ceiling. That is a `RELIEF_FULL` question and belongs to
whoever owns the calibration.

---

## 5. Candidate table

"Retrieved" = downloaded and parsed in this session. NE = covers all six New
England states.

| source | extent | NE? | license | **retrieved** | resolution | cost | pipeline stage | verdict |
|---|---|---|---|---|---|---|---|---|
| **ESA WorldCover v200** | global | ✅ | CC-BY 4.0 *(from registry YAML)* | ✅ **8 tiles, 343 MB, pixels read** | 10 m | NE 343 MB; CONUS 153 tiles ≈6.4 GB; sampling 314k chunks 9.3 s, peak RSS 2.83 GB | new raster stage beside `elevation.py`; new `c_` column | **ADOPT — §6** |
| Terrarium (incumbent) | global | ✅ | per-source attribution *(registry gives only a link to tilezen's attribution doc — it is a blend of SRTM/NED/others, each with its own terms; not verified further)* | ✅ tile, 131 KB | z11 ≈57 m | already paid | `elevation.py` | **KEEP** |
| USGS 3DEP 1/3″ | US | ✅ | public domain | ✅ **pixels read remotely** | 10.31 m | 483 MB/tile, ~6.8 GB NE | would replace `elevation.py` input | **REJECT — Spearman 0.97, §4** |
| NLCD 2021 | CONUS+ | ✅ | public domain | metadata only (50 KB XML) | **30 m** | 1,012 MB CONUS | same as WorldCover | **REJECT — 9× coarser than WorldCover for the same job, no NE-specific gain. The README's open item is answered by choosing WorldCover over NLCD.** |
| USDA Cropland Data Layer | CONUS | ✅ | public domain | listing only (504 MB `Content-Length`) | 30 m, annual | 504 MB/yr | could sharpen `c_farm` | **HOLD — `c_farm` is 1.9–9.7% of road-km and weight 0.06; smallest prize on the board** |
| USGS PAD-US 4 | US | ✅ | **Public Domain** *(item JSON)* | item JSON only | vector | not costed | `extract.py` polygon layer | **HOLD — it maps *designation*, which is the exact failure mode of `c_green`. Would deepen the bias, not fix it.** |
| NHD / NHDPlus HR | US | ✅ | public domain | listing only | vector | not costed | `extract.py` water layer | **HOLD — `c_water` already reaches 29–38% of road-km uniformly (see §1); no gap demonstrated** |
| Copernicus DEM GLO-30 | global | ✅ | "free basis for the general public" under COP-DEM terms *(registry, verbatim)* | listing only | 30 m | not costed | elevation | **REJECT — §4 says elevation is not the problem; a 30 m DEM cannot beat 3DEP's 10 m, which already failed to move the score** |
| USGS GAP land cover | US | ✅ | public domain | item JSON 200 | 30 m | not costed | land cover | **REJECT — superseded by WorldCover on resolution and by NLCD on currency** |
| Overture Maps | global | ✅ | ODbL / CDLA | listing only | vector | not costed | `extract.py` | **HOLD — buildings could inform `c_urban`, but its places/landuse are substantially OSM-derived, so it would re-import the same bias** |
| **NOAA C-CAP hi-res** | coastal states | ❌ | public domain | ✅ **listing read** | 1 m | — | — | **REJECT ON CONSTRAINT — listing has `ct, ma, me, ri` and **no `nh`, no `vt`**. Fails "all six states" outright.** |
| FHWA byway inventory | US | ? | ? | ❌ **403 on root — bot block** | ? | ? | ? | **UNVERIFIABLE.** Also largely redundant: the 54 OSM byway relations are already Phase 0b's job. |
| Google Dynamic World | global | ✅ | CC-BY | not attempted | 10 m | — | — | **EXCLUDED BY PRODUCT CLAIM** — Google-produced; README commits to "no Google/Apple data". Flagged because it is the obvious 10 m near-real-time alternative and someone will suggest it. |
| MassGIS | MA only | ❌ | open | not attempted | — | — | — | **OUT OF BOUNDS** by the review's own rule. Not needed: WorldCover served as the yardstick instead, and unlike MassGIS it works in all six states. |

---

## 6. Recommendation, ranked

### 1. Add a continuous WorldCover tree-cover fraction, and let it take over from `c_green`

The single highest-value change. It is the only candidate that addresses all
three defects at once: the 3.3× cross-state completeness spread (§1), the 26.3%
blind population (§2), and a measured 0.705 separation against a 0.632 null (§3).

**Continuous, not binary.** A ≥10% flag is true on 88.7% of Massachusetts
road-km and would carry almost no ranking signal. The fraction keeps real spread
— p25 0.25, p50 0.62, p75 0.94 — and that is what scored 0.705.

**How it tiles** (the review demands this, and it is where WorldCover wins).
WorldCover needs **no mosaic at all**. The kernel is a 9×9 box, so a chunk can be
sampled directly from the COG that contains it. Measured peak RSS is 2.83 GB and
that is *one 36000×36000 tile* — **it does not grow with the region.** Compare
`elevation.py`'s in-RAM float64 mosaic with `maximum_filter`/`minimum_filter` over
it, ~5× the array, already 5.6 GB for New England and the known wall past the
Northeast. This is the opposite cost shape. The tiles are COGs with overviews and
range requests (verified), so even the 343 MB download is optional.

**What it is not.** Tree fraction correlates with `c_green` at only ρ=+0.31.
This is not a drop-in improvement of the same measurement, it is a different
measurement. Shipping it changes what a score means.

**Full cost chain**, as the review insists it be stated: one new raster stage; one
`c_treecover` column; one `WEIGHTS` entry; a decision on whether `c_green` stays
alongside or is retired; **and if it becomes user-tunable, `router.py:164`
`BEAUTY_TYPES` plus `ios/Sources/BeautyType.swift` with the tests both sides that
assert the lists match.** Changing the composite forces the `BETA`/`PREF_CURVE`
sweep to be re-run, because they were co-fitted against the current scale.

### 2. `c_urban` is confirmed backwards, and WorldCover measures it better

Not a new finding that it is wrong — the review already established 0.35. What
is new: **WorldCover built-up fraction entered negatively separates at 0.728, the
best single signal I measured, against `c_urban`-inverted's 0.644.** OSM
`landuse=retail|commercial` cannot tell a village green from a strip mall;
built-up *fraction* does not try to, and gets the answer right anyway by
measuring how much of the roadside is built at all.

The data answer to "can anything fix `c_urban`" is yes. **The sign-and-weight
decision is not mine** — the review puts `WEIGHTS` out of scope — so this is
handed over, not applied. Note the collinearity (§3 caveat 1): WC built and WC
tree are ρ=−0.859, so this is likely *one* change with two framings, not two.

### 3. Change nothing about elevation

Terrarium z11 is adequate. 3DEP is closed with a number (Spearman 0.97), so
nobody re-surveys it. See §4.

### 4. Do not adopt anything else in §5

All either fail the six-state constraint (C-CAP), re-import the bias they would
be fixing (PAD-US, Overture), are coarser at the same job (NLCD, GAP, Copernicus),
address the smallest component on the board (CDL), or cannot be retrieved (FHWA).

---

## 7. What cannot be settled without a prototype

1. **Whether swapping `c_green` for tree fraction actually improves the
   composite.** §3 measures signals *individually*; the composite is a weighted
   blend whose constants were co-fitted to the current components. Needs: the new
   column built region-wide, `WEIGHTS` re-fitted, the `BETA`/`PREF_CURVE` sweep
   re-run, then separation re-measured. Roughly one pipeline rebuild plus one
   sweep.
2. **Whether any of this transfers outside eastern Massachusetts — the real
   blocker.** All 76 marks are one driver, three drives, one corner of one state,
   and §1's whole finding is that these states are not interchangeable. **The
   cheapest thing that would settle the most is marks from a second state** —
   ideally Maine, the 0.24-completeness extreme. Twenty marks each way in Maine
   would test the uniformity claim and the WorldCover claim in a single drive.
   Nothing in this document is worth shipping region-wide before that exists.
3. **Whether WorldCover built-up beats road class, or is road class.** §3
   caveat 2: underpowered at 17 dull marks. Needs more dull marks specifically —
   they concentrate on motorway/primary/secondary, and residential/tertiary
   produced zero.
4. **WorldCover's 2021 vintage against a 2026 road network.** Forest and water
   move slowly, built-up does not. Un-assessed. NLCD Annual (1985–2024, 30 m) is
   the currency-vs-resolution trade if this turns out to matter.
5. **`CRS_METERS = 26986`** is NAD83 / Massachusetts Mainland. Per the review,
   ≤0.31% scale error anywhere in New England — it does not block this work. It
   does bear on "a much larger part of the United States", and WorldCover being
   global makes that ambition more concrete rather than less. Not fixed here.

---

## 8. Reproducing this

Scripts are standalone and read only `data/raw/`, `data/processed/` and
`traces/`. None of them import or modify anything in `pipeline/`; the constants
they need (`DIST`, `MIN_AREA`, `DRIVABLE`, `CHUNK_LEN`, `RELIEF_WINDOW_M`,
`RELIEF_FULL`) are copied literally from `score.py`, `common.py` and
`elevation.py` at commit `415e0ac`.

| script | what it produces | runtime |
|---|---|---|
| `state_coverage.py` | §1 per-state table, from a state PBF | 24–136 s/state |
| `wc_check.py` | §1 ratio table, §2 no-polygon table | ~3 min, six states |
| `relief_check.py` | §4, via 3DEP HTTP range reads | ~40 s |
| `marks_wc.py`, `marks_compare.py` | §3, imports `analyze_trace.py` | seconds |

They live in this session's scratch directory rather than in `tools/`, because
the review scoped this task to a document. If §6.1 goes ahead, `wc_check.py` is
the thing to promote — its sampling loop is the proposed pipeline stage.

**One bug worth recording**, since it would have silently produced a wrong
answer: WorldCover tiles are named for their **south-west** corner, so the tile
index is `floor(lon/3)*3`, not `ceil`. With `ceil` every point resolves to the
tile 3° east, lands outside its bounds, gets clipped to an edge column, and
returns plausible-looking garbage — the first run of §1's ratio table was wrong
this way and read as merely surprising. It was caught by a bounds assertion, and
the fix was verified by checking five corner points of the region against the
real tile bounds and against the published `esa_worldcover_grid.geojson`. Any
promoted version must keep that assertion.
