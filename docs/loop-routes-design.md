# Loop routes: a start point, a distance slider, and a shuffle button

**Status: designed, measured, and now built — see §12 for what changed on the
way.** Architecture 1 shipped: `pipeline/looper.py`, `/api/loop` and `via` on
`/api/route`, and a Loop segment in the planning sheet. The design below is left
as it was written, with §12 recording where building it proved the design wrong.
The original status line read:

> **Designed and measured. No source file touched, no code written.**
Everything below was measured against the shipped graph at the shipped
calibration (`BETA = 8.0`, `PREF_CURVE = 2.0`, pref 1.0 unless stated) on
2026-08-26, from three starts chosen to span the geography: Needham (suburb),
Petersham (rural), Boston (dense). Measurement scripts were scratch files
outside the repo and are not committed; every number here is reproducible from
the recipe in §11.

The brief asked for two or three architectures with costs and a recommendation.
There are three below, plus the two that the brief ruled out in advance and one
more that measurement ruled out. **The recommendation is Architecture 1.**

---

## 1. The headline result

The enabling fact is stronger than the brief supposed. One full-graph Dijkstra
does not merely rank candidate turnarounds by weighted cost — with about 120 ms
of vectorised post-processing it yields, **exactly**, the road-km, the drive
minutes and the collected scenic-km of the chosen path to *every one of the
313,950 nodes*.

Verified against `route()` on random targets (Needham start, pref 1.0):

| node | `route().km` | field km | diff | `route().minutes` | field minutes |
|---|---|---|---|---|---|
| 185459 | 28.407 | 28.407 | 0.0000 | 39.55 | 39.55 |
| 221600 | 40.486 | 40.486 | 0.0000 | 63.60 | 63.60 |
| 283517 | 19.977 | 19.977 | 0.0000 | 35.36 | 35.36 |
| 299544 | 40.905 | 40.905 | 0.0000 | 64.19 | 64.19 |
| 208185 | 24.958 | 24.958 | 0.0000 | 37.33 | 37.33 |
| 248375 | 42.628 | 42.628 | 0.0000 | 53.88 | 53.88 |

The mechanism is not a second search. `dijkstra(..., return_predecessors=True)`
already returns the shortest-path tree. Any quantity that sums along a path can
be accumulated up that tree by **pointer doubling** — `A ← A[A]`,
`W ← W + W[A]`, about 14 iterations of two gathers over 313,950 int64s — which
turns one predecessor array into per-node km, minutes and scenic-km
simultaneously. It is exact, not an approximation, because it re-sums the same
per-edge quantities `_collect` sums.

This matters because **it retires the brief's framing of cost bands.** Weighted
cost is minutes-equivalent and mixes time with ugliness, so a cost band is not a
distance band:

| weighted-cost band | nodes | km p5 | km p50 | km p95 | km max |
|---|---|---|---|---|---|
| 20–25 | 460 | 3.2 | 4.1 | 4.9 | 5.4 |
| 45–55 | 3,764 | 7.8 | 9.0 | 10.3 | 11.8 |
| 90–110 | 14,971 | 16.5 | 19.0 | 22.0 | 24.7 |

A ±10% cost band spans roughly ±15% in km, and the conversion factor (~5.2 cost
per km here) moves with pref, with the local road mix, and with the user's
beauty weights. You cannot drive a km slider off cost bands. You do not have to:
filter on the accumulated km, which is exact.

**Consequence for the product: the distance slider is free.** Re-filtering all
313,950 candidate loops for a new slider value is **1.1 ms**. Dragging the
distance slider costs no Dijkstra pass at all.

### Costs, measured

| operation | time |
|---|---|
| `csr_matrix` build from the pair arrays | 9 ms |
| full-graph Dijkstra, one source, with predecessors | 113 ms |
| …plus km / scenic-km / minutes accumulation (one "field") | 236 ms |
| reverse field (transposed graph, multi-sourced over the start's copies) | 212 ms |
| re-filter 313,950 candidates on a new distance target | 1.1 ms |
| build one loop from a cached field (1 pass + path reconstruction) | 130–145 ms |
| warm a pool of 8 sector loops | 1,037 ms |

(The brief quotes 193 ms for the Dijkstra; this machine does it in 113 ms. Same
order, and the ratios below are what matter.)

---

## 2. What was ruled out, and by what

### The two traps in the brief, confirmed

**`route()` with a nearby fake destination.** Not re-tested; the brief's
measurement and `docs/scenery-cap-options.md` settle it. The objective
accumulates with distance, so the search returns the shortest thing available
and the slider is inert.

**The scenery objective is not touched.** No proposal here changes `BETA` or
`PREF_CURVE`, and every number above is at the shipped values. §9 states where
this feature and the scenery-cap work share ground.

### A third one, ruled out by measurement: exact edge-disjoint loops

The textbook way to get a non-retracing loop is a minimum-cost pair of
edge-disjoint paths — Suurballe's algorithm, or min-cost flow of value 2, both
of which reduce to about two Dijkstra passes and would fit the existing scipy
call. **It is unusable here, and the reason is a hard number:**

```
dead-end junctions (undirected degree 1):  53,216 of 310,162  (17.2%)
```

From five randomly sampled dead-end starts, target 20 km:

| start node | soft penalty ×3 | hard (fully disjoint) |
|---|---|---|
| 65187 | 21.4 km, 5% repeated, score 6.80 | **no loop exists** |
| 265724 | 19.7 km, 12% repeated, score 5.65 | **no loop exists** |
| 32885 | 20.2 km, 6% repeated, score 5.32 | **no loop exists** |
| 81923 | 20.9 km, 6% repeated, score 6.13 | **no loop exists** |
| 65812 | 22.7 km, 1% repeated, score 4.65 | **no loop exists** |

A house on a cul-de-sac has no edge-disjoint loop through it — every loop must
drive the stub twice — and 17.2% of junctions are such stubs before counting the
streets behind them. An exact formulation returns "no loop" for exactly the
suburban starts the feature exists to serve. **Every design below therefore uses
a soft penalty on repeated roads, never a hard constraint.** This is the single
most important decision in the document and it is the one an optimality-minded
implementation would get wrong.

---

## 3. Architecture 1 — mirror field, then a penalised leg home (**recommended**)

**Cache, once per (start node, pref, beauty weights):**

1. Forward field from the start: one Dijkstra + accumulation → cost, km,
   minutes, scenic-km and the predecessor tree to every node. 236 ms.
2. Reverse field: the same on the **transposed** graph, multi-sourced with
   scipy's `min_only=True` over `{start} ∪ node_copies[start]`. 212 ms.

Two passes — measured 448 ms and 608 ms on two runs, so call it ~0.5 s — and now every node `v` carries an estimate of the whole
out-and-back loop through it: `km_out(v) + km_back(v)`, its minutes, and its
collected scenic-km.

The transpose is the right way to get the way home and it is *not* a symmetry
assumption. Reverse-Dijkstra on the transpose of a directed graph gives exactly
the least-cost legal drive *to* the source, node splits and all. It is
multi-sourced because arriving at a split junction may legitimately land on any
copy — the same reason `route()` picks the cheapest copy for a destination. And
the asymmetry is real, so it has to be computed rather than mirrored:

```
cost home − cost out:   p5 −8.11   p50 −0.73   p95 +3.75   (minutes-equivalent)
km   home − km   out:   p5 −6.42   p50 +0.08   p95 +2.23
identical km to within 1 m: 0.8% of nodes
```

**Select (free):** filter on estimated loop km, rank by estimated scenic-km per
km, bucket by the compass bearing of `v` from the start. 1.1 ms over the whole
graph.

**Build (one pass):** take the cached outbound tree path to `v`; multiply the
weight of every *undirected* edge on it by **3**; run one Dijkstra from `v` and
walk home. Penalising the undirected edge id — not the directed slot — is what
stops the return leg driving the same road backwards. 130–145 ms.

### The measurement that makes this the recommendation

A plain out-and-back retraces badly. Repeated-km share over the 12
best-scoring candidates:

| start | 20 km | 40 km | 80 km |
|---|---|---|---|
| Needham (suburb) | 45% | 26% | 31% |
| Petersham (rural) | **49%** | **50%** | **49%** |
| Boston (dense) | 4% | 20% | 26% |

Petersham sits at 50% — the arithmetic maximum, a pure there-and-back on one
road. That is the feature failing.

One penalised pass fixes it completely:

| start, 40 km target | km | score | repeated | extra cost |
|---|---|---|---|---|
| Needham, plain out-and-back | 43.2 | 6.17 | 26% | — |
| Needham, penalty ×3 | 44.1 | 5.92 | **0%** | +1 pass, 135 ms |
| Needham, penalty ×∞ | 44.1 | 5.92 | 0% | +1 pass, 133 ms |
| Petersham, plain | 43.1 | 6.49 | 50% | — |
| Petersham, penalty ×3 | 45.7 | 6.32 | **0%** | +1 pass, 130 ms |
| Petersham, penalty ×∞ | 50.4 | 6.44 | 0% | +1 pass, 132 ms |
| Boston, plain | 36.9 | 7.46 | 18% | — |
| Boston, penalty ×3 | 37.2 | 7.36 | **0%** | +1 pass, 133 ms |

Zero repeated km at all three starts, for 135 ms and about 0.15 points of mean
score. ×3 and ×∞ give the same retrace, and ×3 stays closer to the requested
distance (45.7 km vs 50.4 km at Petersham), so **×3 — soft — is the right
setting**: it costs nothing in retrace and it degrades gracefully where ×∞ has
no answer at all (§2).

### Regenerate under Architecture 1

Regenerate advances to the next compass sector and rebuilds: **one Dijkstra pass,
~145 ms.** Eight loops from Needham, 40 km target, one per sector:

| sector | km | score | repeated | candidates |
|---|---|---|---|---|
| N | 39.6 | 6.05 | 1% | 3,181 |
| NE | 44.1 | 5.92 | 0% | 4,151 |
| E | 44.2 | 5.88 | 1% | 679 |
| SE | 42.0 | 5.00 | 1% | 908 |
| S | 42.4 | 5.27 | 3% | 634 |
| SW | 47.5 | 5.57 | 0% | 769 |
| W | 34.0 | 5.27 | 0% | 836 |
| NW | 39.7 | 6.12 | 5% | 557 |

**Pairwise road overlap (Jaccard) between those eight loops: min 0%, median 1%,
max 16%** (28 pairs). At Petersham: median 1%, max 38%. These are genuinely
different drives, not jitters of one. That is the shuffle button working.

Regenerate can be made *free*. Warming all eight sector loops costs 1,037 ms of
background work after the first loop is returned. Do that, and every press from
the second onward is served from memory at ~0 ms.

**Latency the user actually waits for:**

| action | passes | wait |
|---|---|---|
| first loop at a new start (or after moving the pref slider) | 2 cached + 3 build | ~1.0 s |
| dragging the distance slider (candidate count, previews) | 0 | 1.1 ms |
| regenerate, pool warm | 0 | ~0 ms |
| regenerate, pool cold | 1–3 | 145–435 ms |
| changing pref | 2 cached + 3 build | ~1.0 s |

---

## 4. Architecture 2 — multi-waypoint sector circuit

Cache the two start fields, then also cache a forward field from each of `k`
**anchors** — one per compass sector, at roughly a third of the target distance.
With `k` anchor fields plus the reverse field, every three-leg loop
`start → anchor_i → v → start` is costed instantly for all `i` and all `v`: that
is `k × 313,950` costed circuits from `k + 2` passes. Regenerate is then array
math forever, with no pool and no per-press Dijkstra.

That is a genuinely attractive latency shape — ~2.5 s of warm-up at `k = 8`,
then free. **It was measured and it loses on quality.**

| start, 40 km | separation | km | score | repeated | v2 options | cost |
|---|---|---|---|---|---|---|
| Needham | ~90° | 38.9 | 6.34 | 14% | 3,321 | +1 pass, 247 ms |
| Needham | ~140° | 37.9 | 6.26 | 13% | 1,245 | +1 pass, 249 ms |
| Petersham | ~90° | 43.1 | 6.54 | 6% | 278 | +1 pass, 249 ms |
| Petersham | ~140° | 37.1 | 6.45 | **37%** | 27 | +1 pass, 253 ms |
| Boston | ~90° | 36.9 | 7.29 | 11% | 4,087 | +1 pass, 250 ms |
| Boston | ~140° | 40.5 | 7.09 | 15% | 1,209 | +1 pass, 241 ms |

Retrace 6–37% against Architecture 1's 0%. **The intuition that geographic
separation prevents retracing is wrong**, and the reason is visible once
measured: the middle leg `anchor → v` is free to run back through the start
region, and at a wide bearing separation it usually must. Forcing the separation
wider makes it worse and empties the candidate set (27 options at Petersham).

Keep it on the shelf for one reason: it is the only architecture here that
extends to genuine 3-and-4-waypoint *shapes* if "make it ramble" ever becomes a
product requirement (see §7). It would need the same penalty machinery as
Architecture 1 layered on top, at which point it is Architecture 1 with extra
passes.

---

## 5. Architecture 3 — chain waypoints on a geometric ring

The intuitive design: a loop of length `D` is a circle of radius `D / 2π`, so
sample `m` points on that circle around the start, snap each to a node, and
chain `route()` calls through them. `m + 1` passes per loop, every loop.
Distance control should be excellent because it is geometric.

**It is the worst option on every axis, including the one it was supposed to
win.**

| start | target | m | built km | error | score | repeated | passes | ms |
|---|---|---|---|---|---|---|---|---|
| Needham | 20 | 4 | 30.1 | **+51%** | 5.24 | 1% | 5 | 612 |
| Needham | 20 | 6 | 37.9 | **+89%** | 5.14 | 9% | 7 | 867 |
| Needham | 40 | 4 | 51.3 | +28% | 4.77 | 3% | 5 | 665 |
| Needham | 80 | 6 | 124.5 | +56% | 5.46 | 2% | 7 | 859 |
| Petersham | 20 | 6 | 40.2 | **+101%** | 5.04 | 30% | 7 | 831 |
| Petersham | 40 | 4 | 62.9 | +57% | 5.38 | 35% | 5 | 593 |
| Boston | 40 | 6 | 74.6 | +87% | 5.08 | 4% | 7 | 889 |
| Boston | 80 | 4 | 148.1 | +85% | 5.83 | 14% | 5 | 601 |

Distance error +28% to +101%; scores 4.5–5.8 against Architecture 1's 5.9–7.4;
rural retrace up to 35%; 590–1,050 ms per loop with no free regenerate.

The failure is structural, not tunable. A circle's circumference is a Euclidean
*lower bound* on the road distance between consecutive ring points — real roads
wander between them, and every leg overshoots, so the built loop is always much
longer than the ring it was drawn from. More waypoints make it worse, not
better, because each added leg adds its own overshoot. Compensating by shrinking
the radius just makes a smaller, uglier loop with the same error band.

Documented at this length because it is the first idea anyone has, it sounds
like the principled one, and it is measurably the worst.

---

## 6. The distance slider: an honest answer

**Range.** Not graph-limited. The largest available out-and-back loop is 516 km
(Needham), 622 km (Petersham), 576 km (Boston). The top of the slider is a
product choice; **5–200 km** is the useful span.

**Accuracy.** The estimate from the cached fields is exact for the *mirror*
loop, but the penalised return leg is a different path, and its km differs from
the mirror's by −13% to +14%. The estimate is a filter, not a prediction. The
fix is to build a few candidates spanning the band and keep the one closest to
target:

| start | target | k=1 | k=3 | k=5 |
|---|---|---|---|---|
| Needham | 20 | 24.3 (+21%) | 19.0 (−5%) | 20.8 (+4%) |
| Needham | 40 | 44.2 (+11%) | 39.7 (−1%) | 39.8 (−0%) |
| Needham | 80 | 69.7 (−13%) | 79.0 (−1%) | 81.2 (+2%) |
| Petersham | 20 | 17.2 (−14%) | 20.2 (+1%) | 20.2 (+1%) |
| Petersham | 40 | 45.7 (+14%) | 42.2 (+6%) | 41.7 (+4%) |
| Petersham | 80 | 88.4 (+11%) | 83.7 (+5%) | 84.1 (+5%) |
| Boston | 20 | 18.6 (−7%) | 18.6 (−7%) | 19.5 (−3%) |
| Boston | 40 | 37.1 (−7%) | 40.9 (+2%) | 40.9 (+2%) |
| Boston | 80 | 81.5 (+2%) | 80.4 (+1%) | 81.5 (+2%) |

**k=3 gives ±7%, k=5 gives ±5%, at 390 ms and 650 ms.** Recommend **k=3** for
the interactive path and k=5 when warming the pool in the background.

Do **not** iterate by re-scaling the target from the built length. It looks
obvious and it oscillates, because re-targeting jumps to a different candidate
with a different stretch: at Petersham 80 km a second iteration went from +5% to
−19%, and at Boston 80 km from +1% to −10%. Building `k` in parallel is stable;
iterating is not.

**Failure modes, and what to show.**

- *Short loops from a rural start.* Petersham at 10 km: only 21 candidates and
  29% repeated km — the one case where the penalty cannot find a way round.
  Below roughly 15 km in open country the honest answer is "the roads here don't
  make a loop this short", with the shortest good loop offered instead.
- *Empty sectors.* Boston has **zero** candidates due east at 20 km and zero
  north-east at 40 and 80 km — that is the harbour and the ocean. Petersham has
  9 candidates south-west at 40 km against 289 north-west. Regenerate must cycle
  the *populated* sectors and may find only five or six; the UI must not promise
  eight directions.
- *Dead-end starts.* 17.2% of junctions. They work — 1–12% repeated km under the
  soft penalty (§2) — and they are the reason the penalty is soft.

---

## 7. What these loops are actually like, and the honest case for the feature

**They are lenses, not rambles.** The chosen 39.8 km Needham loop turns around
14.7 km from home as the crow flies. A *circle* of circumference 39.8 km has a
radius of 6.3 km. These loops reach 2.3× further from home than a ring of the
same length: they go out as far as the budget allows and come back a different
way. That is a direct consequence of leaving the router's objective alone — each
leg is still individually as short as the objective wants, and length is bought
by choosing a distant turnaround rather than by wandering. If the product wants
a ramble, that needs the objective work from the other session, or Architecture
2's extra waypoints.

**The scenery gain over point-to-point is real but modest, and it is not the
main point.** For a Needham start, comparing like with like:

| what you drive, ~40 km | mean score | km scoring ≥7 |
|---|---|---|
| the *fastest* 40 km round trip (pref 0) | **1.94** | 0.1 |
| the Massachusetts road network, km-weighted average | 4.17 | — |
| scenic route to an arbitrary destination, median of 18 (pref 1.0) | 5.59 | — |
| scenic route to the *best* of those 18 destinations | 5.96 | — |
| **the generated loop** | **6.03** | **18.8** |

So the loop matches the best destination a user could have guessed and beats the
median guess by ~0.4 — which is honest but not a revolution, and it means the
brief's premise ("choosing the geography is the dominant term") holds only
weakly for the *score*. The feature's real value is elsewhere and is not
subtle:

- It is a **closed** drive at a **chosen length** with **0% repeated road**, and
  none of those three things is available today at any setting.
- It beats the actual alternative — "just go for a drive" — by **1.94 → 6.03**,
  with 18.8 of 39.8 km on roads scoring 7 or better against 0.1 km.
- The user supplies no destination, so there is nothing to guess wrong.

Also worth recording: loop mean scores of 6.0–7.5 sit **above the 5.6 ceiling**
`router.py:69-73` documents for point-to-point at any `BETA`. Not because
anything was fixed, but because a loop with no destination constraint can
decline to leave the pretty roads.

**Replacing `mean_score`.** The brief is right that it is length-weighted, but
under this design the slider fixes the length, so every candidate and every
regenerate is being compared at the same distance and `mean_score` is a fair
comparator. Report three numbers, not one:

1. **mean score** — continuity with the existing UI, fair at fixed length.
2. **km on roads scoring ≥ 7** — by far the most legible. 18.8 km vs 0.1 km
   separates the scenic loop from the fast one 188-fold, where the means only
   manage 6.03 vs 1.94.
3. **repeated km** — the loop-specific defect. It must be visible, because it is
   the number that tells the user their 40 km drive is really a 20 km drive
   twice.

---

## 8. Do not build more than one loop to pick from

Tempting, and measured to be worthless. Building `k` loops in the band and
keeping the **best-scoring** one:

| start | k=1 | k=5 | k=15 | k=40 | cost at k=40 |
|---|---|---|---|---|---|
| Needham, 40 km | 6.03 | 6.03 | 6.12 | 6.05 | 6,164 ms |
| Petersham, 40 km | 6.18 | 5.32 | 5.32 | 5.44 | 6,077 ms |

Forty passes and six seconds buy **nothing** over one pass and 144 ms. The
cached ranking is a good filter and a useless tie-breaker — Spearman ρ between
estimated and built score over the top 12 is **+0.24** at Needham and **−0.21**
at Petersham — but it does not matter, because the top candidates are all
equally good: trusting rank 1 costs 0.01 points at Needham and 0.13 at
Petersham. Build `k` for **distance** (§6), never for scenery.

---

## 9. Shared ground with the scenery-cap work — and what it does not need

`docs/scenery-cap-options.md` option 5 is "an explicit detour budget as a second
slider… sidesteps the objective problem by making length the input rather than an
emergent property." **The loop tab is that option, shipped on the feature where
the budget is the natural input** — the user is choosing how long a drive they
want, not grudgingly tolerating a detour.

The formal reason it helps: at *fixed* total km,

    minimise Σ km·(1 − score/10)  ≡  maximise Σ km·score

because `Σ km` is a constant. The length-shrinking pathology is a property of
letting length float, and it disappears when length is a constraint. Precisely
where that bites is worth stating, because it is only half the win: the
constraint is applied at **candidate selection**, not inside the Dijkstra, so
each leg is still individually length-minimising. That is exactly why these
loops are lenses rather than rambles (§7).

**What this feature needs from that session: nothing.** Every number here is at
the shipped `BETA = 8.0` and `PREF_CURVE = 2.0`, and the design works. If
option 5 wins for point-to-point, the two features share one piece of
machinery — a length-constrained filter over a cached cost field — and this
document is a working prototype of its behaviour. If option 2 (reward above a
baseline) wins instead, note that it produces negative edge weights, which
breaks scipy's `dijkstra` and therefore breaks **every** architecture here.
That is the one outcome of the other session that this design would have to be
re-costed against.

---

## 10. What it needs from the API and the app

### A new endpoint, not an extension of `/api/route`

`/api/route` takes two endpoints and returns two routes. A loop takes one
endpoint plus a length and returns one route plus alternatives. Overloading it
would mean a `to` parameter that must be absent, and a `fastest` field that has
no meaning. New endpoint:

```
GET /api/loop?from=LAT,LON&km=40[&pref=0..1][&w_<type>=...]
              [&session=<token>][&sector=NE][&exclude=<id>,<id>]
```

- `km` — the slider. Clamp to 5–200. Snap the start with `Router.snap` (no
  heading: a loop is planned from a parked car), and reject beyond `SNAP_MAX_M`
  exactly as `/api/route` does.
- `session` — an opaque token the server returns with the first response and the
  client echoes back. It is the cache key for the two fields. Without it every
  press pays the ~0.5 s of cached passes it does not have to.
- `sector` / `exclude` — what regenerate varies. `sector` requests a compass
  octant; `exclude` lists loop ids already shown so the server can pick
  something new. **Regenerate must be server-driven, not a client random seed**,
  because only the server knows which sectors are populated (§6).

Returns the same GeoJSON `Feature` shape as `/api/route`'s `scenic` — the client
already draws it — plus:

```
loop: { id, km, minutes, mean_score, km_above_7, repeated_km,
        turnaround: [lat, lon], sector: "NE" }
alternatives: [ { sector, id, km, mean_score } ... ]   // populated sectors only
target_km: 40, note: null | "nearest loop here is 18 km"
```

`alternatives` is what lets the app show real options instead of a blind shuffle,
and it is free — it comes out of the same filter.

### Cache sizing, which is a real constraint on this deployment

Per cached `(start node, pref, weights)`: two fields × five `float64`/`int32`
arrays × 313,950 nodes ≈ **25 MB**. The serving process is around 1 GB on a
Windows laptop behind a Cloudflare tunnel, so an **LRU of 4 starts (~100 MB)** is
the safe size, not 8. Two consequences to hand to whoever builds this:

- The distance slider does **not** invalidate the cache (it is a filter). Moving
  the **pref** slider **does** — it changes every edge weight. So a loop tab
  should not put a live pref slider beside the distance slider without accepting
  a ~1 s rebuild on every pref change. Better: set pref once, then shuffle.
- Warm the 8-sector pool in a background thread after returning the first loop
  (1,037 ms). From the second press onward regenerate is instant.

### What the iOS tab needs (not designed here)

A start field defaulting to current location; one distance slider (5–200 km,
live, free to drag); a regenerate button; the loop drawn as a closed line with
the turnaround marked; the three quality numbers from §7 with repeated-km
visible; a "directions available" affordance driven by `alternatives` that shows
five or six when that is all the geography has; and a graceful state for
`note` — "the roads here don't make a loop this short". Everything else is the
existing directions tab's map and step list.

---

## 11. What cannot be settled without building it

1. **The penalty factor.** 3.0 is one measured point at three starts, chosen
   because ×3, ×10 and ×∞ all gave 0% retrace while ×3 stayed closest to the
   requested distance. Whether 2 or 5 is better across hundreds of starts is a
   sweep, not a derivation.
2. **Whether the ranker should be scenic-km per km at all.** ρ = +0.24 / −0.21
   says it is not ordering the top of the band. It does not matter at the top
   (§8), but nothing here measures how badly it misranks the *middle* of the
   band, which is what `exclude`-driven regenerate will reach on the fifth or
   sixth press.
3. **Whether the lens shape is acceptable.** §7 says what it is; only driving
   one says whether it feels like a loop or like an out-and-back with a
   variation. This is the largest open product risk and the cheapest to answer —
   generate three and drive one.
4. **Turn-restriction legality at the turnaround.** The turnaround is the one
   node where a driver both arrives and departs, which is the case the
   split-node model does not directly encode. The construction is nonetheless
   sound, by an invariant rather than a test: `_apply_turn_restrictions`
   redirects *every* restricted approach's head to a copy (`router.py:478`), so
   an arc still pointing at the original index carries no restriction, and the
   original keeps all its exits. Arriving at the original index therefore implies
   the approach was legal to continue from. That is an argument, not a
   verification — a replay check over generated loops, in the shape of
   `tools/audit_directions.py`, should confirm it before ship.
5. **Cache hit rate.** The whole latency story assumes a user shuffles from one
   start. Unknown until there is traffic.
6. **Reproducing these numbers.** Load `Router` once (~13 s), then per start:
   build the per-pair weight/km/score arrays from `_edge_scores` and `_weights`
   collapsed with `np.minimum.at` over `slot_pair`; run `dijkstra(...,
   return_predecessors=True)` forward and on the `(u_head, u_tail)` transpose
   with `min_only=True`; accumulate km/minutes/scenic-km up the predecessor tree
   by pointer doubling; verify against `route().km` before trusting anything
   else. The verification in §1 is the check that catches an error in the
   accumulation, and it should be the first thing an implementation writes.

---

## 12. What changed when it was built

Six things. Four are corrections to numbers above; two are things the design
missed entirely.

**1. Five candidate builds, not three.** §6 recommended `k=3` interactive and
`k=5` in the background. Shipped is 5 everywhere, and the reason is not the
distance accuracy that §6 measured — it is a defect §6 could not see, because it
only looked at length. At `k=3` the slice holding the target sometimes offers
only a candidate that doubles back: at a Needham 40 km target it chose a 39.7 km
loop repeating 2.0 km over a 43.2 km loop repeating none. Narrower slices fix it.

| picks | worst distance error | worst repeated | mean repeated |
|---|---|---|---|
| 1 | +21% | — | — |
| 3 | −7% | 5.0% | 1.7% |
| 5 | +5% | 3.4% | 1.1% |

Weighting repeated road more heavily in the choice was tried first and is worse:
at 4x it saved 0.4% of retrace and cost 14% of distance accuracy. The shipped
rule adds distance error and repeated kilometres with **equal weight**, since
they are the same unit and the same complaint — a kilometre the driver did not
ask for — which leaves no exchange rate to tune.

**2. The 0% retrace figures in §3 are for one hand-picked turnaround, not for
what ships.** Those three loops were chosen for scenery. `plan` chooses for
distance, which is a different candidate: **1.1% repeated road on average and
3.4% at worst** over three starts and three targets. Still against 26%/50%/18%
with the penalty off, so the conclusion holds — but 0% was never the shipped
number.

**3. Latency is higher than §3 quoted, because of point 1.** Measured through the
endpoint rather than in a scratch script:

| action | measured |
|---|---|
| first loop at a new start | 1,216 ms |
| regenerate (warm cache) | ~650 ms |
| new distance, same start | 667 ms |
| after moving `pref` | 1,125 ms |
| an identical repeat request | ~0 ms |
| `/api/route`, for comparison | 281 ms |

§3's 145–435 ms was for 1–3 passes. Five is the price of the slider landing
within 5%. The background pool that would make regenerate free was **not**
built; identical requests are memoised instead, which covers shuffling forward
and back to the one you liked.

**4. The `session` token in §10 was over-engineering and is gone.** The cache key
is derived from the request itself — snapped start node, pref, weights — so the
client sends nothing extra and the server stays stateless from its point of view.

**5. `pref` is a poor control for loops, and not monotone.** Not noticed in
design. Measured at a Needham 40 km target:

| pref | 0.00 | 0.25 | 0.50 | 1.00 |
|---|---|---|---|---|
| mean score | 5.00 | **3.58** | 5.43 | 5.84 |
| km scoring ≥7 | 3.2 | 4.7 | 10.5 | 15.8 |

The endpoints behave; 0.25 comes back worse than 0.0. Two structural causes:
candidate turnarounds are ranked by scenery whatever `pref` is, so a small
non-zero pref moves *where you go* without buying the routing to justify it; and
the final choice among built loops is on distance and repeated road with no
scenery term, which is harmless at pref 1.0 where every candidate is pretty and
is not at low pref. With the length already pinned by the slider, pref has little
left to trade. **The tab pins it at 1.0** — which is also what the field cache
wants, since pref is the one parameter that invalidates it.

**6. Rerouting a loop needed a server change, which the plan said it would not.**
This is the design's real miss. A loop ends where it began, so its destination is
the driver's own driveway: `NavigationModel` reroutes there and the server
correctly returns the short way home. On the 39.8 km Needham loop, going 2 km off
route early replaced 35 remaining kilometres with **4.8**.

Aiming the replacement at the turnaround instead — the obvious fix, and the one
this document would have led to — makes the driver *arrive* at the turnaround,
halfway round. The replacement has to be pinned **through** it. So `/api/route`
gained an optional `via`, and `LoopPlanner.resume` runs the two legs and returns a
single `RouteResult` whose turn-by-turn reads continuously across the join: 41.7
km and 71 steps with one depart and one arrive, at 645 ms instead of 322 ms.

`resume` threads its second leg through whichever index of the waypoint the first
leg arrived at rather than the junction's original index, so a restricted
approach cannot be evaded at the waypoint — the same invariant §11 item 4 argues
for the turnaround.

### Still open

- **§11 items 1, 2, 3 and 5 stand.** The penalty factor is still one measured
  point; the ranker is still unmeasured in the middle of the band; nobody has
  driven one of these, which remains the largest product risk; cache hit rate is
  still unknown.
- **§11 item 4 is now asserted rather than argued** for arrival (a driver sitting
  at the start with the loop ahead does not latch), but the turn-restriction
  legality of the turnaround itself is still an invariant argument and not a
  replay test.
- **`heading` is dropped on a `via` route.** It picks which end of the driver's
  road to leave from, and this caller is mid-drive, so it matters — a rejoin can
  open by turning the car around. `resume` has no way to express it because the
  search starts from the node `snap` returned. Worth fixing.
- **The background pool** (§3) is unbuilt, and worth ~650 ms per regenerate.
