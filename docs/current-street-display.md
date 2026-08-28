# Show the driver what street they are on

**Status: implemented.** `RouteStep.name` is decoded, `NavigationModel.currentRoad`
derives the road under the car, and `NavView` shows it across the top of the trip
bar. Part two — whether the street name bears on the reroute start-point problem
— is answered below: it does not, and the reason is stronger than the prior this
brief started with.

## The goal

Display the road the driver is currently on, at the bottom of the navigation
screen. Separately, assess whether knowing it would help the reroute
start-point problems.

## The finding: the name was already on the wire

`pipeline/router.py` sets `step["name"]` on every step it builds
(lines 1572, 1659, 1675, 1705) and `geojson()` puts `self.steps()` into the
response verbatim, so it survives to the client. Measured on a live
Harvard→Needham route:

```
keys on a step: destination, distance_m, exit_ref, instruction, lat, lon,
                modifier, name, roundabout_exit, type

depart   name=West Bare Hill Road    "Head east on West Bare Hill Road"
fork     name=West Bare Hill Road    "Keep left to stay on West Bare Hill Road"
turn     name=Bolton Road            "Turn left onto Bolton Road"
fork     name=Armstrong Road         "Keep right onto Armstrong Road"

steps with a non-empty name: 47/48
```

`RouteStep` in `ios/Sources/Models.swift` did not declare `name`, so the client
decoded the response and dropped the one field this feature needed. Adding it
was one line, as advertised.

The 48th step is the arrival, which carries `name: ""` by construction
(`router.py:1659`). Empty is also what an unnamed way produces: `label` is
`name or ref or ""`, so a service road, a track, or a ramp with no name of its
own comes through empty rather than absent. That distinction turned out to
matter — see the display rules.

## Deriving "the road I am on now"

`step["name"]` is the road the step takes you **onto**, not the road it starts
from: `_legs` groups edges into stretches worth one instruction, and the step
for leg *i* describes the maneuver at leg *i*'s start while carrying leg *i*'s
label and leg *i*'s length. `currentStep` is `firstStepAhead`, "the first
maneuver not yet driven through" — the maneuver being *approached*. So the road
under the car is the name on the maneuver already completed:

    currentRoad = steps[max(0, currentStep - 1)].name

**Verified, not assumed.** `test_the_road_shown_is_the_one_being_driven_not_the_one_ahead`
drives the standard fixture and pins all three:

| fix | banner | road shown |
|---|---|---|
| 100 m | Turn right onto Elm Street | Test Road |
| 1500 m | Turn left onto Oak Street | Elm Street |
| 3500 m | Arrive at your destination | Oak Street |

`currentStep` is capped at `steps.count - 1` by `firstStepAhead`, so the index
is always in range and the empty arrival name is never the one read — the last
leg keeps naming its road right up to the pin
(`test_the_empty_name_on_arrival_never_reaches_the_screen`).

The fixtures could not test this as they stood: `Fixture.feature` built steps
with `instruction`, `lat`, `lon` and `distance_m` and **no `name` key at all**,
and `Fixture.response` re-encoded through the same builder, so a replacement
route arrived unnamed however the original was built. Both now carry the field.
`Fixture.routeWithRoadNames` is `straightRoute`'s geometry with the names the
server really sends, arrival's empty one included; `straightRoute` still omits
the key, which keeps the older-cached-response path covered.

The fixture's names are deliberately not recoverable from its instructions by
position — the whole failure mode is reading "Turn right onto Elm Street" and
displaying Elm Street, so a test that passes by accident had to be made
impossible.

## The off-route decision

**Chosen: three states — the road, an explicit "Off route", or nothing.** A
stale name is never shown.

- **The road**, whenever the step list still describes where the car is.
- **"Off route"**, once the driver has joined a route and is no longer on the
  line it describes. Said out loud rather than left blank: this is the state a
  bad snap puts the driver in, and it is the one they have no other way to
  notice.
- **Nothing**, when there is nothing honest to say: before the driver reaches
  the route at all, after arriving, and on a road the route never named.

Not "off route" before joining, which is the distinction worth the extra state.
The trip was planned from somewhere the driver isn't; they are on a road this
route has never heard of, and the banner already says "Head to the start of your
route". A second, vaguer version of the same message underneath it is worse than
silence.

Nothing rather than "off route" for an unnamed road, too. Being on a track the
graph never named is not being lost, and 4% of legs are in that position.

### The brief had the mechanism wrong

The brief said `currentStep` freezes when the driver leaves the line, "unless
`hasJoinedRoute && !awaitingJoin`". `advanceSteps` guards on
`hasJoinedRoute, !awaitingJoin, !runningBackwards(here)` — **being off the line
is not one of them.** `hasJoinedRoute` is a latch that never clears, so a driver
500 m down the wrong turning still has it set, still projects onto the abandoned
line, and `advanceSteps` keeps walking the index off that projection.

The conclusion survives and the reasoning gets worse, not better: the name does
not freeze, it *drifts*. It changes, plausibly, at roughly the right rate, and
means nothing. A frozen readout at least looks stuck. So the gate is on the
driver's actual relationship to the line, in four parts:

- **`hasJoinedRoute`** — they have reached the route at least once.
- **`!awaitingJoin`** — `advanceSteps` is deliberately held over the gap to a
  freshly adopted line, and a name off a held index is stale by definition.
  This also covers the one fix on which `lastProgress` still refers to the
  *previous* line, between `adopt` and the next update.
- **`!runningBackwards`** — the match landed on a leg the driver is driving away
  from; every index off it comes from the far side of a turn they never made.
- **`offRoute <= offRouteMeters`** — the same 60 m that triggers a reroute, so
  the readout and the rerouting can never disagree about whether the driver is
  on their route.

`lastProgress == nil` reads as nothing-yet, not off-route: the reroute path
forces `hasJoinedRoute` true by hand, so tapping "fastest" before the first fix
lands would otherwise announce that a stationary driver had strayed.

### What this looks like after a reroute, and why it stays

A replacement line begins at the junction `snap` chose, a median 99 m ahead of
the car. The car is therefore ~99 m from the nearest point of its own new route,
`awaitingJoin` is set, and the readout says **"Off route"** for the few seconds
it takes to reach the line — roughly 5 s at town speed, ~12 s at the p90 offset
— while the banner is already giving the new route's first instruction.

That is honest and it is kept on purpose. The alternative was a second, gentler
label for the post-reroute gap ("Rejoining route"), and it was rejected: the gap
is the app's own snap offset, and giving it a reassuring name hides the exact
symptom this readout is meant to expose. "The app does not think you are on your
route" is true in both cases, and the driver is owed the true version. Recorded
as a decision, not an oversight.

## Known limits, none of them worth fixing here

- **Unnamed roads show nothing.** 4% of legs carry neither `name` nor `ref`.
- **On an unnamed ramp, the road shown is the one the ramp joins.**
  `_describe_ramp` sets `name = leg["label"] or joins`, and 96% of ramps have no
  name. "I 93 North" while on the slip road to I 93 North is arguably what a
  driver wants, but it is not literally the road under the car.
- **A folded leg keeps the previous road's name.** `steps()` folds a leg into
  the previous step when it repeats the name (harmless — same name) or when a
  "continue" is shorter than `MIN_INSTRUCTION_GAP_M`, 60 m (a differently-named
  stretch under 60 m long shows the road before it).
- **One fix of lag after a merge.** `merge` re-arms `awaitingJoin` and
  `settleAwaitingJoin` clears it only after `advanceSteps` has already been held
  on that fix, so the first fix following a merge names the road from the index
  `merge` derived rather than from where the car now is. Inherited, not
  introduced: `currentInstruction` lags by exactly the same fix, and both catch
  up about a second later. Asserted in
  `test_a_merged_route_keeps_naming_the_road` so it is a known second rather
  than a mystery.

## Part two: is this a fix for the reroute start-point problem?

**No.** Not "probably not" — the street name cannot participate in the decision
that goes wrong, and the code says why in one line.

`snap` does not choose a road by name. It calls
`self._edge_tree.nearest(point)`, which picks the nearest road **segment**
geometrically, and then returns one of *that segment's two endpoint nodes*. The
name is a column on the edge that has already been chosen — an output of the
lookup, not an input to it. There is no candidate set for a name to
disambiguate, so there is no way to feed one in.

And the two things a name might plausibly have helped with are already closed by
measurement:

- **Right road.** Going via the nearest segment rather than the nearest junction
  is what guarantees the start is on the driver's own road: "both ends of the
  nearest segment are, by construction, on the road you are standing on."
  Measured over 400 blocks, snapping to the nearest junction outright put 23% of
  residential addresses on the wrong road; via the segment it is 0%.
- **Right direction.** `server/app.py:126` passes the driver's course into
  `snap`, and `_forward_end` picks the end that lies ahead along the road's own
  tangent — not along the straight line to each end, which is what makes a loop
  ramp work. Measured over 3,857 mid-edge samples.

What remains is neither identity nor direction. The returned node is an *end* of
the segment and the car is in the middle of it, so the route starts at a
junction a median 99 m away (p90 217 m). That is edge-to-node quantization: an
error of **distance along a correctly identified road**, and a street name is
not a distance. The README already names the fix and it is geometric —
"splitting the snapped edge into two virtual nodes per request would take that
to zero."

**Where the readout does earn its place**, and this is its real argument: it
makes a bad start point *observable*, which it was not before. The 99 m offset
now shows up on screen as a few seconds of "Off route" immediately after each
reroute, and a snap onto the wrong road entirely shows up as an "Off route" that
does not clear. `RouteModel.nameCurrentLocation` already spends a reverse-geocode
on labelling the trip's start for exactly this reason — "'My Location' on its own
gives the driver no way to notice we've placed them on the wrong road" — and the
same argument holds at 60 km/h. Diagnostic value, not an algorithmic fix, and
worth having on its own terms.

The rerouting logic was not touched. A separate session is auditing it.

## Traps, still true

- **Do not call `CLGeocoder` continuously.** Apple throttles reverse geocoding
  aggressively and it is not built for per-fix use; at 1 Hz it will fail and may
  get the app rate-limited. The single call at `RouteModel.swift:159` remains
  the only one in the codebase and is a one-shot label. This feature adds none:
  the name comes off the route.
- **Do not parse the road name out of `instruction`.** The string is built for
  the screen ("Keep left to stay on West Bare Hill Road"), varies by maneuver
  type, and the structured field exists.
- **`name` is the road a step goes *onto*.** Pinned from both sides now:
  `test_name_is_the_road_the_step_goes_onto` in `tests/test_routing.py` asserts
  the sense server-side, because the client now derives the road under the car
  from it and flipping the field would have nothing else to fail against.

## Tests

Backend `tests/test_routing.py`: `test_name_is_the_road_the_step_goes_onto`,
`test_every_step_carries_a_name_key`.

iOS `ModelsTests`: `test_a_step_carries_the_road_it_puts_you_on`,
`test_an_unnamed_road_decodes_as_empty_not_missing`.

iOS `NavigationModelTests`: the off-by-one table above, the first road before
any maneuver, the empty arrival name, nothing before joining, no name once off
the line, nothing on an unnamed route, nothing after arriving.

iOS `RerouteTests`: `test_no_road_is_named_over_the_gap_to_a_freshly_adopted_line`,
`test_a_merged_route_keeps_naming_the_road`.

iOS `LiveDriveTests`: `test_the_road_shown_matches_the_instruction_that_put_the_driver_there`
drives the real Worcester→Boston scenic route and checks the road shown against
a *second instrument on the same input* — the road named in the instruction of
the step already driven through, which the server renders from the same leg
label it puts in `name`. Over 100 fixes cross-checked per run, on real ramps,
forks and unnamed ways rather than four invented steps.

**The tests have teeth.** Mutating `steps[max(0, currentStep - 1)]` to
`steps[currentStep]` — the off-by-one this whole readout turns on — fails five
tests across three files, the live one among them:

```
on step 1: "Head north on Main Street" put the driver on Main Street,
           the screen says Thomas Street
```

Both suites green: **242 backend** (240 + 2) and **131 iOS** (119 + 12), with
**0 iOS skips** — `LiveDriveTests` was run against a local `server/serve.py`
from the main checkout, since a green run with those 6 skipped means nothing for
a change that touches step indexing.

Verified on screen as well as in tests, by temporarily rooting the app at a
`NavView` fed a canned route (harness deleted before commit): at a fix 1.5 km
along, the banner read "0.9 mi — Turn left onto Oak Street" and the readout
below read "Elm Street". A second fix 300 m off the line turned it to "Off
route" in orange rather than leaving "Elm Street" on screen.
