"""Count what extract.py's handler would put in each bucket, per PBF, no geometry.

    .venv/bin/python tools/bucket_census.py data/raw/vermont-latest.osm.pbf ...

The question this answers is "will a new region flow through the existing
pipeline, or does it tag things differently" — asked before spending hours on a
build that would come out hollow. A state that yields zero green areas or zero
urban areas is a red flag; a state that yields zero coastline may just be
landlocked.

The bucket predicates are *imported* from extract.py rather than restated, so a
disagreement between this and the pipeline is impossible by construction. What
is deliberately left out is geometry: no WKB is built and nothing is written,
which is the whole reason this runs in tens of seconds where extract.py runs in
minutes. Area assembly still makes pyosmium read each file twice.

Calibration, so the counts can be trusted: run over Massachusetts this prints
227,251 roads and 433,969 access ways, the two figures asserted in extract.py's
own comments.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import osmium  # noqa: E402

from common import DRIVABLE, PRIVATE_ACCESS  # noqa: E402
from extract import (ACCESS, FARM_LANDUSE, GREEN_BOUNDARY, GREEN_LANDUSE,  # noqa: E402
                     GREEN_LEISURE, GREEN_NATURAL, PLACE_CENTERS,
                     URBAN_LANDUSE, WATER_LANDUSE, WATER_LINE, WATER_NATURAL)

BUCKETS = ["roads", "access_ways", "coastline", "water_lines", "water_areas",
           "green_areas", "farm_areas", "urban_areas", "viewpoints",
           "place_points"]

# Which of the four green tag families matched. Kept separate because the
# families are not interchangeable across New England: southern states map
# woodland as natural=wood and barely use landuse=forest, northern states use
# both. extract.py ORs them, so green_areas is fine either way — but anything
# downstream that reaches for one tag reads as a state boundary.
GREEN_FAMILIES = (("natural", GREEN_NATURAL), ("landuse", GREEN_LANDUSE),
                  ("leisure", GREEN_LEISURE), ("boundary", GREEN_BOUNDARY))


class Census(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.counts = dict.fromkeys(BUCKETS, 0)
        self.green = {k: 0 for k, _ in GREEN_FAMILIES}

    def node(self, n):
        if n.tags.get("tourism") == "viewpoint":
            self.counts["viewpoints"] += 1
        elif n.tags.get("place") in PLACE_CENTERS:
            self.counts["place_points"] += 1

    def way(self, w):
        tags = w.tags
        hw = tags.get("highway")
        if hw == ACCESS:
            self.counts["access_ways"] += 1
        elif hw in DRIVABLE:
            if tags.get("access") in PRIVATE_ACCESS and tags.get("motor_vehicle") != "yes":
                return
            self.counts["roads"] += 1
        elif tags.get("natural") == "coastline":
            self.counts["coastline"] += 1
        elif tags.get("waterway") in WATER_LINE:
            self.counts["water_lines"] += 1

    def area(self, a):
        tags = a.tags
        if tags.get("natural") in WATER_NATURAL or tags.get("landuse") in WATER_LANDUSE:
            self.counts["water_areas"] += 1
        elif any(tags.get(k) in vals for k, vals in GREEN_FAMILIES):
            self.counts["green_areas"] += 1
            for k, vals in GREEN_FAMILIES:
                if tags.get(k) in vals:
                    self.green[k] += 1
        elif tags.get("landuse") in FARM_LANDUSE:
            self.counts["farm_areas"] += 1
        elif tags.get("landuse") in URBAN_LANDUSE:
            self.counts["urban_areas"] += 1


def census(pbf_path: str) -> dict:
    t0 = time.time()
    h = Census()
    h.apply_file(pbf_path, locations=True, idx="flex_mem")
    return dict(h.counts,
                **{f"green_{k}": v for k, v in h.green.items()},
                secs=round(time.time() - t0, 1))


def main(paths):
    for pbf in paths:
        name = Path(pbf).name.replace("-latest.osm.pbf", "")
        row = census(pbf)
        print(f"{name} {json.dumps(row)}", flush=True)
        for bucket in ("roads", "green_areas", "urban_areas"):
            if row[bucket] == 0:
                print(f"  !! {name} yields zero {bucket} — do not build on this",
                      flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
