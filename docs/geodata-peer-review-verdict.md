# Peer review verdict: the geodata findings

Adversarial review of `docs/geodata-sources-findings.md`, against the brief in
`docs/geodata-peer-review-brief.md`. Measured 2026-08-28. **No file outside
`docs/` was modified**; nothing in `pipeline/` was touched. The test suite was
run, not changed (`tests/test_calibration.py`, 33 passed, on the shipped data).

---

## Verdict

**Make variant D — conditional on the rollout, as the findings already say, and
with four amendments below.** The uniformity argument in §1 is correct. I
attacked it four ways, including one control the findings never ran, and every
attack made it stronger rather than weaker. I could not break it.

What does **not** survive is the last step of the inference — that §1 selects
*this* variant:

1. **D does not make the Phase 4 re-fit safe, and the doc does not say so.** It
   takes the non-uniform share of composite weight from 37.7% to **29.8%**. It
   removes 8 of the 38 points. `c_urban`, `c_farm` and `c_views` are untouched.
2. **The 0.09/0.09 split is not derived from the criterion the doc says should
   decide.** On uniformity — and on calibration — the family is monotone: more
   tree weight is strictly better all the way to variant A. What stops the doc
   at half is disruption, which is the accuracy-adjacent axis it says shouldn't
   decide. D is a defensible choice; it is a *choice*, not a derivation.
3. **§2's headline comparison is against the wrong population** and its sign
   reverses when corrected. The conclusion it supports survives; the sentence
   does not.
4. **"Zero iOS changes" is true of the code and false of the product**, and the
   cost table misses a second hardcoded component list and a distribution guard
   that lands 4% from tripping in Maine.

None of these overturn "add a uniformly-produced tree-cover component alongside
`c_green`". They change what the doc should claim for it.

Three things the doc left open, I closed — all in its favour: the 2021 vintage
(§8.4), the fairness of the ratio estimator (brief doubt 1), and the ≥10%
threshold (brief doubt 2). One thing I went hunting for a failure in — the half
of `calibration_report()` §7.2 skipped — passes, with **more** margin than the
shipped scoring.

---

## 1. What I reproduced, exactly

Independent scripts, written from `score.py`/`common.py`/`extract.py` constants
read at `415e0ac`, importing nothing from `pipeline/`. These are the "I re-ran
this and got X" numbers.

| claim | findings doc | mine | |
|---|---|---|---|
| measurement base | 951,182 chunks, 239,204 km | **951,182 chunks, 239,203 km** | exact |
| per-state road-km | 60,080 / 28,562 / 32,157 / 39,712 / 67,806 / 10,886 | **identical, all six** | exact |
| WorldCover NE tiles | 8 tiles, 343 MB | **8 tiles, 343 MB** (9 probed, `N39W069` is a real 404) | exact |
| §1 green completeness ratio | ME 0.24 · VT 0.45 · NH 0.38 · CT 0.39 · MA 0.36 · RI 0.80 | **0.24 · 0.45 · 0.38 · 0.39 · 0.36 · 0.80** | exact |
| §1 WorldCover tree ≥10% | 89.8 / 86.5 / 91.4 / 91.1 / 88.7 / 82.1 | **89.8 / 86.4 / 91.4 / 91.1 / 88.7 / 82.1** | ±0.1 |
| §0 MA no-polygon, chunk-level | 26.98%, 18,311 km | **26.98%, 18,311 km** | exact |
| §6 tree-fraction spread | p25 0.25, p50 0.62, p75 0.94 | **0.25 / 0.62 / 0.94** | exact |
| §7.3 variant D disruption | ρ 0.953, 20% of km moving >1 pt | **ρ 0.956, 18.2%** | ~ |
| §7.2 variant D scale | p50 4.46, pinned 3.10% / 0.24% | **p50 4.4, 3.01% / 0.25%** | ~ |

Two structural checks passed on the way: `blend()` of the stored `c_` columns
equals the stored `raw` to 2.2e-16, and `composite(raw, score_adj)` equals the
stored `score` for all 313,791 chunks — so everything below is computed on the
same arithmetic the pipeline shipped.

**I did not re-derive §3, §4 or §6's separation numbers.** §3 concedes its own
limits and §4's Spearman 0.97 is not a result an attack on §1 needs.

One reconciliation, so nobody re-finds it: my first pass ran +2.7 to +4.6 pp
above the doc on WorldCover tree prevalence. That is entirely the sampling
window. The doc's "90 m box" is a fixed **9×9 pixel** window, which at these
latitudes is about **83 m × 62 m**, not 90 × 90 — WorldCover pixels are
1/12000° and a degree of longitude is short up here. My latitude-corrected box
(11×13 to 11×15 px) is genuinely ~100 m square. Re-run at a fixed 9×9 the two
agree to 0.1 pp on every state. **The anisotropy is immaterial to §1** — the
ratio spread is 3.28× at 9×9 and 3.21× ground-corrected — but it is *not*
immaterial to §4 below.

Both traps held. `graph_edges` vs `scored_chunks` is a unit mismatch, not a bug;
I worked chunk-level throughout and got 26.98%. I made no comparison across the
two separation estimators. The tile index is `floor`, and every sampler carried
a hard bounds assertion; all passed, no point was clipped to an edge.

---

## 2. The uniformity argument: four attacks, all failed

### 2a. Is the OSM ÷ WorldCover ratio a fair comparison? (brief doubt 1) — **yes, and the residual bias is conservative**

The worry is real: the numerator is a polygon within 80 m of a ≤400 m *line*,
the denominator a box at the midpoint. A straight 400 m chunk buffered at 80 m
sweeps ~84,000 m²; the box is ~8,100 m². The ratio only travels between states
if that ~10× bias is state-invariant. Measured, per state, length-weighted:

| state | mean chunk (m) | km-wtd chunk (m) | km-wtd sinuosity | km-wtd buffer footprint (m²) |
|---|---:|---:|---:|---:|
| Maine | 297.7 | 340.0 | 1.006 | **74,503** |
| Vermont | 289.9 | 337.6 | 1.010 | 74,119 |
| New Hampshire | 264.3 | 321.1 | 1.012 | 71,476 |
| Connecticut | 240.9 | 301.8 | 1.008 | 68,391 |
| Massachusetts | 215.7 | 284.5 | 1.008 | 65,632 |
| Rhode Island | 217.6 | 284.9 | 1.004 | **65,689** |

The brief's suspicion is half right. Maine's chunks *are* longer, and its
footprint is **13.5% larger** than Massachusetts' or Rhode Island's. Sinuosity is
flat — 1.004 to 1.012 — so "Maine's roads are straighter" is empirically nil.

But the direction is the opposite of the worry. A larger swept footprint makes
the numerator *more* likely to find a polygon, so the geometry **inflates**
Maine's green credit relative to Rhode Island's. Deflating first-order, Maine's
0.24 becomes ~0.21 and the spread widens from 3.2× to ~3.6×. **The estimator's
residual bias flatters the state the finding indicts.** 13.5% against a 3.2×
effect, pointing the safe way.

### 2b. Is ≥10% tree too low a bar? (brief doubt 2) — **yes, and that is the conservative choice**

The criticism is sound as stated — 82–91% of road-km clears ≥10% everywhere, so
that denominator *is* nearly saturated. It just does not do what the criticism
assumes. Rebuilding the completeness ratio at every stricter denominator:

| denominator | WorldCover spread | **completeness-ratio spread** |
|---|---:|---:|
| ≥10% tree *(the doc's)* | 1.09× | **3.21×** |
| ≥25% tree | 1.18× | 3.47× |
| ≥50% tree | 1.37× | 4.11× |
| ≥75% tree | 1.70× | 5.11× |
| mean tree fraction (continuous) | 1.31× | 3.93× |

Every stricter or continuous denominator makes the non-uniformity look **worse**.
The doc picked the denominator that *minimises* its own headline number and still
got 3.2×. The doc's stated counter (median tree fraction varies sensibly,
RI 0.42 → ME 0.83) is sufficient, but it is not the strongest available — this
table is, and it is not in the doc.

### 2c. The negative control the findings never ran — **the estimator does not manufacture spread**

This is the test I most expected to break §1, and the one I think the doc most
needed. If you divide two differently-measured quantities you can get spread out
of nothing. The doc calls `c_water` uniform at 1.3× — but that is *raw OSM
prevalence*, not a completeness ratio. The one polygon layer declared safe was
declared safe on reasoning ("a lake is a lake everywhere"), never measured with
the instrument used to condemn the others. So I ran it:

| state | OSM water (doc §1) | WC water ≥10% (mine) | **ratio** |
|---|---:|---:|---:|
| Maine | 32.5 | 1.6 | 20.1 |
| Vermont | 31.7 | 1.5 | 21.1 |
| New Hampshire | 38.4 | 1.4 | 28.2 |
| Connecticut | 29.3 | 1.0 | 30.6 |
| Massachusetts | 32.4 | 1.3 | 25.4 |
| Rhode Island | 32.6 | 1.4 | 23.1 |

**Water completeness-ratio spread: 1.52×. Green's: 3.21×.** Run the identical
machinery on the layer the doc says is fine, and it comes back fine — a spread
roughly in line with the raw OSM water spread (1.31×) and less than half green's.

The absolute level (~25, not ~1) is meaningless and expected: OSM credits
`waterway=river|canal` *lines* out to 120/350 m with no area floor, while
WorldCover class 80 cannot see a river narrower than its pixel. Only the spread
is the test, and it passes. Caveat, stated: water's numerator uses a wider `DIST`
than green's, so this is the same family of operation rather than the identical
one.

This is the single strongest piece of evidence for §1 and it belongs in the doc.

### 2d. n=6 (brief doubt 3) — **the doc leans on the right column**

The Spearman of −0.60 at p=0.21 is worthless on its own and the doc says so. The
per-state ratio column is not a second presentation of the same six points: each
entry is a direct measurement over 10,886–67,806 km with negligible sampling
error. The n=6 objection applies to the rank correlation, which the doc
explicitly does not rest on. Its uncertainty is *systematic* — is the estimator
state-invariant — and that is 2a and 2c, both of which it survives.

---

## 3. Claims I broke

### 3a. D does not make the re-fit safe — the residual is 29.8%, and the doc never states it

§1's own accounting: green 0.18 + urban 0.14 + farm 0.06 + views 0.05 = 0.43 of
1.14 = **37.7%** of composite weight on non-uniform inputs. Under variant D that
becomes **0.34 / 1.14 = 29.8%**.

`c_urban` (weight 0.14, completeness spread 2.3×, and the one component
*measured to point the wrong way*), `c_farm` (0.06, completeness spread 5.9× —
the worst on the board) and `c_views` (0.05, pure designation by construction)
are all untouched. §1's closing line is "Variant D … buys uniformity by
construction." That is true of the 0.09 it moves and reads as true of the
problem. **If the decision criterion is "make Phase 4 safe", D does not meet it**
— it is the first of at least three changes, and the doc should say so.

### 3b. The 0.09/0.09 split does not follow from §1 (brief doubt 4)

Measured across the whole family, holding green + tree = 0.18:

| green / tree | non-uniform weight | % of total | p50 | p99 | %km@0 | %km@10 | ρ vs shipped | %km moving >1 pt | scenic−interstate gap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.180 / 0.000 *(shipped)* | 0.43 | 37.7% | 4.0 | 9.2 | 3.33 | 0.32 | 1.000 | 0.0 | 4.73 |
| 0.135 / 0.045 | 0.39 | 33.8% | 4.2 | 9.1 | 3.17 | 0.26 | 0.990 | **0.0** | 4.94 |
| **0.090 / 0.090 (D)** | 0.34 | **29.8%** | 4.4 | 9.1 | 3.01 | 0.25 | 0.956 | 18.2 | 5.13 |
| 0.060 / 0.120 | 0.31 | 27.2% | 4.6 | 9.1 | 2.94 | 0.26 | 0.919 | 36.7 | 5.25 |
| 0.045 / 0.135 | 0.29 | 25.9% | 4.7 | 9.2 | 2.92 | 0.27 | 0.896 | 42.2 | 5.31 |
| 0.000 / 0.180 *(A)* | 0.25 | 21.9% | 4.9 | 9.4 | 2.84 | 0.39 | 0.813 | 52.8 | 5.47 |

Uniformity is **monotone** in tree weight. Calibration does not stop you either:
p99 holds at 9.1–9.2 throughout, `%km@0` falls monotonically, and p50 crosses
`score.py`'s stated 4.5 target *between* D (4.4) and 0.06/0.12 (4.6). On the two
axes the doc says should decide, nothing distinguishes D from a heavier tree
weight — the criterion recommends A.

What actually stops the doc at half is **disruption** (ρ and %km moved) and
separation. That is the accuracy-adjacent axis §3 argues cannot settle anything.
The same pattern governs the rejection of variant F: F would address `c_urban` —
the second-largest non-uniform weight and the only component measured backwards
— and is rejected for moving 74% of road-km. **A disruption argument is being
used to reject the more-uniform option in a document whose thesis is that
uniformity should decide.**

This does not make D wrong. It makes the stated decision rule incomplete. The
real rule is: *§1 establishes that a change is warranted; disruption tolerance
and the review's "WEIGHTS are out of scope" constraint pick which change.* Both
halves are legitimate. Stating only the first makes D look derived when it is
chosen, and it hides the trade the product owner is actually being asked to make.

One consequence worth putting on the board: **0.135 / 0.045 moves 0.0% of
road-km by more than a point** (ρ 0.990) and still takes the non-uniform share
from 37.7% to 33.8%. If disruption is the binding constraint — and the table
says it is — that is a strictly better uniformity-per-disruption trade than D,
and it is not among the variants the doc considered.

### 3c. §2's headline comparison is against the wrong population, and its sign reverses

§2, bolded: the blind population is *"on average slightly more wooded than the
population the model does credit (0.66 vs 0.62)"*. But "the population the model
does credit" pools green credit with water, coast, farm and urban credit. At the
shipped Massachusetts chunks:

| population | % of MA km | mean WorldCover tree |
|---|---:|---:|
| no polygon at all | 27.0% | **0.678** |
| any polygon *(the doc's comparator)* | 73.0% | 0.642 |
| `c_green > 0` *(like-for-like)* | 31.4% | **0.720** |
| polygon but no green | 41.6% | 0.584 |

The comparator is dragged down by the 41.6% of km credited for something other
than green, which averages 0.584. Against the green-credited population the
blind roads are **less** wooded, 0.678 vs 0.720 — the opposite of the claim as
written.

The substantive finding is untouched and is better stated without the flourish:
blind roads are nearly indistinguishable from the roads OSM *does* call green
(0.68 vs 0.72), and clearly more wooded than the roads it credits for something
else (0.58). **"Under-mapped, not featureless" holds. "More wooded than the
credited population" does not.** (Incidentally: `c_green > 0` and
`c_green >= 1.0` select the identical 31.4% of km — green is binary in practice,
with no partial-credit band.)

### 3d. "Zero iOS changes" is true of the code and false of the product

The code claim **verifies**. `BEAUTY_TYPES` maps an api-name to a column
(`pipeline/router.py:164`), and the two mirror tests assert only the label list
(`tests/test_routing.py:28`) and the api-name set (`:38`) — neither touches
columns. Adding `("c_treecover", WEIGHTS["treecover"])` to `BASELINE`
(`router.py:181`) breaks neither, and
`test_neutral_weights_reproduce_the_precomputed_score` (`:1013`, the tripwire
§7.1 correctly identifies) still holds because all-1.0 weights remain a fixed
point. §7.1's reasoning about `BASELINE` being the right home is right.

What the cost table books at zero is a change in what the tune screen means.
`Router._edge_scores` renormalises the user's weights onto
`DEFAULT_WEIGHTS.sum()` — the total *tunable* mass. Moving 0.09 out of
`c_green` and into `BASELINE` moves it out of that pot:

| | BEAUTY_TYPES mass | BASELINE mass | untunable share | `forest` share of tunable mass |
|---|---:|---:|---:|---:|
| shipped | 0.89 | 0.25 | 21.9% | 20.2% |
| variant D | 0.80 | 0.34 | **29.8%** | **11.2%** |

`_edge_scores`'s own docstring says a weight of 0 means the user "ignores" that
type. Under D, setting **forest to 0 no longer ignores forest** — half of
forest-ness is permanently on for every user, and the `forest/park` slider loses
44% of its relative pull. That may well be the *right* design: tree cover is a
substance signal and `BASELINE` is where "always on, not user-tunable" quality
floors live, next to `c_curves`. But it is a product decision, and here it
arrives as a side effect of choosing the cheap implementation. The alternative —
a seventh `BEAUTY_TYPES` row — keeps the slider honest and costs exactly the iOS
work the doc is avoiding. **Name the trade; don't book it at zero.**

Note the 29.8% in this table and the 29.8% in §3a are different quantities that
coincide.

### 3e. Two things the cost table and §7.1 miss

**A second hardcoded component list.** §7 costs "`tests/test_calibration.py`
`COMPONENTS` list (line 18), ~2 lines". But
`test_calibration.py:77` (`test_score_matches_components`) carries its *own*
`key` dict at lines 82-85 mapping every `c_` column to its `WEIGHTS` name. Add `c_treecover` to
`WEIGHTS` and to the data without touching that dict and the test recomputes
`raw` short by `0.09 * tree` and fails. One more line — but it is precisely the
class of silent-disagreement tripwire §7.1 exists to enumerate, and §7.1 missed
it. (`tests/test_graph.py` derives `ALL_COMPONENTS` from `WEIGHTS` and needs
nothing; `test_scoring.py:91` uses `sum(WEIGHTS.values())`, which D leaves at
1.14 exactly — variant E would move it.)

**A distribution guard that nearly trips in Maine.** `test_calibration.py:46`
asserts `share_of_km(component >= 0.999) < 0.35`. Measured with the doc's own
9×9 box:

| state | km-share at tree = 1.00 | headroom to 0.35 |
|---|---:|---:|
| Rhode Island | 0.149 | 0.20 |
| Massachusetts | 0.198 | 0.15 |
| Connecticut | 0.230 | 0.12 |
| Vermont | 0.281 | 0.07 |
| New Hampshire | 0.293 | 0.06 |
| **Maine** | **0.335** | **0.015** |

The guard is 4% from tripping **in the state the change exists for**, and the
share rises monotonically with how forested a state is. Add `c_treecover` to
`COMPONENTS` as §7 proposes and this is the assertion that breaks first on the
rollout. Two things make it worse than it looks: a *smaller* sampling box raises
the pinned share, and the doc's 9×9 is already smaller in ground terms than the
90 m it is described as.

The mitigation is free and already measured: the latitude-corrected ~100 m box
(11×15 px) drops Maine to **0.258** while leaving §1 untouched (ratio spread
3.21× vs 3.28×). Use the ground-correct box. This is the one place in the review
where the 9×9 anisotropy actually costs something.

Related, and worth knowing before shipping north: tree fraction is a weaker
*discriminator* where it is most needed. Length-weighted IQR runs 0.70 in Rhode
Island, 0.64 in Massachusetts, **0.56 in Maine**, with a third of Maine's
road-km at exactly 1.00. §6's "p25 0.25, p50 0.62, p75 0.94" is the
Massachusetts distribution; Maine's is 0.38 / 0.83 / 1.00. For the *uniformity*
argument this is fine — raising Maine's level toward Rhode Island's is the whole
point. For within-Maine ranking, a third of the network gets a constant.

---

## 4. Where I attacked and failed — the half of `calibration_report()` §7.2 skipped

§7.2 checks percentiles and pinned shares and concludes "no recalibration
needed". `calibration_report()` prints more than that, and
`TestBenchmarkRoads` **asserts** on it: *"scenic byways beat the interstates"*,
*"the Mass Pike scores poorly"*, *"motorways score below ordinary roads"*.

My hypothesis was that this is where D breaks. `c_green` credits *designation*,
so an interstate through the woods gets nothing; WorldCover credits *substance*,
and a 90 m box around a highway is mostly the trees beside it. D should
therefore hand the interstates a gift and compress the gap the guard protects.

**Wrong, and measurably so.** Massachusetts motorway/trunk road-km averages
WorldCover tree **0.437** against **0.669** for the rest of the network —
highways here are *less* wooded than the roads around them, not more.

| road | km | `c_green` | WC tree | shipped | **D** | A | E |
|---|---:|---:|---:|---:|---:|---:|---:|
| Greylock Notch/Rockwell | 31 | 0.86 | 0.92 | 6.55 | **6.62** | 6.68 | 7.53 |
| Jacob's Ladder Trail | 33 | 0.19 | 0.72 | 4.60 | **5.16** | 5.74 | 5.37 |
| Mohawk Trail | 81 | 0.58 | 0.76 | 5.12 | **5.31** | 5.51 | 5.90 |
| Route 6A (Old King's Hwy) | 82 | 0.32 | 0.62 | 5.24 | **5.56** | 5.88 | 5.90 |
| I-90 (Mass Pike) | 447 | 0.34 | 0.45 | 0.63 | **0.57** | 0.58 | 0.87 |
| I-95 | 292 | 0.52 | 0.39 | 0.67 | **0.49** | 0.39 | 0.91 |
| **scenic − interstate gap** | | | | 4.73 | **5.13 (+8.6%)** | 5.47 | 5.29 |

Every scenic benchmark rises, both interstates fall, and all three benchmark
assertions plus both `TestScoreScale` assertions pass with **more** margin than
the shipped scoring. **§7.2's conclusion survives the part of the report it did
not check.**

Two things fell out of this that are worth keeping:

- **Jacob's Ladder Trail is §2's thesis on a road `score.py` itself names as a
  scenic benchmark**: `c_green` 0.19 — almost no designated land — against
  WorldCover tree 0.72, and it is the single biggest mover under D (+0.56). It is
  a better illustration of the finding than the three unidentifiable roads §2
  had to give up on.
- **Variant E does break the calibration**, which the doc does not report. E
  raises `%km` pinned at 10.0 from 0.32% to **1.03%** and p99 to 10.0, because it
  adds weight without removing any (`WEIGHTS` sum 1.14 → 1.23). The doc names B,
  C and F as calibration-breakers and leaves E's status open; E belongs on that
  list, which is one more reason for D over E.

*(One false start, recorded so it isn't re-found: I-90's mean score falls while
its mean `raw` rises. Not a bug. The 0-clip is asymmetric — on the Pike the
chunks where WorldCover exceeds `c_green` are the ones already pinned at 0 by the
−0.45 motorway penalty, so their gain is clipped away, while the chunks above the
floor carry the losses. Per chunk the transform is monotone; the mean need not
be.)*

---

## 5. What I closed that the doc left open

**§8.4, the 2021 vintage against a 2026 road network.** Sampling WorldCover 2020
v100 and 2021 v200 at the same 313,791 Massachusetts midpoints — one full epoch
*plus* a complete algorithm revision, so an upper bound on one year's drift:

| class | 2020 | 2021 | Δ | mean per-chunk \|Δ\| | ρ |
|---|---:|---:|---:|---:|---:|
| tree | 0.6720 | 0.6519 | −0.0200 | 0.046 | 0.978 |
| built | 0.1859 | 0.2217 | **+0.0359** | 0.047 | 0.968 |
| water | 0.0037 | 0.0040 | +0.0003 | 0.002 | 0.775 |

Propagated through variant D, the two epochs produce scores differing by a mean
of **0.049 points**, with 0.04% of road-km moving more than half a point and
**ρ = 0.9990** — against ρ = 0.956 for the shipped→D change itself. **The vintage
uncertainty is ~40× smaller than the change being proposed.** For tree cover the
open item can be closed: it does not matter.

For built-up it is not closed, and the doc's instinct was right — built rose
**19% relative in one step**. That is a reason to be *more* cautious about
variant F than the doc is, on top of the 74%-of-km disruption it already cites.

---

## 6. What I could not check

- **§3 and §6's separation numbers.** Not re-derived — deliberately. The doc
  concedes SE 0.080 and that all variants sit inside one SE; re-running it would
  produce the same non-answer. My review takes the doc at its word here, which is
  safe because it is a concession against interest.
- **§4, Terrarium vs 3DEP.** Not re-derived. Spearman 0.97 across two terrains is
  not a result that an attack on §1 needs, and the "close 3DEP" conclusion does
  not depend on the disputed inference. I read it and found nothing to challenge.
  Its one loose end — 22.9% of western-MA chunks already saturating `c_relief` —
  is correctly handed to whoever owns `RELIEF_FULL`.
- **Whether D actually improves the composite** (§8.1). Still needs the
  prototype. My §4 table above narrows it usefully — D moves the benchmark
  ordering the right way on 966 km of named road — but that is not the same as a
  fitted composite measured on marks.
- **Retrievability of anything the doc could not fetch.** I proposed no new
  source, so I ran no new retrievability probes beyond WorldCover itself
  (8 of 9 candidate NE tiles return 200 at the sizes claimed; `N39W069` is a real
  404, and a nonsense key on the same host also 404s, so absence there is
  absence). The FHWA question stays open and I make no claim about it.
- **Whether the rollout is real.** Not mine. See below.

---

## 7. Are the two arguments correctly separated?

**Yes, and it is the doc's best structural move.** §1 is a statement about input
data measured directly from six PBFs and a raster, and it needs no ground truth;
§3 rests on 76 marks from one driver in one corner of one state and cannot
settle anything at that n. Resting the decision on the first is right, and the
doc is right that "within noise, therefore do nothing" would be a non-answer.

The separation is also honestly maintained — §3 caveat 3 volunteers that the
validation is as un-transferable as the states are, which is the argument against
its own evidence.

**But the recommendation does not follow from the argument it is rested on — not
all the way.** §1 supports:

> *Add a uniformly-produced land-cover input alongside the designation-based
> ones, and do not run Phase 4's re-fit on the current inputs.*

It does not support the 50/50 split (§3b: monotone, and the criterion prefers A),
and it does not support "buys uniformity by construction" (§3a: 29.8% remains).
The gap is filled, invisibly, by disruption tolerance. That is a reasonable thing
to fill it with — I would fill it the same way — but the doc should say it is
doing so, because a reader who accepts "decide on §1" will not notice that §1
stopped being the criterion two paragraphs before the recommendation.

**Is the §7.4 conditional correctly drawn?** Yes. Massachusetts-only kills §1's
force — a roughly constant intra-state mapping bias really is absorbed by
`RAW_BASE`/`STRETCH` — and leaves §2 plus an unprovable accuracy gain, which the
doc correctly calls weak. Regional makes it structural. I would only sharpen both
ends: if the rollout is real, D is *the first of at least three changes*, not the
fix; if it is not, the honest recommendation is to do nothing rather than to do D
cheaply.

**One alternative the doc never costs.** §1's grievance is that Phase 4 wants
7/10 to mean the same in Stowe as in Sudbury, and that a region-wide re-fit would
average the bias. It never considers a **per-state** fit of `RAW_BASE`/`STRETCH`,
which removes the between-state level shift at *zero* pipeline cost — the same
mechanism §7.4 itself invokes when it says a constant intra-MA bias is absorbed
by calibration. The doc has the rebuttal and never assembles it: per-state
calibration cannot touch the *within*-state distortion, and §2 measures that at
27% of Massachusetts road-km scored blind on land that looks like the land OSM
does credit (0.68 vs 0.72). §1 and §2 together rule the alternative out; neither
does alone, and the doc never joins them. The cheapest competing option therefore
goes unrejected on the page, which is the gap most likely to be raised by whoever
has to approve the pipeline stage.

---

## 8. Amendments, if D is made

1. **State the residual.** "Takes the non-uniform share of composite weight from
   38% to 30%" replaces "buys uniformity by construction". Name `c_urban` and
   `c_farm` as the remainder, and D as the first of several.
2. **Use the latitude-corrected ~100 m sampling box**, not a fixed 9×9 pixel
   window. Free, leaves §1 unchanged, and takes Maine's ceiling-guard share from
   0.335 to 0.258 — off the edge of a guard that is otherwise 4% from tripping in
   the state the change is for. Keep the `floor` bounds assertion §9 already
   demands.
3. **Cost the two things §7 misses**: `test_calibration.py`'s second hardcoded
   `key` dict (lines 82-85), and the tunable→untunable weight shift — either
   accept it explicitly as a design decision or pay for the seventh
   `BEAUTY_TYPES` row.
4. **Put 0.135/0.045 on the variant board**, and label 0.09/0.09 as
   disruption-limited rather than derived. If the product owner's real constraint
   is "don't re-rank the map", 0.135/0.045 buys 4 of the 8 uniformity points for
   0.0% of road-km moving more than a point.

And two corrections to the text regardless of the decision: §2's "slightly more
wooded than the credited population" should be the like-for-like comparison
(0.678 vs 0.720, less wooded — the conclusion is unaffected), and variant E
should join B, C and F on the calibration-breaking list.

---

## 9. Reproducing this

Standalone scripts in this session's scratch directory, reading only
`data/raw/`, `data/processed/` and eight WorldCover tiles fetched over HTTP.
Nothing imports from `pipeline/`; constants are copied literally from `score.py`,
`common.py` and `extract.py` at `415e0ac`, the same commit the findings used.

| script | produces |
|---|---|
| `state_chunks.py` | six states → drivable ways → 400 m chunks → midpoints, lengths, chords |
| `wc_sample.py` | latitude-corrected ~100 m WorldCover box per midpoint, with bounds assertions |
| `win9.py` | the same at a fixed 9×9 px box — reproduces §1 exactly |
| `analyse1.py` | §2a footprint table, §2b threshold table, §2c water control |
| `calib.py` | §4 benchmark roads and the full scale block |
| `analyse2.py` | §3a/3b split family, §3c populations, §3d tunable mass |
| `guards.py` | §3e ceiling guard, §5 vintage |
| `spread.py` | per-state tree-fraction distributions |

The 343 MB of 2021 tiles and 180 MB of 2020 tiles were deleted after use; disk
was at ~99% throughout and nothing was left behind.
