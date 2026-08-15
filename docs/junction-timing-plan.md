# Junction timing: making travel times account for stopping

**Status:** planned, not started. Written 2026-08-15, after the first two real
test drives. Nothing in this document has been implemented.

The router's travel times are free-flow — `length ÷ speed_limit`, with nothing
charged for lights, stop signs, turns or traffic. This is the plan to fix that,
and the evidence it rests on.

---

## 1. The evidence

Two drives on 2026-08-14, an out-and-back between Needham and Foxborough,
recorded by `ios/Sources/DriveTrace.swift` and analysed with
`tools/analyze_trace.py`. Traces are gitignored (they are a record of where
someone actually drove); they live in `traces/` on the machine that pulled them
off the phone.

**87.2 min driven against 67.7 predicted — the router is 22% optimistic.**

| drive | when | pref | predicted | actual | error | stopped |
| --- | --- | --- | --- | --- | --- | --- |
| `drive-2026-08-14-155019` | 11:50 | 1.0 | 35.7 min | 41.2 min | **+14%** | 2.5 min / 8 stops |
| `drive-2026-08-14-192546` | 15:25 | 0.295 | 30.9 min | 46.0 min | **+45%** | 12.2 min / 21 stops |

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

### Two findings that close questions

**Grade does not explain slow back roads.** Downhill 1.00, level 0.93, uphill
0.93. The climb is not the reason; junctions are. Do not build a grade term.

**The traffic-vs-structure split is still unanswered.** The two drives share
**0–1% of road** — different prefs sent them down completely different
corridors. So the +14% vs +45% gap confounds three variables at once (roads,
hour, and drive 2's twelve reroutes) and cannot be attributed to traffic. See
§6 for how this is still useful.

---

## 2. What the model is today

One line, in `pipeline/graph.py`:

```python
"minutes": length / 1000.0 / meta["speed"] * 60.0
```

where `meta["speed"]` is the OSM `maxspeed` tag if there is one, and the
`SPEED_KMH[highway]` fallback if not. Nothing anywhere charges for a junction.

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

### Term 1 — corrected speed

Multiply travel time by the per-class factor measured above.

**The trap, and it is the whole point of this section.** The obvious
implementation is to scale the `SPEED_KMH` fallback table. That silently
under-delivers, because the table is only consulted where OSM has no `maxspeed`
tag. Tagged share of km, on the built MA graph:

| motorway | trunk | primary | secondary | tertiary | residential | overall |
| --- | --- | --- | --- | --- | --- | --- |
| 97% | 82% | 56% | 40% | 25% | 11% | 23% |

So scaling `SPEED_KMH` would move 3% of motorway km and 60% of secondary — the
correction lands *least* on the classes whose factor is most trustworthy, and
nothing would say so.

**Apply the factor to `minutes` after the maxspeed lookup, not to the fallback
table.**

### Term 2 — control cost

A time charge per traffic signal, stop sign and give-way, added to the road it
sits on.

---

## 4. Where the cost lives — decided by measurement

The natural design is a per-node cost, with the router adding it per direction
when it builds the directed graph. **The data rules that out.**

Control points vs graph nodes, measured across all 29,772 controls in
`data/processed/traffic_control.parquet` (11,348 signals, 17,567 stop signs,
839 give-ways, 18 mini-roundabouts):

| | on a graph node (within 5 m) |
| --- | --- |
| traffic signals | 39.9% |
| stop signs | **8.9%** |
| give-way | 3.8% |
| **any control** | **20.6%** |

Only a fifth of controls are graph nodes — but **73.2% sit within 15 m of one.**
That is OSM convention: the control is mapped at the stop line, a few metres
back on the approach, not at the intersection centre.

### Decision: attach the cost to the **edge**, not the node

Each control is assigned to the road segment it sits on, and its cost is added
to that segment's `minutes` at build time in `graph.py`.

Why this is the right call:

- **No router change at all.** Dijkstra keeps optimising `minutes`.
- **No edge expansion.** Turn-dependent costs need the graph restructured into
  an edge-expanded form (nodes become directed edges, edges become turns),
  roughly tripling it — on a router whose latency already scales as `E^1.20`
  (see `scenic-expansion-scaling-constants` in memory).
- **It matches the geometry.** A stop line on the approach genuinely *is* a
  property of the approach road.

The trade is losing turn-direction dependence: a left across traffic cannot cost
more than a right. The evidence says that is acceptable for now — once mapped
controls were accounted for, **turns were attributed zero minutes** in both
drives. Revisit only if a later drive shows otherwise.

---

## 5. Phase 0 — the measurement that must come first

**Do not fit any constant before doing this.** The naive version is wrong and it
is worth knowing exactly how.

Counting controls within 20 m of the driven path gives:

| drive | signals encountered | stop signs encountered |
| --- | --- | --- |
| `…155019` | 39 | 37 |
| `…192546` | 50 | 33 |

Against 13 signal stops and 8 stop-sign stops, that implies you stop at only
**15% of signals and 11% of stop signs**. That is implausible — real rates are
far higher — and the reason is clear: a 20 m proximity buffer picks up **stop
signs on the cross street you drove past**, which never applied to you.
Massachusetts has 17,567 stop signs and most sit on minor approaches.

So encounters are over-counted and any per-control cost fitted from them is too
low.

**The fix:** count only controls assigned to the edges the route *actually
traversed*, which is the same assignment step Term 2 needs. Phase 0 and Phase 1
share their machinery — build the assignment once and use it for both.

Deliverable: honest `P(stop)` and mean delay per control, per kind.

---

## 6. What we deliberately do not model

The **32% unexplained — 4.7 min** — stays out of the graph.

Baking one Friday afternoon's congestion into a static road network permanently
is the single mistake here that would be hard to undo and nearly impossible to
notice later. `tools/analyze_trace.py` says this in its own docstring and it is
right. Report it as a known residual instead.

If traffic is ever wanted, it belongs in a live feed or a time-of-day layer, not
in `minutes`.

---

## 7. The consequence beyond the ETA

**This changes which routes get chosen, not just how long they are predicted to
take.**

Today a town route through 20 traffic lights costs the same as an equal-length
country road. Adding ~9.5 s/km of control cost — concentrated in towns, nearly
absent in the country — makes the signalised route genuinely more expensive. The
*fastest* route should begin preferring back roads on its own.

There is a hypothesis attached, from the drives: the max-scenic route had 8
stops in 31.3 km while the low-pref arterial route had 21 in 31.9 km. If that
holds, this **narrows the honest gap between "fastest" and "scenic"**, which is
the product's central claim. It also inverts an assumption in the README, which
says free-flow is worst on the small roads scenic routes prefer. It may be the
opposite.

**Expect routes to move.** The scoring recalibration (2026-08-11) left routes
90–100% identical to before; that baseline will break here, and it should.
Measure how far they move rather than assuming.

---

## 8. How to know it worked

The two drives share **0–1% of road**, which makes them a genuine holdout:

1. Fit the constants on drive 1, measure error on drive 2.
2. Swap and repeat.

Success is drive 2's error falling from +45% toward the traffic-only residual —
**not to zero.** Reaching zero would mean the traffic had been fitted too, which
is exactly the failure §6 is about.

**Caveat, stated plainly:** this rests on 63 km and one afternoon. The framework
is what is durable; today's constants should be re-fitted as drives accumulate.
Judge a change by whether predictions on a *held-out* drive improve.

---

## 9. Sequence

1. **Phase 0** — assign controls to edges; count real encounters on both traces;
   derive honest per-control delays.
2. **Term 1** — speed correction applied to `minutes` after the maxspeed lookup.
3. **Term 2** — control costs folded into `minutes` at build time.
4. **Validate** — holdout across the two drives (§8).
5. **Measure route movement** — how much did the chosen routes change?

---

## 10. Things that will bite

- **`RouteResult.minutes` must stay consistent with what Dijkstra optimised.**
  It currently sums `edges["minutes"]`. This codebase has already been bitten by
  exactly this class of bug once — the router optimised a live re-blend while
  `mean_score` read the stored neutral column, and the two disagreed by 1.9
  points on a 0–10 scale with nothing reporting it.

- **This needs a graph rebuild and both parquets copied.** See `server/DEPLOY.md`.
  The rebuild is now ~135 s (not ~30 s) since `graph.py` reads node tags.

- **`graph.py` already has a `node()` handler**, added for motorway junction exit
  numbers. Control-node tags hook into the same pass, so the per-node callback
  cost is already paid.

- **The control cache already exists** at
  `data/processed/traffic_control.parquet` (`kind`, `lon`, `lat`, `geometry`),
  written by `tools/analyze_trace.py`. Phase 0 does not need a PBF rescan.

- **`tests/test_calibration.py` guards the score distributions**, and
  `test_neutral_weights_reproduce_the_precomputed_score` in `tests/test_routing.py`
  is the tripwire for code and parquets disagreeing. Both should stay green.
