# Scenic (working title)

Scenic-route navigation: pick a destination, get a route that's beautiful instead
of fast. Massachusetts first. A scenic score is computed for every road in the
state from **open geodata only** (no Google/Apple data), a routing engine trades
travel time for beauty via a single preference knob, and the same backend drives
a web demo and (planned) a native iOS app.

![heatmap](out/ma_scenic_heatmap.png)

## Status

- [x] Scoring pipeline: per-road-segment "beauty vector" (water, coastline,
      forest/parks, curvature, terrain relief, farmland, viewpoints, scenic tags)
- [x] Terrain relief from free Terrarium elevation tiles
- [x] Routable graph (~402k edges) split at intersections, scenic-scored
- [x] Scenic router: Dijkstra with a time-vs-scenery preference knob
- [x] Web demo: MapLibre map, click two points, compare fastest vs scenic
- [x] iOS app (SwiftUI + MapKit) on the same API (`ios/`, builds + runs)
- [ ] Land cover (NLCD) feature; official scenic-byway calibration
- [ ] Hosting (small VPS) + public domain (see `server/DEPLOY.md`)

## Architecture

```
OSM PBF ─┐
3DEP/Terrarium ─┼─> score.py ──> scored_chunks.parquet ─┐
                │                                         ├─> graph.py ─> graph_*.parquet
                │                                         │                     │
                └─> elevation.py ─> relief.tif ──────────┘            router.py (Dijkstra)
                                                                              │
                                                          server/app.py (Flask API) ─> web/ (MapLibre)
```

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

## Run the demo

```sh
.venv/bin/python server/app.py        # serves http://127.0.0.1:5057
```

Open the URL, click a start and a destination, drag the **scenery preference**
slider, and the map compares the fastest route against the scenic one with a
time delta and a breakdown of how many km pass coast / forest / water / hills.

Command-line equivalent:

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
