# Scenic (working title)

Scenic-route navigation: pick a destination, get a route that's beautiful instead
of fast. Massachusetts first. A scenic score is computed for every road in the
state from **open geodata only** (no Google/Apple data), a routing engine trades
travel time for beauty via a single preference knob, and a native iOS app
(SwiftUI + MapKit) is the front end on top of the routing API.

![heatmap](docs/ma_scenic_heatmap.png)

<!-- docs/ holds committed showcase images (out/ is gitignored build output;
     referencing it here would render a broken image on GitHub). Refresh with:
     sips -Z 1800 out/ma_scenic_heatmap.png --out docs/ma_scenic_heatmap.png -->


## Status

- [x] Scoring pipeline: per-road-segment "beauty vector" (water, coastline,
      forest/parks, curvature, terrain relief, farmland, viewpoints, scenic tags,
      town/urban)
- [x] Terrain relief from free Terrarium elevation tiles
- [x] Routable graph (~402k edges) split at intersections, scenic-scored
- [x] Scenic router: Dijkstra with a time-vs-scenery preference knob
- [x] Per-beauty-type preferences — weight scenery types live per request
- [x] iOS app (SwiftUI + MapKit): route planning from an address or your own
      location, tunable scenery, turn-by-turn live navigation with arrival
      time / distance remaining and a switch-to-fastest escape hatch
- [x] Scenic-byway calibration (Mohawk Trail, Jacob's Ladder)
- [x] Scoring calibrated against the score distribution, with tests that guard
      it (`tests/`) — see [How scoring works](#how-scoring-works)
- [x] Hosted API: self-hosted on a spare laptop behind a Cloudflare tunnel, so
      the app works off-device on a real phone — see
      [`server/DEPLOY.md`](server/DEPLOY.md)
- [ ] Land cover (NLCD/ESA WorldCover) feature for better score accuracy
- [ ] More accurate travel times (currently free-flow: speed limit ÷ distance,
      no stops or traffic — measured 10-25% optimistic against real drive times,
      and worst on the surface roads scenic routes prefer). The app's "time
      remaining" inherits this, and scales it by the fraction of route left
- [ ] Start from the exact point, not the nearest corner. `snap()` finds the
      road you're on and then routes from that road's *nearer end* — right
      street, but a median 99 m up it (p90 217 m), because graph nodes are
      junctions. Splitting the snapped edge into two virtual nodes per request
      would take that to zero
- [ ] Drive the routes and judge them. Scoring is calibrated and the nav code is
      written, but none of it has met real GPS yet; route *quality* is the one
      question a laptop cannot answer.

> The early MapLibre web demo was retired to focus on iOS; it lives in git
> history (`git show 82044e2`) and is cheap to revive on the same API if needed.

## Architecture

```
OSM PBF ─┐
Terrarium tiles ─┼─> score.py ──> scored_chunks.parquet ─┐
                 │                                         ├─> graph.py ─> graph_*.parquet
                 │                                         │                     │
                 └─> elevation.py ─> relief.tif ──────────┘            router.py (Dijkstra)
                                                                               │
                                                          server/app.py (Flask API) ─> ios/ (SwiftUI app)
```

`pipeline/common.py` holds the constants shared across these stages (what counts
as a drivable road, the Massachusetts projection).

## Run the pipeline

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r pipeline/requirements.txt

# 1. data (free): MA OpenStreetMap extract
curl -L -o data/raw/massachusetts-latest.osm.pbf \
  https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf

# 2. features + score
.venv/bin/python pipeline/extract.py   data/raw/massachusetts-latest.osm.pbf data/processed
.venv/bin/python pipeline/elevation.py data/processed 11      # terrain relief raster
.venv/bin/python pipeline/score.py     data/processed         # scenic score per chunk
.venv/bin/python pipeline/render.py    data/processed out     # heatmap + regional maps

# 3. routable graph
.venv/bin/python pipeline/graph.py     data/raw/massachusetts-latest.osm.pbf data/processed
```

## Run the API

```sh
.venv/bin/python server/app.py        # local dev server on http://127.0.0.1:5057
```

To *host* it (spare laptop or VPS, with a production server + tunnel), see
[`server/DEPLOY.md`](server/DEPLOY.md) — that path runs `server/serve.py`.

`GET /` returns a short description of the service and its endpoints, which
doubles as a liveness check you can open in a browser.

The iOS app (`ios/`, open in Xcode) calls `GET /api/route?from=LAT,LON&to=LAT,LON&pref=0..1`
and renders the fastest vs scenic routes. It reads the backend URL from the
`ScenicAPIBaseURL` Info.plist key set in `ios/project.yml`, overridable at
runtime with a `SCENIC_API` environment variable. Command-line equivalent:

```sh
.venv/bin/python pipeline/router.py data/processed "42.2626,-71.8023" "42.3551,-71.0657" 0.6
```

## How scoring works

Each ~400 m road chunk gets component scores in `[0,1]` for proximity to water,
coastline, forest/parks, farmland and viewpoints, plus road curvature and local
terrain relief. A weighted blend (tunable constants at the top of `score.py`)
produces a 0–10 composite, with penalties for highways and unpaved surfaces.
The router charges a minutes-equivalent penalty per km of *unscenic* road, so the
preference knob trades extra time for scenery.

Every constant in that blend is fitted to the *distribution* it produces, not
guessed, because a single number silently reshapes 66,000 km of road. `score.py`
prints a calibration report on each run — scale percentiles, per-component
coverage, and benchmark roads — and the current numbers are: median road 4.0,
p90 6.8, p99 9.2, with Greylock's Notch Road at 6.6 and the Mass Pike at 0.6.
Three things that report is specifically there to catch, all of which were live
at some point:

- **a component pinned at its ceiling** — curvature is measured between chords
  60 m apart rather than between raw ~20 m OSM vertices, because summing
  vertex-to-vertex heading change measures digitizing jitter (it reached 3,500
  deg/km, ten rotations per kilometre) and rated cul-de-sacs above the Mohawk
  Trail;
- **a component with no range left** — relief is scaled to Massachusetts
  terrain, not alpine, or the Hills slider has nothing to grab;
- **a compressed scale** — no real road collects every component, so the blend
  needs an explicit stretch or "8/10" is unreachable.

## Tests

```sh
.venv/bin/python -m pytest tests/          # backend: 128 tests
```

The geometry and scoring maths run anywhere; the calibration, routing and API
tests need a built graph and skip cleanly without one. Point them at a graph
elsewhere with `SCENIC_DATA=/path/to/processed`.

The iOS app has its own suite for the parts a simulator can't exercise and a
drive only tests once — where the driver is on the route, when a maneuver has
been passed, when the trip has actually ended, and which of two overlapping
reroutes wins:

```sh
cd ios && xcodegen generate && xcodebuild test -project Scenic.xcodeproj -scheme Scenic -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

## Where the next features plug in

The component pipeline is deliberately open-ended: `score.py` writes every
`c_<component>` column it computes, `graph.py` auto-detects those columns and
carries them onto edges, and `router.py` re-blends them live per request. So:

- **A new scenery signal** (e.g. land cover from NLCD/ESA WorldCover) is one new
  `c_...` column plus a `WEIGHTS` entry in `score.py`, then a score + graph
  rebuild. Add it to `BEAUTY_TYPES` in `router.py` only if users should be able
  to tune it (otherwise list it in `BASELINE`).
- **More realistic travel times** land in `graph.py` (the `minutes` column):
  free-flow speed is optimistic on local roads; a small per-junction stop
  penalty is the cheap first fix, real traffic data the expensive one.
- **A second region** is the same pipeline run on another Geofabrik extract.
  The MA-specific bits to generalize: the projection in `common.py`, the BBOX
  in `elevation.py`, the byway names in `score.py`, and the
  `Region.massachusetts` search bias in the iOS app.

The user-facing scenery labels live in one place per language: `SCENERY_BREAKDOWN`
in `router.py` (server) and `RouteProps.sceneryBreakdown` in `Models.swift`
(client), with the tunable type names in `BEAUTY_TYPES` and `BeautyType.all`.
Both suites assert the same lists from their own side, so renaming a type on one
end fails a test rather than quietly dropping a bar from the app.
