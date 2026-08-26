# Raising the scenery cap: five measured options

**Status: measured, nothing changed.** `BETA` is still 8.0, `PREF_CURVE` still
2.0, `_weights` untouched. This document exists to be chosen from; it does not
choose.

The first half is the diagnosis that started this (unchanged, and it holds up).
The second half is the five options, each measured over the same routes, with a
worked example and an honest algorithmic cost.

---

## Part 1 — the diagnosis

### The goal

Let the top of the preference slider produce genuinely longer, less efficient,
more scenic routes — "as long and inefficient as possible" at the maximum — and
present a few concrete options for where the slider's edges should sit.

### BETA is a dead lever

`BETA` is the minutes-equivalent penalty per km of fully-unscenic road at pref=1
(`router.py:74`). Swept across a **64-fold** range at pref=1.0:

| pair | fastest | BETA=8 | BETA=64 | BETA=512 |
|---|---|---|---|---|
| Harvard→Needham | 65.2 km / 1.47 | 49.2 km / 5.73 | 49.2 km / 5.73 | 49.6 km / 5.76 |
| Needham→Wachusett | 93.7 km / 1.42 | 70.9 km / 6.07 | 71.5 km / 6.11 | 71.5 km / 6.11 |
| Needham→Worcester | 66.5 km / 1.12 | 51.6 km / 4.75 | 59.7 km / 5.72 | 60.1 km / 5.75 |
| Needham→Wellesley | 5.4 km / 4.07 | 5.1 km / 3.93 | 5.1 km / 3.93 | 5.1 km / 3.93 |

Multiplying the penalty by 64 changes almost nothing, which confirms in the
field what `router.py:69-73` already says in writing. Unclamping `pref` past 1.0
is the same lever — `strength = pref ** PREF_CURVE` — and is equally dead.

### The mechanism

`_weights` (`router.py:880-881`):

```python
strength = max(0.0, min(1.0, pref)) ** PREF_CURVE
return self.d_minutes + strength * BETA * penalty[self.eidx]
```

with `penalty` proportional to `km * (1 - score/10)`. As `BETA` grows the
`minutes` term becomes negligible and the router minimises `Σ km × (1 − score/10)`
over the path. That sum has a name worth writing down:

    Σ km × (1 − score/10)  =  total_km  −  scenic_km

writing `scenic_km` for `Σ km × score/10`, the "how much prettiness did I drive
through" total. So at high BETA the router **maximises `scenic_km − total_km`**.
No road scores above 10, so `scenic_km ≤ total_km` always: an extra kilometre of
perfect road is *free*, and an extra kilometre of anything else is a **loss**.
There is no kilometre anywhere in Massachusetts that this objective wants to
add. Turning BETA up makes the router more determined to be short, not less.

### The `mean_score` inversion

At pref=1 the Needham→Wellesley route scores **3.90** where the *fastest* route
scores **4.05** — a scenic setting returning a less scenic road. The arithmetic,
in the units the router actually minimises:

    fastest:  5.65 km × (1 − 0.4046)  =  3.361 unscenic-km
    pref=1 :  5.36 km × (1 − 0.3904)  =  3.265 unscenic-km

The pref=1 route wins the objective by 0.096 unscenic-km **by being 290 m
shorter**, despite being uglier per kilometre. `mean_score` is a reporting
artifact, not the thing being optimised.

(That last sentence is right about `mean_score` and wrong about *this example*.
Scanned properly the inversion is a ~2% edge case, and on the cases where it
happens the route loses on total scenic-km too — so Wellesley is a demonstration
of the objective defect, not of the reporting one. See
[Is it general?](#the-needhamwellesley-inversion-is-it-general) at the end.)

---

## Part 2 — one correction before the options

The brief this grew out of said "the slider does not currently buy length at any
price". That is true of **distance** and false of **time**, and the difference
matters for what "leave it alone" means. Over ten origin-destination pairs, what
pref=1 buys against the fastest route today:

| | median | range |
|---|---|---|
| distance | **0.86×** | 0.76 – 1.01× |
| **travel time** | **1.61×** | 1.02 – 1.85× |
| total scenic-km | 2.89× | 0.92 – 5.80× |
| minutes on roads scoring ≥ 7 | 14.8× | see below |

(The `min ≥ 7` ratio is undefined at both ends of its range — Worcester→Groton's
fastest route has 0.0 minutes above 7 and Needham→Wellesley's pref=1 route has
0.0 — so read that row as the median of a set containing a zero and an infinity.
Stated in absolute minutes instead, which is cleaner: a median of **0.66 minutes**
above score 7 on the fastest routes becomes **10.98 minutes** at pref=1.)

The slider already turns a 42-minute drive into a 69-minute one. What it will
not do is turn a 65 km drive into a 100 km drive — it gets its extra time by
going slowly on small roads, not by going further. Whether that gap matters is a
product question, not a routing one, and it is most of what separates option 1
from option 5.

### How everything below was measured

**Routes.** Ten OD pairs: the four from the original brief plus six taken from
recorded drives in `traces/`, so the set is not all spokes from one town. The
Harvard→Needham pins are the ones from `drive-2026-08-25-211808.ndjson` and
reproduce the brief's row **exactly** (65.2 km / 1.47 → 49.2 km / 5.73), which is
what pins this measurement to the one it extends.

The brief's other three pins were not recorded, so they are re-derived here and
land a few hundred metres off. Wachusett and Worcester match to ±0.2 km and ±0.01
mean_score. Wellesley is the loose one: 5.6 km / 4.05 here against the brief's
5.4 km / 4.07 for the fastest route, and 5.4 / 3.90 against 5.1 / 3.93 at pref=1
— a different pin on the same street, same story, ~0.3 km apart. Every table
below uses *these* pins throughout, so they are internally comparable; only the
BETA table in Part 1 is the brief's own.

**Two scenery measures, never `mean_score` alone.** `mean_score` is
length-weighted, so a route can score lower while being strictly more scenic in
total — that is the inversion above. Everything below reports:

- **`scenic_km` = Σ km × score/10** — total prettiness driven through. Grows with
  length, as a scenic route should.
- **`min≥7` = minutes spent on roads scoring ≥ 7** — the top 8% of Massachusetts
  road-km (5,420 km of 66,195). This is the number closest to "how much of the
  drive was actually nice".

`mean_score` is carried alongside for continuity, and is never the basis of a
claim.

### One weight family covers options 1–4

Options 1 through 4 are all the same shortest-path problem with different
constants:

```
w_e  =  d_minutes_e  +  A · km_e · (b − score_e/10)
```

| | A | b |
|---|---|---|
| **1** shipped router | `pref**2 × 8.0` | 1 |
| **2** reward above a baseline | free | < 1 |
| **3** CSP, Lagrangian subproblem | `1/λ` | 0 |
| **4** ratio objective, Dinkelbach step | 1 | `μ` |

Equivalently the objective is `minutes + A·(b·total_km − scenic_km)`: `b` is a
per-km toll on distance, and today's router charges the full toll (`b = 1`).

**The validity wall.** `w_e ≥ 0` for every edge iff

    b  ≥  b*(A)  =  max over edges of [ score_e/10 − d_minutes_e / (A · km_e) ]

Below `b*(A)` some edge is negative, and scipy's `dijkstra` is not valid.
Measured on the shipped graph (313,950 nodes, 749,468 directed edges):

| A | 1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 512 |
|---|---|---|---|---|---|---|---|---|---|
| **b\*(A)** | 0.286 | 0.643 | 0.822 | **0.911** | 0.956 | 0.978 | 0.989 | 0.994 | 0.999 |

This wall is the single most important fact in the document, because of what it
*is*. An edge has negative weight exactly when adding it lowers the total cost —
which is exactly what "reward me for driving further" means. So:

> **The region where Dijkstra is valid is precisely the region where no
> kilometre of road is ever worth adding for its own sake.** Wanting longer
> routes and wanting a legal shortest-path problem are the same wish, pointing
> opposite ways.

And it is worse than "slower". On a two-way road both directions carry the same
km and score, so as soon as one direction goes negative the driver can shuttle
back and forth for unbounded negative cost. Measured at A=8: at b=0.95 there are
zero negative edges; at **b=0.90 there are six, and they already form a negative
2-cycle**. The problem does not become expensive at the wall — it becomes
**undefined**, and no algorithm returns a shortest path because none exists.

**The wall is a cliff, not a slope.** At 0.01 below `b*(A)` there is already a
negative 2-cycle for **every** A from 1 to 512 — six negative edges at A ≤ 8,
eight at A=16, 943 at A=64, 2,230 at A=512, and a 2-cycle in every one of them.
There is no margin to tune inside: you are either in the valid region getting
option 1's route, or in the undefined region getting nothing.

---

## Option 1 — leave it alone

Change the label, not the maths. The slider does something real and large; it
just does not do what "maximum scenery" implies.

| pair | km | minutes | scenic-km | min ≥ 7 | mean |
|---|---|---|---|---|---|
| Harvard→Needham | 65.2 → **49.2** | 42.3 → **68.9** | 9.6 → **28.2** | 1.3 → **19.0** | 1.47 → 5.73 |
| Needham→Wachusett | 93.5 → **71.1** | 62.6 → **98.0** | 13.2 → **43.1** | 2.1 → **30.2** | 1.42 → 6.06 |
| Needham→Worcester | 66.3 → **51.8** | 42.9 → **68.2** | 7.4 → **24.5** | 0.5 → **12.3** | 1.11 → 4.74 |
| Needham→Wellesley | 5.6 → **5.4** | 7.8 → **7.9** | 2.3 → **2.1** | 0.2 → **0.0** | 4.05 → 3.90 |
| Needham→Foxborough | 34.9 → **30.4** | 25.8 → **42.4** | 5.5 → **15.7** | 0.5 → **8.4** | 1.57 → 5.16 |
| Needham→Groton | 51.9 → **48.9** | 44.2 → **67.9** | 13.1 → **27.1** | 1.4 → **12.4** | 2.52 → 5.54 |
| Needham→Lexington | 22.8 → **19.3** | 19.0 → **32.4** | 6.0 → **9.5** | 0.5 → **7.3** | 2.61 → 4.95 |
| Needham→Chelmsford | 29.3 → **29.5** | 23.9 → **43.2** | 6.4 → **16.2** | 0.8 → **7.2** | 2.20 → 5.50 |
| Boston→Gloucester | 59.1 → **54.9** | 42.0 → **77.8** | 10.4 → **34.7** | 0.9 → **25.1** | 1.77 → 6.32 |
| Worcester→Groton | 53.2 → **43.8** | 34.0 → **53.8** | 4.2 → **24.4** | 0.0 → **9.6** | 0.79 → 5.58 |

**Worked example.** Boston to Gloucester. The fastest route is 59 km and 42
minutes, and 0.9 minutes of it — 54 seconds — is on road scoring 7 or better.
At pref=1 you get 55 km and 78 minutes, of which **25 minutes** is on road
scoring 7 or better. You arrive 36 minutes later having spent 25 minutes on
genuinely beautiful road instead of one. You did not drive further.

**Cost.** Zero. One Dijkstra: **112 ms** for the solve, ~150 ms for a full
`Router.route()` call, ~196 ms for the served request.

**What it does not do.** Nine of ten routes come back shorter in km, and the
tenth is 1.01×. Where the fastest route is already a back road, the slider has
almost nothing to find: Needham→Wellesley goes 5.6 → 5.4 km, 7.8 → 7.9 minutes,
and **0.2 → 0.0** minutes above score 7 — a scenic setting that removes the only
pretty stretch the fast route had. That failure mode is the short-trip case, and
it is the one thing on this list option 1 is genuinely bad at.

---

## Option 2 — reward score above a baseline

Replace `(1 − score/10)` with `(b − score/10)` so roads above `b` carry negative
cost. Measured two ways.

**Inside the valid window** (A = 8, the shipped strength, b swept down to
b\* = 0.911). This is flat — as flat as the BETA sweep:

| pair | b=1.00 | b=0.95 | b=0.94 | b=0.9115 |
|---|---|---|---|---|
| Harvard→Needham | 49.2 km / 28.2 / 19.0 | 49.2 / 28.2 / 19.0 | 49.2 / 28.2 / 19.0 | 49.2 / 28.2 / 19.0 |
| Needham→Wachusett | 71.1 / 43.1 / 30.2 | 71.1 / 43.1 / 30.2 | 71.1 / 43.1 / 30.2 | 71.5 / 43.6 / 31.3 |
| Needham→Worcester | 51.8 / 24.5 / 12.3 | 51.8 / 24.6 / 12.3 | **59.1 / 33.4 / 22.9** | 59.6 / 33.9 / 24.1 |
| Needham→Wellesley | 5.4 / 2.1 / 0.0 | 5.4 / 2.1 / 0.0 | 5.4 / 2.1 / 0.0 | 5.4 / 2.1 / 0.0 |

*(km / scenic-km / min≥7)*

**On the whole valid frontier** — for each A, b set 0.0001 above b\*(A), the most
aggressive legal setting there is. Every row below was checked to have **zero**
negative edges:

| | A=1, b=.287 | A=2, b=.643 | A=4, b=.822 | A=8, b=.911 | A=64, b=.989 | *fastest* |
|---|---|---|---|---|---|---|
| Harvard→Needham | 52.6 km | 51.3 | 50.9 | 49.2 | 49.4 | *65.2* |
| Needham→Wachusett | 69.1 | 70.4 | 71.2 | 71.5 | 71.7 | *93.5* |
| Needham→Worcester | 50.2 | 50.0 | 51.9 | 59.6 | 59.9 | *66.3* |
| Needham→Wellesley | 5.6 | 5.6 | 5.4 | 5.4 | 5.4 | *5.6* |

**Every legal setting of option 2, anywhere in the family, still returns a route
shorter than the fastest route.** Lowering `b` does push mildly toward length
(Harvard→Needham goes 49.2 → 52.6 km as A falls to 1), but it buys that by
weakening the scenery term at the same time, and it never reaches 65.2 km.

**Worked example.** There isn't one worth writing, which is the finding. At the
shipped A=8, sweeping `b` all the way to the wall returns a **byte-identical
route** on three of the four pairs; only Needham→Worcester moves, and it moves to
the route option 1 already gives at a slightly different BETA. Across the whole
frontier the biggest thing option 2 buys anywhere is Harvard→Needham going 49.2 →
52.6 km — still 12.6 km short of the *fastest* route, and paid for by dropping
the scenery weight from 8 to 1, which costs 11.4 minutes above score 7. A driver
would not notice, and where they would, they would not thank you.

**Cost — this is the part to read.** Outside the window the objective is not
expensive, it is **ill-posed**: at b=0.90, A=8 the graph has a negative 2-cycle,
so no shortest path exists. `scipy.sparse.csgraph.dijkstra` returns an answer
anyway, with a `UserWarning` and no guarantee attached (observed). `bellman_ford`
is the documented alternative, and on this graph it is not a practical one.
Measured, one single-source solve, same matrix, same source node:

| weights | solver | result |
|---|---|---|
| b=1.00 shipped, 0 negative edges | `dijkstra` | **112 ms** (median of 7) |
| b=1.00 shipped, 0 negative edges | `bellman_ford` | **still running at 900 s** |
| b=0.95 valid, 0 negative edges | `bellman_ford` | **still running at 900 s** |
| b=0.90 invalid, 6 negative edges + 2-cycle | `bellman_ford` | **still running at 900 s** |

Each Bellman-Ford was killed at a 15-minute wall, so 900 s is a floor, not a
time. That is **≥ 8,000×** the Dijkstra, and it is that slow even on the rows
where Bellman-Ford is *correct* and unnecessary — 313,950 nodes × 749,468 edges
is what O(V·E) means here.

Note the last row especially: scipy does not raise `NegativeCycleError` early. It
completes its V−1 relaxation passes before checking, so the *failure* costs the
full 15 minutes too. There is no cheap "detect and fall back".

Johnson's algorithm computes its potentials with Bellman-Ford and so inherits
that cost. A potential/reweighting trick needs a valid potential, which is what
Bellman-Ford is *for*. And restricting to *simple* paths to escape the negative
cycle is the elementary-shortest-path problem, which is NP-hard. There is no
cheap way round the wall; there is only staying inside it, where the option does
nothing.

---

## Option 3 — constrained shortest path

Maximise scenery subject to "no more than N× the fastest time". This is what the
"50× longer / 3× longer" framing actually describes, and N becomes the
user-facing knob.

The standard practical attack is Lagrangian relaxation over the existing
Dijkstra: minimise `λ·minutes − scenic_km` and tune λ. That subproblem is the
family above at `A = 1/λ, b = 0`, and it is valid only for
`λ ≥ λ* = max_e (score_e/10)(v_e/60)` = **1.403** on this graph. Sweeping λ down
to the wall:

| λ | Harvard→Needham | Needham→Wachusett | Needham→Worcester | Needham→Wellesley |
|---|---|---|---|---|
| 50 → 3 | 65.2 km / 42.3 min | 93.5 / 62.6 | 66.3 / 42.9 | 5.6 / 7.8 |
| 2.0 | 65.2 / 42.3 | 86.0 / 65.0 | 66.3 / 42.9 | 5.6 / 7.8 |
| 1.403 (λ\*) | 65.2 / 42.3 | 86.0 / 65.0 | 66.3 / 42.9 | 5.6 / 7.8 |

**Max time ratio reachable by Lagrangian + Dijkstra: 1.04×, and 1.00× on three
of the four pairs.** Driving λ all the way to the validity wall returns the
*fastest route, unchanged*, on three pairs. Setting `b = 0` forces `A ≤ 0.71`,
which is a scenery weight eleven times weaker than the shipped one — the
Lagrangian is the single weakest point on the valid frontier.

Binary-searching λ for a time budget (14 iterations, **2.0–2.9 s**, measured)
therefore fails to reach even a 1.25× budget on three of four pairs: it reports
the fastest route and 1.00× the scenery.

**Worked example.** Ask for "a Needham→Worcester drive up to 64 minutes, as
scenic as possible" — a 1.5× budget on a 43-minute trip. Lagrangian+Dijkstra
answers with the 43-minute fastest route and 7.4 scenic-km: it hands back 21
minutes of unused budget and 30 seconds of pretty road. Option 5, given the same
budget, returns 58.8 km / 63.6 minutes with **12.1 minutes** above score 7 —
using the budget it was given.

**Cost.** The Lagrangian version is 14 Dijkstras, ~2.4 s, and does not work. The
exact version is a resource-constrained shortest path: label-setting with one
label per (node, time-bucket), which is pseudo-polynomial — roughly a Dijkstra
on a graph B times larger for B discretised time buckets. Extrapolating from the
measured Dijkstra time, a 90-minute budget at 1-minute buckets is ~B=90, so of
order **10 s and ~28 M labels** per request (90 × the measured 112 ms solve,
313,950 nodes × 90 buckets), against 196 ms today. That is an extrapolation, not
a measurement — see "what could not be measured". CSP is
NP-hard in general; the pseudo-polynomial bound is what makes it tractable at
all, and it is bought with the bucket count.

**Two honest caveats.** Lagrangian relaxation only finds routes on the *convex
hull* of the (time, scenery) trade-off, so it can miss the true constrained
optimum even where it is valid — the duality gap. And the numbers above are the
relaxation's, not the exact CSP's; an exact solver would do better than 1.04×.
How much better was not measured.

---

## Option 4 — a ratio objective

Maximise mean score directly. Solved by Dinkelbach: repeatedly find the path
minimising `Σ km × (μ − score/10)` and set μ to the mean score of what came
back. Weights are non-negative only for `μ ≥ s_max/10`, and `s_max` on this
graph is exactly **10.00**, so only `μ ≥ 1.0` is Dijkstra-valid.

Measured, Harvard→Needham:

```
iter 0:  mu = 1.0000  ->  49.6 km, mean 5.758, scenic-km 28.5, min>=7 19.2
         next mu = 0.5758; at that mu 146,578 of 749,468 directed edges go NEGATIVE
```

and Needham→Wachusett:

```
iter 0:  mu = 1.0000  ->  71.7 km, mean 6.104, scenic-km 43.8, min>=7 32.1
         next mu = 0.6104; at that mu 115,431 of 749,468 directed edges go NEGATIVE
```

Two things. The first iteration at μ=1.0 returns **the option-1 route at
BETA=∞** — 49.6 km / 5.76 is the brief's BETA=512 row to the decimal, which is
the family identity doing what it says. And the very first Dinkelbach step then
demands μ ≈ 0.58, where a fifth of the network goes negative. **Option 4 leaves
the tractable region at iteration 2, always**, because a path's mean score is
always below the network's best edge and the validity threshold is the network's
best edge.

**Worked example.** Also not worth writing, and for a more interesting reason
than option 2: a ratio has no scale. Appending a 10 km loop of mean-6 road to a
mean-6 route leaves the objective *exactly unchanged*. Option 4 fixes the
Wellesley inversion — it would never return a 3.90 route when a 4.05 exists —
but it has no more reason to produce a long route than option 1 does. It solves
the reporting problem, not the length problem. (This is an argument from the
form of the objective, not a measurement; the optimum was not computed, because
it cannot be.)

**Cost.** Iteration 1 is one Dijkstra. Iteration 2 onward needs negative-weight
shortest paths on a graph with negative cycles, so the same wall as option 2:
Bellman-Ford is impractical here, and the elementary-path version is NP-hard.
Number of iterations is not the problem; the second one is.

---

## Option 5 — an explicit detour budget

"I have 2 hours for a 90-minute trip." Length becomes an **input**, so the
objective never has to reward distance and no weight ever goes negative. This is
the only option measured here that produces genuinely longer routes.

Prototyped as scenic-waypoint insertion under a time cap:

1. one forward Dijkstra from the origin under the **shipped** scenic weights,
2. one backward Dijkstra into the destination (same weights, transposed graph),
3. propagate minutes and scenic-km along both predecessor trees — O(V), which
   gives the cost of routing via *every node in Massachusetts* at once,
4. keep the via-node with the most scenic-km whose total time fits the budget.

| pair | budget | km | minutes | scenic-km | min ≥ 7 | mean | repeat |
|---|---|---|---|---|---|---|---|
| Harvard→Needham | *fastest* | *65.2* | *42.3* | *9.6* | *1.3* | *1.47* | |
| | *pref=1 today* | *49.2* | *68.9* | *28.2* | *19.0* | *5.73* | |
| | 1.75× | 57.8 | 73.9 | 33.9 | 19.6 | 5.87 | 5.1% |
| | 2.0× | 61.5 | 83.9 | 37.4 | 29.6 | 6.08 | 0.8% |
| | 3.0× | 99.5 | 126.8 | 59.3 | 41.9 | 5.96 | 22.3% |
| | 4.0× | **135.3** | 169.0 | **86.1** | **72.0** | 6.36 | 29.5% |
| Needham→Wachusett | *fastest* | *93.5* | *62.6* | *13.2* | *2.1* | *1.42* | |
| | *pref=1 today* | *71.1* | *98.0* | *43.1* | *30.2* | *6.06* | |
| | 1.5× | 73.5 | 93.3 | 42.7 | 30.4 | 5.81 | 0.2% |
| | 2.0× | 95.7 | 124.8 | 60.8 | 50.3 | 6.35 | 0.5% |
| | 3.0× | 145.8 | 186.5 | 96.7 | 87.6 | 6.63 | 18.2% |
| | 4.0× | **198.4** | 249.2 | **131.8** | **100.3** | 6.64 | 28.6% |
| Needham→Worcester | *fastest* | *66.3* | *42.9* | *7.4* | *0.5* | *1.11* | |
| | *pref=1 today* | *51.8* | *68.2* | *24.5* | *12.3* | *4.74* | |
| | 1.5× | 58.8 | 63.6 | 19.3 | 12.1 | 3.29 | 0.0% |
| | 2.0× | 67.4 | 85.7 | 36.6 | 12.8 | 5.43 | 0.2% |
| | 3.0× | 99.2 | 128.1 | 58.7 | 38.2 | 5.92 | 6.1% |
| | 4.0× | **132.3** | 171.2 | **84.4** | **70.5** | 6.38 | 6.1% |
| Needham→Wellesley | *fastest* | *5.6* | *7.8* | *2.3* | *0.2* | *4.05* | |
| | *pref=1 today* | *5.4* | *7.9* | *2.1* | *0.0* | *3.90* | |
| | 1.25× | 6.8 | 9.5 | 3.0 | 0.5 | 4.40 | 7.6% |
| | 2.0× | 10.8 | 15.5 | 6.4 | 4.3 | 5.97 | 15.6% |
| | 3.0× | 18.0 | 23.2 | 9.9 | 3.0 | 5.46 | 21.5% |
| | 4.0× | **24.2** | 30.5 | **12.7** | 3.0 | 5.25 | 28.2% |

**Worked example.** Needham to Worcester. The fastest way is 66 km and 43
minutes, and **30 seconds** of it is on road scoring 7 or better. Give the app a
2-hour-51-minute budget for that 43-minute drive and it returns 132 km and 171
minutes, of which **70 minutes** — over an hour — is on road scoring 7 or better,
with 11.4× the total scenic-km. That is the request "as long and inefficient as
possible" actually being honoured. At the gentler end a 1.5× budget on the same
trip gives 59 km and 64 minutes with 12.1 minutes above 7 — the *same* time above
score 7 as today's pref=1 route (12.3) and four minutes quicker, though with 21%
less total scenic-km (19.3 against 24.5), because it spends its budget on one
good stretch rather than on being slow everywhere.

Note the Wellesley row: option 5 is also the only option that fixes the
inversion by *doing something a driver wants*. 2× budget turns a 5.6 km / 4.05
trip into a 10.8 km / 5.97 one with 4.3 minutes above score 7 where the fastest
route had 0.2.

**Cost.** Two Dijkstras (**measured 230–254 ms for the pair**) plus two O(V) tree
passes. The tree passes are 700–720 ms as prototyped in Python and are a single
linear scan over 313,950 nodes — the same shape of work `_collect` already does
in numpy, so a compiled version is tens of milliseconds. **Realistic budget:
~250–400 ms, against a 112 ms solve and a ~196 ms request today**, and it
answers *every* budget in one pass rather than re-solving per budget value. This
is the cheapest option on the list after leaving it alone, which is the opposite
of what the option ordering suggests.

**The real limitation, measured.** A single via-node means the two halves can
share road, and the driver drives it twice. Measured as a share of route km:

| budget | ≤ 2.0× | 2.5 – 3.0× | 4.0× |
|---|---|---|---|
| across all four pairs | 0.0 – 15.6% | 1.6 – 23.6% | 6.1 – **29.5%** |
| the three long pairs only | 0.0 – 5.1% | 1.6 – 22.3% | 6.1 – 29.5% |

**The split is by trip length, not by budget.** On the three trips over 60 km,
retracing stays under 5.1% all the way to a 2× budget (0.0–0.2% on
Needham→Worcester). On the 5.6 km Needham→Wellesley it is already **15.6% at 2×**
— a short trip has fewer distinct ways to spend an extra ten minutes near home,
so the waypoint search sends you out and back. Past 2.5× everything retraces:
at 4× roughly **a third of the "scenic" drive is road you have already driven**.

Fixing it means penalising already-used edges on the second leg, or two via-nodes
instead of one. Neither was built or measured, so treat the 3× and 4× rows as an
upper bound on what this construction delivers rather than a shipping estimate.

**Product shape.** This is a second slider, or a "how long have you got?" field,
not a change to the existing one. `pref` keeps its current meaning and the
budget composes with it. Describing it is in scope here; building it is not, and
no iOS file was touched.

---

## Side by side

Median over the four brief pairs at the most aggressive setting each option
supports, against the fastest route:

| option | time | distance | scenic-km | min ≥ 7 | solver | cost/request |
|---|---|---|---|---|---|---|
| **1** leave it alone | 1.58× | **0.77×** | 3.09× | 14.3× | Dijkstra | 196 ms |
| **2** reward above baseline | 1.59× | **0.84×** | 3.19× | 13.9× | Dijkstra *(in window)* | 196 ms |
| **3** CSP, Lagrangian | **1.00×** | **1.00×** | **1.00×** | **1.00×** | Dijkstra × 14 | 2.4 s |
| **3** CSP, exact | not measured | — | — | — | label-setting | ~10 s *(est.)* |
| **4** ratio objective, iter 0 | 1.62× | **0.84×** | 3.14× | 14.8× | breaks at iter 2 | — |
| **5** detour budget @ 2× | 2.00× | **1.02×** | 4.24× | 23.0× | Dijkstra × 2 | ~250–400 ms |
| **5** detour budget @ 3× | 2.99× | **1.54×** | 6.74× | 36.3× | Dijkstra × 2 | ~250–400 ms |
| **5** detour budget @ 4× | **3.99×** | **2.10×** | **9.46×** | **50.7×** | Dijkstra × 2 | ~250–400 ms |

Options 1, 2 and 4 are the same route wearing different clothes — within 0.07×
of each other on every column. Option 3's median is 1.00× across the board
because it returns the fastest route unchanged on three of the four pairs, which
makes it strictly worse than doing nothing while costing 12× more. Option 5 is
the only option whose distance column ever exceeds 1.0.

Two caveats on the option 2 row. Its "most aggressive setting" is the best of six
frontier points chosen per pair after the fact, and the winning `A` is different
for every pair (4, 64, 64, 1) — there is no single setting that produces that
row, so it flatters the option. And the option 4 row is its *first* Dinkelbach
iteration, which is the only one that can be computed; it is not the ratio
optimum.

---

## The Needham→Wellesley inversion: is it general?

**No — it is a ~2% edge case, and the brief overstated it.** It is real, and it
does happen at the shipped BETA=8 and not only at 512 (measured: identical
inversion, 4.05 → 3.90, at both). But scanning it properly:

| sample | n | `mean_score` inverted | `scenic_km` inverted |
|---|---|---|---|
| 60 random pairs, 1–80 km apart | 60 | **0 (0%)** | 0 (0%) |
| 120 random short pairs, 0.5–8 km apart | 120 | **3 (2%)** | 9 (8%) |
| — of those, routes under 3 km | 3 | 0 | 1 |
| — 3–6 km | 25 | 1 (4%) | 2 (8%) |
| — 6–10 km | 59 | 2 (3%) | 3 (5%) |
| — 10 km and over | 33 | 0 | 3 (9%) |

Three inversions in 180 routed pairs. The ones that do invert are short trips
where the fastest route is *already* prettier than average: median `mean_score`
of the fastest route **4.67** on the inverting pairs against **4.04** on the rest.
That is the mechanism — when the fastest route is already good, the shortening is
worth more to the objective than the prettiness it gives up. On Wellesley that
trade is 290 m against 0.15 points.

One correction to the brief's framing. It says `mean_score` is "the misleading
number, not the route". On these three cases it is not: **all three inverting
routes also have less total `scenic_km`**, so the route really is less scenic on
both measures, not just the length-weighted one. The reporting artifact and the
objective defect happen to point the same way here. The reason to distrust
`mean_score` stands on its own — it is length-weighted and this task is about
length — but Wellesley is not the demonstration of it.

---

## What could not be measured, and why

1. **The exact CSP (option 3).** Only the Lagrangian relaxation was run. The
   exact resource-constrained solver is a label-setting algorithm that does not
   exist in scipy and was not built. Its cost above (~10 s, ~28 M labels) is
   an **extrapolation** from the measured single Dijkstra scaled by bucket count,
   not a measurement, and the *routes* it would return are unknown — they would
   be better than the 1.04× the relaxation manages, by an unmeasured amount.
   If option 3 is a serious candidate this is the gap to close first.

2. **The ratio optimum (option 4).** Cannot be computed with the tools here. The
   claim that it does not reward length is an argument from the form of the
   objective — a ratio is scale-free — not an experiment.

3. **Anything below the validity wall in option 2.** Not "expensive", not
   measurable: with a negative 2-cycle there is no shortest path to measure. The
   numbers reported for option 2 are all from its legal region.

4. **Bellman-Ford's actual completion time.** Bounded below, not measured. All
   three runs were killed at a 15-minute wall, so "≥ 900 s" is a floor and the
   true figure could be far worse. Enough to rule the approach out; not enough
   to say by how much.

5. **Option 5's repeat-road fix.** The out-and-back share is measured; the
   two-via-node or used-edge-penalty version that would reduce it is not built.

6. **`BETA`/`PREF_CURVE` recalibration.** Out of scope by construction — every
   option above holds the shipped calibration fixed and changes the *objective*,
   not the constants. Any option adopted will need the pair re-swept together
   (`router.py:60-73`), including what it does to the *bottom* of the slider.
   None of that is measured here.

7. **Every measurement is Massachusetts on the 2026-08-24 graph**, with neutral
   beauty weights (all 1.0). Under non-neutral weights `s_max` and the validity
   wall move; the direction is the same but the numbers are not.

---

## Reproducing this

Prototype harness on this branch, none of it wired into the router:
`pipeline/scenery_cap_experiment.py`. It patches `Router._weights` between calls
and holds one loaded `Router` for the whole sweep, so a full option sweep is
~15 s of load and ~0.15 s per route.

```bash
.venv/bin/python pipeline/scenery_cap_experiment.py data/processed
```

`data/processed` lives only in the main checkout, never in a worktree.
