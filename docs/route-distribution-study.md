# What the router actually offers, across a thousand routes

**Status: run.** 983 town-to-town trips on the shipping graph — 4,232 routes —
four questions answered, one of them only partly. Everything below is measured
unless it says otherwise. No production code was touched: this study adds
`tools/route_census.py` and the three files in `docs/route-census/`, and nothing
in `pipeline/`, `server/` or `ios/` moved. `pytest tests/ -q` is 294 passed.

**The build.** `data/processed-ne` — the New England parquets the deployed
server runs — graph tables written **2026-08-29 14:33**, after the landcover
merge and the byway relations. 801,719 nodes, 998,252 edges, 5,421 place points.
Every number here is against that build and is not comparable to anything
measured before it; that rebuild has already moved published separations once.

**Reproduce it:**

```bash
.venv/bin/python tools/route_census.py --processed data/processed-ne
```

19.5 minutes, one process, seed 20260829 recorded in the output. Both
directories live in the main checkout, not in a worktree; run it from there or
pass absolute paths. Re-cut any
threshold from the CSVs without re-routing:

```bash
.venv/bin/python tools/route_census.py --report-only --out-dir docs/route-census
```

---

## Three things the brief got wrong, which change what was measured

**1. `town` off is not a shipped default.** The brief says turning the `town`
weight off "landed as a client default on 2026-08-29". It did not. On `main`,
`ios/Sources/RouteModel.swift:71` builds the weights dict from
`BeautyType.all.map { ($0.apiName, BeautyType.neutralWeight) }` — all six types
at 1.0 — and no ref in the repository does otherwise.

`w_town=0` does appear across every branch, in exactly one place:
`ios/Tests/LiveDriveTests.swift:190,202`, where the *coastal* live-drive
fixture sends `&w_coast=4&w_town=0&w_farm=0` at pref 0.8 to check that asking
for coast finds coast. It is a tune-screen test, it zeroes `farm` alongside
`town`, and it landed on 2026-08-12 in `e20094f`, not on the 29th. That is the
most likely source of the claim. The uncommitted
`docs/driver-preferences-study.md` draft, meanwhile, reaches the opposite
verdict for `town` ("not mine", on mapping-uniformity grounds), and the
three-pair measurement the brief refers to is not recorded anywhere I could
find.

So the study reports questions 1 and 2 at the **real** shipped default — pref
0.50, all six weights 1.0 — and treats town-off as the proposed variant it
actually is. Both settings were routed for all 983 pairs; the shipped one is
the headline, and the town-off run is summarised at the end of Q2 and printed
in full by `--report-only`.

**2. Going direct is worth ~10%, not "a large speedup".** The brief attributes
the pilot's 0.23 s to JSON: a 4,600-point geometry and 86 turn instructions per
arm. Measured on one graph (`data/processed`, Massachusetts, 310,162 nodes),
same 20 pairs, this machine:

| path | per route | per request |
|---|---|---|
| `Router.route` direct | **0.0865 s** | — |
| through `/api/route` | 0.0956 s | 0.1912 s (two arms, 144 KB response) |

**1.11x.** The pilot's 0.23 s was per *request*, and a request routes both arms,
so it was ~0.115 s per route — of which about 9 ms was HTTP. The cost is
Dijkstra, and `server/serve.py` already says so: "~95 ms of CPU" per route on
this graph, which the 86.5 ms above confirms.

Going direct is still the right call — it dodges the `pref=0` short-circuit, the
weight clamps, and the geometry nobody reads — but it buys control, not speed.
What actually sets the cost is graph size: `scipy.sparse.csgraph.dijkstra` is
single-source-to-every-node with no early exit, so a 15 km trip and a 190 km
trip cost the same, and moving from Massachusetts to New England (2.6x the
nodes) took a route from 0.087 s to **0.277 s**. Budget by graph, not by trip.

**3. The islands fail earlier than expected, and never as "no path".**
`pipeline/graph.py` keeps only the largest strongly connected component, so
Martha's Vineyard, Nantucket and Block Island are not in the graph at all. A pin
on one snaps to the nearest *mainland* road — 6.2 to 45.0 km across the water —
and is refused by `SNAP_MAX_M`. Every one of the 17 failures below is an island,
and `no path` is 0.

---

## The sampling frame

Ordered pairs of OSM place nodes — towns, villages, hamlets — from
`place_points.parquet`, so both ends are somewhere a person might actually
drive to. Rejection-sampled by great-circle distance into four bands, 250 pairs
each, seed fixed and recorded. Uniform points in a bounding box would have
landed in the ocean and over-sampled the empty north; that trap was called in
the brief and avoided.

Each pair was routed four times at pref 0.5 — fastest and scenic, under shipped
weights and under town-off — and 60 of them (15 per band) got a five-point pref
sweep as well. 4,232 routes, 1,170 s.

**Coverage**

| band | ok | snap > 5 km | same node | no path |
|---|---|---|---|---|
| 10–25 km | 247 | 3 | 0 | 0 |
| 25–50 km | 246 | 4 | 0 | 0 |
| 50–100 km | 246 | 4 | 0 | 0 |
| 100–200 km | 244 | 6 | 0 | 0 |
| **all** | **983** | **17** | 0 | 0 |

All 17 are islands; the coordinates are in
`docs/route-census/census-pairs.csv`. 1.7% is the true rate for pairs drawn
from place nodes; it is not a coverage defect on the mainland, and an island
user gets "outside the covered road network" rather than a wrong route, which
is the right failure.

---

## Q1 — what the trade actually looks like

At the shipped defaults (pref 0.50, all weights 1.0). Cells are
min / p10 / median / p90 / max. **Both arms come from the same weight vector**,
so beautiful-km is one ruler over two routes.

| band | n | extra minutes | beautiful mi gained | mi per extra min |
|---|---|---|---|---|
| 10–25 km | 247 | 0.0 / 0.0 / **1.2** / 9.2 / 24.6 | −1.22 / 0.00 / **0.33** / 4.76 / 15.63 | −1.31 / 0.00 / **0.15** / 1.62 / 7.06 |
| 25–50 km | 246 | 0.0 / 0.1 / **10.8** / 25.3 / 41.8 | −3.45 / 0.00 / **3.10** / 9.27 / 29.32 | −2.25 / 0.00 / **0.24** / 1.32 / 12.31 |
| 50–100 km | 246 | 0.0 / 9.4 / **33.8** / 57.1 / 77.0 | −18.86 / 1.94 / **7.60** / 18.89 / 45.12 | −2.48 / 0.09 / **0.25** / 0.70 / 8.99 |
| 100–200 km | 244 | 0.0 / 30.1 / **69.4** / 113.1 / 146.4 | −0.57 / 8.91 / **21.57** / 43.69 / 90.54 | −0.01 / 0.14 / **0.30** / 0.76 / 2.40 |

| band | extra km (p10/p50/p90) | beautiful share of the scenic route (p10/p50/p90) | median beautiful mi, fastest → scenic | median % longer in time |
|---|---|---|---|---|
| 10–25 km | −7.4 / **−0.5** / 0.0 | 4% / 18% / 49% | 1.3 → 2.5 | **4%** |
| 25–50 km | −18.3 / **−4.1** / 0.0 | 7% / 20% / 43% | 2.0 → 6.2 | **25%** |
| 50–100 km | −28.9 / **−9.4** / −0.2 | 9% / 19% / 42% | 2.1 → 11.8 | **46%** |
| 100–200 km | −52.8 / **−18.1** / −0.6 | 13% / 24% / 46% | 3.7 → 26.7 | **54%** |

**Trip length sets the levels and leaves the rate alone.** Longer trips give up
far more time, absolutely (1.2 → 69.4 median minutes) and relatively (4% → 54%
longer), and get far more back (0.33 → 21.57 median beautiful miles). The
*exchange rate* barely moves: 0.15, 0.24, 0.25, 0.30 median beautiful miles per
extra minute. So a pooled median would have been roughly right about the rate
and badly wrong about everything a user experiences — which is why the brief was
right to forbid pooling in the headline.

**The pilot's twenty-fold spread was an under-estimate.** Twelve routes gave
0.13–2.71; 983 give −2.48 to 12.31, with p10–p90 spanning 0.00–1.05. There is no
central value here to site anything on, which is exactly the finding the pilot
was reaching for.

**The scenic route is usually shorter in distance, and never faster.** The
median extra km is negative in every band (−0.5 to −18.1) while the minimum
extra minute count is 0.0 in every band. This is not incidental: the
detour cost is `km * (1 - score/10)` (`Router._weights`), which is proportional
to length, so at any `pref > 0` the router is also, quietly, a shortest-distance
router. It swaps highway miles for fewer, slower, prettier ones.

**And that mechanism has a cost.** On **3.1%** of pairs (30/983, CI 2.1–4.3) the
scenic arm comes back *slower and with fewer beautiful miles* than the fastest
arm — median 0.41 miles lost, worst 18.86, median 1.8 extra minutes, on a route
median 3.6 km shorter. It concentrates in short trips (16 of the 30 are 10–25
km). `mean_score` rose on **27 of those 30** (median +0.58), because a shorter
route with a better average genuinely scores higher per km — so on those trips
the app's sentence (`RouteResults.swift:93`) reads "Scenic adds N min and raises
scenery 4.1 → 4.7" while the beautiful miles fell. That is not the app lying,
but it does mean **the on-screen score cannot detect this case**, and a warning
built on the score will never fire on it.

Frequencies worth having in hand, pooled deliberately because they are counts,
not levels:

- **10.8%** (106/983, CI 9.0–12.9) — the scenic route *is* the fastest route:
  under 0.1 extra minutes and under 0.1 miles gained. Nothing was found.
- **0.7%** (7/983, CI 0.3–1.5) — scenery for free: real gain, no measurable
  time.
- **3.1%** — strictly worse, as above.

---

## Q2 — where a warning could fire

**A warning already ships, and it is good.** `RouteResults.isSameDrive`
(`ios/Sources/RouteResults.swift:74`, landed in `187825c` while this study was
running) prints "**Same as the fastest route** at this setting" when the rounded
minutes do not rise and the two printed scores are within 0.05. Replayed exactly
over the 983 pairs:

| | 10–25 km | 25–50 km | 50–100 km | 100–200 km | all |
|---|---|---|---|---|---|
| fires on | 32.8% (81/247) | 11.4% (28/246) | 1.2% (3/246) | 0.4% (1/244) | **11.5%** (113/983) |

- It catches **105 of the 106** trips where the scenic arm genuinely is the
  fastest arm.
- It has effectively no false positives: across all 113 trips it fires on, the
  largest gain is **0.22 beautiful miles** for 0.81 extra minutes.
- But **120 trips (12.2%)** gain under a mile of beautiful road and are told
  nothing, because they are not literally the same drive.

So the decision is not "should there be a warning". It is whether to extend an
accurate narrow one to those 120 trips, and the tables below price that. Note
also that `isSameDrive` fires on only 3 of the 30 strictly-worse routes from Q1,
for the reason given there: it is built on `mean_score`, which rises on those.

Fire-rates at several thresholds, per band, with 95% Wilson intervals. **These
are rates, not a recommendation**; picking T is the owner's call and the bands
disagree enough that one T may not serve all four.

**Rule A — fewer than T beautiful miles gained**

| T (mi) | 10–25 km | 25–50 km | 50–100 km | 100–200 km | all |
|---|---|---|---|---|---|
| 0.25 | 48% (42–54) | 18% (14–24) | 4% (2–7) | 1% (0–3) | 18% (16–20) |
| 0.5 | 54% (48–60) | 20% (15–25) | 5% (3–8) | 1% (0–3) | 20% (18–23) |
| **1** | **64% (57–69)** | **24% (19–29)** | **7% (4–10)** | **1% (0–3)** | **24% (21–26)** |
| 2 | 73% (67–78) | 34% (29–40) | 11% (7–15) | 2% (1–4) | 30% (27–33) |
| 3 | 81% (76–85) | 49% (43–55) | 16% (12–21) | 2% (1–5) | 37% (34–40) |

The brief's headline question — how often the scenic arm gains under a mile of
beautiful road — is the bolded row: **24% overall**, but that single number is
the one thing not worth quoting. It is 64% of short trips and 1% of long ones.

**Rule B — fewer than T beautiful miles per extra minute**

| T | 10–25 km | 25–50 km | 50–100 km | 100–200 km | all |
|---|---|---|---|---|---|
| 0.05 | 43% (37–49) | 18% (14–24) | 5% (3–9) | 1% (0–3) | 17% (15–19) |
| 0.1 | 47% (41–54) | 24% (19–30) | 13% (9–18) | 3% (1–6) | 22% (19–25) |
| 0.2 | 52% (46–58) | 41% (35–48) | 40% (34–46) | 23% (18–29) | 39% (36–42) |
| 0.3 | 60% (54–66) | 56% (50–62) | 59% (52–65) | 49% (43–55) | 56% (53–59) |
| 0.5 | 73% (67–78) | 74% (68–79) | 80% (74–84) | 73% (67–78) | 75% (72–77) |

Rule B is the only one of the three that is close to **band-neutral** in the
0.2–0.5 range — at T = 0.3 it fires on 49–60% everywhere, at T = 0.5 on 73–80%.
If one threshold has to serve every trip length, this is the statistic that
allows it. Below T = 0.2 it collapses back into a short-trip detector.

The ratio's denominator is floored at 0.1 minutes, so "gained nothing for
nothing" scores 0 and fires, while "gained five miles for nothing" scores 50 and
never does.

**Rule C — the scenic route is under T% beautiful**

| T | 10–25 km | 25–50 km | 50–100 km | 100–200 km | all |
|---|---|---|---|---|---|
| 10% | 30% (25–36) | 20% (15–25) | 13% (9–17) | 4% (2–7) | 17% (14–19) |
| 20% | 55% (48–61) | 50% (43–56) | 52% (46–58) | 37% (31–43) | 48% (45–51) |
| 30% | 72% (67–78) | 73% (67–78) | 73% (67–78) | 65% (59–70) | 71% (68–74) |
| 40% | 83% (78–88) | 87% (82–91) | 89% (85–93) | 82% (77–86) | 85% (83–88) |

Rule C answers a different question — "the drive you are about to get is mostly
not beautiful" rather than "we could not improve on the fast route" — and it is
also nearly band-neutral. Note it fires on 71% of everything at 30%, so as a
warning it would be background noise; it is more useful as a description than as
an alert.

**To fire on the worst ~10%** of trips regardless of band — and note
`isSameDrive` already covers 11.5%, so a replacement rule has to beat that, not
just reach it — the candidates are
Rule A at T ≈ 0.25 mi (1–48% by band, 18% overall — but that is *all* short
trips), Rule B at T ≈ 0.05–0.1, or Rule C at T ≈ 10%. Only Rule B's 0.05 row
comes close to hitting one-in-ten in the two longest bands without hitting
one-in-two in the shortest.

**The same rules under town-off**, each measured on its own consistent ruler.
The two settings' fire-rates may be compared *as policies* — "if we shipped X
and rule Y, this is how often it would fire" — but the underlying beautiful-km
values must not be differenced across them — see *The self-scoring trap,
measured*, below.

| | 10–25 km | 25–50 km | 50–100 km | 100–200 km | all |
|---|---|---|---|---|---|
| median beautiful mi gained | 0.44 | 3.41 | 8.99 | 24.17 | — |
| median mi per extra min | 0.21 | 0.31 | 0.29 | 0.36 | — |
| Rule A fires at T = 1 mi | 59% | 22% | 5% | 0% | 22% |
| Rule B fires at T = 0.3 | 57% | 49% | 52% | 42% | 50% |
| Rule C fires at T = 30% | 71% | 65% | 66% | 56% | 65% |

Across all three rules and all fourteen thresholds, town-off fires at or below
the shipped rate in every band — never above, and usually a few points below.
Both the levels and the shape of the trade survive the weight change, so nothing
in Q1 or Q2 is an artifact of which weight vector was chosen.

**One implementation note the choice depends on.** `/api/route` returns `km`,
`minutes`, `mean_score` and the `scenery_km` breakdown, and nothing else
(`RouteResult.geojson`). Beautiful-km — the statistic Rules A and B are built
on — is not in the response, and is not the same thing as `scenery_km` (which
thresholds the raw `c_*` components at 0.4, not the blended score at 7.0).
Adopting A or B means the server has to start returning it. Rule C needs the
same. Only a `mean_score`-based rule works with the API as it stands — which is
exactly what `isSameDrive` is, and exactly why it sees the 106 identical drives
and misses 27 of the 30 strictly-worse ones. Extending the warning beyond
"same drive" and keeping it on `mean_score` are not both possible.

---

## Q3 — is `pref` monotone, and is 0.50 a sensible default?

60 pairs, 15 per band, sweeping pref ∈ {0, 0.25, 0.50, 0.75, 1.00} at shipped
weights. Comparing across pref is valid: pref enters the edge cost, not the
score blend, so all five routes of a pair are measured with one ruler.

- **Minutes are monotone: 0 violations of 60** (CI 0.0–6.0).
- **Beautiful-km is not: 5 of 60** go backwards somewhere (8.3%, CI 3.6–18.1).
- 7 of 60 (11.7%) return the *same route* at pref 0 and pref 0.5.

The split is exactly what the cost function predicts and is worth stating as
theory, because it says which of the two results is a fact about the router and
which is a fact about the metric. Dijkstra minimises
`minutes + pref² · BETA · Σ km(1 − score/10)`. By the standard parametric
argument, raising the multiplier cannot lower the chosen route's time or raise
its penalty — so **monotone minutes is guaranteed**, and the 0/60 is a passed
self-check rather than a discovery. Beautiful-km is a *threshold* functional
(km scoring ≥ 7.0), which the objective never optimises, so nothing guarantees
it and 8.3% is the real measurement.

| pair | pref 0 | pref 0.25 | pref 0.5 | pref 0.75 | pref 1 |
|---|---|---|---|---|---|
| 25-50:0012 | 1.35 | **0.83** | 4.31 | 4.31 | 4.78 |
| 50-100:0012 | 2.07 | 2.07 | 20.83 | **18.30** | 19.33 |
| 50-100:0014 | 11.71 | 11.71 | 32.90 | **32.54** | 33.26 |
| 100-200:0010 | 8.85 | **7.66** | 40.03 | 48.42 | 67.71 |
| 100-200:0012 | 23.03 | **19.03** | 43.61 | 43.61 | 46.55 |

Three of the five dip at **0 → 0.25**: a small non-zero pref moves the route
without buying enough to pay for the move. That is the same symptom
`docs/loop-routes-design.md` §5 records for loops, arriving by a different
route — there is no turnaround ranking here, just a threshold metric that the
objective does not track. The endpoints behave in all five cases, as they do
for loops.

**What the slider is worth**

| step | strength (pref²) | cum. extra min (p50) | cum. beautiful mi (p50) | marginal mi/min (p50) | step changes nothing |
|---|---|---|---|---|---|
| 0 (definitional) | 0.00 | 0.0 | 0.00 | — | — |
| 0 → 0.25 | 0.06 | **0.1** | **0.03** | 0.01 | 33% |
| 0.25 → 0.5 | 0.25 | 20.0 | 5.24 | 0.25 | 17% |
| 0.5 → 0.75 | 0.56 | 24.4 | 6.88 | 0.24 | 25% |
| 0.75 → 1 | 1.00 | 25.6 | 9.32 | 0.27 | 30% |

| span | extra min (p50) | beautiful mi (p50) | per-pair mi/min (p50) | aggregate mi/min |
|---|---|---|---|---|
| pref 0 → 0.5 | 20.0 | 5.24 | 0.33 | 0.35 |
| pref 0.5 → 1 | 5.6 | 2.26 | 0.35 | **0.44** |
| pref 0 → 1 | 25.6 | 9.32 | 0.38 | 0.38 |

**The bottom quarter of the slider is dead.** The control is linear —
`Slider(value: $model.pref, in: 0...1)`, `ios/Sources/RoutePanel.swift:376` —
but `PREF_CURVE = 2.0`, so strength is pref², and pref 0.25 is 6% strength:
median 0.1 extra minutes and 0.03 extra beautiful miles over pref 0, with a
third of pairs returning an identical route. A user who nudges the slider off
zero gets nothing, and three of the five non-monotonicities live there.

**0.50 is defensible, and it is not the efficient point.** It takes **71%** of
what pref 1.0 gains for **75%** of what pref 1.0 spends — very slightly worse
than pro-rata. The top half of the travel is the cheaper half (aggregate 0.44
mi/min against 0.35 for the bottom half), because most of the time cost is paid
crossing 0.25 → 0.5 and the route has largely settled by 0.5. So: nothing here
argues 0.50 is *wrong*, and it lands just past the expensive step; but "the
midpoint is the balanced choice" is not what the numbers say, and the usable
range is really 0.25–1.0 rather than 0–1.

**This is the question the sample cannot fully settle.** 60 pairs puts the
beautiful-km violation rate at 8.3% with a CI of **3.6–18.1%** — wide enough to
cover "rare curiosity" and "one trip in six". It is enough to say the direction
of the effect (dips concentrate at the bottom of the slider, endpoints behave)
and enough to have found five concrete examples. It is not enough to quote a
rate. **250 sweep pairs would bring the CI to about ±3.5 points** at this
estimate, and costs 250 × 5 ≈ 1,250 routes ≈ 6 minutes: cheap, and worth doing
before anything is changed on the strength of the 8.3%.

---

## Q4 — does turning `town` off hold up beyond three pairs?

983 pairs, pref 0.50, `w_town` 1.0 against 0.0. **The obvious comparison is
invalid** and the study does not make it: `Router._edge_scores` renormalises the
weight vector to hold total tunable mass constant, so zeroing `town` scales the
other five *up*, and `mean_score` and beautiful-km land on a different scale.
The verdict is taken on `scenery_km` — thresholded on the raw `c_*` columns,
therefore scale-free — plus minutes and km.

**First half of the answer: on 47.1% of pairs (463/983, CI 44.0–50.2) the route
does not change at all.** Among the 520 that do:

| metric | median Δ | 95% CI on median | mean Δ | p10 | p90 |
|---|---|---|---|---|---|
| minutes | **+0.214** | +0.096 … +0.461 | +0.388 | −4.37 | +5.41 |
| km | +0.152 | +0.064 … +0.250 | +0.901 | −1.29 | +2.92 |
| forest/park | **+0.851** | +0.561 … +1.202 | +2.658 | −1.26 | +8.41 |
| water | +0.323 | +0.072 … +0.626 | +1.396 | −3.65 | +7.63 |
| hills | +0.074 | +0.000 … +0.366 | +1.609 | −2.04 | +6.07 |
| farmland | 0.000 | +0.000 … +0.000 | +0.370 | −1.41 | +2.52 |
| coast | 0.000 | +0.000 … +0.000 | −0.117 | +0.00 | +0.02 |
| town | **−2.760** | −3.086 … −2.344 | −4.665 | −12.35 | +0.00 |

Sign tests over all 983 (ties dropped) put direction beyond doubt where it
matters: town 51 up / 450 down (p ≈ 1e-70), forest 354/161 (p = 3e-17), hills
278/155 (p = 5e-9), water 300/191 (p = 1e-6), minutes 319/201 (p = 3e-7),
farmland 166/115 (p = 0.003). **Coast is the one null: 56/48, p = 0.49.**

**It does what it says, at a price that is small but real.** Turning `town` off
trades about 2.8 km of town for about 0.9 km of forest and 0.3 km of water, for
about 0.2 extra minutes and 0.15 extra km, on the half of trips where it changes
anything. The effect is decisively in the intended direction at 983 pairs, which
is a far stronger footing than three pairs — the mechanism holds up.

**What it does not settle: whether drivers want that.** The census can show the
router responds to the weight as designed; it cannot show that less town and
more forest is a better drive. That is a preference question, and the
uncommitted `docs/driver-preferences-study.md` draft argues on separate grounds
— `c_urban` is not uniformly mapped across New England — that it should stay a
user control rather than become a default. Nothing here contradicts that, and
nothing here supports changing the default either way. What it does remove is
the "measured on three pairs" objection to the mechanism.

---

## The self-scoring trap, measured

The fastest arm is routed at pref 0, which zeroes the scenery term, so its path
cannot depend on the beauty weights. The census routes it separately under both
weight settings anyway, and confirms the paths are identical on **all 983
pairs**. That makes the two arms a clean instrument: same road, two rulers.

| metric, on one identical path | median Δ | p10 | p90 | largest \|Δ\| |
|---|---|---|---|---|
| beautiful_km | +0.001 | −1.018 | +3.656 | **22.887** |
| mean_score | +0.057 | −0.179 | +0.252 | 0.759 |

**Up to 22.9 km of "beautiful road" appears or vanishes on a route nobody
changed**, purely from renormalising the weight vector. Any cross-weight
comparison of beautiful-km or `mean_score` is reading that number as if it were
a route difference. This is what reversed the first pass of the
driver-preferences study, and it is the reason Q4 above is answered on
`scenery_km`.

The trap's reach is exactly as the brief scoped it, and the study relies on both
halves: comparing across `pref` at fixed weights (Q3) and comparing the two arms
of one setting (Q1, Q2) are both valid, and being over-cautious about them would
have cost the two most useful sections here.

---

## What this cannot settle

1. **Q3's non-monotonicity rate.** 8.3% with a CI of 3.6–18.1 at 60 pairs. Fix
   with 250 sweep pairs (~6 minutes); see Q3.
2. **Whether any of these warnings should exist.** The census gives fire-rates
   for a stated rule. It cannot say what a driver does when warned, or what
   fraction of a "little to offer" trip the driver was going to take anyway.
3. **Whether town-off is a better product.** Mechanism yes, preference no.
   Needs marks, not routes — the instrument the driver-preferences draft uses.
4. **Anything about loops.** Point-to-point only. The pref finding here (bottom
   quarter dead, endpoints behave) rhymes with the loop finding but was measured
   on a different code path.
5. **Region-level variation.** The frame is New England pooled. `c_forest` and
   `c_urban` are known to be mapped unevenly across the states
   (`docs/geodata-peer-review-verdict.md`), so a per-state cut of these same
   983 routes would probably not be flat. The CSV has the coordinates; it was
   not cut that way here.

## Files

- `tools/route_census.py` — routes the sample and reports it. `--report-only`
  re-analyses without re-routing.
- `docs/route-census/census-pairs.csv` — 1,000 rows: the sample, snap offsets,
  status, sweep membership.
- `docs/route-census/census-routes.csv` — 4,232 rows: one per route, with km,
  minutes, `mean_score`, beautiful-km and the six `scenery_km` columns.
- `docs/route-census/census-summary.json` — build dir, build date, seed,
  weight sets, status counts, timings.
