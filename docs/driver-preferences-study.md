# What else should be a driver preference, and what shape should preferences take

**Status: measured 2026-08-29, nothing changed.** No source file, no constant,
no test was touched. Every measurement below was made by scratch scripts outside
the repository that monkeypatch `Router._edge_scores` / `Router._weights` in
memory, the same way `pipeline/scenery_cap_experiment.py` does for `BETA` — see
*Reproducing this* at the end.

`SCENIC_DATA=<abs>/data/processed-ne pytest tests/` is green — **294 passed, 0
skipped** — run from a worktree holding `main` plus this file and nothing else.
It was run there deliberately: the main checkout's `score.py`, `router.py`,
`looper.py`, `graph.py` and `server/app.py` were all modified partway through
the afternoon by the session working `docs/unpaved-and-urban-brief.md`, so a
test run there would report on their work in progress rather than on mine.
Nothing in this study depends on those edits, and none of the measurements
below were taken after them: the last route sweep finished at 15:59 and the
first of those files changed at 16:04.

Measured against the New England build now serving live (`data/processed-ne`,
942,448 chunks / 236,477 km, 998,252 graph edges, built 2026-08-29 14:33).

---

## The question, and the answer in three lines

Scenic exposes six scenery preferences. The owner's proposal is that judgements
now frozen into constants are really *preferences* — that "is a dirt road
beautiful" is unanswerable while "do you want dirt roads" is answerable by the
person asking.

**Road size qualifies, decisively, and for a sharper reason than the one that
motivated the question.** It is not that back roads are prettier. It is that the
scenery model *cannot see road size at all* — raw beauty is flat to within 0.19
points of 10 across every road class — while the driver's verdict tracks it at
0.76. It is a genuinely separate axis, and separate axes must not share a
weight pot.

**Twistiness qualifies and is nearly free**, but only in the direction nobody
asked for.

**The mechanism should be a third term in the routing cost, not a seventh
attraction and not an offset inside `score_adj`.** The recommended default is
*off*, and §8 says why that is the finding rather than a failure to finish.

---

## 1. What exists today, in three tiers

**Tunable attractions** — `pipeline/router.py:164` `BEAUTY_TYPES`: water, coast,
forest, hills, farmland, town. Sent per request as `w_<type>`, clamped to 0..4
by `server/app.py:57` and offered as 0..2 by the app
(`ios/Sources/BeautyType.swift`). Default 1.0.

**Fixed attractions** — `pipeline/router.py:181` `BASELINE`: `c_curves` (0.13),
`c_views` (0.05), `c_scenic_tag` (0.07). Always on, no user control.

**Fixed penalties** — `pipeline/score.py:290`, `score_adj = CLASS_ADJ +
UNPAVED_ADJ`, applied outside the blend after the stretch. Can only subtract.

The renormalisation in `Router._edge_scores` holds the total tunable weight
constant so `pref` alone sets strength. It is load-bearing and §6 works through
what each candidate mechanism does to it.

### 1a. Numbers in the previous draft of this study that have moved

`data/processed-ne` was rebuilt at 14:33 today, after the landcover merge
(`fde75f7`, blended `c_forest`) and after `536ca38` (byways from OSM relations).
The separations in the earlier draft predate that build. Re-measured against
what is actually serving:

| claim in the previous draft | re-measured today |
|---|---|
| whole scenery model separates at **0.71** | **0.739** over all 79 marks (0.722 over the 76 from 2026-08-25) |
| road-class rank separates at **0.74** | **0.757** |
| Spearman(score, class rank) = **0.40** | **0.531** over all marks — but **0.074** once motorway and trunk are excluded |
| `c_curves` is the best component at **0.81** | **0.678** overall; 0.791 on non-motorway marks. `c_forest` is now the best component at **0.786** |
| combined **0.88** on non-highway marks | **0.895** |
| **79 marks, three drives 2026-08-25** | the three 08-25 drives give **76**; 79 is the pool of all twelve traces (2 dull + 1 nice come from 2026-08-22) |
| **8 of 8** residential marks were nice | **9 of 9** |
| **16 of 16** tertiary marks were nice | confirmed, 16 of 16 |
| resid+unclass+tertiary+living_street = **78.3%** of road-km | confirmed exactly, 78.3% |
| `highway` is on every edge, `surface` is not | confirmed — `pipeline/graph.py:226` carries `highway`; `surface` is consumed into `score_adj` in score.py and never reaches the graph |

None of the *directions* changed. The point of the table is that the headline
margin — road class over the whole model — is **0.757 against 0.739**, not 0.74
against 0.71, and §3c shows that margin is inside the noise. The case for road
class does not rest on it and should never have been stated as if it did.

---

## 2. The test a candidate has to pass

Four conditions. The first three decide whether something *is* a preference; the
fourth decides whether it can ship.

1. **Disagreement is legitimate.** Two reasonable drivers want opposite things
   and neither is wrong. ("Is this road wooded" fails: that is a fact.)
2. **The signal discriminates.** What it keys on is neither near-universal nor
   near-absent in the network. This is the 78.3% trap, and the thing
   `test_town_is_not_most_of_the_state` guards.
3. **It is measured uniformly.** The underlying data is not a proxy for how hard
   local mappers worked. This is the trap `docs/geodata-sources-findings.md`
   established for `c_green` and `docs/unpaved-and-urban-brief.md` re-found for
   surface.
4. **A default is defensible without the preference.** Most users never open a
   settings screen, so the default *is* the behaviour. Making something tunable
   relocates the calibration question; it does not remove it.

| judgement | constant | (1) legit | (2) discriminates | (3) uniform | verdict |
|---|---|---|---|---|---|
| road size | `CLASS_ADJ` | yes | as an **ordinal** yes; as a binary no | mostly — `unclassified` is 0.4% of CT and 20.8% of VT | **ship** (§3) |
| twistiness | `c_curves` in `BASELINE` | yes | yes, 49.9% of km ≥ 0.4, mean 0.395 | **yes — the only component that is pure geometry** | **ship, second** (§4) |
| surface | `UNPAVED_ADJ` | yes | 0.8% of CT, 45.1% of VT | **no** — 36% surface-tagging in ME vs 90% in VT | **not mine** (§5) |
| town | `c_urban` | already tunable | yes | no | **not mine** (§5) |
| mapped viewpoints | `c_views` in `BASELINE` | yes | **no — 1.0% of km** | n/a | **reject** (§5c) |
| scenic-byway designation | `c_scenic_tag` in `BASELINE` | arguable | **no — 1.4% of km** | no, it is an authority's opinion | **reject** (§5c) |

---

## 3. Candidate 1 — road size

### 3a. The code cannot express what the driver is doing

`pipeline/score.py:79`:

```python
"secondary": 0.0, "tertiary": 0.0, "unclassified": 0.0, "residential": -0.05,
```

Every class the driver marked "nice" scores 0.0 or negative. The only sentence
available is "motorways are bad". Measured on the marks, that sentence carries
**no information at all** about the roads a driver actually chooses between:

| instrument | over all 79 marks | over the 63 non-motorway marks |
|---|---|---|
| shipped scenery score | 0.739 | 0.779 |
| rank over the `highway` tag alone | **0.757** | **0.789** |
| `CLASS_ADJ` at its shipped values | 0.635 | **0.574** |
| `CLASS_ADJ`'s *shape* — motorway bad, everything else identical | 0.557 | **0.500** |
| binary small-vs-large (the 78.3% version) | 0.708 | 0.745 |
| 95% null ceiling at this sample size | 0.626 | 0.654 |

Two rows matter. **0.500 is a coin**: restricted to the roads a driver picks
between, today's vocabulary is exactly uninformative. And **0.574 is below the
0.654 null ceiling**: `CLASS_ADJ` as currently valued is not measurably better
than chance on those roads either.

### 3b. The scenery model is blind to road class, by its own arithmetic

Length-weighted over the region, splitting `score_adj` into its two halves so
the surface question stays in its own session's hands:

| class | road-km | % net | raw beauty | `CLASS_ADJ` | unpaved adj | % unpaved | score |
|---|---|---|---|---|---|---|---|
| residential | 140,600 | 59.5% | 0.314 | -0.05 | -0.032 | 12.9% | 4.66 |
| tertiary | 24,166 | 10.2% | 0.303 | 0.00 | -0.015 | 5.9% | 5.20 |
| secondary | 22,671 | 9.6% | 0.306 | 0.00 | -0.001 | 0.3% | 5.37 |
| unclassified | 20,426 | 8.6% | **0.317** | 0.00 | **-0.128** | **51.4%** | 4.24 |
| primary | 11,728 | 5.0% | 0.301 | -0.04 | 0.000 | 0.0% | 4.92 |
| motorway | 8,581 | 3.6% | 0.237 | -0.45 | 0.000 | 0.0% | 0.63 |
| trunk | 4,926 | 2.1% | 0.305 | -0.10 | 0.000 | 0.0% | 4.38 |

**Across every non-motorway class above 1% of the network, raw beauty runs
0.301 to 0.317 — a spread of 0.016, which is 0.19 points on the 0-10 scale.**
Nine components, 236,000 km, and the model cannot tell a residential lane from a
secondary highway. The whole 4.24-to-5.37 spread in the final column is
`score_adj`, and the class half of it takes exactly three distinct values.

Note also that `unclassified`'s low score is **not** a class effect: 51.4% of it
is unpaved, and that is the sibling session's constant, not this one's.

### 3c. Complementary — three ways, only one of which is a correlation

A correlation is the weakest of the three and the previous draft leaned on it.

**(i) Over the marks, holding the other instrument fixed.** The rank statistic
is already built out of (nice, dull) pairs, so restricting the pairs is the
natural test:

| | separation | pairs |
|---|---|---|
| class rank, among pairs the **score** calls equal (±1.0 of 10) | **0.728** | 213 of 1,140 |
| score, among pairs of the **same class rank** | **0.700** | 213 |
| — the same two, non-motorway marks only | 0.733 / **0.827** | 180 / 168 |
| class rank, among pairs **`c_curves`** calls equal (±0.10) | 0.592 | 278 |

Neither subsumes the other. The last row is the honest caveat: a good part of
the class signal *is* twistiness, which is why §4 is a real candidate and not an
afterthought.

**(ii) Over the whole region, independent of any driver.** Spearman of the
back-road ordinal against every component, on a 120,000-chunk sample:

```
c_curves +0.199   c_forest +0.149   c_relief +0.084   c_water -0.049   c_urban -0.107
```

Nearly orthogonal to everything the model measures, across 236,000 km. **This is
the strongest evidence in the study and it does not touch the 79 marks at all.**

**(iii) The correlation, corrected.** Spearman(score, class rank) over the marks
is 0.531 — but **0.074** once motorway and trunk are dropped. The 0.531 is
almost entirely the model and the driver agreeing that motorways are not
back roads.

### 3d. What would overturn this, and what already dents it

**The margin over the scenery model is noise.** Bootstrap over the marks (4,000
resamples): class ordinal 0.761 [0.670, 0.836], shipped score 0.744 [0.645,
0.832]. The intervals overlap almost completely. Leave one drive out:

| drive held out | marks left | class ordinal | shipped score | null ceiling |
|---|---|---|---|---|
| 2026-08-22-171920 | 78 | 0.752 | 0.735 | 0.626 |
| 2026-08-22-183419 | 77 | 0.760 | 0.726 | 0.631 |
| 2026-08-25-180813 | 59 | 0.820 | 0.813 | 0.650 |
| 2026-08-25-202122 | 53 | 0.808 | 0.737 | 0.668 |
| **2026-08-25-211808** | **49** | **0.607** | **0.676** | **0.644** |

Drop the last drive and the ordinal falls *below* the null ceiling and *below*
the score. **"Road class beats the whole scenery model" is one drive deep and
should not be repeated.** "Road class carries information the scenery model does
not" survives every fold, and is the claim §3c actually supports.

**The driver liked motorways.** Eight of eleven motorway marks were "nice", all
of them on one westbound run of the Massachusetts Turnpike, on stretches the
model scores 0.00 to 1.05 — while the model's own `c_forest` on those same
stretches reads 0.63 to 0.73. `CLASS_ADJ = -0.45` is doing all of the
destroying. That is structurally the same defect
`docs/unpaved-and-urban-brief.md` found in Vermont: a flat penalty overriding a
beauty measurement that says the road is fine.

Pulling motorway up to primary's rank *improves* the ordinal, 0.757 → 0.775. So
this driver's evidence does not support motorway sitting at the bottom of the
axis. It is one road on one afternoon, and I would not act on it — but it is
also the second-largest single behaviour in `CLASS_ADJ` and nobody has ever
measured it.

**There is no evidence at all about `unclassified`.** Zero of 79 marks landed on
one; eastern Massachusetts is 1.9% unclassified. The region is 8.6%, and Vermont
is 20.8%. Wherever `unclassified` is placed in the ordinal, that placement is a
guess.

### 3e. The two shapes of the ordinal, and why the ordering is not obvious

The binary is measurably worse (0.708 against 0.757) *and* fires on 78.3% of the
network — the failure `test_town_is_not_most_of_the_state` exists to catch, and
what "forest/park" now does at 77.6%. Any proposal must be an ordinal. Two
candidate ordinals, both 0 (biggest) to 1 (smallest):

| | motorway | trunk | primary | secondary | tertiary | unclassified | residential |
|---|---|---|---|---|---|---|---|
| **size** | 0.0 | 0.2 | 0.4 | 0.6 | 0.8 | **1.0** | **1.0** |
| **backroad** | 0.0 | 0.2 | 0.4 | 0.6 | **1.0** | 0.9 | 0.8 |

**The 79 marks cannot choose between them** — tertiary and residential are both
100% "nice", so any ordering of the two gives the identical 0.757. Three things
do choose, and all three point the same way:

- **59.5% of the region's road-km is residential.** Putting it at the top of the
  ordinal parks three roads in five at maximum, which is the binary trap wearing
  a different hat. Under `backroad`, 10.2% sits at the top.
- **The nine residential marks are country lanes, not side streets** — West Bare
  Hill Road, Pinebrook Road, Westcott Road, `c_forest` 0.36 to 1.00, `c_urban`
  zero on seven of nine. What the driver liked was a wooded lane. `c_forest` and
  `c_urban` are already sliders; the class axis should not be asked to re-say
  what they say.
- **On routes, `backroad` wins.** Same mechanism, same knob setting, same 16
  pairs, everything on the shipped score:

  | | scenic-km | min ≥ 7 | % big | ×time |
  |---|---|---|---|---|
  | pref 0, road 2.0 — `backroad` | **37.0** | **16.5** | 14.4 | 1.73 |
  | pref 0, road 2.0 — `size` | 35.4 | 14.7 | 13.6 | 1.72 |
  | pref 0.35, road 2.0 — `backroad` | **38.0** | **17.7** | 16.1 | 1.70 |
  | pref 0.35, road 2.0 — `size` | 35.6 | 16.1 | 16.8 | 1.71 |
  | pref 0.7, road 2.0 — `backroad` | **38.8** | 21.2 | 18.1 | 1.67 |
  | pref 0.7, road 2.0 — `size` | 38.1 | 21.2 | 19.8 | 1.64 |

  `backroad` holds more scenic-km at all three settings and more minutes above 7
  at two of three (tied at the third), for the same road-character shift and the
  same time. It also keeps `test_not_pinned_at_the_ceiling` in reach if the
  ordinal ever becomes a `c_` column: `size` puts 68.1% of km at exactly 1.0,
  and that test refuses anything above 35%.

**Recommendation: the `backroad` ordinal.** Note that the marks did not choose
it — network structure and route measurement did.

### 3f. The `highway` tag is not region-uniform either, and that is survivable

Condition (3) is the one surface fails outright. Road class passes it, but not
cleanly, and the study should not pretend otherwise. Percent of each state's
road-km, from `scored_chunks.way_id` against `/tmp/way_state.json`:

| | motorway | trunk | primary | secondary | tertiary | unclassified | residential |
|---|---|---|---|---|---|---|---|
| CT | 5.1 | 1.6 | 3.5 | 11.4 | 9.7 | **0.4** | 66.1 |
| MA | 4.1 | 1.9 | 4.7 | 11.7 | 10.9 | 1.9 | 62.8 |
| RI | 4.1 | 1.4 | 7.1 | 7.3 | 14.3 | 2.6 | 60.7 |
| ME | 2.1 | 2.5 | 4.9 | 8.0 | 9.0 | 13.7 | 59.4 |
| NH | 3.2 | 2.4 | 5.0 | 8.7 | 9.4 | 15.1 | 54.8 |
| VT | 3.8 | 2.1 | 6.8 | 7.4 | 11.3 | **20.8** | 47.3 |

**`unclassified` runs from 0.4% of Connecticut to 20.8% of Vermont — a factor of
fifty.** That is the same shape of problem `docs/geodata-peer-review-verdict.md`
established for green mapping and the sibling brief re-found for surface, and it
is why the ordinal's placement of `unclassified` matters far more in the north
than the 8.6% region-wide figure suggests.

Two things make it survivable where surface is not:

- **Every routable way is classified.** There is no untagged remainder for the
  preference to fire unevenly across — `highway` is what makes a way a road in
  the first place. Surface's problem is 64% of Maine having no tag at all;
  road class has no equivalent.
- **The aggregate is stable.** The "small" share (tertiary + unclassified +
  residential + living_street) runs 75.6% (MA) to 82.0% (ME) — a spread of six
  points across six states. The *composition* of that block moves; its size
  barely does. An ordinal that distinguishes within the block inherits the
  variation; the binary would not, which is a second reason the binary is worse
  than it looks.

The practical consequence is §9.2: the placement of `unclassified` cannot be
settled from Massachusetts marks, and Vermont is where it matters.

---

## 4. Candidate 2 — twistiness

`c_curves` is the one component derived purely from geometry, so it is uniform
across states where the polygon components are not. On the marks it separates at
0.678 overall and 0.791 on non-motorway roads — no longer the best component
(`c_forest` is, at 0.786, since yesterday's landcover merge), but strong.

### 4a. Promoting it is an exact no-op, which is unusual and worth stating

`c_curves` is *already in the blend* at a calibrated 0.13. Moving it from
`BASELINE` into `BEAUTY_TYPES` at a default slider of 1.0 changes the tunable
mass from 0.89 to 1.02 and the renormalisation is a fixed point at all-1.0, so
the raw sum is arithmetically identical. Measured:

```
max |score change| over 998,252 edges = 5.33e-15      (an exact no-op)
```

No rebuild, no redeploy, no `RAW_BASE`/`STRETCH` re-fit, no `BETA`/`PREF_CURVE`
re-sweep, and `test_neutral_weights_reproduce_the_precomputed_score` keeps
passing untouched. The entire cost is the coordinated client release
(`BeautyType.all`, `RouteProps.sceneryBreakdown`, and the two tests that assert
the lists match).

### 4b. It only works in one direction, and it is the wrong one

The product argument was always two-sided: a driver who wants a twisty road and
a passenger who gets carsick want opposite things. Measured over 16 pairs at
pref 0.7, everything on the shipped score:

| twistiness slider | mean `c_curves` on route | km at `c_curves` ≥ 0.5 | minutes | scenic-km | min ≥ 7 |
|---|---|---|---|---|---|
| 0 (ignore) | 0.337 | 14.7 | 74.7 | 37.0 | 19.0 |
| 1 (= shipped) | 0.347 | 15.2 | 74.9 | 36.9 | 18.6 |
| 2 (app max) | 0.375 | 18.1 | 75.9 | 37.2 | 19.9 |
| 4 (API max) | 0.419 | 21.9 | 78.5 | 38.1 | 21.1 |

Turning it **up** works: +21% twistiness and +44% twisty-km for 4.8% more time,
and scenic-km rises rather than falls. Turning it **down to zero straightens the
route by 3%.** The pot clamps at 0, so a slider in it can say "I don't care
about twisty roads" and can never say "please, straight ones" — and because
twistiness correlates with forest (ρ = 0.26) and relief (ρ = 0.19), the other
sliders keep steering onto the same roads anyway.

**So the carsick passenger needs the two-sided mechanism of §6, not a seventh
attraction.** Ship the attraction (it is free and the up-direction is real);
recognise that it does not answer the question that motivated the candidate.

One caution for whoever writes the label. `c_curves` is 0.546 on residential and
0.399 on tertiary, and 0.550 on motorway ramps — cul-de-sacs and cloverleafs are
twisty. It is measuring geometry honestly and geometry is not always sweep.

---

## 5. Candidates that are not mine, and two that are rejected

**Surface (`UNPAVED_ADJ`) and town (`c_urban`) belong to the session working
against `docs/unpaved-and-urban-brief.md`.** Its framing is taken as input here
and its conclusions are not duplicated. Three points of contact:

- Its central finding — that a flat penalty overrides a beauty measurement which
  says the road is fine — is the *same shape* as §3d's motorway finding. Two
  instances of one defect in `score_adj`.
- Its condition (3) failure is decisive and mine is not: surface tagging runs
  36% in Maine to 90% in Vermont, so the penalty measures mapping diligence.
  `highway` is tagged on every routable way by construction. The `unclassified`
  variation in §3d is a real regional difference, not a coverage artefact — but
  it has the same practical consequence and §9 says what to do about it.
- **The mechanism recommended in §6 is where surface should land if that session
  concludes the penalty is a preference.** One axis, two entries. That is the
  integration point; it is theirs to take or leave.

**`c_urban` is separately confirmed as pointing the wrong way** — 0.331 on all
79 marks, 0.329 on non-motorway, where 0.5 is a coin and 0.626 is the ceiling.
Mean `c_urban` is 0.383 on marks the driver called dull and 0.181 on ones they
called nice. Recorded for completeness; not acted on here.

### 5c. Two rejections, measured

`c_views` (weight 0.05) and `c_scenic_tag` (0.07) sit in `BASELINE` alongside
`c_curves` with no stated reason, while `c_farm` is tunable on 0.06 — lighter
than frozen `c_scenic_tag`. So "why is this one frozen and that one not" is a
fair question. The answer is coverage:

| component | weight | mean | % of network km ≥ 0.4 |
|---|---|---|---|
| `c_curves` | 0.13 | 0.395 | **49.9%** |
| `c_views` | 0.05 | 0.012 | **1.0%** |
| `c_scenic_tag` | 0.07 | 0.012 | **1.4%** |

Both fail condition (2) by two orders of magnitude, and both separate at chance
on the marks (0.507 and 0.500 — no marked road carried a byway tag). 0.12 of
weight is spread across 2% of the network. A slider for either would be a
control that does nothing on 98% of roads. **Reject; do not promote.** Whether
they earn their weight *at all* is a different question and not this study's.

---

## 6. The mechanism, which is the real design content

Three shapes. Only one of them is right, and the reason is measurable rather
than aesthetic.

**B — a seventh entry in `BEAUTY_TYPES`.** The attraction reading: small roads
are *liked*, so make them a kind of beauty.

**A — a signed offset inside `score_adj`**, outside the renormalised blend:
`score_adj = CLASS_ADJ + UNPAVED_ADJ + k · offset(highway)`.

**A′ — its own term in the routing cost**, alongside the scenery penalty:

```
w = d_minutes + pref^PREF_CURVE · BETA · km · (1 − score/10)
              + k_road · BETA_ROAD · km · (1 − rank)
```

### 6a. Why B is wrong: the pot is calibrated for sparse components

Every existing tunable attraction is *sparse*. A road-class component is
*dense* — every road has a class:

| component | weight | mean | % km ≥ 0.5 |
|---|---|---|---|
| `c_farm` | 0.06 | 0.027 | 3.2% |
| `c_coast` | 0.13 | 0.084 | 7.0% |
| `c_urban` | 0.14 | 0.242 | 26.8% |
| `c_water` | 0.22 | 0.244 | 15.6% |
| `c_relief` | 0.16 | 0.383 | 29.6% |
| `c_forest` | 0.18 | 0.471 | 43.9% |
| **a road-class column (`size`)** | — | **0.841** | **88.0%** |

The six tunable attractions together contribute **0.246** to the mean road's
raw. A road-class column at weight 0.15 would add **0.126** on its own — half
again as much as all six combined, to every road in the region. `RAW_BASE` and
`STRETCH` were fitted to that sum. Length-weighted over the region:

| variant | p10 | p50 | p90 | mean | % km at 0 | % km at 10 |
|---|---|---|---|---|---|---|
| **shipped** | 2.19 | **4.50** | 7.12 | 4.57 | 2.4% | **0.33%** |
| A / backroad / spread 0.10 | 2.17 | 4.55 | 7.16 | 4.59 | 3.2% | 0.36% |
| A / backroad / spread 0.20 | 2.13 | 4.59 | 7.21 | 4.61 | 3.8% | 0.41% |
| **B / w=0.15, every slider at its default 1.0** | 3.47 | **5.91** | 8.52 | 5.88 | 2.3% | **2.40%** |
| B / w=0.15, road slider at 2 (app max) | 4.20 | 6.65 | 9.01 | 6.50 | 2.6% | 3.70% |
| B / `size` shape, w=0.15, slider at 2 | 4.51 | **7.03** | 9.38 | 6.82 | 2.6% | **5.33%** |

**A is scale-neutral by construction** (the offset is centred so its
length-weighted mean is zero) and moves p50 by 0.05. **B moves p50 by 1.4 points
before the user touches anything**, and pins seven times as much road-km at
exactly 10.0 — nine times, on the `size` shape — where the router's
`km × (1 − score/10)` penalty makes every pinned road free and
indistinguishable, which is the failure
`Router._edge_scores`' own docstring is written about. At the app's maximum the
`size` shape reaches 7.03 / 5.33%, outside what
`test_uses_most_of_the_range` (p50 < 6.5) and `test_not_mostly_clipped` (< 5% at
9.99) permit of a build. Those tests run on the stored column rather than on the
live re-blend, so they would not literally fail — which is worse, not better:
the guard exists and the live path walks straight past it.

**And B makes the two axes compete, which is exactly backwards.** The
renormalisation holds total tunable weight constant, so the factor applied to
every existing attraction when the new slider moves and the other six stay at
1.0:

| new type's weight | slider 0 | 1.0 | 2.0 (app max) | 4.0 (API max) |
|---|---|---|---|---|
| 0.10 | 1.112 | 1.000 | 0.908 | 0.767 |
| **0.15** | 1.169 | 1.000 | **0.874** | **0.698** |
| 0.22 | 1.247 | 1.000 | 0.835 | 0.627 |

A user asking for back roads at the app's maximum silently gives up 13% of their
water, forest and coast weight. That trade is *correct* for substitutes — coast
against farmland is a real either/or, which is what the pot models — and wrong
for an axis measured at ρ ≈ 0.07 to 0.20 against everything in it. **Nothing
orthogonal belongs in a pot.**

Finally, B is not cheap. `test_neutral_weights_reproduce_the_precomputed_score`
requires the live re-blend to equal the stored `score` column, so a seventh type
needs `score.py` to emit a matching `c_roadclass` and a matching `score` — **a
full rebuild and a 364 MB redeploy**, plus a `RAW_BASE`/`STRETCH` re-fit, plus a
`BETA`/`PREF_CURVE` re-sweep, plus a coordinated iOS release. The cost asymmetry
that makes road class attractive holds for A and A′ and evaporates for B.

### 6b. Why A is not enough: `pref` gates it to nothing

Under A the offset lands inside `score`, and the whole score term in
`Router._weights` is multiplied by `strength = pref ** PREF_CURVE`. At
`pref = 0.35` that factor is 0.12; at `pref = 0` it is exactly zero.

**A driver at pref 0 cannot express a road preference at all under A.** That is
arithmetic, not measurement. And "get me there, but not on the highway" is a
completely ordinary request that the product cannot make today and would still
not be able to make.

A also breaks `test_neutral_weights_reproduce_the_precomputed_score` for any
non-zero default, because the live re-blend would no longer equal the stored
column. So A can only ever ship default-off.

### 6c. A′, recommended

A′ leaves `_edge_scores`, `composite`, `score_adj` and the stored `score` column
completely untouched. The score keeps meaning "how scenic", full stop; the road
preference is a separate reason to prefer an edge. Consequences:

- Every scoring and calibration test passes unchanged. Only `_weights` moves.
- The knob is not gated by `pref`, so it works at pref 0 — measured in §7.
- **Every edge weight stays non-negative**, so scipy's Dijkstra remains valid:
  `k_road ≥ 0`, `BETA_ROAD > 0`, `km > 0` and `rank ≤ 1` make the new term ≥ 0,
  added to a `d_minutes` that is already positive. That is not a detail —
  `docs/scenery-cap-options.md` is entirely about the wall on the other side of
  it, where a two-way road's two directions form a negative 2-cycle and no
  shortest path exists at any price.
- It can be made two-sided, which the pot cannot be and the carsick passenger
  needs — **but not by letting `k_road` go negative.** That would drive the term
  below zero and walk straight into the wall above. The two-sided form penalises
  the far end instead of rewarding the near one: `k · km · (1 − rank)` to push
  towards back roads, `|k| · km · rank` to push towards main roads. Both are
  non-negative; only one is active at a time.
- `highway` is already on every edge (`router.py:762` maps it for
  `SPEED_FACTOR`). **This is a `router.py` change and a restart.** No rebuild, no
  redeploy of `data/processed-ne`.

---

## 7. What it does to real routes

16 origin-destination pairs: the 10 from `pipeline/scenery_cap_experiment.py`
(so these rows are comparable with `docs/scenery-cap-options.md`) plus 6 in
Vermont, New Hampshire and Maine, because a default fitted in eastern
Massachusetts has to be checked where the network is shaped differently.

**Everything below is measured on the shipped score.** A mechanism that inflates
the scale otherwise marks its own homework: `scenic_km = Σ km · score/10` rises
for an unchanged route whenever the scale moves under it, and B's did (§6a). The
first draft of this measurement read each variant on its own scale and drew the
opposite conclusion.

### 7a. A′ over the 16 pairs

| | ×time | scenic-km | min ≥ 7 | % km on big roads | % km on small roads |
|---|---|---|---|---|---|
| **pref 0 — "fastest"** | 1.00 | 16.5 | 3.5 | **94.8** | 2.8 |
| pref 0 + road 0.5 | 1.10 | 21.3 | 5.9 | 74.1 | 12.5 |
| pref 0 + road 1.0 | 1.32 | 30.4 | 11.1 | **42.4** | 37.9 |
| **pref 0.7 — shipped** | 1.49 | 36.9 | 18.6 | 49.9 | 23.2 |
| pref 0.7 + road 0.5 | 1.56 | 37.4 | 20.1 | 32.2 | 38.5 |
| pref 0.7 + road 1.0 | 1.59 | 37.5 | 20.0 | **26.8** | 44.1 |
| pref 0.7 + road 2.0 | 1.65 | 38.8 | 21.2 | 18.1 | 54.2 |

("big" = motorway/trunk/primary and their links; "small" = tertiary,
unclassified, residential, living_street.)

### 7b. The control the claim needs — and it is bad news, correctly read

The road term buys back roads *and* spends time, and so does the `pref` slider.
So the question is not "does it help" but "does it beat the slider the product
already has, at equal cost". Sweeping shipped `pref` over the same 16 pairs and
interpolating to matched travel time:

| road setting | ×time | scenic-km | *slider at same time* | min ≥ 7 | *slider* | % big | *slider* |
|---|---|---|---|---|---|---|---|
| pref 0 + road 0.5 | 1.104 | 21.3 | **23.2** | 5.9 | **6.9** | **74.1** | 81.0 |
| pref 0 + road 1.0 | 1.321 | 30.4 | **32.9** | 11.1 | **14.3** | **42.4** | 63.4 |
| pref 0.35 + road 0.5 | 1.357 | 33.3 | **34.2** | 13.9 | **15.4** | **47.8** | 60.7 |
| pref 0.7 + road 0.5 | 1.564 | 37.4 | **38.5** | 20.1 | **21.9** | **32.2** | 40.9 |
| pref 0.7 + road 1.0 | 1.588 | 37.5 | **39.2** | 20.0 | **23.0** | **26.8** | 37.6 |
| pref 0.7 + road 2.0 | 1.654 | 38.8 | — | 21.2 | — | **18.1** | — |

Bold is the better number in each pair; for "% big" lower is better.

**On the model's own scenery metrics, the road-class term is dominated by the
`pref` slider at every setting tested.** If road class is sold as "a way to get
more scenery", the honest answer is: turn the existing slider up instead, it is
cheaper in minutes.

**On road composition it does what the slider cannot.** At 1.32× time the slider
gives 63.4% big-road km and the road term gives 42.4% — a 21-point swing, bought
for 2.5 scenic-km. And the slider has a **floor: 34.6% big-road km at pref = 1.0
and 1.61× time, and it cannot go below that at any setting.** The road term
reaches 18.1% at 1.65× and 14.4% at 1.74×.

That is the whole finding in one paragraph. The road-class preference is not
more scenery. It is the only access to a region of the trade-off space the
product currently cannot reach, and whether a driver wants to be there is
precisely the question that is theirs to answer.

### 7c. It is not uniform, and Vermont is the warning

Per pair, pref 0.7, shipped → `road 1.0`:

| pair | km | minutes | % big | scenic-km |
|---|---|---|---|---|
| Montpelier → Burlington | 61.1 → 63.3 | 64.6 → 81.5 | **99 → 46** | 36.2 → **41.5** |
| Keene → Bennington | 94.5 → 100.8 | 90.1 → 108.2 | 93 → 59 | 59.2 → **68.3** |
| Harvard → Needham | 49.0 → 48.8 | 65.6 → 68.8 | 25 → 4 | 28.2 → 28.3 |
| Needham → Chelmsford | 29.9 → 29.9 | 41.8 → 41.8 | 10 → 10 | 17.7 → 17.7 |
| **Brattleboro → Woodstock VT** | 116.7 → **103.7** | 120.0 → 113.9 | 65 → 26 | **85.1 → 68.3** |

The last row is the one to look at. **Vermont's best scenic roads are tagged
primary and secondary** — Route 100, Route 9 — so a back-road preference steers
directly off them, and here it cost 17 scenic-km on the model's own reckoning.
The first two rows are the opposite case: a route pinned to an interstate that
the scenery slider could not pull off, freed for 20-26% more time.

**The road preference is most valuable exactly where the scenery model has
nothing to work with, and actively harmful where a highway *is* the scenic
road.** No single default handles both. A user control does.

---

## 8. Defaults

The default is the product. Here is one for everything recommended, with what it
rests on.

### Road character: **default 0 (off). One knob, `road` ∈ [0, 1], `BETA_ROAD = 1.0`.**

Off is the finding, not a failure to finish. Three reasons, in order of weight:

1. **At every matched travel time, a non-zero default makes routes worse on the
   only scenery metric the product can currently measure** (§7b). Shipping a
   default that the model scores down, on the strength of 79 marks from one
   driver on one afternoon in a 6%-unpaved, 1.9%-unclassified corner of one
   state, is not a defensible trade.
2. **It forces a decision about what "fastest" means.** `server/app.py:232`
   computes `fastest = ROUTER.route(s, t, 0.0, weights)`, and the comment beside
   it says "pref 0 zeroes the scenery term, so its path is time-only either
   way". A non-zero default makes the app's "fastest" arm 1.10× to 1.32× slower
   than the fastest route, or else makes the two arms disagree about the
   driver's stated preference. Either answer is defensible; neither should be
   arrived at by accident. Off defers it.
3. **It squeezes what `pref` has left to do.**
   `test_the_whole_slider_does_something` requires pref 1.0 to beat pref 0.0 by
   more than 1.0 of mean score.
   Over the 16 pairs the shipped router spans 2.27 (pref 0) to 5.61 (pref 0.7);
   with `BETA_ROAD = 2.0` always on, the same span is 5.43 to 5.77 — 0.34 across
   most of the slider, because the road term has already taken the detour. The
   test runs on one Worcester–Boston pair rather than on this average, so treat
   it as the mechanism rather than a predicted failure; the mechanism is real.

`BETA_ROAD = 1.0` for the knob's *top* end, because it is where the ×time cost
stops being proportionate: 1.0 lands at 1.59× against the slider-alone 1.49×,
while 2.0 costs 1.65× and starts routing 54% of a long trip onto residential
streets. `road = 0.5` is the setting worth putting under the thumb as a detent.

**What would change this to a non-zero default:** the A/B in §9. Nothing else.

### Twistiness: **promote to `BEAUTY_TYPES`, default weight `WEIGHTS["curves"]` = 0.13, slider default 1.0.**

This default is free and it is not a judgement — it is arithmetically the
current behaviour to 5.33e-15 (§4a). It is the one recommendation here with no
calibration risk whatsoever. The 79 marks are not load-bearing for it; they only
say the component is worth exposing, which its 0.678/0.791 separation and 49.9%
coverage already say.

### The ordinal: **`backroad`, not `size`** (§3e). Placement of `unclassified` at 0.9 is a guess (§3d) and §9 says how to stop guessing.

### How many sliders the screen ends up with

Six exist. This recommends **seven scenery sliders and one control that is not a
scenery slider** — a net one extra row on the tune screen, not two:

- Twistiness joins water / coast / forest / hills / farmland / town. It is the
  same kind of thing they are ("what should the view be like"), it has the
  second-best separation of any component on the marks, and it fires on half the
  network. Seven is one more than six; the screen can carry it.
- Road character is **not** a scenery slider and should not be listed with them.
  It answers a different question — "what should the road be like" — it lives
  outside the renormalised pot (§6), and putting it in the same list would
  invite exactly the substitution the arithmetic says is wrong. Its natural home
  is beside `pref`, which is also a "how, not what" control.

Nothing is proposed for removal. `c_views` and `c_scenic_tag` are the obvious
folding candidates on coverage grounds (§5c), but they are frozen weights rather
than screen rows, so folding them buys no space and is a separate question.

### What no default can be proposed for

**Where motorway sits on the axis.** The eight "nice" Pike marks say it is not
at the bottom; they are eight taps on one road on one afternoon. The axis has to
put motorway *somewhere*, and 0.0 is what every proposal above assumes purely
because it is the status quo. It is unmeasured.

---

## 9. What cannot be settled without more drive marks

Named specifically enough to go and collect. Roughly 30 usable marks per drive
at the rate the 2026-08-25 drives managed.

1. **The A/B that decides the default.** Pick 4 pairs where §7b shows the
   biggest divergence (Montpelier→Burlington, Keene→Bennington,
   Needham→Worcester, Worcester→Groton). For each, drive **both** the shipped
   pref-0.85 route and the pref-0.7 + road-0.5 route — matched to within 2% on
   travel time, ~10 points apart on big-road share — and mark both. **8 drives,
   ~240 marks.** This is the only experiment that can move the road default off
   zero, because it is the only one that asks the driver to choose between two
   routes that cost the same.
2. **`unclassified`.** Zero of 79 marks. It is 20.8% of Vermont, 15.1% of New
   Hampshire, 13.7% of Maine, and 0.4% of Connecticut — and 51.4% of it is
   unpaved, so it cannot be marked without also marking the sibling session's
   question. **Two drives in central Vermont (Montpelier–Woodstock via the
   town-road network) and one in western Maine (Bethel–Rangeley), ~90 marks**,
   tapping surface and class separately if the app can be made to.
3. **Whether motorway belongs at the bottom.** One deliberate drive of 40 km of
   rural interstate with marks — I-91 north of Brattleboro, or I-93 through
   Franconia Notch. ~25 marks. If the driver marks those "nice" too, `CLASS_ADJ`
   = -0.45 is a bigger error than anything in this study.
4. **Whether "tertiary above residential" is right.** The current evidence is
   structural, not behavioural. One drive that deliberately alternates rural
   tertiary and rural residential in the same town — Harvard/Bolton MA is where
   the existing marks already are — ~30 marks.
5. **Anything outside eastern Massachusetts at all.** Every behavioural number
   in this study comes from a 40 km radius. Three drives, one each in VT, NH and
   ME, is the minimum before any of this is called calibrated rather than
   motivated.

---

## 10. Ordering, and traps for whoever implements

**Order, by evidence-per-cost:**

1. **Twistiness → `BEAUTY_TYPES`.** Provably free on the server, one coordinated
   client release. Do it first because it costs nothing to be wrong about.
2. **Road character → A′, `router.py` only, default off.** A `router.py` change
   and a restart: no rebuild, no 364 MB redeploy. Ship it as a control, not as a
   behaviour change, and run experiment 9.1 before touching the default.
3. **Fix the "fastest" arm** (below). This is a bug the moment 2 lands, and it
   is small.
4. **Surface**, if and when the sibling session concludes it is a preference —
   into the same A′ axis as a second entry.
5. **Motorway's position on the axis**, after experiment 9.3. Not before.

**Traps:**

- **`server/app.py:232-234` assumes pref 0 means time-only.** It computes
  `fastest = ROUTER.route(s, t, 0.0, weights)` and then
  `scenic = fastest if pref == 0.0 else ...`. Both assumptions break under A′:
  the fastest arm would silently carry the road preference, and the pref-0
  short-circuit would return one route where the user asked for two different
  ones. The fastest arm must be computed with `road = 0` explicitly, and the
  short-circuit must test both knobs.
- **`looper.py:791` `_weights_key` hashes only the beauty-weight dict.** A road
  knob passed as a separate argument would collide in the loop cache
  (`_fields`, `_cost`) and serve a route computed under a different driver's
  preference — a silent wrong-answer bug, not a crash. **Thread the new knob
  through the existing `weights` dict and `_weights_key` picks it up for free.**
- **The `size` ordinal trips `test_not_pinned_at_the_ceiling`** if it is ever
  made into a `c_` column: it puts 68.1% of km at exactly 1.0 against a 35%
  limit. `backroad` puts 10.2% there.
- **Do not re-fit anything to the 79 marks.** They are one driver, one
  afternoon, eastern Massachusetts. They motivated this study; §3d shows a
  single drive moves the headline by 0.15.
- **Do not report a mechanism's scenery on its own score scale.** §7b's first
  draft did and reached the opposite conclusion.
- **`CLASS_ADJ` and `UNPAVED_ADJ` both live in `score_adj`** and the sibling
  session may be moving the second. Nothing recommended here writes to
  `score_adj`, which is deliberate: A′ was chosen partly so the two sessions
  cannot collide in one expression.

---

## Reproducing this

Nothing in the repository was changed. The scratch scripts live in this
session's scratchpad, outside the repo, and each is standalone:

| script | what it produces |
|---|---|
| `build_marks.py` | the 79 marks with their per-component vectors and snapped edge indices, via `tools/analyze_trace.py`'s own `marks()` and window logic |
| `sep.py`, `reconcile.py`, `robust.py`, `pairs.py` | §1a, §3a, §3c, §3d — separations, bootstrap, leave-one-drive-out, matched pairs |
| `dist.py`, `decomp.py`, `mechanism.py` | §3b, §5c, §6a — network distributions, the `CLASS_ADJ`/`UNPAVED_ADJ` split, the renormalisation and scale tables |
| `routes2.py`, `aprime.py`, `frontier.py`, `curves.py` | §4b, §7 — the route sweeps. Each loads one `Router` (~40 s, 4.2 GB) and monkeypatches `Router._edge_scores` / `Router._weights` in memory, restoring them afterwards, exactly as `pipeline/scenery_cap_experiment.py` does |

Run them from the main checkout (`data/`, `.venv/` and `traces/` do not exist in
a worktree), with `.venv/bin/python`, never `.venv/bin/pip` — the project path
contains spaces and the console scripts are broken by it.
