# Audit the reroute path for what the first two fixes did not catch

**Status: AUDITED AND FIXED 2026-08-26. Lead 1 confirmed, lead 2 killed as
stated, one new and more severe defect found underneath both — and all of them
now fixed.** The original brief is preserved below unaltered. Findings begin at
"# Findings"; what was changed, and how it was verified, at "# What was fixed".

## Context: what is already fixed, so do not re-find it

Three defects from the 2026-08-25 drives are **already fixed and merged to
main** (`beedb2c`, `0f7b5ba`, `e6109d9`). Do not report them again:

- The banner skipping the first turn of a reroute — `advanceSteps` guarded on
  `hasJoinedRoute` alone, which `adopt` leaves true, and its advance condition
  fired on equality. Both closed; the guard is now
  `hasJoinedRoute, !awaitingJoin, !runningBackwards(here)` and the comparison is
  strictly-past with a margin. See `docs/reroute-step-offset.md`.
- The projection running backwards along a line that doubles back.
- A finished drive restartable from a stale plan. See
  `docs/stale-plan-after-arrival.md`.

One known defect is **deliberately still open and is not yours**: all three
arrival tests at `NavigationModel.swift:571-582` gate on `hasJoinedRoute`, so a
car parked off a line it never joined can never end its drive. It is being
handled separately. Report anything you find *about* it, change nothing.

## Lead 1 — the server hands back a route the driver is already on, and it is adopted anyway

Across the 7 reroutes in `traces/drive-2026-08-25-202122.ndjson`, **two pairs are
identical on every field the trace records**:

| pair | gap | km | minutes | mean_score | steps | coords |
|---|---|---|---|---|---|---|
| seq 4 → 5 | **17 s** | 43.3 | 48.9 | 5.94 | 25 | 2063 |
| seq 6 → 7 | **46 s** | 37.1 | 41.5 | 6.25 | 19 | 1821 |

Five signature fields matching including the exact coordinate count is strong
evidence of the same polyline, though the trace does not store enough to prove
it byte-for-byte. **Nothing in the reroute path compares the incoming route to
the one already being followed before calling `adopt`.**

Adopting an identical route is not free: `adopt` resets `currentStep`,
`travelled`, `remainingMeters` and re-arms `awaitingJoin`, so a no-op reroute
still throws away progress and restarts the banner.

## Lead 2 — the backoff appears not to survive a same-route adoption

`05f3f00` added interval backoff: the reroute interval doubles for each reroute
the driver declines, capped at two minutes, and **resets the moment one settles
them**. Observed gaps in that same drive:

```
91 s, 33 s, 33 s, 86 s, 17 s, 808 s, 46 s
```

The 17 s gap is the problem. It comes *after* four prior reroutes, exactly where
the backoff should be at its longest.

**Hypothesis, not established:** a same-route adoption settles instantly and so
resets the backoff. `settleAwaitingJoin` (`NavigationModel.swift:338`) clears
`awaitingJoin` as soon as `here.offRoute <= joinConfirmMeters` (30 m) — and when
the adopted line *is* the line the car is already on, that is true on the very
next fix. The reroute gate at `NavigationModel.swift:616-622` then has nothing
left holding it.

If that holds, leads 1 and 2 are one bug, and it is why `05f3f00`'s backoff did
not prevent the 2026-08-25 storms. **What would kill this hypothesis:** finding
that the backoff counter is keyed on something a same-route adoption does not
touch, or that the 17 s gap has a different cause entirely. Say so if so.

## What to actually do

1. **Verify both leads against the traces**, then against the code as it stands
   on `main` *after* the three fixes — some of what produced these timings may
   already be gone.
2. **Re-run the same measurements over all eight drives**, not just the three
   from 08-25. The 08-14 and 08-22 traces are on disk. The step-offset fix was
   validated against "21 of 51 reroutes across five drives", so that wider set
   is known to be readable.
3. **Review the whole reroute path for defects neither fix touched.** The gate
   at `616-622` has six clauses, each guarding a different runaway that has
   actually happened. Ask what a seventh would guard.
4. **Write findings into this file.** One section per finding: what, the
   evidence, the file:line, and a confidence level.

## Traps

- **Change no source file.** Two other sessions have work in flight and the
  value here is the findings, not a patch. If a fix is obvious, describe it
  precisely and leave it.
- **Do not add tests to `ios/Tests/RerouteTests.swift`** — it gained 170 lines
  an hour ago and is the most likely file to be edited next. If a failing test
  is the clearest way to state a finding, put it in a new file and say so.
- **"The server returned the same route" is not automatically a server bug.**
  If the driver is off-route and the best route from where they are happens to
  be the one they are ignoring, returning it again is correct. The defect, if
  there is one, is the *client adopting it as though it were new*.
- **Traces are gitignored** and live only in the main checkout, as do `data/`
  and `.venv/`. Run from the main checkout.
- Do not trust the `km`/`minutes`/`mean_score` fields to more precision than
  they carry — they are rounded to one decimal in the trace, so "identical" on
  those alone would be weak. The step and coordinate counts are what make the
  match convincing.

## Out of scope

Fixing anything. The arrival-gate defect. Voice directions, which are queued
behind this work precisely because a voice that speaks a wrong instruction is
worse than a banner that shows one.

## Done looks like

1. Lead 1 confirmed, killed, or explicitly undecided, with the evidence.
2. Lead 2 the same, including whether it and lead 1 are one bug.
3. The reroute measurements re-run across all eight traces, with a table.
4. Any further findings written up to the same standard.
5. A plain statement of what could not be determined from the data available —
   that is a result, not a failure.

---

# Findings

**Audited 2026-08-26 against `main` @ `e6109d9`, i.e. after all three fixes.
No source file was changed.** Measurements were re-run over every trace on
disk. Verdicts:

| | verdict | confidence |
|---|---|---|
| Lead 1 — same route adopted as new | **confirmed**, and provable byte-for-byte | high |
| Lead 2 — backoff reset by a same-route adoption | **killed as stated.** The premise is wrong: the 17 s gap was legal | high |
| Are they one bug? | **Partly** — 6 of 8, but through a third mechanism neither lead names | high |
| Finding 3 — phantom off-route from the pinned match floor | **new**, and the most severe of the three | high (mechanism), medium (prevalence) |

## Method, and what the instruments were

Three, deliberately independent:

- `seq`-level route identity: SHA-256 over the full `coords` array of each
  `route` record, then confirmed with a literal `==` on the decoded lists.
- A faithful Python port of `Geo.progress(of:along:notBefore:)` re-run over
  every fix with `notBefore = 0`, then diffed against the `off` the app
  recorded for that same fix. Two instruments, one input — any gap between
  them is the floor, not the driver.
- A replay of the gate at `NavigationModel.swift:614-620` over the recorded
  fix stream, using only values the model itself saw (`off`, `travelled`,
  `remaining`), so it reproduces the gate rather than re-deriving it.

Scripts are in the session scratchpad, not the repo — they are throwaway
instruments, and `tools/analyze_trace.py` is the thing that should grow if any
of this becomes a standing measurement.

## Correction to the brief's own premises

Three, because they change what the evidence can support:

1. **The trace does store enough to prove identity byte-for-byte.** The brief
   says it does not. `DriveTrace.route` (`DriveTrace.swift:182-210`) writes
   `"coords": feature.geometry.coordinates` in full — the whole polyline, every
   reroute. Lead 1 does not rest on five rounded fields; it is exact.
2. **The gate has seven clauses, at `614-620`,** not six at `616-622`.
3. **"All eight traces" is 12 files, 8 of which contain a reroute.** The other
   four (`08-14-155019`, `08-22-171905`, `08-22-171920`, `08-25-222344`)
   record a route and no replacement. All 12 were read; the four contribute
   nothing to any reroute count. There are **four** 08-25 drives, not three.

## The corpus, re-measured

All 12 traces on disk, not just the three the brief names. `same-as-prev` is a
byte-identical polyline; `ran backwards` is `runningBackwards` true at the fix
that tripped the gate; `phantom off-route` is `off_log > 60` with the
unconstrained projection at `<= 30`.

| drive | fixes | reroutes | same-as-prev | ran backwards | phantom off-route | gaps (s) |
|---|---:|---:|---:|---:|---:|---|
| 2026-08-14-155019 | 2512 | 0 | 0 | 0 | 0 | – |
| 2026-08-14-192546 | 2829 | 11 | 3 | 2 | 1 | 8, 8, 9, 9, 8, 19, 956, 8, 17, 115 |
| 2026-08-22-171307 | 327 | 0 | 0 | 0 | 0 | – |
| 2026-08-22-171905 | 4 | 0 | 0 | 0 | 0 | – |
| 2026-08-22-171920 | 1615 | 0 | 0 | 0 | 0 | – |
| 2026-08-22-183419 | 2066 | 3 | 0 | 0 | 0 | 9, 777 |
| 2026-08-22-202700 | 1980 | 13 | 2 | 6 | 3 | 92, 1515, 75, 9, 10, 9, 10, 13, 46, 13, 11, 40 |
| 2026-08-22-222623 | 2679 | 15 | 1 | 4 | 3 | 17, 22, 11, 14, 9, 9, 46, 145, 252, 775, 74, 69, 9, 26 |
| 2026-08-25-180813 | 7956 | 1 | 0 | 0 | 0 | – |
| 2026-08-25-202122 | 3394 | 7 | 2 | 3 | 1 | 33, 33, 86, 17, 808, 46 |
| 2026-08-25-211808 | 3851 | 1 | 0 | 0 | 0 | – |
| 2026-08-25-222344 | 523 | 0 | 0 | 0 | 0 | – |
| **total** | **29736** | **51** | **8** | **15** | **8** | |

Counts are off-route reroutes only; the two user-initiated `fastest` adoptions
(`08-14-192546` seq 8, `08-22-171307` seq 1) are excluded, which is why the
row for `171307` reads 0. Across the 12 traces there are 65 `route` records —
12 initial routes and **53 adoptions after them**, of which 51 are off-route.
That 53 is the same denominator the `runningBackwards` comment uses at
`:683-684` ("12 of 53 reroutes"), so the corpus here is the one that work was
measured on, plus the 08-25 drives.

Where my numbers differ from that comment — it reports 12 of 53 running
backwards, I measure 15 of 51 — the likely cause is where the predicate is
evaluated: I test it at the fix that tripped the gate, which is the moment
that matters for rerouting. I did not reconcile the two, and the difference
does not bear on any finding below.

Two drives dominate the short gaps, and both predate the backoff: `08-22-202700`
and `08-22-222623` are the storms `05f3f00` was written against. The four
post-backoff drives produced 9 off-route reroutes between them, all legal
against today's gate.

## Finding 1 — an incoming route is never compared to the one being followed

**Confirmed. Confidence: high.**

`adopt` (`NavigationModel.swift:859-878`) is called unconditionally from
`reroute` (`NavigationModel.swift:839`). Nothing between the server response and
`adopt` looks at `route`, `coordinates`, or `steps` as they currently stand.
There is no identity check anywhere on the path.

Across all 51 off-route reroutes in the corpus, **8 adopted a polyline
byte-identical to the one already being followed:**

```
seq 4 vs seq 5   (drive-2026-08-25-202122)
  coords identical (==): True (2063 pts)
  steps  identical (==): True
  gap: 17.1 s

seq 6 vs seq 7   (drive-2026-08-25-202122)
  coords identical (==): True (1821 pts)
  steps  identical (==): False        <- see below
  gap: 46.0 s
```

Both pairs the brief flagged are confirmed exactly, and six more exist that the
brief did not have: `08-14-192546` seq 4, 5, 7; `08-22-202700` seq 6, 11;
`08-22-222623` seq 9. A ninth adoption (`08-22-222623` seq 8) matched an
*earlier* route rather than the immediately preceding one.

The cost of a no-op adoption, read off `adopt`:

- `currentStep = 0` (`:868`) — banner back to the first instruction
- `travelled = 0` (`:869`) — see Finding 3; this is load-bearing
- `matchAtAdoption = nil` (`:870`) — the `runningBackwards` anchor is rebuilt
- `remainingMeters`/`remainingMinutes` reset to the whole route (`:871-872`)
- `awaitingJoin = true` (`:876`) — off-route recovery disarmed again
- two statewide Dijkstras server-side, and 283 KB of geometry re-downloaded and
  re-appended to the trace across the 8 (83 KB for `202122` seq 5 alone)

Some of this is now transient. Since `beedb2c` and `0f7b5ba`, `advanceSteps`
re-walks the step index by distance on the next fix, so `currentStep = 0`
usually costs one fix rather than the whole drive — at `202122` seq 5 the
banner went step 3 → step 2, a one-step regression, not a reset to zero. **The
brief overstates this consequence for `main` as it now stands.** The reset of
`remainingMeters` and `awaitingJoin` is real on every fix in between.

### The obvious fix is wrong in a way worth stating

`seq 6 → 7` is why. The geometry is identical to the point, but the steps are
not — the opening maneuver changed:

```
step 0   seq 6: type='turn'    modifier='right'     'Turn right onto Lake Avenue'
         seq 7: type='depart'  modifier='straight'  'Head north on Lake Avenue'
```

That is the server correctly re-deriving the departure instruction for a car
whose heading had changed (`usableHeading`, `:358-362`). So a guard of the form
"if the coordinates match, discard the response" would suppress a genuine
instruction correction.

**What to do instead, precisely:** when `feature.coordinates == coordinates`,
do not call `adopt`. Swap `steps` and recompute `stepRemaining`, and leave
`currentStep`, `travelled`, `matchAtAdoption`, `remainingMeters` and
`awaitingJoin` untouched — a *merge* rather than an adoption. `currentStep`
stays valid because `stepRemaining` is recomputed against the same line, so the
`advanceSteps` walk lands in the same place. Unwritten, as instructed.

## Finding 2 — Lead 2's premise does not hold; the 17 s gap was legal

**Killed as stated. Confidence: high.**

The hypothesis names the wrong variable. `settleAwaitingJoin` (`:338-346`)
clears `awaitingJoin` and nothing else. The backoff counter is
`consecutiveReroutes` (`:211`), and it is cleared in exactly two places:
`trackSettling` (`:227-237`) and `switchToFastest` (`:777`). `settleAwaitingJoin`
never touches it. So "a same-route adoption settles instantly and therefore
resets the backoff" is false as a mechanism — nothing resets it instantly.

The brief's underlying *observation* is still correct: at `202122` seq 5,
`awaitingJoin` was cleared on the very first fix after adoption (`off` = 11.6 m
≤ 30 m). It just isn't the backoff.

`trackSettling` requires `off <= 30 m` sustained for `rerouteSettledSeconds`
(30 s) before zeroing the counter, and `reroute` sets `onRouteSince = nil` on
every off-route reroute (`:845`). Between seq 4 and seq 5 only 17 s elapsed, so
`trackSettling` **cannot** have fired in that window.

Replaying the gate resolves it. The counter had already been zeroed *earlier*:

```
 seq reason     t_rel    gap   cr     cd  time-gate-ok
   1 offroute      92      -    0      8   True
   2 offroute     124     33    1     16   True
   3 offroute     157     33    2     32   True
   4 offroute     243     86    0      8   True     <- cr zeroed during the 86 s
   5 offroute     260     17    1     16   True     <- 17 > 16. Legal.
   6 offroute    1069    808    0      8   True
   7 offroute    1115     46    1     16   True
```

During the 86 s between seq 3 and seq 4 the driver sat within 30 m of the line
for well over 30 s, so `trackSettling` zeroed the counter legitimately. At seq 5
the backoff was therefore 16 s, not 120 s, and a 17 s gap clears it with a
second to spare. **Every off-route reroute in every post-`05f3f00` drive passes
the time gate.** There is no backoff defect here.

The brief's expectation — "the backoff should have been at its longest by the
fifth reroute" — assumes the counter accumulates across a drive. It does not:
it is zeroed by any 30 s spell on-route, and this driver had one before almost
every reroute. That is `05f3f00` behaving as designed.

**Caveat, and it matters:** the `cr`/`cd` columns are only meaningful for the
08-25 drives. `05f3f00` landed 2026-08-24; the 08-14 and 08-22 traces were
recorded by a build with no backoff at all, so the `False` time-gate entries in
those drives are an artefact of replaying today's gate against yesterday's
recording, **not** evidence of a defect. Route identity and `runningBackwards`
are properties of the recorded geometry and are valid across all 12.

## Finding 3 — a pinned match floor manufactures off-route, and reroutes on it

**New. Confidence: high on the mechanism, medium on prevalence.**
**This is the one I would fix first, and it is what actually links the two leads.**

`offRoute` is not the distance from the car to the route. It is the distance
from the car to the nearest point of the route **at or after `notBefore`** —
`Geo.swift:94` rejects any segment ending before the floor. `update` sets that
floor from a monotonic running maximum (`:547`, `:560`):

```swift
let floor = hasJoinedRoute ? max(0, travelled - Self.backtrackToleranceMeters) : 0
...
travelled = max(travelled, here.travelled)
```

When a route runs over the same road twice — a reroute that opens by doubling
back past the car, exactly the shape `0f7b5ba` was written for — the match can
land on the wrong pass. As the car drives forward, its distance-along the
*wrong* pass decreases. Once it has decreased by more than
`backtrackToleranceMeters` (100 m), the floor pins the match to a point the car
is driving away from, and `offRoute` starts measuring the distance to that
pinned point rather than to the road the car is on.

Here is the whole loop, from `drive-2026-08-25-202122`. `off_log` is what the
app recorded; `off_free` is the same projection with the floor removed;
`trav_log` is the app's match, `trav_free` the unconstrained one:

```
 t_rel  rt  off_log off_free   gap  trav_log trav_free
   244   4     12.6     12.6    0.0    566.8     566.8   <- adopted; match 567 m in
   248   4     12.3     12.3    0.0    495.6     495.6   <- sliding backwards
   250   4     12.0     12.0    0.0    459.3     459.3
   251   4     16.8     12.0    4.9    453.2     441.4   <- floor bites; match freezes
   254   4     65.2     11.9   53.3    453.2     388.9   <- crosses 60 m. Not the driver.
   257   4    116.3     11.7  104.5    453.2     337.0
   260   4    168.2     11.8  156.4    453.2     284.6   <- reroute fires
   261   5     11.6     11.6    0.0    267.3     267.3   <- identical line; floor reset
   263   5      9.8      7.0    2.9    232.8       0.0   <- free match finds the right pass
```

Read the two columns at t=260: on a polyline that is *byte-identical* to the one
at t=261, the car is 11.8 m from its route and the app reports 168.2 m. The car
did not move between those two rows in any way that matters — the only variable
that changed is `travelled`, which `adopt` sets to 0 (`:869`), releasing the
floor. Distance to a fixed line cannot jump 156 m in one second; the reading
was wrong, not the road.

So the sequence is closed and self-sustaining:

1. The match pins behind the car; `offRoute` climbs with no physical cause.
2. It crosses 60 m; the gate fires (`:620`).
3. The server returns the same route, **correctly** — the car really is on the
   best route, which is exactly the case the brief warns not to call a server
   bug.
4. `adopt` resets `travelled = 0`, the floor releases, `off` collapses to 12 m,
   the car "settles", `trackSettling` starts a fresh 30 s clock.
5. The match re-pins. Back to 1.

That is why the two leads look like one bug: **6 of the 8 same-route adoptions
fired on a fix where `runningBackwards` was already true.** The identity is a
symptom; the pinned floor is the cause.

The remaining 2 (`08-14-192546` seq 5, `08-22-222623` seq 9) are *not* phantom —
`off_free` agreed with `off_log` at 180.6 m and 61.6 m. Those are the pure
Lead-1 case: a driver genuinely off route, a server correctly returning the
route they are ignoring, and a client adopting it as though it were new.
**Both findings are real and neither subsumes the other.**

### Prevalence, and why I only claim medium on it

Of 51 off-route reroutes: **15 fired while `runningBackwards` was true**, and
**8 fired on a fix the unconstrained projection puts within 30 m of the route**
(`off_log > 60` and `off_free <= 30`). Per drive:

- `08-25-202122`: 3 of 7 backwards, 1 clearly phantom (the 156 m case above)
- `08-22-202700`: 6 of 13 backwards, 3 phantom
- `08-22-222623`: 4 of 15 backwards, 3 phantom
- `08-14-192546`: 2 of 11 backwards, 1 phantom

The medium is on the 8, not the mechanism. `off_free <= 30` searches the whole
line, so it also reads "on route" for a car near a *far later* part of its own
route, which would be a genuine off-route. The 156 m case is proved by the
byte-identical polyline either side of the adoption and needs no such
inference; the corpus-wide 8 does.

### The seventh clause — and why the obvious one is not enough

The brief asks what a seventh clause would guard. The honest answer is that a
seventh clause is the wrong shape for this.

Adding `!runningBackwards(here)` to the gate at `:614-620` would have blocked
`202122` seq 5 — I checked, the predicate was true at t=260. But it would have
traded a reroute storm for a frozen banner. With the reroute suppressed,
`travelled` stays 566.8, the floor stays 466.8, the match stays pinned at 453.2,
and `advanceSteps` stays held by its own `!runningBackwards` guard (`:639`).
Nothing would ever release it: `runningBackwards` only clears once `travelled`
climbs past its anchor, and the pinned match is what stops `travelled` climbing.
The drive would navigate on a frozen banner and a wrong `remaining` instead.

Today the accidental rescue is `adopt` resetting `travelled = 0`. The reroute
storm *is* the recovery mechanism.

**So the fix belongs in the match, not the gate:** when `runningBackwards`
holds and `offRoute` is growing while the unconstrained projection is not, the
floor should be released — re-run `progress` with `notBefore = 0` and re-seat
`travelled` on the result — rather than suppressing the reroute that currently
papers over it. `runningBackwards` already detects the condition exactly and is
already computed every fix; it is used at `:639` for the banner and nowhere
else. Unwritten, as instructed.

The doc comment at `:690-693` asserts this cannot bind late in a drive because
"`update`'s own backtrack floor keeps the match within `backtrackToleranceMeters`
of a running maximum that has long since passed the anchor". That holds only
once `travelled` exceeds `anchor + 100`. At `202122` seq 4 `travelled` peaked at
566.8 — its value at the anchor fix — and never exceeded it, so the escape the
comment relies on never opened.

## Finding 4 — a failed "fastest" tap silently wipes the backoff

**Confidence: medium. Code-read only; not observed in any trace.**

`switchToFastest` (`:771-798`) zeroes `consecutiveReroutes` and `onRouteSince`
at `:777-778`, *before* awaiting the fetch, on the reasoning that "the driver
has changed their mind about where they are going". On `.failed` or `.ended` it
carefully restores `followingFastest` and `pref` — but not the two backoff
fields. If the request never lands, the driver did not change their mind, and a
backoff that had climbed to 120 s is back at 8 s with nothing to show for it.

Narrow: it needs a tap and a failed request. But the restore block at `:792-794`
is already the right home for it, and it is one line.

## Finding 5 — the backoff counts only reroutes that succeed

**Confidence: medium-low as a defect. Code-read only.**

`consecutiveReroutes += 1` sits at `:844`, after the `await` and after the
`guard !arrived`. A reroute that fails — server unreachable, request superseded,
drive ended mid-flight — never increments it. Against a server that is down, the
interval therefore stays at the 8 s base indefinitely rather than backing off.

I am not confident this is wrong. `lastRerouteAttempt` is set before the await
(`:816`), so the 8 s floor does hold, and the backoff was explicitly written for
"each reroute that fails to settle the driver" rather than for transport
failure (`:196-201`, and the gate comment at `:611-613`). Flagging it as a deliberate-or-not question rather than a defect.

## What could not be determined

Stated plainly, as asked:

1. **Whether the server was right to return the same route.** The traces record
   what came back, never the request. Origin, heading and `pref` are all
   reconstructable-ish but not recorded per reroute, so "given this origin and
   this heading, was that the best route?" cannot be answered from the trace
   alone. For the six phantom cases it is very likely yes — the car was on the
   route — but that is inference. **Recording the request alongside the response
   in `DriveTrace.route` would close this**, and is the single cheapest addition
   to the instrument. *(Done in `db52645`, after this was written — but only for
   drives recorded from now on; it changes nothing about the traces above.)*
2. **Why the match landed 567 m into a freshly adopted route** at `202122`
   seq 4. That it *did* is certain, and the doubling-back explanation fits
   every column. But proving the route doubles back over that road needs the
   geometry walked against the road graph, which is `data/`-dependent work I did
   not do.
3. **Whether the 8 phantom fixes generalise.** See the prevalence caveat above.
   A drive deliberately routed over a road the route uses twice would settle it
   in one run.
4. **Anything about the arrival-gate defect at `:571-582`.** It is out of scope
   and I did not touch it. I did not see it interact with the reroute path in
   any trace — no drive in the corpus ends inside a reroute — but I was not
   looking for it.
5. **Whether any of this reproduces on a 12th trace or a simulator run.** No
   test was written and no source was changed, per the brief. The claims here
   rest on recorded drives and on reading `main`.

## Suggested order, if these are picked up

1. Finding 3 — it is the cause of 6 of the 8, and the current recovery is
   accidental.
2. Finding 1 as a merge, not a discard — it still stands alone for the other 2.
3. Finding 4 — one line, in a block that already exists.
4. Finding 5 — decide whether it is deliberate, then leave a comment either way.

Findings 1 and 3 want a test each. Per the brief, **not** in
`ios/Tests/RerouteTests.swift`; a new `ios/Tests/RerouteIdentityTests.swift`
would hold both — one asserting that adopting a route equal to the current one
preserves `currentStep`/`travelled`, one driving the fix sequence above and
asserting `offRoute` does not climb while the car holds station near the line.

---

# What was fixed

**2026-08-26, after the audit above and on the same branch.** The brief's "change
no source file" was lifted explicitly. Before touching anything I re-checked that
no other branch had landed reroute work — `main` was still at `e6109d9` and every
branch touching this path was at or behind it.

Four changes in `ios/Sources/NavigationModel.swift`, plus one new test file.
Findings 1, 3, 4 and 5 are all fixed. Finding 2 needed no fix: it was killed as a
defect, and the replay below re-confirms every gap was legal.

## Finding 3 — `reseatIfPinned`

The root cause, so it went first. `update` now runs the floored projection
through `reseatIfPinned`: only when the constrained match claims off-route does
it ask the unconstrained one, and only if *that* puts the driver within
`joinConfirmMeters` — and no further back than `reseatWindowMeters` (500 m) — is
the match re-seated. `travelled`, `matchAtAdoption` and `currentStep` are
re-derived from the corrected match.

Two bounds make this safe, and both matter:

- The free match can only ever be *earlier* than the floor, because anything
  later is available to the constrained search too. This can never skip a driver
  forwards.
- The 500 m window stops a scenic loop's outbound leg standing in for its
  return. Without it the measured 282 m correction and a 35 km one look alike.

The audit warned that a `!runningBackwards` gate clause would trade the storm for
a frozen banner. That is why the fix is in the match and not in the gate; the
gate is untouched and still has its seven clauses.

## Finding 1 — `sameLine` / `merge`

`reroute` now compares the incoming geometry against the line being driven. On a
match it calls `merge` instead of `adopt`: the steps and `stepRemaining` are
replaced, `currentStep` is re-derived, and `travelled`, `matchAtAdoption` and
`remainingMeters` are left alone.

Two things the audit called for, and one it did not:

- **Merge, not discard.** `seq 6 → 7` returned the same polyline with a corrected
  departure instruction, so the words are still taken.
- **The step index is re-derived, not kept.** An index into the old step list
  names a different maneuver in the new one. `firstStepAhead` was split out of
  `advanceSteps` so both can use the same walk.
- **`awaitingJoin` is still armed.** This the audit missed, and the existing
  suite caught it: the driver was sent a replacement *because they had left the
  route*, and nothing about the line being unchanged puts them back on it.
  Without the join gate a driver drifting beside their route asks again on every
  cooldown — the exact storm this path exists to stop.

Merged routes are recorded in the trace with `-same` appended to the reason, so a
drive handed the same line six times still says so.

## Findings 4 and 5 — the two small ones

`switchToFastest` now saves `consecutiveReroutes`/`onRouteSince` alongside `pref`
and restores all four when the request fails. And a reroute that never lands now
counts toward the backoff, so a dead server is retried on a widening interval
rather than every 8 s forever.

**Finding 5 is the one judgement call in here rather than a proven defect** — the
audit rated it medium-low and said so. It is in its own commit, so it can be
dropped without disturbing the rest. The reasoning for keeping it: reaching the
120 s cap takes four consecutive failures, by which point the network is gone,
and `trackSettling` clears the counter as soon as the driver holds the line for
30 s.

## Verification

**The tests have teeth.** Each fix was reverted in turn and the suite re-run.
Every mutation was caught, by exactly one test and no others:

| mutation | test that failed |
|---|---|
| projection no longer re-seated | `test_a_match_pinned_behind_the_car_does_not_read_as_off_route` |
| adopt unconditionally again | `..._keeps_the_drive_where_it_is`, `..._still_takes_the_better_words` |
| `merge` drops the join guard | `test_the_same_line_back_again_still_holds_off_the_next_reroute` |
| failed `fastest` tap not restored | `test_a_fastest_switch_that_never_lands_leaves_the_backoff_alone` |
| failed reroute costs nothing | `test_a_reroute_that_never_lands_still_costs_an_interval` |

**Replayed against all twelve recorded drives.** The matcher was re-implemented
in numpy and first validated against the phone's own numbers: **max
|simulated − logged| = 0.0000 m across 29,736 fixes**, so the counts below are
the app's arithmetic, not an approximation of it.

| | before | after |
|---|---:|---:|
| fixes reading off-route (> 60 m) | 2069 | 2053 |
| re-seats performed | – | 7 |
| off-route reroutes that would fire | 51 | 43 |

**8 of the 51 off-route reroutes stop firing at all** — the phantom ones. Of the
8 byte-identical adoptions, 3 disappear entirely and the other 5 still fire,
correctly, and are now merged rather than adopted. `202122` seq 5, the 17 s gap
that started this, is one of the 3: by the trigger fix the match has already been
re-seated and reads 11.8 m instead of 168.2 m.

Both suites green: **240 backend, 118 iOS** (110 before, plus the 8 new).

## Test files that were edited, and why

The brief said not to touch `ios/Tests/RerouteTests.swift`. Two files needed a
fixture change anyway, and it is worth being explicit about what changed:

- `RerouteTests.namedRoute` and two `DriveTraceTests` fixtures built their
  "replacement" route with `Fixture.straightRoute()` at `start: 0` — the
  **byte-identical line** the model was already following. So tests written for
  the adopt path silently exercised the merge path once merging existed. They now
  pass `start: 150`, which is what `Fixture.straightRoute`'s own documentation
  says a replacement looks like ("it begins at the graph junction `snap` chose").
  Collinear with the original, so every geometric assumption in those tests is
  unchanged.
- **No assertion was weakened.** One of the two DriveTrace fixtures was
  contradicting its own comment, which opens "`travelled` restarts at zero on a
  new line" over a fixture whose line was not new.

Everything about the same-line path is tested in the new
`ios/Tests/RerouteIdentityTests.swift`, as the audit proposed.

## What is still open

- The arrival-gate defect at `NavigationModel.swift:571-582` — out of scope
  throughout, still open, still someone else's.
- **The request is now recorded** — `req_lat`, `req_lon`, `req_heading` and
  `req_pref` on every `route` record, added in `db52645`. That closes the gap
  for drives from here on, but **it cannot be applied backwards**: all twelve
  traces on disk were recorded without it, so "was the server right to return
  the same route?" stays unanswerable for every reroute analysed above. The next
  drive is what settles it.
- The 500 m re-seat window is bounded by argument and by one measured case
  (282 m), not by a fitted distribution. A drive deliberately routed over a road
  the route uses twice would be the way to calibrate it.
