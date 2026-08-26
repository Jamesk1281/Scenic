# The banner skips the first turn of every reroute

Reported by the driver on 2026-08-25 ("it always seems to place me one step of
the drive ahead of where it should place me on a reroute"), then confirmed
against that day's three traces. **Fixed 2026-08-25** — see "What was changed"
below. The diagnosis is kept above the fix because two of its three conclusions
survived contact with the code and one did not.

All four rows of the table below are now closed. Getting the last one took a
fourth fault, and killing a fifth idea that measured worse than the bug.

## The symptom, measured

Across the four traces of 2026-08-25 there were 13 route adoptions. **On 11 of
them the first fix after adoption already reports `step >= 1`** — step 0 is
consumed before the driver has moved. Where step[0] is a `depart` that is
harmless, because a depart is not a maneuver. Where step[0] is a real turn, the
driver is never told to make it:

| drive | adopted | step[0], skipped | shown instead |
|---|---|---|---|
| `drive-2026-08-25-202122` | 16:22:53 | Turn left onto Cliff Street | Turn right onto Granite Street |
| `drive-2026-08-25-202122` | 16:23:26 | Make a U-turn on Millbury Street | Turn right onto Cliff Street |
| `drive-2026-08-25-202122` | 16:39:10 | Turn right onto Lake Avenue | Continue onto Lake Avenue North |
| `drive-2026-08-25-211808` | 18:21:25 | Turn right onto Great Plain Avenue | Turn left onto Linden Street |

Widening to all five recorded drive days gives 51 reroute adoptions, and their
first fix lands a median 8.2 m along the new line — but **21 of the 51 land at
exactly 0.0 m**, which is the case the arithmetic below turns on.

## Four faults, not two

**1. The guard that exists for this is bypassed.** `adopt()` resets
`currentStep = 0` and sets `awaitingJoin = true`, but `reroute()` then sets
`hasJoinedRoute` **true** by hand, deliberately, so the banner keeps giving
instructions over the gap between the car and the new line. `advanceSteps`
guarded on `hasJoinedRoute`, so its own comment described a situation it could
never catch:

> Before joining, the projection onto the line is meaningless (it can land
> anywhere), so leave the step where it is.

`awaitingJoin` is the state that means what the comment says, and
`settleAwaitingJoin` already clears it. Reading that instead is the fix.

Worst case measured: the 16:22:53 reroute returned a line whose first maneuver
was **481 m away**. The banner skipped it, showed the maneuver after it, and —
because the projection stayed pinned to the start of a line the driver had not
reached — **froze the countdown at 216 m for the 28 seconds it took to drive
those 481 m**.

**2. The advance condition fired on equality.**

```swift
while currentStep < steps.count - 1,
      stepRemaining[currentStep] >= here.remaining {
```

`stepRemaining[0]` is the distance from step 0 to the end. A reroute's first
maneuver sits at the line's origin, so that is the whole route — and at the
instant of adoption `here.remaining` is the whole route too. `>=` consumed the
instruction on equality. That is the 21 of 51 first fixes at exactly 0.0 m.

**3. The one the brief got wrong.** The original diagnosis attributed the
multi-step jumps to fault 1 and expected the `awaitingJoin` gate to close them.
It does not, and the traces say why: `settleAwaitingJoin` releases at
`joinConfirmMeters`, 30 m, and in three of the worst cases the driver was
*already* inside that at adoption — 12.6 m at 16:25:25, 0.0 m at 16:23:26. The
gate opens on the first fix and the jump happens on the second.

Fixing the brief's two faults exactly as written was run against the tests in
this change: it leaves the 18:21:25 Needham case still broken. What is actually
happening there is that a driver **rounding the corner** the new route turns at
projects *past* that corner onto the outgoing leg — 18.2 m along at adoption,
9.9 m at the moment the join gate let go — which reads as having driven through
a maneuver they had not yet reached. `>` versus `>=` cannot see the difference
between 9.9 m and 0 m. Passing a maneuver needs a deadband, not a strict
comparison.

**4. The route that turns you around.** A replacement route can begin ahead of
the car and double back over the road it is already on — it opens with a U-turn,
or the driver is standing on the leg that comes *back*. The match then lands
**legitimately**, on tarmac the route really does cover, but hundreds of metres
along it. Every maneuver before that point reads as driven through.

The 16:23:26 reroute opened 229.6 m along at **0.0 m off the line** and skipped
the U-turn that was the entire point of the route. Neither the join gate nor the
deadband can see this: the driver is genuinely *on* the line, so every "have they
reached it?" test passes. What gives it away is the direction — their position
along the line runs backwards, fix after fix, while they drive forwards. 12 of 53
reroutes across five drives did this, sliding as much as 154 m.

## What was changed

All of it in `advanceSteps` (`ios/Sources/NavigationModel.swift`). The arrival
gate, the off-route recovery guards and `hasJoinedRoute` itself are untouched.

1. The guard reads `hasJoinedRoute, !awaitingJoin`. While the driver is on their
   way to a freshly adopted line the step holds and `distanceToNext` becomes the
   straight-line distance to the maneuver — so the countdown runs down instead
   of freezing.
2. The comparison is strict, and the first maneuver of a route carries a
   deadband (`passedMargin`): 30 m, the same `joinConfirmMeters` and for the
   same geometry, capped at half the opening leg so saving the first maneuver
   cannot cost the second.
3. The step also holds while the driver is `runningBackwards` — while their
   position along the freshly adopted line is more than 5 m below where that
   line first put them. Deliberately not a latch: it is re-decided every fix, so
   the moment they turn around and the match climbs again the banner picks up
   where they now are. The 5 m comes from the traces — on a stopped car sitting
   on its route the match wobbles a median 0.09 m between fixes and never more
   than 2.9 m over 1,028 such fixes, while one second of driving is 15 m.

Tests, expressed as values rather than trace fixtures (`traces/` is gitignored):

| test | the row it comes from |
|---|---|
| `test_a_reroute_does_not_withhold_the_turn_that_gets_you_onto_it` | 16:22:53 Cliff Street — and asserts the countdown runs down |
| `test_a_reroute_that_begins_under_the_car_still_gives_its_first_turn` | 16:39:10 Lake Avenue, and the 21-of-51 equality case |
| `test_rounding_the_corner_onto_a_reroute_does_not_count_as_taking_it` | 18:21:25 Great Plain Avenue |
| `test_a_short_first_leg_is_not_swallowed_by_the_deadband` | guards the deadband against becoming its own way to skip a turn |
| `test_stopping_at_a_maneuver_does_not_drop_it_from_the_banner` | fault 2 mid-route: standing at the turn is when you need telling |
| `test_a_reroute_that_turns_you_around_keeps_giving_you_the_u_turn` | 16:23:26 Millbury Street — and that turning around releases the hold |

Each was checked against the unfixed code and fails there, reproducing the trace
values — the frozen countdown comes out at 216.0 m, the same number the driver
saw.

## The storm hypothesis: confirmed for the cluster, not the whole cause

The brief proposed that this bug *is* the reroute storms — a driver never told
to make the turn stays off-route and triggers another reroute. **Confirmed as a
mechanism, rejected as the sole cause, and the "steady 33-second cadence" that
suggested it is a coincidence.**

What holds. Within the 16:22–16:26 cluster the withheld maneuver is the one the
driver then fails to make, four times over:

| adopted | first maneuver, as shown | driver | outcome |
|---|---|---|---|
| 16:22:53 | withheld (Cliff Street) | drove the 481 m to the line, reached it at 16:23:21 at 2.0 m off, then left it within 5 s | reroute +33 s |
| 16:23:26 | withheld (U-turn on Millbury) | carried straight on, 0 → 269 m off | reroute +33 s |
| 16:23:59 | **correct** (step[0] a depart) | followed it for 61 s of driving, 3 → 813 m travelled, never more than 3.0 m off | — |
| 16:25:25 | wrong (jumped 3 steps, skipping a U-turn) | left the line | reroute +17 s |
| 16:25:42 | wrong (jumped 2 steps, same U-turn) | left the line, then stopped | — |

The one adoption in the cluster whose banner was right is the one the driver
took. That is as close to a natural control as a trace gets.

What does not hold.

- **One link in the chain has a different cause.** The driver left the 16:23:59
  route at 16:25:19, at its exit maneuver — which the banner had displayed
  correctly the whole way in, with an 8 m countdown. That off-route was not the
  step bug.
- **The 33 s cadence is not a signature.** Reconstructing each gap against the
  guards: 16:22:53→16:23:26 was set by the driver leaving the line 6 s after
  reaching it (the 16 s cooldown had expired 16 s earlier); 16:23:26→16:23:59
  was set by the 32 s exponential backoff. Two different binding constraints
  that happen to land on the same number. The other gaps in the same cluster are
  17 s, 86 s and 46 s, and the 46 s one is bound by a third thing again — the
  45 s `joinGraceSeconds`.
- **The snap offset feeds the storms independently.** The 16:22:53 reroute
  returned a line starting 481 m away, so the car was over the 60 m off-route
  threshold *by construction* from the first fix. `awaitingJoin` suppresses the
  reroute for that gap but does not make the driver on-route. That is the
  separate destination/snap defect, and it is untouched by this change.

So: expect this fix to break most of these loops and not all of them. All four
withheld-turn rows in the table at the top are corrected, but the 16:23:59 link
had a different cause and the snap offset is untouched.

## The idea that measured worse than the bug

The obvious fix for fault 4 is to anchor the *match* rather than the banner: put
a ceiling on how far along a fresh line the driver can plausibly be, the mirror
of the `notBefore` floor, seeded at 0 because a replacement route is planned from
where the car is standing. **Do not do this.** It was built and measured before
the guard above was written, and it is worse than the defect.

The method is worth keeping: `Geo.progress` and the whole banner state machine
were ported to Python and replayed over all twelve recorded drives. The port
reproduces the phone's own recorded `off` and `travelled` to **0.0000 m** across
7,245 fixes, and its step index matches on **3,394 of 3,394**, so any difference
under an A/B is the change and nothing else.

Under the ceiling, wrong-pass openings fall from 12 of 52 to 3 — and two real
drives break:

- `drive-2026-08-25-222344`: the driver was **116 m from the line's end and
  38 km from its start**. The ceiling clamps them to 28.7 m along and reports
  them **38,197 m off route**. The premise is simply false — an initial route
  need not begin where the driver is standing. That is the case
  `hasJoinedRoute` exists for.
- `drive-2026-08-22-171905`: the snap put the line start 314 m *behind* the
  driver, who was 2.6 m off it at 320 m along — legitimately. The ceiling drags
  them to 52 m along and 262 m off.

The banner symptom and the distance symptom are separable, and only the banner
one is worth fixing. `runningBackwards` fixes it in nine lines without touching
`progress`, arrival, or off-route recovery.

## Still open

**Remaining distance and ETA still credit undriven route.** Fault 4 is fixed for
the banner, not for the odometer: a driver matched 229.6 m along a line they
have not started still has 229.6 m knocked off `remainingMeters`. On a 42 km trip
that is 0.5%, and it corrects itself as soon as they are on the route properly.
Fixing it means constraining the match, which is the section above.

**The arrival gate.** Unrelated and still open: a drive that never joins its
route can never end (`hasJoinedRoute` gates all three arrival tests). Left alone
here on purpose.

## Traps, for whoever picks up what is left

- **Do not clear `hasJoinedRoute` in `adopt()`.** It also gates arrival
  detection and the backtrack `floor` in `update()`, and the arrival defect
  above would be tangled into it. Change what `advanceSteps` reads instead —
  which is what this did.
- **`traces/` is gitignored** (`*.ndjson`), so the evidence here cannot be
  checked into a test. Express cases as values in `ios/Tests/RerouteTests.swift`
  / `NavigationModelTests.swift`.
- **A test that only adopts a route and asserts `currentStep == 0` proves
  nothing** — `adopt()` sets it to 0 regardless. The assertion has to be on the
  state after the first `update(_:)` following adoption, and for the deadband
  after the *second*, since the first is held by the join guard either way.
- **Run the suite with the local server up** (`.venv/bin/python
  server/serve.py`). Six `LiveDriveTests` skip silently without it, and they are
  the only tests that drive real route geometry end to end — exactly what a
  change to step advancement can break.
