# Voice guidance: the design, and the one thing that had to be measured

Companion to `docs/voice-guidance-plan-brief.md`, which scoped this and settled
the central decision (the schedule is time-based, not distance-based). This
document chooses the thresholds, the phrasing, the audio session, the mute
control and the reroute behaviour, and answers the background-audio question by
measurement.

**Nothing here is built.** No Swift file under `ios/Sources/` was touched, and
`ios/project.yml` is unchanged. The spike that produced the measurements is on
the throwaway branch `throwaway/voice-audio-spike`, unmerged.

---

## 1. The measured answer: `UIBackgroundModes: audio` is required

**Measured 2026-08-30 on the real phone** — iPhone 17 (`iPhone18,3`), iOS 26,
`xcrun devicectl` identifier `CDB28C5A…`. Not a simulator result; see §1.3 for
what the simulator claimed and why it is worthless here.

### 1.1 Method

Two app targets over **identical sources**, differing in exactly one Info.plist
key — one declares `UIBackgroundModes: [location]`, the other
`[location, audio]`. Both reproduce `LocationManager`'s configuration exactly:
when-in-use authorization, `allowsBackgroundLocationUpdates = true`,
`showsBackgroundLocationIndicator = true`,
`kCLLocationAccuracyBestForNavigation`, `distanceFilter = kCLDistanceFilterNone`,
`activityType = .automotiveNavigation`,
`pausesLocationUpdatesAutomatically = false`.

Each app speaks from the CoreLocation delegate callback — the same `onFix` path
a drive uses, not a timer — attempting one utterance every 12 s, and writes a
JSONL line per event to `Documents/`, with file protection forced to `.none` so
a write during a locked-device wakeup cannot fail for an unrelated reason. Each
attempt records `setActive(true)`'s outcome, whether
`AVSpeechSynthesizerDelegate.didStart` fired, and how long the utterance took.
A `noStartWithin2s` line is written when nothing starts, so a silent failure
leaves evidence rather than an absent row.

Driven by XCUITest because `devicectl` refuses to launch anything on a locked
phone (*"the device was not, or could not be, unlocked"*) and this phone
auto-locks in about a minute: the test answers the location prompt, waits in the
foreground, presses Home, then leaves the device completely alone so it locks by
itself.

### 1.2 Result

| state | `[location]` | `[location, audio]` |
|---|---|---|
| foreground | **4/4 spoke** | **3/3 spoke** |
| backgrounded, screen on | **0/8** — `setActive` threw `'!pla'` | **4/4 spoke** |
| backgrounded, screen off (locked) | **0/11** — `setActive` threw `'!pla'` | **16/16 spoke** |

`'!pla'` is `AVAudioSessionErrorCodeCannotStartPlaying`, description *"Session
activation failed"*. On every one of those 19 attempts `didStart` never fired:
nothing was spoken, not merely spoken quietly.

Three further things the same logs settle:

- **It is backgrounding, not locking, that kills it.** The device unlocked
  again at t+277 s while the app was still backgrounded, and the failures
  continued unchanged — five more `'!pla'` after the screen came back on. Screen
  state is irrelevant; foreground/background is the whole discriminator.
- **The app was awake the entire time.** Location fixes kept arriving and every
  12 s tick ran and logged. The `location` background mode did its job and kept
  the process executing; what was refused was purely the audio session. So this
  is not "the app was suspended" — it was running, and trying, and denied.
- **An utterance already in flight is cut off by backgrounding.** Under
  `[location]`, utterance 5 logged `didStart` 0.6 s before `willResignActive`
  and never logged `didFinish`. Under `[location, audio]` all 23 utterances
  completed.

**So: `audio` has to be added to `UIBackgroundModes`.** Without it the feature
does nothing on any drive taken the way this app is actually used — screen off,
phone in a pocket — which is every drive.

That is a reviewable claim on the App Store. Turn-by-turn navigation is an
accepted justification for the `audio` background mode, and this app has the
supporting evidence in the bundle already: it declares
`NSLocationWhenInUseUsageDescription` for following a route, it shows the
background location indicator while driving, and the audio it plays is spoken
maneuver instructions and nothing else. The one thing review will look for that
this app must not do is hold an active session when it is not speaking — see
§5.

### 1.3 What the simulator claimed, and why it was ignored

On a simulator (iPhone 16 Pro, iOS 26.4) the location-only build spoke happily
while backgrounded — `setActive: ok`, `didStart`, `didFinish`, 18 for 18. **The
simulator does not enforce the background-audio entitlement.** Had this been
tested only there, the answer would have been the opposite of the truth and the
feature would have shipped mute.

Worth recording as a general fact about this project: the simulator cannot
answer any question about background execution policy.

### 1.4 What was *not* measured

- **Music from another app, on the device.** A free developer profile allows
  three apps on a phone and the real Scenic app holds one, so the tone-player
  app built for this could not be installed alongside a spike variant and the
  test runner. On the simulator the tone app ran but `isOtherAudioPlaying` never
  went true, which is the same cross-process arbitration the simulator does not
  implement — so that result is worthless too. **Ducking behaviour is therefore
  designed in §5 from the documented semantics and is unverified.** It is
  settled by one drive with music playing, or by freeing an app slot and
  installing the tone app that already exists on the spike branch.
- **That sound audibly came out of the speaker.** What is measured is that the
  session activated and the synthesiser rendered for the full natural duration
  of the sentence (median 4.75 s for the same 12-word sentence, every time).
  There is no loopback audio device on this Mac and no way to record the phone's
  output from here. The inference from "session active + renderer ran to
  completion" to "audible" is not proven, only very strongly implied — and the
  A/B is unaffected either way, since the same instrument reports total failure
  in one configuration and total success in the other.

---

## 2. What else the spike measured

Two numbers that the schedule below depends on, and which were not otherwise
known.

**Utterances are long.** Measured by offline synthesis (`AVSpeechSynthesizer.write`,
en-US, `AVSpeechUtteranceDefaultSpeechRate`) over the **176 unique instructions**
in 194 real steps from four routes on the shipping New England graph. Calibrated
against the device: the anchor sentence measures 4.617 s offline and the phone
spoke it in a median 4.75 s — within 2%, so the offline numbers transfer.

| form | median | p75 | p90 | max |
|---|---|---|---|---|
| bare — *"Turn right onto Morton Street"* | **1.83 s** | 2.06 | 2.38 | 4.64 |
| with distance — *"In a quarter mile, turn right onto…"* | **3.18 s** | 3.39 | 3.73 | 6.00 |
| chained — *"X, then Y"* | **4.08 s** | 4.48 | 5.39 | 7.29 |

The distance prefix costs about 1.35 s. On a leg that gives fifteen seconds of
warning that is a tenth of the budget, and it is why the prepare has to be
skippable rather than merely early.

**Handing the audio session back costs about half a second.**
`setActive(false, options: .notifyOthersOnDeactivation)` measured **573 ms**
(n=27, min 570, max 577) between the utterance finishing and the call returning,
on device. On the simulator the same call is instantaneous, so it is real
hardware cost and not the instrument. Called on the main actor from the
`didFinish` delegate — which is where it naturally goes — that is 573 ms in
which the main thread cannot process a location fix, on a stream that delivers
about one a second. **Deactivate off the main actor** (§5).

---

## 3. The announcement schedule

### 3.1 Corroboration of the leg distribution

Not a re-derivation — the brief's 455-leg measurement stands. But four different
routes had to be fetched anyway to get an instruction corpus, and their leg
distribution is worth one line because it is *tighter* than the brief's, not
looser: 194 legs over 222 km, median **507 m** (brief: 746 m), **14.4%** under
100 m (brief: 12.1%), **62.9%** under 800 m (brief: 50.8%). These routes are
shorter and more urban. Whatever schedule survives the brief's distribution has
to survive this one too.

### 3.2 The thresholds

Everything is computed from **projected seconds to the maneuver**,
`distanceToNext / pace`, recomputed on every fix. `distanceToNext` is already
maintained by `advanceSteps` (`NavigationModel.swift:933`); `pace` is defined in
§7.

| name | value | what it does |
|---|---|---|
| `finalAt` | **6 s** | speak the maneuver itself |
| `finalFloorMeters` | **40 m** | speak it anyway, whatever the speed says |
| `prepareAt` | **25 s** | speak *"In a quarter mile, …"* |
| `prepareFloor` | **14 s** | below this when the maneuver becomes current, no prepare at all |
| `chainWithin` | **12 s** | the next maneuver is named inside this one's utterance |

**Why 6 s for the final.** The bare instruction takes a median 1.83 s and a p90
of 2.38 s. Fixes land at about 1 Hz, so a threshold crossing is noticed up to a
second late. Firing at 6 s therefore starts the words 5–6 s out and finishes
them 2.6–3.6 s before the maneuver even for a p90-length instruction. Less than
that and the longest instructions are still being spoken as the driver arrives
at the turn; more and the "now" stops meaning now — at 45 mph, 8 s is 161 m.

**Why the 40 m floor.** A time-based rule divides by speed, and a car stopped
30 m short of its turn at a light has an infinite projection and would never be
told. Forty metres is the same `arrivalMeters` the model already uses for "you
are at the point", so the two cannot drift apart.

**Why 25 s for the prepare.** Long enough to change lane and start slowing;
short enough that on this route profile it is still on the same leg. At 30 mph
25 s is 335 m — inside the median leg of either sample.

**Why 14 s as the floor.** 6 s (the final) + 3.7 s (a p90 prepare) + 4 s of
silence between them, rounded up. If the maneuver becomes current with less than
that projected, there is no room for two utterances and the prepare is dropped
rather than crammed. This is the rule that implements *"the prepare is skipped
entirely when the leg is too short to fit it"*.

**Why 12 s for chaining.** The next maneuver needs 6 s (its own final) plus
2.4 s (a p90 instruction) plus a fix of latency plus margin. Below 12 s it
cannot be announced on its own account in time, so it must be named inside the
current utterance: *"Turn right onto Morton Street, then left onto Webster
Street."*

### 3.3 What those thresholds do to the real routes

Simulated over all 190 consecutive maneuver pairs in the corpus:

| | 25 mph | 30 mph | 45 mph |
|---|---|---|---|
| prepare + final | 80.0% | 74.2% | 62.6% |
| final only | 2.1% | 5.8% | 6.3% |
| chained into the previous utterance | **17.9%** | **20.0%** | **31.1%** |

So one maneuver in five at town speed, and nearly one in three at 45, is spoken
as the second half of the previous sentence. That is not a refinement, it is the
main path, and it confirms the brief's second consequence with a number.

**And it is what makes the feature work at all.** A final-only schedule — one
utterance per maneuver, no chaining — leaves **13.7% of maneuvers at 30 mph and
20.0% at 45 mph** with no time to be spoken before the driver reaches them. One
maneuver in seven, silently missed, on a route profile chosen for being
turn-dense. Chaining is not stage two of a nicety; it is the difference between
guidance and a lie.

### 3.4 Rules that fall out

- **At most two utterances per maneuver**, never a third.
- **Chains are at most two maneuvers.** *"X, then Y"* already measures 4.08 s
  median and 7.29 s worst case; a third clause would push the median past six
  seconds and be unusable. When three short legs run together, the chain
  advances two at a time: after *"A, then B"*, B is marked spoken and the next
  decision is taken from B, so C is chained onto B's own final if it is close,
  or announced normally if it is not.
- **A maneuver named inside a chain is not announced again.** It got its
  mention; repeating it in the four seconds before the turn is worse than
  silence.
- **One utterance in flight, never a queue.** `AVSpeechSynthesizer` enqueues by
  default. Before every `speak`, if `isSpeaking`, call
  `stopSpeaking(at: .word)` — which also discards anything queued behind it. A
  stale *"in a quarter mile"* arriving after the turn is the failure this
  prevents. `.word` rather than `.immediate` because the pre-emption is normally
  a final overtaking a prepare, and a clipped word is more alarming than a
  quarter-second wait. `.immediate` is used only on a route change (§6).
- **`Continue` is 25% of all steps** (49 of 194 in the corpus) and every one of
  them is *"Continue onto <road>"* — `pipeline/router.py` already folds the bare
  ones away. It is still not an action. **Give a `continue` step a final but
  never a prepare**: hearing *"In a quarter mile, continue onto Wolcott Street"*
  spends 3 s and a whole announcement slot to tell the driver to keep doing what
  they are doing, and on this route profile that slot is frequently the one the
  next real turn needed. This also removes about a quarter of all prepares,
  which is the cheapest possible reduction in chatter.
- **Arrival speaks.** `update` returns early once `arrived` latches
  (`NavigationModel.swift:812-819`), so the arrival announcement has to be made
  inside that branch, before the return, or it never happens.

---

## 4. Phrasing

Per the brief, start from a **normalised `instruction`** rather than assembling
from `type`/`modifier`/`name`. The corpus supports that: 176 of 194 steps need
no work at all, and the ones that do are enumerable rather than open-ended.

### 4.1 What the corpus actually contains

All 194 steps from four routes, Boston → Sherborn, Boston → Falmouth,
Somerville → Lowell, Sherborn → Gloucester, `pref=0.60`.

| shape | count | share | example |
|---|---|---|---|
| colon | 5 | 2.6% | `Take the exit toward I 93 South: Quincy` |
| slash | 3 | 1.5% | `Take the exit toward Soldiers Field Road West / Newton` |
| road ref (`MA 60`, `I 93`, `US 1`, `I-93`) | 6 | 3.1% | `Keep right onto MA 60` |
| numeric ordinal (`1st`, `2nd`) | 10 | 5.2% | `Take the 2nd exit onto High Street` |
| bare exit number | 2 | 1.0% | `Take exit 14 toward …` |
| all-caps token | 1 | 0.5% | `Turn left onto YMCA Drive` |
| **abbreviations** (`Rd`, `St`, `Ave`, `Blvd`…) | **0** | **0.0%** | — |
| parentheses, ampersands | 0 | 0.0% | — |

Only **18 unique instructions in 194 steps** contain a digit, a colon or a
slash, and they are listed in full in §4.3. That is the entire problem.

The zero row matters as much as the others: **do not build an abbreviation
expander.** OSM names in this region come through spelled out, so a `Rd → Road`
table would be pure untested code. If New England expansion ever changes that,
the way to find out is to re-run the census in §9, not to write the expander
speculatively.

### 4.2 The rewrite rules — measured, and three of four dropped

**Corrected 2026-08-30, after implementing.** The frequencies in §4.1 were
measured from the start; how the synthesiser *pronounces* those strings was
originally listed as an unverified prediction. It is now measured, by comparing
the offline duration of each ambiguous string against unambiguous renderings of
each way it could be read — a syllable is 0.15–0.2 s, so the readings separate.
**Three of the four rules were unnecessary**, and are recorded here so nobody
adds them back:

| string | reads as | verdict |
|---|---|---|
| `Take the 2nd exit` | 1.277 s — *identical* to `Take the second exit` | already expanded; **no rule** |
| `Take exit 14 …` | 2.299 s — *identical* to `… exit fourteen …` | already correct; **no rule** |
| `YMCA Drive` | 1.997 s vs 2.055 s spelled out, 1.625 s as a word | already spelled out; **no rule** |
| `I 93 South: Quincy` | 3.077 s — *identical* to the same line with a comma | colon already reads as a comma; **no rule** |
| `Soldiers Field Road West / Newton` | 2.171 s — **shorter** than either replacement | the slash is *dropped*, running two names together; **rule** |
| `MA 60` | 1.693 s vs 1.728 s as `M A 60`, 1.623 s as one syllable | read as letters or as "ma", never as a road; **rule** |
| `US 1` | 2.090 s vs 2.194 s as `U S 1`, 2.020 s as `Route 1` | read as the pronoun "us"; **rule** |
| `I 93` | 2.299 s — *identical* to `eye 93` | correct as-is; **rule kept as polish only** |

So `String.spokenAloud` does exactly three things:

1. `" / "` → `" and "`. The slash is silently dropped, so
   *"toward Soldiers Field Road West Newton"* is one name where there are two.
2. `MA|RI|NH|VT|ME|CT|US` + number → `Route <n>`. Every prefix listed
   exhaustively rather than matching any two capitals, so `YMCA Drive` cannot
   become `Route CA` — there is a test for exactly that.
3. `I <n>` → `Interstate <n>`. Polish, not repair: "eye ninety-three" is what
   people say. It costs 0.38 s and cannot be misheard as the pronoun.

**Caveat on the method.** Duration comparison infers pronunciation rather than
hearing it. It is conclusive where the match is exact — the ordinals, the exit
number and the colon are identical to the millisecond, which no coincidence
explains — and merely strong for `MA 60` and `US 1`, where the gaps are 35 ms
and 104 ms. Those two are the ones to re-check by ear if anything sounds wrong;
they are also the two whose rewrite is harmless if the premise is false, since
"Route 60" is what the sign says either way.

### 4.3 What the corpus contains, in full

Every instruction in the corpus with a digit, colon or slash, and what is now
spoken:

| as returned | as spoken |
|---|---|
| `Take the exit toward I 93 South: Quincy` | Take the exit toward Interstate 93 South: Quincy |
| `Take the exit toward I 93 South / US 1 South: Braintree` | Take the exit toward Interstate 93 South and Route 1 South: Braintree |
| `Take exit 14 toward I-93 South / US 1 South: Columbia Road` | Take exit 14 toward Interstate 93 South and Route 1 South: Columbia Road |
| `Take the exit toward US 1 North: Lynnfield` | Take the exit toward Route 1 North: Lynnfield |
| `Take the exit toward MA 114 East: Peabody` | Take the exit toward Route 114 East: Peabody |
| `Take the exit toward Soldiers Field Road West / Newton` | Take the exit toward Soldiers Field Road West and Newton |
| `Keep right onto MA 60` | Keep right onto Route 60 |
| the eight `Take the {1st,2nd} exit …` lines | unchanged |
| `Take exit 9 toward West Quincy` | unchanged |
| `Turn left onto YMCA Drive` | unchanged |

### 4.4 When to prefer the parts over the sentence

The structured fields exist (`Models.swift:71-138`) and stay available. Nothing
in the corpus demands them today. The case that would is a maneuver whose
`instruction` is *correct on screen but wrong in the ear* — the leading
candidate being the roundabout wording, where *"Take the 2nd exit at Weston
Street onto Weston Street"* is a real corpus line that reads as a stutter aloud
and would be better assembled as *"At the roundabout, take the second exit"*
using `roundabout_exit` and dropping the duplicated name. That is a fourth
rewrite rule if listening confirms it, not a reason to abandon the normalised
sentence.

---

## 5. The audio session

```swift
try session.setCategory(.playback, mode: .voicePrompt,
                        options: [.duckOthers,
                                  .interruptSpokenAudioAndMixWithOthers])
try session.setActive(true)
// speak
// on didFinish / didCancel, off the main actor:
try session.setActive(false, options: .notifyOthersOnDeactivation)
```

This is the exact configuration the spike ran in every state, so it is measured
working rather than chosen from documentation.

- **`.playback`** means the app speaks through the ring/silent switch. That is a
  deliberate choice, not a side effect: a driver whose phone is on silent still
  needs to be told about the turn, and the app's own mute control (§8) is the
  affordance for "I want silence", not the hardware switch. It is also what
  makes `audio` in `UIBackgroundModes` legible to review — a nav app that spoke
  under `.ambient` would be silenced by the very thing it is claiming an
  exemption for.
- **`.duckOthers`** dips music rather than stopping it.
  **`.interruptSpokenAudioAndMixWithOthers`** pauses a podcast or audiobook
  outright instead, because ducked speech under speech is unintelligible.
  *Unverified against a real second app — see §1.4.*
- **Activated around each utterance, never held.** An always-active playback
  session ducks the driver's music for the whole drive. The cost of doing it
  properly is the 573 ms measured in §2, paid once per utterance.
- **Deactivate off the main actor.** `didFinish` arrives on main and the naive
  call blocks it for 573 ms, which is most of a location fix. Hop to a serial
  background queue for the `setActive(false)` and nothing else; the synthesiser
  and all announcement state stay on the main actor with the rest of
  `NavigationModel`.
- **Activation can fail, and the failure must not be silent.** The `'!pla'`
  finding is the proof: a `try?` here would have turned the entire background
  question into "voice mysteriously does nothing". Any throw from `setCategory`
  or `setActive` must (a) leave the announcement **unlatched**, so the next fix
  retries, and (b) be reported. The natural surface is
  `NavigationModel.report(_:)` → `actionProblem`, which already prints under the
  controls without displacing the banner (`NavView.swift:201-207`) — with a
  cooldown, because a failure that repeats every fix must not become its own
  storm.

### Interruptions — phone calls

Observe `AVAudioSession.interruptionNotification`.

- **`.began`** — the system has already stopped the synthesiser. Call
  `stopSpeaking(at: .immediate)` to clear anything queued, and **un-latch the
  announcement that was in flight**, so the log of what has been said matches
  what the driver actually heard.
- **`.ended`** — do **not** resume the interrupted utterance. Take no action at
  all; the next location fix re-evaluates from scratch. A maneuver still ahead
  and still inside its threshold is re-announced with a currently correct
  distance; a maneuver now behind is dropped. That is the right rule in both
  directions, and it needs no code beyond the un-latch above, because the
  schedule is a function of the present fix rather than a queue of decisions
  already taken.
- While a call is up, `setActive(true)` will throw. Handled by the same rule as
  above: unlatched, reported once, retried on the next fix.

---

## 6. Reroutes, and what the latch keys on

The trap: `adopt` sets `currentStep = 0` (`NavigationModel.swift:1306`) and
`merge` re-derives it from zero (`:1271`). Across five recorded drives there
were 51 reroutes, 8 of them byte-identical to the line already being followed. A
latch keyed on the step index re-speaks the opening maneuver on every one.

**The latch keys on `(routeGeneration, maneuver coordinate, phase)`.**

- `phase` is `.prepare` or `.final`, so the two are latched independently.
- The **coordinate**, rounded to about a metre — not the step index, which
  `adopt` resets, and *not* the instruction text either. `merge` exists
  precisely because the words can improve on an unchanged line: the measured
  case on 2026-08-25 rewrote an opening maneuver from *"Turn right onto Lake
  Avenue"* to *"Head north on Lake Avenue"* at the same junction. Keyed on text,
  that rewording would re-announce; keyed on place, it does not. Place is also
  what the driver's memory is indexed by.
- `routeGeneration` **increments in `adopt` only, never in `merge`.** That is
  exactly the distinction `sameLine` (`NavigationModel.swift:1237`) already
  draws, and it is the right one:
  - **`merge`** — same line, same position, same ground already driven. The
    latch survives untouched, so all 8 byte-identical reroutes stay silent.
    This is the case the trap is about.
  - **`adopt`** — a genuinely different route. The generation bumps, which
    clears the latch by construction, and the new route's opening maneuver is
    announced once. That is correct: the driver has just been given a different
    way to go and needs to hear its first instruction.

**What happens to a pending announcement when the route changes underneath it:**

- **On `merge`:** nothing. The line and the driver's place on it are unchanged,
  so an utterance in flight is still true. Let it finish.
- **On `adopt`:** `stopSpeaking(at: .immediate)` — mid-word. A prepare for a
  maneuver that may no longer be on the route is not merely stale, it is wrong,
  and this is the one place where clipping a word is the lesser harm. Nothing is
  spoken again until a fix produces a fresh decision.

And nothing more is needed to hold the silence over the gap, because `adopt`
already sets `awaitingJoin = true`, and announcements are gated on the same
predicate `advanceSteps` uses (§7). The new line starts at a junction a median
99 m ahead; the voice waits for the driver to reach it exactly as the banner
does.

**Announcements are gated on the step list describing where the car is** —
`hasJoinedRoute && !awaitingJoin && !runningBackwards(here) && here.offRoute <=
offRouteMeters`. That is the identical predicate behind
`NavigationModel.currentRoad` returning something other than `.offRoute`
(`NavigationModel.swift:705-718`), and it should be *extracted and shared*, not
copied, so the readout and the voice can never disagree about whether the
instructions apply. A driver 500 m down the wrong road keeps projecting onto the
abandoned line and `advanceSteps` keeps walking the index off that projection —
on screen that is a plausible wrong street name, and spoken aloud it would be a
plausible wrong instruction.

---

## 7. Where it hangs, and what happens when speed is unavailable

### 7.1 One source of truth

`NavigationModel.update(_:)` (`:738`), fed by the single `onFix` closure from
CoreLocation (`LocationManager.swift:34`, `RouteModel.swift:234`). The
announcement decision is a call at the end of `update`, immediately after
`updateRemaining(here)` and under the **same gate** `advanceSteps` used on this
fix — so voice and banner cannot disagree about which maneuver is current.

No `Timer`, no `TimelineView`, no `onChange`, nothing driven by SwiftUI body
evaluation. `LocationManager.swift:24-32` documents this mistake already having
been made once, and the measurement in §1.2 is the reason it must not be made
again: the states where voice matters are exactly the states where a view-driven
clock has stopped.

Shape it like `DriveTrace`: a `VoiceGuide` **injected into `NavigationModel`,
nil by default**, holding the schedule and the latch and talking to a `Speaker`
protocol. Nil means silent, which is what unit tests and `xcodebuild test` want,
and a fake `Speaker` is what makes §10's replay test possible.

### 7.2 Speed

`CLLocation.speed` is −1 when CoreLocation declines to say, and a time-based
schedule divides by it. `pace` is:

1. **The smoothed recent speed.** An exponential moving average over fixes with
   `speed >= 0`, α = 0.3 — smoothed rather than instantaneous because a single
   noisy fix should not fire an announcement 200 m early.
2. **Falling back to the route's own planned mean** when no fix has yet reported
   a speed: `route.properties.km * 1000 / (route.properties.minutes * 60)`. Per
   route, already in hand, and better than a constant — a motorway route and a
   lane in Vermont get different fallbacks. Clamped to 4–35 m/s, because the
   backend's minutes are free-flow and measurably optimistic on exactly the
   small roads scenic routes favour.
3. **With the 40 m distance floor underneath everything** (§3.2), which is what
   actually protects the stopped and crawling cases. A car at 0.4 m/s has a
   perfectly valid speed and a projection of several minutes; that is correct
   and nothing should fire — until it is 40 m out, when it should.

Deliberately *not* `max(speed, someMinimum)`: flooring the speed makes a car
crawling in traffic appear to be arriving at its turn and fires the final
several hundred metres early, every fix, for the length of the queue.

Note that this reuses no threshold from `usableHeading`
(`NavigationModel.swift:458`). That guard exists because a *direction* derived
from a car inching forward swings through the compass; a *speed* of 0.4 m/s is
not junk, it is 0.4 m/s.

---

## 8. Muting

**A speaker glyph on the trailing edge of the banner** (`NavView.swift:114-160`).

The banner is the only element on the nav screen that is always present — the
verdict buttons vanish before joining and after arriving, the Fastest button
vanishes once taken, and the recenter button only appears when the user has
moved the map. A control that moves mid-drive is a control a driver has to look
for. The banner is also, semantically, the thing being silenced: the glyph sits
next to the words it governs.

It deliberately does not go in `controlRow` (`NavView.swift:358`). That row's
layout is already load-bearing — `tripStats` in the middle because that is where
a resting thumb lands and the numbers are untappable, `End` and `Fastest` in the
corners, and a `Color.clear` spacer holding the stats centred once `Fastest`
disappears (`NavView.swift:264-272`, `:388`). Adding a fourth control there
either unbalances the row or shifts everything when `Fastest` goes.

The reach is longer at the top of the screen. That is the correct trade for a
once-per-drive action, and it is the same reasoning that puts the destructive
`End` button in a corner rather than under the thumb.

**Persistence.** `UserDefaults`, read when `NavigationModel` is constructed —
not from a view. This is the app's first persisted preference; there is no
`@AppStorage` or `UserDefaults` anywhere in `ios/Sources/` today, which is worth
knowing before adding one. It must survive a relaunch, not just a drive: iOS can
jettison and relaunch the app mid-drive, which is documented as having actually
happened (`RouteService.swift:16-22`), and a driver who muted the voice and then
had it come back on at 60 km/h has been ambushed by their own phone.

**Global, not per-drive**, deliberately. Someone who wants silence today
probably wants it tomorrow, and the alternative — a mute that quietly resets —
is the worse surprise of the two.

Muting stops utterances but **does not stop the latch from advancing.** A
maneuver that came due while muted is marked spoken, so unmuting mid-leg does
not produce a burst of catch-up.

---

## 9. Staged plan

| stage | what | estimate | status |
|---|---|---|---|
| **0** | Add `audio` to `UIBackgroundModes` in `ios/project.yml`, `xcodegen generate`. | 10 min | **done** |
| **1** | `Speaker` (session + synthesiser + pre-emption + interruptions) and `VoiceGuide` (schedule, latch, `pace`), injected into `NavigationModel`; final utterance, arrival announcement. | 1 day | **done** |
| **2** | Prepare utterance, `prepareFloor`, chaining, the `continue`-gets-no-prepare rule. | ½ day | **done** |
| **3** | Mute glyph in the banner, `UserDefaults` persistence. | 2–3 h | **done** |
| **4** | Phrasing normaliser. Came in well under estimate — measuring the pronunciations first (§4.2) removed three of the four rules. | ½ day | **done** |
| **5** | `actionProblem` reporting for session failures. | 2 h | **done** |
| **6** | An ndjson replay harness over the recorded drives. | 1 day | **done** — and it did not prove what stage 6 was for; see §10 |

All of it landed in `ios/Sources/VoiceGuide.swift` plus wiring in
`NavigationModel`, `RouteModel` and `NavView`, with 28 tests in
`ios/Tests/VoiceGuideTests.swift` and the replay in `ios/Tests/DriveReplay.swift`.

**The minimum that makes the app driveable without looking at the screen is
0 + 1 + 2, not 0 + 1.** Stage 1 alone sounds sufficient and is not: §3.3 measures
that a final-only schedule cannot deliver **13.7% of maneuvers at 30 mph and
20.0% at 45 mph** before the driver reaches them. One turn in seven missed is
worse than no voice at all, because a driver who has stopped watching the screen
has no fallback. Stage 2 is what turns those into the second half of the
previous sentence.

Stage 3 is the minimum to *ship* rather than to drive: a voice with no off
switch is not a feature.

Stages 0–2 are also the only ones that touch `NavigationModel`, so they are the
ones to land while `ios/Sources/` is quiet.

---

## 10. Testing, and what the replay turned out not to prove

**Built, 2026-08-30.** 28 tests in `ios/Tests/VoiceGuideTests.swift` and the
replay harness in `ios/Tests/DriveReplay.swift` + `DriveReplayTests.swift`.

- **Unit, on the schedule.** `VoiceGuide` is a pure function of
  (`distanceToNext`, `pace`, step list, latch) and needs no phone.
- **Integration, through `NavigationModel`.** Fixes in, utterances out, over
  the fixture route — this is what covers the wiring, the gate, and arrival.
- **Replay, over the twelve recorded drives.** Real polylines of 1,300–6,700
  points, real GPS wander, 42 reroutes exercised. Skipped when
  `traces/*.ndjson` is absent, which it will be almost everywhere: the traces
  are gitignored because they record where someone drove, so they cannot be
  committed as a fixture. Costs ~70 s.
- **Not by listening on the simulator.** §1.3.

### The replay does not guard the latch, and that was worth finding out

The plan above asserted that a replay would fail against a step-index latch.
**It does not.** Two mutations were run and both passed:

1. Keying the latch on the step index rather than the maneuver's place.
2. Clearing the latch on a `merge` as well as an `adopt` — the naive
   implementation this whole design is arranged around.

Mutation 2 produces **byte-identical utterances on all twelve drives**. The
reason is `awaitingJoin`. A same-line reply only ever arrives *because* the
driver left the route; the voice is gated off until they rejoin; and by the
time it clears they have passed the maneuver that would have been repeated. On
real driving the off-route gate reaches the problem first, and the latch is
defence sitting behind it.

That does not make the latch wrong — the gate is not designed to carry this,
and a brief GPS excursion punches straight through it. It makes the *claim*
about the replay wrong. What has teeth against mutation 2 is
`test_the_same_line_handed_back_does_not_re_announce_anything` at the unit
level, and at the integration level a stray-and-return quick enough that the
driver is still approaching a maneuver they have already been told about — 300 m
sideways and back inside ten seconds, which is a GPS glitch rather than a
driver. Both fail under the mutation; the second only started to after being
rewritten for it, having originally strayed for long enough that
`awaitingJoin` swallowed the whole thing.

What the replay *is* worth keeping for is the pair of failures nothing else can
see: a storm, and a voice that quietly stops. It also caught one real defect in
its own instrument — keyed on the words alone it reported a repeat on the
2026-08-14 evening drive, whose route turns left onto Washington Street at two
junctions 11 km apart, and both deserve saying.

### What the twelve drives actually sound like

| drive | fixes | on route | utterances | maneuvers | replies |
|---|---|---|---|---|---|
| 2026-08-14 15:50 | 2,512 | 2,473 | 39 | 23 | 0 |
| 2026-08-14 19:25 | 2,829 | 2,415 | 42 | 11 | 12 |
| 2026-08-22 17:13 | 327 | 327 | 8 | 6 | 0 |
| 2026-08-22 17:19 | 1,615 | 1,614 | 23 | 17 | 0 |
| 2026-08-22 18:34 | 2,066 | 2,008 | 38 | 13 | 3 |
| 2026-08-22 20:27 | 1,980 | 1,779 | 21 | 9 | 4 |
| 2026-08-22 22:26 | 2,679 | **26** | 2 | 3 | 15 |
| 2026-08-25 18:08 | 7,956 | 7,933 | 22 | 9 | 1 |
| 2026-08-25 20:21 | 3,394 | 3,226 | 42 | 18 | 6 |
| 2026-08-25 21:18 | 3,851 | 3,833 | 69 | 45 | 1 |
| 2026-08-25 22:23 | 523 | **0** | 0 | 1 | 0 |

About one utterance a minute on a drive that is going well, which is the right
order. The two silent drives are correctly silent and are the reason
`describableFixes` is measured at all: one never joined its route, and the
other — the reroute-storm drive — spent 2,653 of its 2,679 fixes off the line
it had been given. Neither is the voice failing; both would be indistinguishable
from the voice failing without that column.

---

## 11. Out of scope, and what turned out to be near-free

Confirming the brief's list, with the two answers it asked for:

- **Lane guidance** — not near-free. `turn:lanes` is unread by the pipeline;
  it needs extractor, router, API and model changes before the phone sees it.
- **CarPlay** — not near-free (a new scene type and an entitlement Apple must
  grant). But one genuinely free consequence is worth stating: once the session
  is `.playback`, spoken instructions route to the car over Bluetooth or a cable
  with no further work. The driver gets voice in the car without the app
  supporting CarPlay at all, which is most of the value for a fraction of a
  percent of the cost.
- **Speed-limit announcements** — not near-free, but nearer than expected.
  `pipeline/graph.py:248` already parses `maxspeed` and falls back to a
  per-road-class table, so the number exists in the graph; what is missing is
  only carrying it onto `RouteStep`. Still out of scope, and still a poor idea
  to announce, but the data is not the obstacle.
- **Anything that speaks about scenery** — out, unchanged. Worth noting that the
  measurements above are an argument against it rather than a neutral silence:
  on a route where a fifth of maneuvers already have to share an utterance,
  there is no spare airtime.

---

## 12. Reproducing the spike

Branch `throwaway/voice-audio-spike`, directory `spike/voice-audio/`. **Not
merged, and not to be merged.**

```sh
cd spike/voice-audio && xcodegen generate
xcodebuild -project VoiceSpike.xcodeproj -scheme SpikeLoc \
  -destination "id=$DEVICE_UDID" -derivedDataPath build-dev \
  -allowProvisioningUpdates build
xcrun devicectl device install app --device "$DEVICE_UDID" \
  build-dev/Build/Products/Debug-iphoneos/SpikeLoc.app
TEST_RUNNER_SPIKE_BUNDLE=app.scenic.spike.loc \
TEST_RUNNER_SPIKE_BACKGROUND_SECONDS=300 \
xcodebuild test -project VoiceSpike.xcodeproj -scheme SpikeUITests \
  -destination "id=$DEVICE_UDID" -derivedDataPath build-dev -allowProvisioningUpdates
xcrun devicectl device copy from --device "$DEVICE_UDID" \
  --domain-type appDataContainer --domain-identifier app.scenic.spike.loc \
  --source Documents/spike-log.jsonl --destination ./loc.jsonl
```

Then repeat with `SpikeLocAudio` / `app.scenic.spike.locaudio` and diff.

Things that cost time and will cost it again:

- **A free developer profile allows three apps on the device**, and
  `app.scenic.demo` holds one. The runner plus one variant is the whole budget,
  which is why the `Tone` target could not be installed and §1.4 is open. The
  UI test target therefore declares no app dependency and drives whatever is
  installed by bundle id.
- **`devicectl` cannot launch anything on a locked phone**, and this one
  auto-locks in about a minute. Every launch and relaunch has to go through
  XCUITest, or through a window while someone is holding the phone.
- `xcodebuild test` passes environment through with a `TEST_RUNNER_` prefix.
- The `Failed to load provisioning paramter list … No provider was found.`
  banner on every `devicectl` invocation is noise; ignore it.
- Uninstall the spike apps afterwards. They occupy the profile slots the real
  app needs.
