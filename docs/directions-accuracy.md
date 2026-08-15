# How wrong can the directions be?

Measured and fixed 2026-08-15. `tools/audit_directions.py` is the instrument and
the acceptance test; every number here is one of its runs.

"Accurate directions" is not one property. It is a set of separable ways a route
can mislead a driver, and they have different causes, different fixes and very
different costs. A single pass/fail number would have hidden the biggest one
entirely — it had never been measured.

## Where it started, and where it is

Over 120 random Massachusetts routes, 33,750 junctions, ~4,500 instructions:

| failure | before | after |
| --- | --- | --- |
| route makes a turn OSM forbids | **18% of routes** | **1%** |
| driver holds the wheel and leaves the route, unwarned | **78% of routes** | **0%** |
| a road leaves within 45° and is not named | 98% | 98% |

The third is not a correctness defect and is discussed in §4.

---

## 1. Illegal turns

Massachusetts maps 8,694 `type=restriction` relations. The router read none of
them, and **a Dijkstra over nodes structurally cannot**: a restriction is a
property of the pair (road you arrived on, road you leave by), and the search's
entire state on arriving at a junction is which junction it is.

Measured before the fix: 463 traversals of a restricted junction over 40 routes,
and 7 of those routes (18%) contained at least one movement the map explicitly
forbids.

### The fix: split the junctions that need it, and only those

The textbook answer is to edge-expand the whole graph — every directed edge
becomes a node, every turn an edge. That is ruled out here: it roughly triples
`E` on a router whose latency scales as `E^1.20` (see
`scenic-expansion-scaling-constants` in memory).

But restrictions are sparse. For a junction `V` and an approach that forbids
something, `router.py` makes one copy of `V`, points that approach at the copy,
and gives the copy only the exits that approach may take. Every other approach
still arrives at `V` itself and can still go anywhere. Measured cost:

- **+3,795 nodes (+1.2%)**, 3,078 junctions split
- latency unchanged within noise (~125 ms → ~165 ms including the fork work)

Two details are load-bearing and neither is obvious:

**Ordering.** Every approach is redirected *before* any copy is given its exits,
so a copy's exits inherit the redirected head. Done the other way round, a
driver who reached `V` through a restricted approach leaves along a *duplicate*
edge that no restriction is keyed to, and evades the restriction at the **next**
junction along — a bug that only shows up two junctions from the thing it fixes.

**Arriving is never forbidden.** Only continuing through is. So a route *to* a
split junction may legitimately end at any of its copies, and `route()` takes
whichever is cheapest. The source needs no such treatment: the original index
keeps all the junction's exits, which is exactly right for a driver setting off
from it with no direction of arrival to be restricted by.

### What is left: 1 route in 120

The graph enforces **69% of restricted junctions** (3,176 of 4,609). The
remainder breaks down as:

- **1,001 junctions whose only restriction is `no_u_turn`** — skipped on purpose
  where the from- and to-way are the same. A shortest path cannot make a pure
  backtrack, so encoding it would cost a junction copy to forbid nothing.
- **604 `via`-way restrictions** — "no U-turn via the crossover", where the
  forbidden movement spans a whole little road rather than a point. These need
  the search to remember more than one junction back. Not approximated.
- **restrictions naming roads this graph does not carry** — a `service` road, or
  something outside the largest strongly connected component.

So the honest ceiling on this without via-way support is a fraction of a percent
of routes, and the measured residual is 1 in 120.

---

## 2. Misleading forks — the one nobody had measured

**Every instruction correct, and the driver still ends up on the wrong road.**

A road is allowed to keep its name and bend at a junction while a side road
carries straight on. `_legs()` merged across exactly that seam — same kind, same
label, under 45° — so no instruction was emitted, and a driver who does what the
wheel is already doing leaves the route.

Measured: **123 of them over 60 routes, on 78% of routes.**

The fix is to ask, before merging, whether any road leaves the junction
*straighter* than the route does — `ManeuverContext.fork_side`. If one does,
the merge is refused and a maneuver is emitted: a `fork` with a slight-left or
slight-right modifier, rendered "Keep left to stay on Washington Street".

Only a **straighter** rival counts. A road peeling off at 40° while the route
goes straight needs no instruction; nobody drifts onto it, and announcing it
would bury the turns that matter. Instruction count rose 5%.

### The measurement bug that hid the last 12%

Twice, this tool disagreed with the router about which road was the straight
one, and both times the tool was wrong first and the router wrong after:

- the audit took the arrival heading over the **last vertex pair**, which is the
  digitising noise `TURN_CHORD_M` exists to avoid — OSM packs vertices tightly
  through a junction to shape the corner, so the final few metres already point
  round the bend. Fixed, five phantom forks disappeared.
- the audit then took its chord over the **last edge**, which is often shorter
  than the chord, because a way is split at every junction and a short block
  between two of them is a whole edge. Fixed by walking back 60 m across edge
  boundaries — and that found **16 real forks the router was still missing**, at
  which point the same fix was needed in `router.py` (`_approach`).

Neither would have been found by a test that checked the router against itself.
That is why the audit derives its facts from the raw PBF rather than from
`turn_restrictions.parquet`.

---

## 3. Start and end offset — the largest measurable gap left

Routes begin and end at **junctions**, because `snap()` returns a graph node.
From a random point: median 135 m to the route's start, 195 m to its end
(p90 589 m / 496 m). From a point actually on a road it is smaller — the README
measures a median of 99 m — but it is never zero.

The driver is told to set off from the corner rather than from where they are.
The fix is to split the snapped edge at the projection point into two virtual
nodes per request, which also stops the first and last edge being charged whole
in the ETA. Not done.

---

## 4. Ambiguous forks are not a defect

98% of routes pass a junction where another road leaves within 45° of the route
and nothing names it. This is reported because it is worth knowing, not because
it is wrong: by construction these are junctions where the route takes the
**straighter** option, so a driver following the road arrives correctly. The
cost is confidence, not correctness, and the fix would be more instructions
rather than different ones.

Real navigation apps do not announce every junction either: 4,496 instructions
over 33,750 junctions is one per 7.5, which is in the normal range.

---

## 5. Known, unmeasured

- **Lane guidance.** No `turn:lanes` or `destination:lanes` is read, so nothing
  ever says "use the right two lanes". At a multi-lane exit, the wrong lane is
  a missed exit, and no amount of maneuver accuracy fixes it.
- **Node-level access.** `barrier=gate`, `access=private` on a *node* are not
  read; only way-level access is.
- **Conditional restrictions** are enforced unconditionally. Massachusetts has
  two. Routing a driver around a turn they could have made costs a minute; the
  other way costs a ticket.
