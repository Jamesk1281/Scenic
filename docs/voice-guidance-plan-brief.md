# Voice guidance: plan it, and settle the one thing that cannot be guessed

**Status: scoped and measured, nothing built.** No Swift file, no `project.yml`
entry, no test has been touched. The deliverable of this task is a **design
document plus one measured answer**, not shipped guidance — see *Done looks
like*.

## The goal

The app has turn-by-turn navigation and no spoken instructions. `grep -rn
"AVSpeech\|AVAudio" ios/Sources/` returns nothing. The driver reads every
maneuver off the banner in `ios/Sources/NavView.swift:149`.

That is a bigger problem here than it would be in a normal nav app, because a
scenic route is deliberately turn-dense. Measured across six scenic routes on
the shipping graph (70–164 km, 24–110 steps, `pref=0.60`), **455 maneuver legs**:

| | m |
|---|---|
| min | 10 |
| p10 | 85 |
| p25 | 195 |
| **median** | **746** |
| p75 | 2,007 |
| p90 | 3,847 |

| legs shorter than | count | share |
|---|---|---|
| 100 m (0.06 mi) | 55 | **12.1%** |
| 200 m | 114 | 25.1% |
| 400 m (0.25 mi) | 172 | 37.8% |
| 800 m (0.5 mi) | 231 | **50.8%** |
| 1,609 m (1 mi) | 310 | **68.1%** |
| 3,218 m (2 mi) | 392 | **86.2%** |

At 30 mph, **25.3% of legs give under fifteen seconds of warning**; at 45 mph,
32.7% do. The median leg is 56 s at 30 mph and 37 s at 45.

## What that distribution rules out, and it is the usual design

The standard nav schedule — "in 2 miles", "in 1 mile", "in half a mile", "in
500 feet", "now" — **cannot work on this route profile**. 86% of legs are
shorter than two miles, 68% shorter than one, 51% shorter than half. On a
typical leg every one of those thresholds is already behind the driver at the
moment the previous maneuver completes, so a naive implementation fires three or
four prompts at once, or talks continuously, or queues stale ones that arrive
after the turn.

**So the schedule must be time-based, not distance-based.** Announce on
projected seconds-to-maneuver at the current speed, not on metres. That is the
central design decision and it is decided; the plan's job is to choose the
thresholds and the phrasing, not to revisit this.

Three consequences that follow from the same table and should be treated as
decided:

1. **At most two utterances per leg** — one "prepare", one "now" — and the
   prepare is skipped entirely when the leg is too short to fit it.
2. **Chain consecutive short legs into one utterance.** With 12% of legs under
   100 m, "Turn right onto Morton Street, then left onto Webster Street" is not
   a nicety, it is the only way to say both in time. This is what every mature
   nav app does and it is what this distribution demands.
3. **One utterance in flight, never a queue.** `AVSpeechSynthesizer` enqueues by
   default. A stale "in a quarter mile" delivered after the turn is worse than
   silence; a new announcement must pre-empt, not stack.

## What the codebase already gives you

The phrasing inputs exist and were put there for this. `ios/Sources/Models.swift`
`RouteStep` carries `type` (a `ManeuverType`), `modifier`, `exit_ref`,
`destination`, `roundabout_exit` and `name` alongside `instruction`, and
`Models.swift:79-83` says why:

> Present so the app can style a motorway exit differently from a left turn, and
> so voice guidance can assemble its own phrasing from the parts rather than
> reading a sentence built for the screen.

The trigger point is `NavigationModel.advanceSteps`
(`ios/Sources/NavigationModel.swift:901`), which already computes
`distanceToNext` on every fix and owns `currentStep`. Speed is on the
`CLLocation` handed to `update`. Everything needed is in hand; nothing new has
to come from the server.

**A phrasing decision, made here so the plan does not relitigate it:** start by
speaking a *normalised* `instruction`, not by assembling from parts. It is
already good English for the common cases ("Turn right onto Morton Street"), it
cannot read worse than what is on screen, and the parts stay available for the
cases where it demonstrably fails — the colon in "Take exit 26 toward I 93
North: Boston", and road refs like "MA 1A" that a synthesiser will mangle. The
plan should list the specific known-bad shapes and how each is rewritten. If the
plan concludes full assembly is warranted, it must say what the normalised
version gets wrong that assembly fixes.

## The one thing that must be measured, not designed

`ios/project.yml:30-31` declares exactly one background mode:

```yaml
        UIBackgroundModes:
          - location
```

**Does `AVSpeechSynthesizer` actually speak while the app is backgrounded or the
phone is locked, with `location` declared and `audio` not?** Every drive on this
app runs with the screen locked or the phone in a pocket — that is the whole
reason `UIBackgroundModes: location` and `allowsBackgroundLocationUpdates` are
there (`ios/Sources/LocationManager.swift:133-144`). Voice that only works while
someone is looking at the screen is voice that does nothing.

This is not answerable from documentation with confidence, and the answer decides
the shape of everything else. Build a throwaway spike, run it on the **simulator
and on a real device**, and record what actually happens in four states: app
foregrounded, app backgrounded, phone locked, and music playing from another
app. If `audio` has to be added to `UIBackgroundModes`, say so and note that it
is a reviewable claim on the App Store — navigation is an accepted use, but it
is a claim the app then has to be able to justify.

## Traps

**1. Reroutes will re-announce everything if announcements key on
`currentStep`.** `NavigationModel.adopt` sets `currentStep = 0`
(`NavigationModel.swift:1253`) and `merge` re-derives it from zero
(`:1218`). Across five recorded drives there were **51 reroutes**, 8 of which
returned a byte-identical line. An announcement latch keyed on the step index
alone will re-speak the opening maneuver on every one of them — the audible
version of the banner-reset bug that
`docs/reroute-audit.md` and the `awaitingJoin` machinery exist to stop. Key the
latch on something that survives a route swap, and state in the plan what
happens to a pending announcement when the route changes underneath it.

**2. The audio session category decides whether this is usable or hostile.**
Getting it wrong either kills the driver's music for the whole drive or makes
the app silent behind it. `.playback` with `.duckOthers` and
`.interruptSpokenAudioAndMixWithOthers` is the usual navigation shape, and the
session must be *activated around each utterance and deactivated after*, not
held for the whole drive — a nav app that holds an active playback session
suppresses other audio for an hour. `.playback` also means the app speaks
through the silent switch, which is correct for navigation and must be a
deliberate, stated choice rather than a side effect. Also decide what happens on
a phone call: `AVAudioSession` interruption notifications, and whether a
maneuver missed during a call is re-announced or dropped.

**3. Do not add a second source of truth for "where is the driver".** Everything
about progress lives in `NavigationModel` and is fed by one `onFix` closure
straight from CoreLocation, deliberately with no view in the path
(`LocationManager.swift:34`, `RouteModel.startNavigation`). Announcements must
hang off that same stream. A `Timer`, a `TimelineView`, or anything driven by
SwiftUI body evaluation stops firing the moment the phone locks — which is
precisely when voice matters most, and is the same mistake documented at
`LocationManager.swift:24-32`.

**4. Speed can be −1.** `CLLocation.speed` is negative when CoreLocation
declines to say, and `NavigationModel` already handles this in two places
(`trackStopping`, `usableHeading`, `NavigationModel.swift:145` and `:459`). A
time-based schedule divides by speed. Decide the fallback — a nominal speed, the
road class, or falling back to a distance threshold for that fix — and say which.

**5. Muting is not optional, and neither is remembering it.** A driver who wants
silence needs one obvious control on the nav screen, and it must persist across
the drive and across a relaunch (iOS can jettison and relaunch the app mid-drive
— `RouteService.swift:17-22` documents exactly that happening). Decide where the
control lives; note that `NavView`'s control row and the two verdict buttons are
already placement-constrained for a reason documented at `NavView.swift:248-264`.

## Out of scope, deliberately

Lane guidance (`turn:lanes` is unread by the pipeline), CarPlay, speed-limit
announcements, and anything that speaks about *scenery* rather than maneuvers.
Say in the plan if any of them turn out to be near-free consequences of the
design, but do not design for them.

## Done looks like

1. `docs/voice-guidance-plan.md` — the design: the announcement schedule with
   its thresholds and the reasoning tied to the leg distribution above; the
   phrasing rules with the known-bad `instruction` shapes enumerated; the audio
   session configuration; the mute control and where it lives; the reroute
   behaviour; and the speed-unavailable fallback.
2. **The background-audio question answered by measurement**, with the four
   states above recorded, and an explicit statement of whether
   `UIBackgroundModes: audio` is required.
3. A staged implementation plan with an estimate per stage, and an explicit
   statement of which stage is the minimum that makes the app driveable without
   looking at the screen.
4. The spike code either deleted or left on a clearly-marked throwaway branch —
   **not merged**, and no production Swift file changed by this task.
5. This brief committed alongside the plan.
6. For anything the spike cannot settle on this machine (a real device may not
   be reachable — `xcrun devicectl list devices` currently shows the phone
   `unavailable`), a plain statement of what was not measured and what would
   settle it. A measured "could not test on device" is a fine outcome; a guess
   presented as a finding is not.

## Running it

Work off `main`; do not commit to `main`. `data/`, `.venv` and the graph live in
the **main checkout**, never in a worktree. To see real routes:

```sh
SCENIC_DATA="$PWD/data/processed" .venv/bin/python server/app.py
```

Check `lsof -ti :5057` first — a stale server from another session serves stale
code and looks exactly like your change not working. Point the app at it with
the `SIMCTL_CHILD_` prefix (`--setenv` fails with "Invalid device"):

```sh
SIMCTL_CHILD_SCENIC_API=http://127.0.0.1:5057 xcrun simctl launch booted app.scenic.demo
```

`ios/Scenic.xcodeproj` is gitignored build output — run `xcodegen generate`
after any pull and after adding a file. `xcodebuild test` needs
`-destination 'id=<UDID>'`; `name=` is rejected on this machine.
`ios/Tests/LiveDriveTests.swift:208` already fails on `main` whenever a backend
is up; it is pre-existing and out of scope.

Two other sessions are live in `ios/Sources/` — `RouteResults.swift`,
`Models.swift`, `RoutePanel.swift` and `LoopPanel.swift` are being edited
elsewhere. This task should not need to modify any Swift file at all; if the
spike touches one, keep it on the throwaway branch.
