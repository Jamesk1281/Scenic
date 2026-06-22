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

# The projected coordinate system we do all metric work in: NAD83 / Massachusetts
# Mainland, whose units are meters. Lengths, buffers and nearest-feature
# distances are only meaningful in a projected CRS like this — raw lon/lat
# (EPSG:4326) degrees are not a constant distance apart. State data starts in
# 4326 and is reprojected to this before any distance math.
CRS_METERS = 26986
