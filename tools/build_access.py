"""Rebuild only the access layers, without re-running the whole extract.

    .venv/bin/python tools/build_access.py data/raw/<state>.osm.pbf data/processed

`extract.py` writes these two layers as part of a full run, but a full run also
reassembles every polygon layer — which makes pyosmium read the PBF twice and
takes far longer than the layers here need. This does the one pass they do need
(31 s on the Massachusetts extract) and leaves every other parquet alone.

The component and entry logic is imported from `extract.py` rather than repeated,
so there is one definition of what an entrance is. Only the handler differs, and
only by collecting less.

What they are for, and why service ways are not simply added to `DRIVABLE`, is
in `Router.access_point`.
"""

import sys
import time
from pathlib import Path

import geopandas as gpd
import osmium
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from common import DRIVABLE, PRIVATE_ACCESS  # noqa: E402
from extract import ACCESS, access_layers  # noqa: E402


class AccessOnly(osmium.SimpleHandler):
    """Service ways, plus the ids of every node a drivable way passes through.

    The second is the whole trick: a service way sharing one of those nodes is
    joined to the public network there, and that node is the entrance.
    """

    def __init__(self):
        super().__init__()
        self.access = []
        self.drivable_nodes = set()

    def way(self, w):
        highway = w.tags.get("highway")
        if highway == ACCESS:
            points = [(n.ref, n.location.lon, n.location.lat)
                      for n in w.nodes if n.location.valid()]
            if len(points) >= 2:
                self.access.append(([p[0] for p in points],
                                    [(p[1], p[2]) for p in points]))
        elif highway in DRIVABLE:
            if (w.tags.get("access") in PRIVATE_ACCESS
                    and w.tags.get("motor_vehicle") != "yes"):
                return
            self.drivable_nodes.update(n.ref for n in w.nodes)


def main(pbf_path: str, out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    handler = AccessOnly()
    handler.apply_file(pbf_path, locations=True, idx="flex_mem")
    print(f"parsed PBF in {time.time() - t0:.0f}s: {len(handler.access):,} service "
          f"ways, {len(handler.drivable_nodes):,} drivable nodes")

    ways, entries = access_layers(handler.access, handler.drivable_nodes)
    components = {w["component"] for w in ways}
    reachable = {e["component"] for e in entries}
    # Worth printing rather than assuming: a car park mapped with no connection
    # to any drivable way cannot be routed to, and `access_point` falls back to
    # the nearest road for it. If this share ever climbs, the fallback is
    # carrying more weight than the fix.
    print(f"{len(components):,} components, {len(entries):,} entries, "
          f"{len(components - reachable):,} components touch no drivable way")

    for name, rows in (("access_ways", ways), ("access_entries", entries)):
        frame = pd.DataFrame(rows)
        geometry = gpd.GeoSeries.from_wkb(frame.pop("wkb"), crs=4326)
        gdf = gpd.GeoDataFrame(frame, geometry=geometry)
        gdf.to_parquet(out / f"{name}.parquet")
        size = (out / f"{name}.parquet").stat().st_size / 1e6
        print(f"{name}: {len(gdf):,} features ({size:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1], sys.argv[2])
