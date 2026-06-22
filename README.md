# Scenic (working title)

Scenic-route navigation: pick a destination, get a route that's beautiful instead
of fast. Massachusetts first. A scenic score is computed for every road in the
state from **open geodata only** (no Google/Apple data), a routing engine trades
travel time for beauty via a single preference knob, and a native iOS app
(SwiftUI + MapKit) is the front end on top of the routing API.

![heatmap](out/ma_scenic_heatmap.png)

## Status

- [x] Scoring pipeline: per-road-segment "beauty vector" (water, coastline,
      forest/parks, curvature, terrain relief, farmland, viewpoints, scenic tags)
- [x] Terrain relief from free Terrarium elevation tiles
- [x] Routable graph (~402k edges) split at intersections, scenic-scored
- [x] Scenic router: Dijkstra with a time-vs-scenery preference knob
- [x] iOS app (SwiftUI + MapKit) on the routing API (`ios/`, builds + runs)
- [x] Scenic-byway calibration (Mohawk Trail, Jacob's Ladder)
- [ ] Land cover (NLCD/ESA WorldCover) feature for better score accuracy
- [ ] Host the API (small VPS) so the app works off-device (see `server/DEPLOY.md`)

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
python3 -m venv .venv && .venv/bin/pip install -r pipeline/requirements.txt

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
.venv/bin/python server/app.py        # serves the routing API on http://127.0.0.1:5057
```

The iOS app (`ios/`, open in Xcode) calls `GET /api/route?from=LAT,LON&to=LAT,LON&pref=0..1`
and renders the fastest vs scenic routes. Command-line equivalent:

```sh
.venv/bin/python pipeline/router.py data/processed "42.2626,-71.8023" "42.3551,-71.0657" 0.6
```

## How scoring works

Each ~400 m road chunk gets component scores in `[0,1]` for proximity to water,
coastline, forest/parks, farmland and viewpoints, plus road curvature and local
terrain relief. A weighted blend (tunable constants at the top of `score.py`)
produces a 0–10 composite, with penalties for highways and unpaved surfaces.
The router charges a minutes-equivalent penalty per km of *unscenic* road, so the
preference knob smoothly trades extra time for scenery.

## Where the next features plug in

Two planned features have seams already prepared in the code, so they can be
added without re-architecting:

- **Per-beauty-type preferences** (let a user weight coast vs. forest vs. hills).
  `graph.py` already carries every component column (`c_water`, `c_coast`, …) onto
  each edge, so the data is in place. The change lands in `router.py`: instead of
  baking the penalty from the single composite `score`, blend the components with
  the user's weights per edge. The exact spot is marked `SEAM` in
  `Router._build_directed`.
- **Turn-by-turn directions.** `Router._collect` returns the chosen edges *in
  travel order*, each with its `name`/`ref`/`highway` and geometry. Walking that
  sequence — watching for name changes and heading changes at junctions — is
  enough to emit "turn onto X" maneuvers; no new graph data is required.

The user-facing scenery labels live in one place per language: `SCENERY_BREAKDOWN`
in `router.py` (server) and `RouteProps.sceneryBreakdown` in `Models.swift`
(client). Keep those two in sync when adding a type.
