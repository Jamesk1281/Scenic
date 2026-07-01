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
- [x] iOS app (SwiftUI + MapKit): route planning, tunable scenery, turn-by-turn
      live navigation with a switch-to-fastest escape hatch
- [x] Scenic-byway calibration (Mohawk Trail, Jacob's Ladder)
- [ ] Land cover (NLCD/ESA WorldCover) feature for better score accuracy
- [ ] More accurate travel times (currently free-flow: speed limit ÷ distance,
      no stops or traffic — optimistic on short/local trips)
- [ ] Host the API (spare laptop or VPS) so the app works off-device — see
      [`server/DEPLOY.md`](server/DEPLOY.md)

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
.venv/bin/python server/app.py        # local dev server on http://127.0.0.1:5057
```

To *host* it (spare laptop or VPS, with a production server + tunnel), see
[`server/DEPLOY.md`](server/DEPLOY.md) — that path runs `server/serve.py`.

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
(client). Keep those two in sync when adding a type.
