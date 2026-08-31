# Licensing and data attribution: what the app owes, and to whom

**Status: diagnosed 2026-08-31, nothing changed.** No source file, no test, no
`ios/project.yml` was touched. Every claim below was checked against the working
tree at that date, not recalled.

Scenic renders OpenStreetMap-derived geometry and scores built from two more
open datasets, and **attributes none of them anywhere**. This is a legal
precondition for putting the app in front of anyone but its author — it is not
polish, and it is not the README's job.

The measurement:

```
grep -rniE "attribution|openstreetmap|odbl|CC-BY|copyright|licen" ios/   →  0 hits
ls LICENSE*                                                             →  none
```

---

## Out of scope — do not touch these

- **`pipeline/`, `server/`, `tests/`.** Nothing in this brief needs them.
- **A repo `LICENSE` file. Deliberately excluded — do not add one.** The
  repository is **private** (verified: `api.github.com/repos/Jamesk1281/Scenic`
  returns 404 unauthenticated). Nobody can read the code, so no licence is owed.
  Which licence to publish under, if it is ever made public, is the owner's
  commercial decision and is being held in the master session.
- **`PrivacyInfo.xcprivacy`.** Adjacent and also missing, but it is a privacy
  manifest, not a licence. Noted at the end so it is not lost; not part of this.

## Known-failing tests that are NOT yours

At the time of writing, `SCENIC_DATA=<abs>/data/processed pytest tests/` reports
**2 failed, 345 passed, 1 skipped**:

- `tests/test_loops.py::TestLoopsAreLoops::test_the_penalty_is_what_removes_the_retrace`
- `tests/test_routing.py::TestSurfaceAvoidanceIsNotAScenerySetting::test_surface_is_absent_from_the_reported_score`

Both come from the `claude/unpaved-and-urban-verdict` merge and both **pass
against `data/processed-ne`**. They are being handled separately. Do not chase
them, and do not "fix" them. Your bar is that the count does not get worse.

---

## The three sources, and what each actually requires

### 1. OpenStreetMap — ODbL 1.0

**Where it enters:** the Geofabrik extract (`README.md:104`), consumed by
`pipeline/extract.py` and `pipeline/graph.py`. Every road, every street name,
every turn restriction.

**Where it reaches the user:** the app draws OSM-derived polylines onto the map
— `ios/Sources/ContentView.swift:45`, `:49`, `:62` — and names OSM streets in
its maneuvers and (since the voice merge) speaks them aloud.

**What that makes it:** a **Produced Work** under ODbL §4.3. Attribution is
required. Share-alike is **not** triggered.

**Required, per the OSMF's attribution guidance:** the credit
`© OpenStreetMap contributors`, and a statement that the data is available under
the Open Database License. Confirm the current exact wording and the required
link at <https://www.openstreetmap.org/copyright> and the OSMF attribution
guideline before writing it — reproduce what those say, do not paraphrase this
brief.

**The separate obligation, which is not triggered today but constrains the
future.** `data/processed/{scored_chunks,graph_edges,graph_nodes,turn_restrictions}.parquet`
are a **Derivative Database**, and distributing *those files* does trigger
share-alike (ODbL §4.4). Today they move only from the author's Mac to the
author's own serving box, which is not distribution. **Any future "download this
region for offline use" feature changes that**, and would oblige offering the
derived database under ODbL. Record this; do not act on it.

### 2. ESA WorldCover v200 (2021) — CC-BY 4.0

**Where it enters:** `pipeline/landcover.py:52` —
`esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/`.

**How much it matters:** it is half of `c_forest` at `pipeline/score.py:324`
(`chunks["c_forest"] = 0.5 * green + 0.5 * tree`), and forest carries weight
0.18 of 1.14. It is in every score the app displays. Not incidental.

**Required:** CC-BY 4.0 attribution. ESA's own prescribed credit is of the form
`© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data
(2021) processed by ESA WorldCover consortium`. **Verify the exact string
against ESA's current terms** — the consortium has published slightly different
wording per version, and v200/2021 is the one this repo fetches.

### 3. AWS Terrain Tiles (Terrarium) — an aggregate, not a dataset

**Where it enters:** `pipeline/elevation.py:35` —
`elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png`. Feeds
`c_relief`.

**The trap, and the reason this item is listed third but is the one most likely
to be got wrong:** this endpoint is the AWS Open Data terrain tile set (the
former Mapzen service). It is **not one dataset under one licence** — it is a
mosaic of many national elevation products, each with its own attribution, and
the required credit depends on which sources cover your footprint. Over New
England the contributing sources are believed to be USGS 3DEP/NED and SRTM, both
US-government public domain, which would make the burden light.

**Do not ship that belief.** Read the current source-and-attribution list on the
AWS Open Data registry entry for Terrain Tiles and reproduce the lines that
apply, or — if the list cannot be resolved to a confident answer — say so and
credit the aggregate by name. An honest "attributed the aggregate because the
per-source list could not be pinned down" is a fine outcome; an invented
public-domain claim is not.

### 4. Apple MapKit — already handled, but possibly obscured

The basemap is Apple's (`Map(position:)`, `ios/Sources/ContentView.swift:40`).
MapKit renders Apple's own attribution and legal link itself, bottom-left, and
Apple's guidelines require it not be covered.

**This is a real, checkable defect and not a theoretical one.** The app presents
a permanently-open bottom sheet — `ContentView.swift:103`,
`.sheet(isPresented: .constant(true))` with `.interactiveDismissDisabled()` —
across detents `[.planningCompact, .medium, .large]`. A sheet occupying the
bottom of the screen is exactly where MapKit puts that attribution. **Check it
at all three detents.** If it is covered, that is an App Review rejection item
as well as a licence one.

---

## Where it goes in the app

**There is no settings screen, no about screen, and no tab bar.** The entire UI
is a map plus one persistent sheet (`RoutePanel`, presented at
`ContentView.swift:103`) holding a mode picker, two address fields, the
preference slider, and a button that opens the "Tune scenery" sub-sheet
(`RoutePanel.swift:165`).

**The design that matches the existing idiom:** an unobtrusive row at the bottom
of `RoutePanel`'s `ScrollView` — "Data sources" or "About" — presenting a sheet
exactly the way "Tune scenery" already does. New file, e.g.
`ios/Sources/AboutView.swift`; wire it from `RoutePanel.swift`.

Files you will touch: `ios/Sources/RoutePanel.swift`, a new
`ios/Sources/AboutView.swift`, possibly `ios/Sources/ContentView.swift` for the
MapKit-attribution fix. Nothing else is in flight in `ios/` — all outstanding
branches were merged on 2026-08-31 — so there is nothing to collide with.

---

## Traps

1. **"Put it in the README and call it done."** The obligation attaches to what
   is *distributed to users*. The README is in a private repo and reaches
   nobody. It is worth updating too, but it does not discharge anything.

2. **"ODbL means the app has to be open-sourced."** It does not. Share-alike
   attaches to a Derivative *Database*, not to a Produced Work, and the routes
   drawn on screen are a Produced Work. Do not let this drive a licence change,
   and do not let it stop the work either.

3. **Burying it.** OSM's guidance is that attribution be reasonably visible for
   the medium. The accepted small-screen pattern is one tap from the main view.
   Do **not** put it inside "Tune scenery" — that is two taps and it is a
   settings screen for something else.

4. **Asserting Terrarium is public domain because 3DEP is.** See §3.

5. **`xcodegen`.** `ios/Scenic.xcodeproj` is a gitignored build output of
   `ios/project.yml`. Adding a Swift file without running `cd ios &&
   xcodegen generate` first produces "Cannot find type X in scope" with the file
   plainly on disk. Run it before every build.

6. **The iOS suite needs a live backend for 6 of its tests.** `LiveDriveTests`
   skips silently when nothing answers on `127.0.0.1:5057`, so a green run can
   be hiding them. Serve it with `.venv/bin/python server/serve.py` from the
   **main checkout** (the parquets live only there, not in a worktree). Nothing
   in this brief should affect those tests; the point is that "green" needs
   checking for skips.

---

## Done looks like

1. An attribution surface reachable in **one tap** from the app's main screen,
   naming: OpenStreetMap (ODbL), ESA WorldCover (CC-BY 4.0), and whatever the
   Terrain Tiles source list actually requires — each in the wording its licence
   asks for, with links, not paraphrased.
2. A statement, with a simulator screenshot at each of the three detents, of
   whether MapKit's own Apple attribution is covered by the planning sheet — and
   the fix if it is.
3. `cd ios && xcodegen generate && xcodebuild test ... -destination
   'platform=iOS Simulator,name=iPhone 17 Pro'` green, with the skip count
   reported, plus a case asserting the attribution strings are present (so a
   later refactor that drops a bar fails a test rather than quietly shipping).
4. Backend suite no worse than the 2 known failures listed above. If that count
   moves, something out of scope was edited.
5. A short note appended to this file recording the ODbL Derivative-Database
   constraint, so the offline-download idea meets it at design time.
6. The README's data-source lines updated to name the licences alongside the
   sources it already lists (`README.md:21`, `:104`, `:109`).
7. **Or, for any item: a statement of why this brief is wrong, quoting the
   source that proves it.** A refuted item is a good outcome. A silently skipped
   one is not.

---

## Noted, not in scope: the privacy manifest

`PrivacyInfo.xcprivacy` does not exist. Until 2026-08-31 `main` did not need one
— it used no required-reason API. **The voice-guidance merge changed that**:
`ios/Sources/VoiceCatalogue.swift:118` persists the chosen voice in
`UserDefaults`, which is `NSPrivacyAccessedAPICategoryUserDefaults` and must be
declared with a reason code for App Store submission. Separate piece of work;
recorded here so it is not rediscovered late.
