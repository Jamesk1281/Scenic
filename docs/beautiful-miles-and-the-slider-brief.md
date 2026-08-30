# Show beautiful miles instead of a 0–10 score, and give the slider back its dead quarter

**Status: decided; the server half is already written and tested, the client
half is not.** Two independent changes, batched because both are small and both
land in the planning sheet.

**Start from `claude/holistic-code-review-6bce7c`, not from `main`.** That
branch is `main` plus one commit, `19dadba` "Report beautiful km on
point-to-point routes, not just loops", which adds the field this work displays.
It is tested — `SCENIC_DATA=<abs>/data/processed .venv/bin/python -m pytest
tests/ -q` is **317 passed** on it — and nothing else on it differs from `main`.
Branch off it and do not re-derive that commit.

Everything below the server half is untouched: no Swift file, no UI, no test in
`ios/` has been changed for either item.

---

# Item 1 — the cards and the sentence should count miles, not print a score

## The goal, as measured

`/api/route` now returns `beautiful_km` per arm: kilometres of the route on road
scoring `BEAUTIFUL_SCORE` (7.0) or better, the same statistic the loop tab has
led with since it shipped. The planning sheet still prints the 0–10
length-weighted mean. Measured on the live graph, the difference on screen:

| trip | today | with this change |
|---|---|---|
| Needham→Rockport | adds 49 min, raises scenery **1.4 → 5.7** | adds 49 min, turns **1 mi** of beautiful road into **12** |
| Worcester→Boston | adds 26 min, raises scenery **0.8 → 3.4** | adds 26 min, turns **0 mi** of beautiful road into **3** |

Both look mediocre on the mean. Only the second is a bad deal, and only the
second phrasing says so.

## Why it matters beyond legibility

`docs/route-distribution-study.md` measured 983 trips and found the mean cannot
see a case that the miles can. On **3.1% of trips (30/983)** the scenic arm comes
back slower *and* with fewer beautiful miles than the fastest arm — and
`mean_score` **rose on 27 of those 30** (median +0.58), because a shorter route
with a better per-kilometre average genuinely scores higher. So the app today
prints "Scenic adds 2 min and raises scenery 4.1 → 4.7" on trips where the
beautiful road went *down*. That is not the app lying; it is the metric being
blind. Six of the 30 were worse on the score too, and `_no_worse_than_fastest`
(`server/app.py`) now guards those — the other 24 are still shipped and still
described wrongly.

The same study reached this change from the other side: every warning rule worth
having is built on beautiful-km, and *"only a `mean_score`-based rule works with
the API as it stands"*.

## What to change

- **`ios/Sources/Models.swift:141`** — `RouteProps` gains `beautiful_km` and
  `beautiful_score`. **Both optional.** See trap 1.
- **`ios/Sources/RouteResults.swift:32`** — the card's detail line is
  `"\(p.km.wholeMilesFromKm) mi · \(p.mean_score …)/10"`. It should read the
  miles instead, e.g. `"54 mi · 3 mi beautiful"`.
- **`ios/Sources/RouteResults.swift:106`** — `RouteComparison.summary`. It
  currently has four shapes built on `printedFastestScore` /
  `printedScenicScore`; it needs the same four built on the two mile counts,
  including the case where the count **falls** (trap 3).
- **`ios/Sources/LoopPanel.swift:227`** — the loop summary still says
  `"**26 mi** scoring **6.4/10**, back where you started"`. Drop the 0–10 clause;
  the loop's own "BEAUTIFUL ROAD" card two rows up already carries the mile
  count, so exact wording is yours — just do not leave a second scale on screen.
- **`ios/Tests/RouteComparisonTests.swift`** — extend. It already exists and
  covers the four sentence shapes.

## Traps

**1. The new fields must be optional, and there must be a fallback.** The
deployed backend does not have them yet — an app in the store talks to whichever
backend is deployed, which is exactly the reasoning already written at
`ios/Sources/RouteResults.swift:102-105` about `_no_worse_than_fastest`. A
non-optional `beautiful_km` makes every route fail to decode against the live
server. When it is nil, fall back to the score sentence that ships today; keep
that path alive and tested.

**2. Do not redefine `isSameDrive`.** `RouteResults.swift:90` is
`extraMinutes <= 0 && !scoreMoves`, and the census replayed *that exact
definition* over 983 pairs: it fires on 11.5%, catches 105 of the 106 trips
where the scenic arm genuinely is the fastest arm, and across all 113 trips it
fires on, the largest gain is **0.22 beautiful miles**. It is measured and good.
Rebuilding it on mile counts throws that measurement away and needs a new one.
`scoreMoves` may keep reading `mean_score` even though the score is no longer
printed — but then its docstring, which says the point is that "the sentence has
to check out against what is on screen", no longer describes it. Say so in the
comment rather than leaving it stale.

**3. The sentence must be able to say the miles went down.** Exposing the 24
trips above is the *purpose* of this change. Do not clamp at zero, do not use
"adds", and do not treat a fall as an error state — it is a true statement about
a real route the server still returns.

**4. Beautiful-km is more weight-sensitive than the score, not less.** The
census measured the fastest arm's path as byte-identical under two weight
settings on all 983 pairs, then compared the numbers on that unchanged road:
`mean_score` moved by at most 0.759, while `beautiful_km` moved by up to
**22.9 km**. Within one response both arms share one ruler, so the pair on
screen is a fair comparison and this change is sound. But the number is *not* a
stable property of a road across Tune settings — so do not build anything that
differences it across tunings, and do not describe it in the UI as an absolute
fact about the route.

**5. The scores stay in the API and the traces.** `mean_score` is what
`tools/analyze_trace.py` and every recorded drive are calibrated against. This
is a display change only; nothing server-side and nothing in `DriveTrace` should
lose the score.

---

# Item 2 — the bottom quarter of the preference slider does almost nothing

## The symptom, as measured

`docs/route-distribution-study.md` Q3 swept 252 pairs across five preference
settings on the shipping graph. Cumulative medians against pref 0:

| pref | strength (pref²) | extra min | beautiful mi | step returns an identical route |
|---|---|---|---|---|
| 0.25 | 0.06 | **0.5** | **0.20** | **33%** |
| 0.50 | 0.25 | 16.5 | 5.06 | 16% |
| 0.75 | 0.56 | 24.5 | 7.01 | 22% |
| 1.00 | 1.00 | 28.1 | 9.20 | 28% |

**The first quarter of the travel buys 2% of what the whole slider buys**, and a
third of trips get back the route they already had. The usable range is
0.25–1.0, not 0–1.

## The mechanism

The control is linear in `pref` — `ios/Sources/RoutePanel.swift:376`:

```swift
Slider(value: $model.pref, in: 0...1) { editing in
```

but the router squares it. `pipeline/router.py:152` sets `PREF_CURVE = 2.0` and
`router.py:919` applies it:

```python
strength = max(0.0, min(1.0, pref)) ** PREF_CURVE
```

So the handle moves linearly through a quantity whose effect is quadratic, and
the left quarter of the track lands in the flat part of the curve.

## The change

Make the *handle* linear in strength, leaving the model and the API alone. The
slider binds to a position `p ∈ [0, 1]` and maps `pref = sqrt(p)`, so
`strength == p` and every part of the travel does comparable work. The transform
lives in a computed `Binding` inside `RoutePanel`; nothing else moves.

Under that mapping the first quarter of the travel buys about 55% of the
available beautiful miles for about 59% of the available minutes, read off the
table above — front-loaded still, because the outcome saturates in strength too,
but no longer dead. Nothing can make the travel linear in *outcome*: that curve
is route-dependent. Linear in strength is the honest, fixed transform.

## Traps

**1. `model.pref` must keep meaning what the API means.** It is not a UI value.
It is written into every `DriveTrace` header (`DriveTrace.init`, `"pref"`),
handed to `NavigationModel` for mid-drive reroutes, and is the quantity every
number in the census and every trace on disk is indexed by. Only the `Binding`
inside `RoutePanel` may hold the transformed value; `RouteModel.pref` stays the
0–1 the server takes. Getting this wrong silently makes the twelve recorded
drives incomparable with future ones.

**2. `pref == 0` is a magic value in three places.** `server/app.py` short
circuits (`scenic = fastest if pref == 0.0`), `NavigationModel.reroute` decides
`wantFastest = pref == 0`, and `switchToFastest` sets `pref = 0`. `sqrt(0)` is
exactly 0 so the mapping preserves it — but any rounding, clamping or animation
smoothing on the way must not turn the far-left position into 1e-9, or the
"Fastest" end of the slider quietly stops being the fastest route.

**3. Do not touch `PREF_CURVE` or `BETA`.** `pipeline/router.py:60` says they
are "**one calibration** and have to be swept together". This is a UI mapping
change; the model is not in scope, and changing the exponent would invalidate
the census and the recorded drives at once.

**4. The caption prints the wrong number afterwards.**
`ios/Sources/RoutePanel.swift:381` reads
`"scenery preference \(model.pref, …)"`. After the change the handle sits at `p`
while `model.pref` is `sqrt(p)`, so the printed number would disagree with where
the handle is. Print the position, or relabel, or drop it — but do not leave it
reading `pref`.

---

## Done looks like

1. Both items implemented as above, on a branch off
   `claude/holistic-code-review-6bce7c`.
2. `cd ios && xcodegen generate && xcodebuild test -project Scenic.xcodeproj
   -scheme Scenic -destination "id=<booted udid>"` green, with new cases for:
   the nil-`beautiful_km` fallback, a sentence where the miles fall, and the
   slider's position↔pref round trip including exact 0 and exact 1.
3. `SCENIC_DATA=<abs>/data/processed .venv/bin/python -m pytest tests/ -q` still
   **317 passed** — neither item should touch the backend, so a move there means
   something out of scope was edited.
4. Screenshots from a booted simulator against a local server: the two cards and
   the sentence for a good trip and for a poor one, and the Tune-free planning
   sheet showing the slider at its far left, quarter, and midpoint.
5. This brief committed with the change.
6. For either item, a statement of why the diagnosis here is wrong — quoting the
   line that proves it — is a good outcome. A quietly skipped item is not.

## Running it

The graph, `.venv` and `data/` live in the **main checkout**, never in a
worktree:

```sh
SCENIC_DATA="$PWD/data/processed" .venv/bin/python server/app.py
```

Flask runs without the reloader, so restart it after any edit — and check
nothing else already holds port 5057 (`lsof -ti :5057`), because a stale server
from another session will serve stale code and look exactly like your change not
working. Point the app at it with the `SIMCTL_CHILD_` prefix; `--setenv` fails:

```sh
SIMCTL_CHILD_SCENIC_API=http://127.0.0.1:5057 xcrun simctl launch booted app.scenic.demo
```

`ios/Scenic.xcodeproj` is gitignored build output — run `xcodegen generate`
after any pull. `-destination 'name=iPhone 15 Pro'` is rejected on this machine
even when the device is listed; use `id=`.

Note that `ios/Tests/LiveDriveTests.swift:208` fails when a backend happens to be
up, on `main` and before this work: it asserts the fastest arm's `mean_score`
differs under two weight settings on Boston→Buzzards Bay, an almost entirely
motorway route where every component is floored and both round to 0.38. It is
pre-existing and out of scope; fix it only if you want to, and say so if you do.
