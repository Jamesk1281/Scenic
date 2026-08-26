# Arriving leaves a stale route armed, and it can be driven again

**Status: fixed** — `endNavigation()` in `ios/Sources/RouteModel.swift`, with
`ios/Tests/RouteModelTests.swift` to hold it; see "What was done". One of four
defects found on the 2026-08-25 drives; the other three are tracked separately
and deliberately excluded here — see "Out of scope".

## What happened

At 18:22:18 on 2026-08-25 the Harvard→Needham drive latched `arrived` 35 m from
the pin, correctly, and ended. **Eighty-six seconds later a fourth drive began**
and recorded a parked car for the next nine minutes and counting
(`traces/drive-2026-08-25-222344.ndjson`, still growing when last checked, with
no `end` record).

It was not a new trip. Its header carries `from: [42.47824, -71.62095]` —
Harvard, the *previous* drive's origin — while its very first GPS fix is in
Needham, **38 km away**. Its opening `route` record is the previous drive's route
verbatim: same 49.2 km, same `mean_score` 5.73. The car was projected onto the
far end of that line (`travelled: 49219`, `remaining: 0`) and every one of its
523 fixes reads `joined: false`.

## The mechanism

`RouteModel.endNavigation()` (`ios/Sources/RouteModel.swift`, ~line 217) tears
down the drive and nothing else:

```swift
func endNavigation() {
    locationManager.onFix = nil
    nav?.finish()
    nav = nil
}
```

`start`, `end` and `response` are all left exactly as they were. So the planning
screen comes back still holding the origin you departed from an hour ago and the
`RouteResponse` computed for it. `RoutePanel.startButton(for:)`
(`RoutePanel.swift:251`) renders whenever a `response` exists, and
`RouteModel.startNavigation(_:)` (~line 202) starts a drive from `self.start`
without consulting the current fix. Tapping it replays the old drive verbatim —
which is exactly the record above.

**`clear()` already exists** (`RouteModel.swift:181`) and already does the right
thing. It is simply never called when a drive ends.

## Why it matters

Three costs, in increasing order of seriousness:

1. It records junk that looks like a real drive and has to be spotted by hand.
2. It holds GPS at 1 Hz with the screen awake, indefinitely, in the background.
3. **It cannot stop.** All three arrival tests are gated on `hasJoinedRoute`
   (`NavigationModel.swift:571-582`), and a parked car 126 m off a line it never
   joined can never flip that flag. That is a separate defect, tracked
   separately, but this bug is the cheapest way to reach it.

## The fix

On `endNavigation()`, discard the computed routes and reset the origin. Nilling
`response` alone makes the phantom structurally impossible, because the start
button only renders when a response exists.

Keeping `end` is reasonable — wanting to route back to where you just were is a
real thing. **The stale item is `response`**, which is a route computed from an
origin the driver has since left.

## Traps

- **Do not add a "refuse to start if the origin is far from the current fix"
  guard.** Planning a trip from somewhere you are not is a *supported* workflow —
  it is the entire reason `hasJoinedRoute` exists, per the reasoning in commit
  `05f3f00` about "a trip planned from the sofa". A distance check would break
  it. Fix the staleness, not the distance.
- **Do not touch `ios/Sources/NavigationModel.swift`.** Another session is
  editing it right now, around `advanceSteps` and `adopt`. This fix does not
  need that file and must not enter it — including the arrival gate at
  `571-582`, which is a known defect being handled separately and after.
- **Do not call `clear()` blindly** without deciding about `end` and `endQuery`.
  Wiping the destination too is a product choice, not a bug fix; if you make it,
  say so explicitly rather than letting it ride in as a side effect.
- `arrived` never un-latches by design, so there is no path where a *resumed*
  drive needs the old response back.

## Out of scope

The other three defects from the same drives, all of which live in
`NavigationModel.swift`: the reroute step offset (dispatched, in progress), the
arrival gate that cannot fire for an unjoined car, and the reroute storms. Do
not start any of them here.

## What was done

`endNavigation()` now drops the origin and the routes along with the session:

```swift
start = nil
startQuery = ""
response = nil
```

**`end` and `endQuery` were deliberately kept**, which is why this is not a call
to `clear()` — `clear()` would take the destination with it. A destination is a
place, not a route computed from an origin the driver has left, and heading back
from where you just arrived is a real trip; leaving the pin set makes that one
"My Location" tap rather than a re-search. It is safe to keep because `end` is
not what arms the start button — `response` is. `errorText` was left alone as
well: it is not stale in the way a route is, and wiping it was not part of the
defect.

`startQuery` goes with `start` because the text is the worse half of the two. A
field still reading "My Location · Ayer Road, Harvard" under an empty coordinate
is how a driver would fail to notice the trip is being planned from an hour ago.

`RouteModelTests.swift` covers four things, built on the 2026-08-25 values —
Harvard as the origin, Needham 38 km away, the 49.2 km route between them:

| test | catches |
| --- | --- |
| `..._leaves_no_route_armed` | the defect: `response` survived, so the button did |
| `..._forgets_the_origin_it_departed_from` | the defect: `start`/`startQuery` survived |
| `..._keeps_the_destination` | the decision above, not the defect — it passed before the fix too |
| `..._cannot_be_recomputed_without_a_fresh_origin` | the second lock: nothing can re-arm the screen until an origin is set |

All four were run against the unfixed `endNavigation()` first. Three fail there;
the fourth — the destination one — passes, and says so in its own comment so it
is not mistaken for coverage. The recompute test failed pre-fix with `HTTP 530`,
which is worth noting: it proves the request was genuinely attempted from the
stale origin rather than merely permitted, and it is why that test asserts on
`errorText` as well as on `response` (with the backend down, `response` comes
back nil either way).

Suites at the time of the fix: iOS 110 tests, 6 skipped, 0 failures; backend 133
passed, 107 skipped (the graph-dependent ones — `data/` is not in a worktree).

## Done looks like

1. A drive that ends cannot be restarted from the stale plan — asserted in a
   test, since there is no `RouteModelTests.swift` yet and this is a good reason
   for one. `RouteFixtures.swift` holds the shared fixtures.
2. `endNavigation()` leaves `response` nil, with a comment saying why, naming the
   38 km case.
3. An explicit statement of what you decided about `end`/`endQuery` and why.
4. Both suites green: `.venv/bin/python -m pytest tests/` and the iOS suite per
   the README's Tests section.
