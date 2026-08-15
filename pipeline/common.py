"""Shared vocabulary for the scoring + routing pipeline.

These constants describe *what counts as a road* and *which map projection we
measure distances in*. They were duplicated across extract.py, graph.py,
score.py and router.py; keeping them here means there's one place to change when
(say) a new road class should be considered drivable.
"""

# OSM highway= values we treat as drivable roads. Everything else (footways,
# cycleways, service alleys, etc.) is ignored when extracting and graph-building.
DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street",
}

# access= values that mean "not open to the public". A road tagged this way is
# skipped unless it also carries motor_vehicle=yes (a common override).
PRIVATE_ACCESS = {"private", "no"}

# The traffic controls a car has to stop at, mapped to the three kinds worth
# telling apart. Massachusetts has 29,772 of them — 11,348 signals, 17,567 stop
# signs, 839 give-ways and 18 mini-roundabouts — and the graph charged nothing
# for any of them until 2026-08-15, which is most of why its travel times ran
# 22% optimistic (see docs/junction-timing-plan.md).
#
# Mini-roundabouts join the give-ways: there are 18 in the state and only 7 sit
# on a drivable way, so a column of their own would be a column of zeroes.
CONTROL_KINDS = {
    "traffic_signals": "signal",
    "stop": "stop",
    "give_way": "giveway",
    "mini_roundabout": "giveway",
}

# One count per kind per direction of travel, written by graph.py and priced by
# router.py. Here rather than in graph.py because the serving box deliberately
# does not install the pipeline's dependencies (osmium, rasterio — see
# server/DEPLOY.md), so router.py cannot import graph.py to learn the names of
# the columns it has to read.
CONTROL_COLUMNS = [f"n_{kind}_{d}" for kind in ("signal", "stop", "giveway")
                   for d in ("fwd", "rev")]

# oneway= values, and which direction they permit. Shared because graph.py and
# router.py have to agree exactly: graph.py decides which nodes are mutually
# reachable and router.py decides which edges may be traversed, so a road that
# one treats as two-way and the other as one-way produces a node the API accepts
# and then cannot route out of.
ONEWAY_FWD = {"yes", "true", "1"}      # forward only
ONEWAY_REV = {"-1", "reverse"}         # reverse only

# The projected coordinate system we do all metric work in: NAD83 / Massachusetts
# Mainland, whose units are meters. Lengths, buffers and nearest-feature
# distances are only meaningful in a projected CRS like this — raw lon/lat
# (EPSG:4326) degrees are not a constant distance apart. State data starts in
# 4326 and is reprojected to this before any distance math.
CRS_METERS = 26986
