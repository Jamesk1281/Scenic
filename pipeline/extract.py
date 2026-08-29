"""Extract drivable roads and scenic-relevant features from an OSM PBF.

Reads a Geofabrik state extract and writes GeoParquet layers:
  roads          - drivable ways (LineString) with highway/name/ref/scenic/surface,
                   where `scenic` is scenic=yes OR membership of a designated
                   scenic-byway route relation (see BYWAY_NETWORKS)
  access_ways    - highway=service (parking aisles, driveways) with a component id
  access_entries - where each of those components meets the drivable network
  water_areas    - lakes, reservoirs, bays (MultiPolygon)
  water_lines    - rivers and canals (LineString)
  coastline      - natural=coastline ways (LineString)
  green_areas    - woods, forests, parks, reserves (MultiPolygon)
  farm_areas     - farmland, orchards, meadows (MultiPolygon)
  viewpoints     - tourism=viewpoint (Point)
  place_points   - city/town/village/hamlet/square centers (Point)
  urban_areas    - retail and commercial districts / downtowns (MultiPolygon)

Usage: python extract.py <input.osm.pbf> <output_dir>
"""

import sys
import time
from pathlib import Path

import geopandas as gpd
import osmium
import pandas as pd
import shapely

from common import DRIVABLE, PRIVATE_ACCESS

WKB = osmium.geom.WKBFactory()

GREEN_NATURAL = {"wood"}
GREEN_LANDUSE = {"forest"}
GREEN_LEISURE = {"park", "nature_reserve"}
GREEN_BOUNDARY = {"national_park", "protected_area"}
WATER_NATURAL = {"water", "bay"}
WATER_LANDUSE = {"reservoir", "basin"}
WATER_LINE = {"river", "canal"}
FARM_LANDUSE = {"farmland", "orchard", "vineyard", "meadow"}

# "Townscape" signal — the heart of a settlement and its main-street fabric.
# PLACE_CENTERS are point nodes marking a town/village center; URBAN_LANDUSE are
# the retail/commercial districts (downtowns, shopping streets). We deliberately
# leave out `residential` (would reward generic suburbs) and `industrial` (ugly);
# add "residential" here later if you want broader "urban fabric".
PLACE_CENTERS = {"city", "town", "village", "hamlet", "square"}
URBAN_LANDUSE = {"retail", "commercial"}


# Parking lots, drive-throughs and driveways. Deliberately *not* added to
# DRIVABLE: there are 433,969 of them in Massachusetts against 227,251 drivable
# ways, and routing through them would cost far more than it buys (see the
# E^1.20 latency scaling in the README). They are extracted for one purpose —
# working out where a destination inside one is actually entered from.
#
# The defect that made this necessary, measured on the drives of 2026-08-22: a
# pin dropped on a building in a parking lot snaps to whichever *public* road is
# nearest as the crow flies, which is routinely the wrong side of the building.
# Three of five destinations were inside mapped lots, and two of those snapped
# to a road with no connection to the lot at all — one to a cul-de-sac 102 m
# away whose only real entrance was a secondary road 226 m away in the other
# direction. The driver was sent on a 3.2 km loop past the entrance they wanted.
ACCESS = "service"


# Scenic byways live in OSM as route *relations*, not as way tags. The
# `scenic=yes` way tag read below is nearly dead data — 28 ways in all six New
# England states (MA 0, VT 0, RI 0, NH 3, ME 3, CT 22) — while the relations
# admitted below carry 38 designated routes, 4,510 drivable member ways and
# 3,235 km: VT 1,461, MA 738, NH 639, ME 283, CT 54.
#
# A relation is admitted when it is a *road* route and is *designated* scenic:
#
#   route=road  AND  (network in BYWAY_NETWORKS  OR  scenic=yes on the relation)
#
# Both halves of that OR are load-bearing. Not one of the 14 `US:MA:Scenic`
# relations carries `scenic=yes`, so without the network clause the home state
# goes to zero. And New Hampshire, Maine, Rhode Island and Connecticut have no
# byway network at all: their byways — Kancamagus, Acadia All-American Road,
# Old Canada Road, Rangeley Lakes, Schoodic, Connecticut Route 169, White
# Mountain Trail — are network-less relations tagged `scenic=yes`, so without
# the tag clause four of the six states go dark.
#
# `route=road` is what keeps footpaths off the roads, and it is not cosmetic.
# Four hiking routes and two cycle routes carry "scenic" in their names, and
# where they road-walk they share ways with the drivable network: 206 ways and
# 91 km of ordinary road, 132 ways of it under the New England National Scenic
# Trail alone. Selecting on the word "scenic" would flag every one of them.
# The same clause drops three railway/train routes ("Conway Branch",
# "Milford & Bennington Railroad", "Winnipesaukee Railway").
#
# Networks are allowlisted by name rather than pattern-matched because three of
# the networks containing a scenic-sounding relation are general
# numbered-highway systems: US:US (56 relations, 18,692 ways), US:ME (187 /
# 7,523) and US:RI (62 / 3,300). Allowlisting those would designate every US
# and state highway in the region a scenic byway. Their three candidates are
# rejected individually and on their own evidence: both Rhode Island Route 1A
# relations hedge in their own tags ("sometimes signed with 'SCENIC' in the
# shield", "sometimes bannered as scenic, and sometimes not"), and Maine SR 11
# is merely *named* "Aroostock Scenic Highway" — 615 ways and 655 km, the
# largest single candidate in the region, carrying no scenic designation tag.
#
# Counts are from the 2026-08-25 New England extract; see
# docs/byway-relations-brief.md.
BYWAY_NETWORKS = {
    "US:MA:Scenic",                 # 14 relations, every one a designated byway
    "US:VT:byway",                  # 9
    "US:AB:NSB:Connecticut River",  # 2 — America's Byways / National Scenic Byway
    "US:NY:Scenic",                 # 1 — Lakes to Locks Passage, over the NY line
    "CA:NB:scenic",                 # 2 — New Brunswick, in the extract's border overlap
}


class Handler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.roads = []
        # Service ways as (node refs, coordinates) — refs so components can be
        # found by shared nodes, coordinates parallel to them so an attachment
        # node's position is a lookup rather than a second pass over the PBF.
        self.access = []
        # Every node any drivable way passes through. A service way sharing one
        # of these is joined to the public network there, and that shared node
        # is the entrance.
        self.drivable_nodes = set()
        self.water_lines = []
        self.coastline = []
        self.viewpoints = []
        self.place_points = []
        self.water_areas = []
        self.green_areas = []
        self.farm_areas = []
        self.urban_areas = []
        # Member way ids of every designated scenic byway route.
        self.byway_ways = set()
        self.byway_rels = set()
        self.errors = 0

    def _add_access(self, w, tags):
        """One service way, kept as refs plus coordinates.

        Private ways are kept, unlike drivable ones. A gated lot or a private
        drive is still how you reach what is inside it, and the question here is
        only "which public road does this hang off", never "may I drive it".
        Dropping them broke the component in exactly the cases that matter — a
        lot whose spine is tagged private and whose aisles are not.
        """
        points = [(n.ref, n.location.lon, n.location.lat)
                  for n in w.nodes if n.location.valid()]
        if len(points) < 2:
            return
        self.access.append(([p[0] for p in points],
                            [(p[1], p[2]) for p in points]))

    def _add_point(self, bucket, n):
        try:
            bucket.append({"wkb": WKB.create_point(n)})
        except Exception:
            self.errors += 1

    def node(self, n):
        if n.tags.get("tourism") == "viewpoint":
            self._add_point(self.viewpoints, n)
        elif n.tags.get("place") in PLACE_CENTERS:
            self._add_point(self.place_points, n)

    def way(self, w):
        tags = w.tags
        hw = tags.get("highway")
        if hw == ACCESS:
            self._add_access(w, tags)
            return
        if hw in DRIVABLE:
            if tags.get("access") in PRIVATE_ACCESS and tags.get("motor_vehicle") != "yes":
                return
            try:
                wkb = WKB.create_linestring(w)
            except Exception:
                self.errors += 1
                return
            self.drivable_nodes.update(n.ref for n in w.nodes)
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

    def relation(self, r):
        """Collect the member ways of designated scenic byway routes.

        Relations sort last in a PBF, so `byway_ways` is complete by the time
        main() builds the frame; and because it is a set, the second pass
        pyosmium makes for area assembly only re-adds ids it already holds.
        Membership is applied to `roads` in main() rather than in way() for
        that ordering reason — when way() runs, this set is still empty.
        """
        tags = r.tags
        if tags.get("type") != "route" or tags.get("route") != "road":
            return
        if (tags.get("network") not in BYWAY_NETWORKS
                and tags.get("scenic") != "yes"):
            return
        self.byway_rels.add(r.id)
        self.byway_ways.update(m.ref for m in r.members if m.type == "w")

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
        elif tags.get("landuse") in URBAN_LANDUSE:
            bucket = self.urban_areas
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


def access_layers(access, drivable_nodes):
    """Turn raw service ways into (ways with a component id, entry points).

    Two tables because the router asks two questions in sequence: *which lot is
    this pin in* (nearest way -> its component) and *where do I drive to* (that
    component's entries). Keeping them apart means the geometry index it
    searches holds only the ways, and the entries are a dictionary lookup.

    Components are found over shared node ids rather than by touching geometry.
    Two aisles that cross without a shared node are not connected in OSM's model
    and a car cannot turn between them either, so id-sharing is not an
    approximation here — it is the definition.
    """
    parent = list(range(len(access)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    by_ref = {}
    for i, (refs, _) in enumerate(access):
        for ref in refs:
            first = by_ref.setdefault(ref, i)
            if first != i:
                union(first, i)

    ways, entries, seen = [], [], set()
    for i, (refs, coords) in enumerate(access):
        component = find(i)
        ways.append({"component": component,
                     "wkb": shapely.to_wkb(shapely.LineString(coords))})
        for ref, (lon, lat) in zip(refs, coords):
            # The shared node *is* the entrance: it is where the lot's geometry
            # and the public road's geometry meet.
            if ref in drivable_nodes and (component, ref) not in seen:
                seen.add((component, ref))
                entries.append({"component": component, "lon": lon, "lat": lat,
                                "wkb": shapely.to_wkb(shapely.Point(lon, lat))})
    return ways, entries


def main(pbf_path: str, out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    h = Handler()
    # Area assembly makes pyosmium read the file twice; flex_mem holds node locations in RAM.
    h.apply_file(pbf_path, locations=True, idx="flex_mem")
    print(f"parsed PBF in {time.time() - t0:.0f}s ({h.errors} geometry errors skipped)")

    # `scenic` is the union of the way tag with byway route membership. It is
    # applied here and not in way() because relations sort after ways in a PBF,
    # so byway_ways is only complete now.
    tagged = sum(r["scenic"] for r in h.roads)
    for r in h.roads:
        r["scenic"] = r["scenic"] or r["way_id"] in h.byway_ways
    scenic = sum(r["scenic"] for r in h.roads)
    print(f"byways: {len(h.byway_rels):,} designated route relations, "
          f"{len(h.byway_ways):,} member ways, of which {scenic - tagged:,} are "
          f"drivable and new; scenic=yes tagged {tagged:,} -> {scenic:,} total")

    layers = {
        "roads": h.roads,
        "water_areas": h.water_areas,
        "water_lines": h.water_lines,
        "coastline": h.coastline,
        "green_areas": h.green_areas,
        "farm_areas": h.farm_areas,
        "viewpoints": h.viewpoints,
        "place_points": h.place_points,
        "urban_areas": h.urban_areas,
    }
    access_ways, access_entries = access_layers(h.access, h.drivable_nodes)
    stranded = len({w["component"] for w in access_ways}) - \
        len({e["component"] for e in access_entries})
    print(f"access: {len(h.access):,} service ways in "
          f"{len({w['component'] for w in access_ways}):,} components, "
          f"{len(access_entries):,} entries "
          f"({stranded:,} components touch no drivable way)")
    layers["access_ways"] = access_ways
    layers["access_entries"] = access_entries
    for name, rows in layers.items():
        gdf = to_gdf(rows)
        gdf.to_parquet(out / f"{name}.parquet")
        print(f"{name}: {len(gdf):,} features")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
