# Scenic (working title)

Scenic-route navigation: pick a destination, get a route that's beautiful instead of fast.
Massachusetts first. Planned shape: open-geodata scoring pipeline → custom-cost routing
(Valhalla) → web demo + native iOS app on the same backend.

## Status

- [x] v0 scoring pipeline: per-road-segment "beauty vector" from OSM features
      (water, coastline, forest/parks, curvature, farmland, viewpoints, scenic tags)
- [ ] Elevation (USGS 3DEP) + land cover (NLCD) features
- [ ] Routing engine with scenic costing + detour budget
- [ ] Web demo (MapLibre)
- [ ] iOS app (SwiftUI)

## Pipeline

```sh
python3 -m venv .venv && .venv/bin/pip install -r pipeline/requirements.txt
curl -L -o data/raw/massachusetts-latest.osm.pbf \
  https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf
.venv/bin/python pipeline/extract.py data/raw/massachusetts-latest.osm.pbf data/processed
.venv/bin/python pipeline/score.py data/processed
.venv/bin/python pipeline/render.py data/processed out
```

Outputs: `data/processed/scored_chunks.parquet` (every drivable road in ~400 m chunks,
component scores + composite 0–10), `out/ma_scenic_heatmap.png` (statewide heatmap),
`out/explore_*.html` (interactive regional maps).
