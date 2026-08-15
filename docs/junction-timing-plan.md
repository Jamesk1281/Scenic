# Junction timing: making travel times account for stopping

**Status:** revised 2026-08-15 and implemented. The first version of this
document (branch `claude/junction-timing-plan`, written the same day) proposed a
design that its own evidence does not support; §§4–5 below are rewritten and the
reasons are kept rather than deleted, because the wrong version is instructive.

The router's travel times were free-flow — `length ÷ speed_limit`, with nothing
charged for lights, stop signs, turns or traffic. This is how that was fixed,
and the evidence it rests on.

---

## 1. The evidence

Two drives on 2026-08-14, an out-and-back between Needham and Foxborough,
recorded by `ios/Sources/DriveTrace.swift` and analysed with
`tools/analyze_trace.py`. Traces are gitignored (they are a record of where
someone actually drove); they live in `traces/` on the machine that pulled them
off the phone. Every number in this section was re-derived from the traces on
2026-08-15 and reproduces exactly.

**87.2 min driven against 67.7 predicted.**

| drive | when | pref | predicted | actual | error | stopped |
| --- | --- | --- | --- | --- | --- | --- |
| `drive-2026-08-14-155019` | 11:50 | 1.0 | 35.7 min | 41.2 min | **12.6%** | 2.5 min / 8 stops |
| `drive-2026-08-14-192546` | 15:25 | 0.295 | 30.9 min | 46.0 min | **31.1%** | 12.2 min / 21 stops |
| pooled | | | 67.7 min | 87.2 min | **22.4%** | 14.7 min / 29 stops |

### One error measure, stated once

The first draft mixed two. It reported the pooled figure as "22% optimistic"
(`1 − predicted/actual`) and the per-drive column as "+14% / +45%"
(`actual/predicted − 1`). Both are defensible and they are not comparable — the
same drive 2 is either 45% or 31% wrong depending on the denominator.

Everything here uses **error = |predicted − actual| ÷ actual**, because the
target is stated as accuracy against what happened. On that scale the goal of
80% accuracy means error under 20%, and **drive 1 already passes**. Drive 2 is
the problem.

### Where the 14.7 min of stopping went

| cause | stops | time | in OSM? |
| --- | --- | --- | --- |
| traffic signal | 13 | 6.8 min | yes |
| **unexplained** | 7 | **4.7 min** | **no — traffic** |
| stop sign | 8 | 2.9 min | yes |
| give-way | 1 | 0.3 min | yes |

**68% of stopped time sits at something OSM already maps.** That is the part a
static graph can predict. The junction cost overall is **13.9 s per km driven**.

### Moving speed, by road class

Stops excluded — these are how fast the car moved when it was moving.

| class | km driven | assumed | measured | factor |
| --- | --- | --- | --- | --- |
| secondary | 33.1 | 52 | 48 | 0.93 |
| tertiary | 11.4 | 51 | 45 | 0.89 |
| motorway | 8.4 | 89 | 102 | **1.16** |
| primary | 8.0 | 65 | 56 | 0.86 |

Everything else (residential 1.51, unclassified 0.30, the `_link` classes) has
under 1.5 km behind it and is noise, not measurement. Do not use those.

Note what motorway says: drivers exceed the posted limit by 16%, and 97% of
motorway km carry a real `maxspeed` tag, so that is measured against the actual
sign rather than against a guess. An honest ETA predicts what the driver will
do, not what the sign says, so the factor is applied as measured.

### Two findings that close questions

**Grade does not explain slow back roads.** Downhill 1.00, level 0.93, uphill
0.93. The climb is not the reason; junctions are. Do not build a grade term.

**The traffic-vs-structure split is still unanswered.** The two drives share
**0–1% of road** — different prefs sent them down completely different
corridors. So the gap between them confounds three variables at once (roads,
hour, and drive 2's twelve reroutes) and cannot be attributed to traffic. See
§6 for how this is still useful.

---

## 2. What the model was

One line, in `pipeline/graph.py`:

```python
"minutes": length / 1000.0 / meta["speed"] * 60.0
```

where `meta["speed"]` is the OSM `maxspeed` tag if there is one, and the
`SPEED_KMH[highway]` fallback if not. Nothing anywhere charged for a junction.

This is not approximately right — it answers a different question ("how long if
you never slow down"), and the answer is never.

---

## 3. The model to build

```
time = distance ÷ corrected speed  +  control cost
```

Two terms, from two independent pieces of evidence, kept **separate on
purpose**. Collapsing them into one fudge factor would fit these two drives and
nothing else: they scale with different things (distance vs junction count), so
a route with twice the intersections needs a different correction, and only the
split can tell you that.

### They are additive, not double counted

This is the first thing that looks wrong about the model, so it is worth
stating. The speed factor is measured over *moving* steps only and the control
cost over *stopped* runs only, and `analyze_trace.py` widens its stopped mask by
the smoothing window's half-width precisely so the decelerating and accelerating
seconds around a junction land in the stop rather than bleeding into the speed
factor. The two measurements partition the drive's clock; they do not overlap.

The consequence worth knowing: a green light you merely slow for produces no
stop at all, so its cost is carried entirely by the speed factor. That is why
the factor for a junction-dense class like `primary` (0.86) is lower than for
`secondary` (0.93) even after every stop has been taken out.

### Term 1 — corrected speed

Multiply travel time by the per-class factor measured above.

**The trap.** The obvious implementation is to scale the `SPEED_KMH` fallback
table. That silently under-delivers, because the table is only consulted where
OSM has no `maxspeed` tag. Tagged share of km on the built MA graph, re-measured
2026-08-15:

| motorway | trunk | primary | secondary | tertiary | residential | overall |
| --- | --- | --- | --- | --- | --- | --- |
| 97% | 82% | 55% | 40% | 25% | 11% | 23% |

So scaling `SPEED_KMH` would move 3% of motorway km and 60% of secondary — the
correction lands *least* on the classes whose factor is most trustworthy, and
nothing would say so. Apply the factor to the computed time, not to the table.

### Term 2 — control cost

A time charge per traffic signal, stop sign and give-way, on the road it sits
on, in the direction it faces.

---

## 4. Where the cost lives — the first draft got this wrong

The original §4 measured how close each control sits to a **graph node**, found
20.6% within 5 m and 73.2% within 15 m, concluded that controls are "not graph
nodes", and therefore assigned each control to its nearest *edge* by proximity.
Those measurements are correct. They answer the wrong question.

A graph node is a **junction** — `build_edges` only keeps a node where two ways
meet. A traffic control is not usually at a junction *centre*; OSM maps it at
the stop line, a few metres back. But it is still a **node of the way**, and the
way is the road it governs. Re-measured against the PBF rather than against the
node table:

| | is a node of a drivable way |
| --- | --- |
| traffic signals | **94.9%** |
| stop signs | **82.6%** |
| give-way | 90.1% |
| **any control** | **87.5%** |

The 12.5% that are not sit on ways this pipeline does not route over (service
roads, footway crossings), and ignoring them is correct rather than lossy.

### Why proximity is not merely approximate — it is undecidable

Measured on the built graph: **81.7% of controls have more than one candidate
road within 15 m, and 72.4% have three or more.** A stop sign at a crossroads is
within a few metres of all four approaches and governs exactly one of them.
There is no radius that fixes this, because the ambiguity is not about scale.

This matters because the first draft's §5 identifies the failure precisely —
"a 20 m proximity buffer picks up stop signs on the cross street you drove
past" — and then prescribes a remedy that does not address it: *"count only
controls assigned to the edges the route actually traversed."* If the
**assignment** is itself by proximity, the cross-street sign was already
assigned to your edge, and traversing your edge counts it. The buffer moved; the
error did not.

### Decision: read controls as way members, in `graph.py`'s existing node pass

`GraphHandler.node()` already exists for motorway exit numbers, so the per-node
callback cost is paid. It now also collects `highway=traffic_signals|stop|
give_way|mini_roundabout` with their `direction` tag, and `build_edges` charges
each control to the segment whose node list contains it. Exact, no radius, no
tie to break.

### The cost is per direction, and the first draft gave that away for free

The original decided to attach cost to the **undirected edge**, accepting the
loss of turn-direction dependence. That trade is right: turn-dependent costs
need an edge-expanded graph, roughly tripling `E` on a router whose latency
scales as `E^1.20` (see `scenic-expansion-scaling-constants` in memory).

But it also gave up **travel**-direction dependence, which costs nothing to
keep. A stop sign facing northbound traffic does not stop the driver heading
south past its back — and 81% of Massachusetts' 17,567 stop signs carry
`direction=forward` or `direction=backward` saying which. Charging an undirected
edge charges both, roughly doubling the stop-sign term for no reason.

`router.py` already expands each edge into directed slots with a `flip` array,
so a per-direction cost is a `np.where`, not a graph restructuring. The edge
table therefore carries six counts — signal / stop / give-way × forward /
reverse — and the router picks the side it is travelling.

Charging a control exactly once needs one more rule, because a junction node
belongs to two edges at once: forward travel pays for everything past the
segment's first node through its last, reverse for everything from the last back
through its first. Each direction excludes the end it departs from, so the edge
you *arrive* on pays and the edge you leave on does not. See `count_controls`.

### "Turns cost zero" is an artifact, not a finding

The original justified dropping turn-dependence partly on this: *"once mapped
controls were accounted for, turns were attributed zero minutes in both drives."*

That zero is produced by `classify_stops`, which labels stops by maneuver first
and then lets any control within 45 m overwrite the label. So it means "no stop
was at a turn **and** more than 45 m from any control" — turn cost is absorbed
into control cost by construction, and the measurement cannot report otherwise.
The conclusion still stands on latency grounds. It does not stand on this.

---

## 5. Where the arithmetic lives — not in the parquet

Neither term is baked into `graph_edges.parquet`. `minutes` stays free-flow and
the router applies both corrections when it loads. Two reasons, and the first is
a silent-failure trap the first draft walks into:

- **`tools/analyze_trace.py` measures the factor against `length_m / minutes`.**
  Bake the correction into that column and the next drive reports a factor of
  ~1.00 whether or not the correction was any good — the instrument would be
  calibrated against its own output, with nothing saying so.
- **Re-fitting stays cheap.** The constants will move as drives accumulate. In
  the router they are a constant and a restart; in the parquet they are a 135 s
  rebuild plus copying 80 MB to a laptop behind a home tunnel (`server/DEPLOY.md`).

What *is* in the parquet is the six control counts, which are facts about the
map rather than fitted numbers.

---

## 6. Phase 0 — measure before fitting

**Do not fit any constant before doing this.** Counting controls within 20 m of
the driven path gives 39 and 50 signals and 37 and 33 stop signs for the two
drives. Against 13 signal stops and 8 stop-sign stops that implies stopping at
only 15% of signals and 11% of stop signs, which is implausible — and the reason
is the cross-street over-count of §4.

With way-membership assignment the encounter count is honest, and the number the
graph needs is not `P(stop)` but the expectation:

```
cost per control of kind k  =  total stopped seconds at k  ÷  encounters of k
```

which folds `P(stop)` and `E[delay | stop]` into the one quantity a static graph
can charge. `tools/fit_junction_cost.py` computes it, replaying each trace
against the driven edges rather than against a buffer.

---

## 7. What we deliberately do not model

The **32% unexplained — 4.7 min** — stays out of the graph.

Baking one Friday afternoon's congestion into a static road network permanently
is the single mistake here that would be hard to undo and nearly impossible to
notice later. Report it as a known residual instead.

It also sets the floor. 4.7 min on 87.2 is 5.4%, so a perfect static model still
carries roughly that much error on these drives — which is comfortably inside a
20% target and is the reason the target is reachable at all. If traffic is ever
wanted it belongs in a live feed or a time-of-day layer, not in `minutes`.

---

## 8. The consequence beyond the ETA

**This changes which routes get chosen, not just how long they are predicted to
take.** Control density per 100 km of network, by class, measured on the built
graph:

| primary | trunk | unclassified | secondary | tertiary | residential | motorway |
| --- | --- | --- | --- | --- | --- | --- |
| 123 | 100 | 75 | 63 | 55 | 28 | **0.9** |

Controls are densest on **arterials**, not in residential streets, and almost
absent from motorways. So the cost falls hardest on exactly the mid-tier roads a
low-pref route prefers — which supports the original hypothesis (the max-scenic
drive had 8 stops in 31.3 km, the low-pref arterial drive 21 in 31.9 km) and
inverts the README's claim that free-flow is worst on the small roads scenic
routes like. It is the opposite.

The speed factor pulls the other way: motorways get 16% faster while primary and
tertiary get 12–14% slower. Net effect on route choice is an empirical question,
measured in §10.

### The term the first draft misses entirely: `BETA`

The scenery penalty is `minutes + pref^1.3 × BETA × km × (1 − score/10)`, a
**minutes-equivalent** cost calibrated against free-flow time. Raising travel
time by ~20% and adding ~9.5 s/km of control cost makes `BETA = 7` a smaller
share of the total edge weight — so the same `pref` now buys less detour, and
the slider quietly gets weaker.

The original predicts routes will move and attributes it entirely to control
costs making town routes expensive. That is half of it. The other half is that
the scenery term's *scale* changed underneath it. Measure the slider's spread
after the change, and re-fit `BETA` if it has compressed.

---

## 9. How to know it worked

The two drives share **0–1% of road**, which makes them a genuine holdout for
*roads*:

1. Fit the constants on drive 1, measure error on drive 2.
2. Swap and repeat.

**But it is weaker than the first draft claims.** The drives share no road, yet
they share road *classes*, and their class mix is very different because their
prefs were (drive 1 is back roads, drive 2 arterials). Fitting per-class factors
on one drive gives a confident `tertiary` from drive 1 and a confident `primary`
from drive 2 with little overlap — so a pass validates the *shape* of the model,
not the values of its coefficients. Read it that way.

Success is drive 2's error falling toward the traffic-only residual — **not to
zero.** Reaching zero would mean the traffic had been fitted too, which is what
§7 is about.

**Caveat, stated plainly:** this rests on 63 km and one afternoon. The framework
is what is durable; today's constants should be re-fitted as drives accumulate.

---

## 10. Results

Fitted by `tools/fit_junction_cost.py` against the two drives:

| kind | met | stops | P(stop) | s / stop | **s / control met** |
| --- | --- | --- | --- | --- | --- |
| signal | 53 | 16 | 0.30 | 31.7 | **9.5** |
| stop sign | 9 | 6 | 0.67 | 14.0 | **9.3** |
| give-way | 0 | — | — | — | 4.7 *(assumed)* |

Sanity: 9.5 s of average delay per signal is what the Highway Capacity Manual
would call a well-timed one, and the encounter counts agree with the network's
own density — 26 and 29 signals measured against ~27 predicted from the
per-class control density over the classes each drive used. The old buffer
method claimed 39 and 50 signals and 37 and 33 stop signs; against 9 stop signs
actually met, that was a 7× over-count, and it is what made the implied stop
rates absurd.

### Error, per drive, per term

| | actual | free-flow | + speed | + controls |
| --- | --- | --- | --- | --- |
| drive 1 | 41.2 | 37.0 (10.2%) | 39.0 (5.4%) | 43.9 (**6.5%**) |
| drive 2 | 46.0 | 32.7 (29.0%) | 33.3 (27.6%) | 38.3 (**16.8%**) |
| pooled | 87.2 | 69.7 (20.1%) | | 82.2 (**5.7%**) |

**Both drives come in under 20%, and the pooled error is 5.7% — which is the
unexplained residual of §7 almost exactly.** Re-planned end to end through
`server/app.py`, the ETA the app would show for drive 1 goes from 13.3% out to
**2.2%**.

Drive 2 cannot be checked that way any more, and the reason is worth stating
rather than hiding: at its recorded `pref` of 0.295 the router now returns a
materially faster route than the one that was driven, so comparing its 28 min
prediction against a 46 min drive down different roads measures nothing. The
like-for-like figures above — predicted for the ground actually covered — are
the ones to read.

### The speed term is the strong one

Compare the corrected prediction against the *moving* clock rather than the
whole drive, and the speed factor is right to within 2% on both drives — 39.4
against 38.7 minutes of movement, and 33.3 against 33.8. Every remaining minute
of error is in the control term.

### And the control term is where the honesty runs out

The two drives met almost the same number of signals — 26 and 27 — and stopped
at **4 and 12** of them. Fitting either drive alone gives 2.7 s or 16.1 s per
signal, a six-fold spread. Held out (§9):

| predicted from | error | was |
| --- | --- | --- |
| drive 1 ← drive 2's constants | 16.2% | 12.6% |
| drive 2 ← drive 1's constants | 24.7% | 31.2% |

Drive 1 gets **worse** than free-flow when priced from drive 2's afternoon. That
is not a defect in the model; it is the model telling the truth about what two
samples can support. The *location* of a signal is structural and the graph now
knows it. The *wait* at it is not, and a static graph can only ever charge the
average — which is what the pooled constants are.

This is the ceiling on per-drive accuracy, and no amount of further constant
fitting moves it. Time of day is the next real term.

### What it did to the routes

Over 25 random Massachusetts trips, free-flow vs corrected:

| pref | same road | km | minutes | scenic score |
| --- | --- | --- | --- | --- |
| 0.0 | 93% | 52.0 → 53.0 | 41.3 → 41.4 | 2.54 → 2.44 |
| 0.5 | 77% | 48.4 → 47.8 | 53.9 → 61.9 | 5.12 → 4.94 |
| 1.0 | 93% | 49.9 → 49.8 | 57.8 → 68.2 | 5.35 → 5.33 |

Routes move less than §8 expected, and latency is unchanged (125 ms vs 127 ms —
the corrections are vectorised at load, and Dijkstra sees the same matrix).

**§8's central hypothesis is false.** The gap between fastest and scenic
*widened*, from 44% to 71% on 40–90 km trips and from 11% to 16% on 6–18 km
ones. The reasoning in §8 was sound and the premise was not: controls really are
densest on arterials, but the fastest route across Massachusetts is 73% motorway
— which has 0.9 signals per 100 km and gets 16% *faster* under the speed factor.
The correction rewards exactly the road type the scenic route is running away
from. §8 was written from drives whose fast option was an arterial; generalising
that to all trips was the error.

### The fallback speed limits were the bigger defect all along

Fitted per class, the surface roads came out **0.86 / 0.89 / 0.93** for primary /
tertiary / secondary, which reads as three facts about three kinds of road. It
was not. `SPEED_KMH` — the assumed limit where OSM has no `maxspeed` tag, which
is **77% of the network's kilometres** — was a table of guesses, and the speed
factor was quietly absorbing how wrong each one was.

Read off the roads of the same class that *are* tagged (harmonic mean weighted
by length, since that is the statistic that reproduces the right total time):

| class | was | tagged roads say | untagged km it governs |
| --- | --- | --- | --- |
| **residential** | **30** | **40** | **36,871** |
| trunk | 85 | 64 | 186 |
| unclassified | 45 | 37 | 1,061 |
| primary | 65 | 59 | 1,398 |
| tertiary | 50 | 47 | 5,532 |
| secondary | 55 | 53 | 4,862 |
| motorway | 105 | 97 | 75 |

30 km/h is not a speed limit Massachusetts posts anywhere. 40 (25 mph) is its
statutory default in a thickly settled district, and it governs 56% of the
state's road network.

With the limits fixed, the same traces give **0.94 / 0.94 / 0.95** — the same
number three times, from 52 km of driving. That is worth more than three fitted
constants, because it *generalises*: `residential` has 41,348 km of network and
0.9 km of trace behind it, and a rule every measured class agrees on is better
evidence for it than its own noise. So `SPEED_FACTOR` is now one number for
surface roads and one exception for motorway.

### Junction size does not explain the gap between the drives

The obvious next term, and the last one available from structure rather than
from the clock: a signal where two arterials cross should cost more than one on
a back street, and drive 2 was arterials while drive 1 was back roads.

**It is not that.** Splitting the signals met by the class of road they sit on:

| drive | major-road signals met | stops | s per signal met |
| --- | --- | --- | --- |
| drive 1 | 25 | 4 | **2.8** |
| drive 2 | 24 | 10 | **14.0** |

The same kind of signal, in the same number, at five times the cost. Nothing
structural distinguishes them. This is the measurement that closes the question:
the residual is the hour of the day, and no static term reaches it.

### BETA had to move, as §8's last section warned

At `BETA = 7.0` the slider's bottom half went soft: a pref-0.25 route kept 50%
of the fastest route's road where it used to keep 30%, and the scenery it found
fell from 4.62 to 3.39.

Raising `BETA` alone then overcorrected — it fixed the bottom by handing it the
*whole* gain, and the back half of the travel went inert (a guard in
`test_the_whole_slider_does_something` caught it). `BETA` and `PREF_CURVE` are
one calibration and have to be swept together. Share of the total scenery gain
won by pref 0.25, and by the back half, over 10 routes:

| BETA | curve | bottom | top | | BETA | curve | bottom | top |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8 | 1.30 | 0.68 | 0.07 | | 10 | 1.30 | 0.77 | 0.05 |
| 8 | 1.60 | 0.35 | 0.11 | | 10 | 2.00 | 0.34 | 0.12 |
| **8** | **2.00** | **0.32** | **0.15** | | 12 | 2.00 | 0.35 | 0.10 |

`BETA = 8.0, PREF_CURVE = 2.0`. Nothing reaches a flat 0.25/0.25 and nothing
will: the penalty saturates, so past pref ~0.5 the router has already taken
every detour worth taking and the ceiling is 5.6 on the 0–10 scale whatever
these are set to.

Note this overturns the note that used to sit on `PREF_CURVE` — "exponents above
~1.5 overcorrect, trading the dead top for a dead bottom". That was true, and it
was true *of `BETA = 7`*. A steep curve weakens every pref below 1.0, so with a
small `BETA` it empties the bottom; with a larger one it does not. Neither
constant means anything without the other.

That is calibration, not preference: `pref` means the same thing to a driver as
it did, and it takes different numbers to mean it now.

---

## 11. Things that will bite

- **`RouteResult.minutes` must stay consistent with what Dijkstra optimised.**
  It used to sum `edges["minutes"]`, which is now neither corrected nor
  directional. It now sums the directed per-hop minutes the router actually
  weighted, so the two cannot drift. This codebase has been bitten by exactly
  this class of bug once — the router optimised a live re-blend while
  `mean_score` read the stored neutral column, and the two disagreed by 1.9
  points on a 0–10 scale with nothing reporting it.

- **This needs a graph rebuild and both parquets copied.** See `server/DEPLOY.md`.
  `Router` refuses to load a graph without the control columns, for the same
  reason it refuses one without `junction`/`dest_ref`: a graph that merely
  routes while charging nothing for 29,772 traffic controls is the
  silent-disagreement case that file exists to warn about.

- **`analyze_trace.py`'s stop durations are not a pure floor.** The `STOP_SMOOTH`
  docstring says a stop reads ~2.5 s shorter than it was; the dilation added
  below it widens the stopped mask by the same half-width, which is what
  captures the decelerating seconds. The comment predates the fix.

- **Only mapped controls are charged.** OSM's coverage of stop signs is
  uneven — a missing sign is a missing cost, and it will read as congestion in
  the unexplained residual.

- **`BETA` and the travel-time constants are now coupled.** Any future change to
  `SPEED_FACTOR` or `CONTROL_SECONDS` changes what a minute of scenery penalty
  is worth relative to a minute of driving. Re-run the slider sweep, not just
  the ETA check.

---

## 12. What is left

Ranked by what each is worth against the 20% error target.

1. **Time of day.** The ceiling, and the only term that addresses the 4.7 min of
   congestion §7 leaves out and the 6× signal-wait variance §10 measures. It
   does not belong in `minutes`; it belongs in a layer over it.
2. **More drives.** Every constant here rests on 63 km and one afternoon. The
   framework is durable, the numbers are provisional, and `fit_junction_cost.py`
   re-derives them from whatever traces exist.
3. **`residential`.** 62% of the network's kilometres and 0.9 km of measurement,
   so it is left at a factor of 1.0 — an untested surface directly under the
   roads scenic routes prefer. Its assumed 30 km/h is also the lowest-confidence
   entry in `SPEED_KMH`, being 89% fallback.
4. **Starting mid-edge.** Routes begin and end at junctions, so the first and
   last edge are charged whole and their controls with them. Median 99 m of
   error per end.
5. **Turn restrictions** — not an ETA problem, but the other thing measured on
   2026-08-15 and the larger correctness defect. See the README roadmap.
