"""Extract drivable roads and scenic-relevant features from an OSM PBF.

Reads a Geofabrik state extract and writes GeoParquet layers:
  roads          - drivable ways (LineString) with highway/name/ref/scenic/surface
  water_areas    - lakes, reservoirs, bays (MultiPolygon)
  water_lines    - rivers and canals (LineString)
  coastline      - natural=coastline ways (LineString)
  green_areas    - woods, forests, parks, reserves (MultiPolygon)
  farm_areas     - farmland, orchards, meadows (MultiPolygon)
  viewpoints     - tourism=viewpoint (Point)

Usage: python extract.py <input.osm.pbf> <output_dir>
"""

import sys
import time
from pathlib import Path

import geopandas as gpd
import osmium
import pandas as pd

WKB = osmium.geom.WKBFactory()

DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street",
}
PRIVATE_ACCESS = {"private", "no"}

GREEN_NATURAL = {"wood"}
GREEN_LANDUSE = {"forest"}
GREEN_LEISURE = {"park", "nature_reserve"}
GREEN_BOUNDARY = {"national_park", "protected_area"}
WATER_NATURAL = {"water", "bay"}
WATER_LANDUSE = {"reservoir", "basin"}
WATER_LINE = {"river", "canal"}
FARM_LANDUSE = {"farmland", "orchard", "vineyard", "meadow"}


class Handler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.roads = []
        self.water_lines = []
        self.coastline = []
        self.viewpoints = []
        self.water_areas = []
        self.green_areas = []
        self.farm_areas = []
        self.errors = 0

    def node(self, n):
        if n.tags.get("tourism") == "viewpoint":
            try:
                self.viewpoints.append({"wkb": WKB.create_point(n)})
            except Exception:
                self.errors += 1

    def way(self, w):
        tags = w.tags
        hw = tags.get("highway")
        if hw in DRIVABLE:
            if tags.get("access") in PRIVATE_ACCESS and tags.get("motor_vehicle") != "yes":
                return
            try:
                wkb = WKB.create_linestring(w)
            except Exception:
                self.errors += 1
                return
            self.roads.append({
                "way_id": w.id,
                "highway": hw,
                "name": tags.get("name", ""),
                "ref": tags.get("ref", ""),
                "scenic": tags.get("scenic") == "yes",
                "surface": tags.get("surface", ""),
                "wkb": wkb,
            })
        elif tags.get("natural") == "coastline":
            try:
                self.coastline.append({"wkb": WKB.create_linestring(w)})
            except Exception:
                self.errors += 1
        elif tags.get("waterway") in WATER_LINE:
            try:
                self.water_lines.append({"wkb": WKB.create_linestring(w)})
            except Exception:
                self.errors += 1

    def area(self, a):
        tags = a.tags
        if tags.get("natural") in WATER_NATURAL or tags.get("landuse") in WATER_LANDUSE:
            bucket = self.water_areas
        elif (
            tags.get("natural") in GREEN_NATURAL
            or tags.get("landuse") in GREEN_LANDUSE
            or tags.get("leisure") in GREEN_LEISURE
            or tags.get("boundary") in GREEN_BOUNDARY
        ):
            bucket = self.green_areas
        elif tags.get("landuse") in FARM_LANDUSE:
            bucket = self.farm_areas
        else:
            return
        try:
            bucket.append({"wkb": WKB.create_multipolygon(a)})
        except Exception:
            self.errors += 1


def to_gdf(rows):
    if not rows:
        return gpd.GeoDataFrame({"geometry": gpd.GeoSeries([], crs=4326)})
    df = pd.DataFrame(rows)
    geom = gpd.GeoSeries.from_wkb(df.pop("wkb"), crs=4326)
    return gpd.GeoDataFrame(df, geometry=geom)


def main(pbf_path: str, out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    h = Handler()
    # Area assembly makes pyosmium read the file twice; flex_mem holds node locations in RAM.
    h.apply_file(pbf_path, locations=True, idx="flex_mem")
    print(f"parsed PBF in {time.time() - t0:.0f}s ({h.errors} geometry errors skipped)")

    layers = {
        "roads": h.roads,
        "water_areas": h.water_areas,
        "water_lines": h.water_lines,
        "coastline": h.coastline,
        "green_areas": h.green_areas,
        "farm_areas": h.farm_areas,
        "viewpoints": h.viewpoints,
    }
    for name, rows in layers.items():
        gdf = to_gdf(rows)
        gdf.to_parquet(out / f"{name}.parquet")
        print(f"{name}: {len(gdf):,} features")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
