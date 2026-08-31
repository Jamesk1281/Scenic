"""Scenic routing over the annotated road graph.

Loads graph_edges/graph_nodes, expands directed edges (honoring oneway), and
runs a Dijkstra whose edge weight blends travel time with an "unscenic" penalty:

    weight = minutes + pref**PREF_CURVE * BETA * km * (1 - score/10)

where `minutes` is driving time at the class's *measured* speed plus the time
lost to the traffic signals and stop signs on that road in that direction — not
the free-flow number stored in the graph. See SPEED_FACTOR and CONTROL_SECONDS.

The penalty is a minutes-equivalent cost charged per kilometer of *unscenic*
road (BETA min/km at full ugliness), so the router trades extra distance for
beauty instead of only shaving seconds. `pref` (0..1) is the overall scenery
strength: 0 gives the fastest route, 1 leans hard into scenery.

The per-edge `score` is computed *live* from the road's beauty vector so the
user can weight beauty types differently (see BEAUTY_TYPES) — e.g. favor coast
and town centers, ignore farmland. At neutral weights (all 1.0) the live score
exactly reproduces the precomputed composite from score.py. The same graph
answers fastest vs scenic, so we return them side by side with a breakdown.

CLI:
    python router.py <processed_dir> "lat,lon" "lat,lon" [pref]
writes out/route_fastest.geojson and out/route_scenic.geojson.
"""

import json
import math
import sys
import warnings
from functools import cached_property
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from shapely.strtree import STRtree
from pyproj import Transformer

from common import CONTROL_COLUMNS, CRS_METERS, ONEWAY_FWD, ONEWAY_REV
from score import (CLASS_ADJ, LEGACY_UNPAVED_ADJ, WEIGHTS, blend, components,
                   composite)

# Minutes-equivalent penalty per km of fully-unscenic road at pref=1.
#
# Raised from 7.0 when travel time stopped being free-flow. This is a cost in
# *minutes*, competing against a `minutes` term that grew — scenic back roads
# got slower and picked up the stop signs on them, while motorways got 16%
# faster and carry almost none — so the same 7.0 bought measurably less detour
# than it used to. Measured over 20 routes, the share of a pref-0.25 route that
# leaves the fastest road fell from 70% to 50% and the scenery it found fell
# from 4.62 to 3.39 on the 0-10 scale: the slider's bottom half had gone soft.
#
# Read that as calibration, not preference: `pref` means the same thing to a
# driver as it did before, and it takes a bigger number to mean it now.
#
# BETA and PREF_CURVE are **one calibration** and have to be swept together —
# raising BETA alone fixes the bottom of the slider by handing the whole gain to
# it. Over 10 routes, the share of the total scenery gain won by pref 0.25 and
# by the back half (0.5 -> 1.0):
#
#     BETA  curve    bottom   top          BETA  curve    bottom   top
#      8.0   1.30      0.68  0.07          10.0   1.30      0.77  0.05
#      8.0   1.60      0.35  0.11          10.0   2.00      0.34  0.12
#      8.0   2.00      0.32  0.15          12.0   2.00      0.35  0.10
#
# 8.0 with a curve of 2.0 is the most even the pair can be made. Nothing reaches
# a flat 0.25/0.25 and nothing will: the penalty saturates, so past pref ~0.5
# the router has already taken every detour worth taking, and the ceiling is
# 5.6 on the 0-10 scale whatever these are set to.
BETA = 8.0

# **Re-swept 2026-08-29, when surface left the score, and deliberately left
# alone.** Moving `UNPAVED_ADJ` out raised the region's mean score (4.57 -> 4.87
# length-weighted), which weakens `km * (1 - score/10)` and so weakens BETA for
# the same slider position. Re-running the sweep above, before against after,
# with the same ten routes:
#
#            New England routes        Massachusetts routes
#            bottom   top              bottom   top
#   before     0.21  0.51                0.14  0.21
#   after      0.14  0.59                0.14  0.21
#
# **Massachusetts does not move at all** — to two decimals, identically — which
# is the whole reason not to touch this. The shift is confined to routes that
# have dirt roads on them, and raising BETA is a global instrument: it would
# re-shape every route in six states to compensate for something that only
# happens in two. Restoring the New England bottom would take BETA ~10.0, a 25%
# raise, and the table above already records 10.0 as measured and rejected.
#
# Read the softer bottom as the new behaviour rather than as drift. The surface
# avoidance does not scale with `pref`, so at pref 0.25 it runs at full strength
# against a scenery term that is barely awake — which is the point (a driver on
# the fastest setting can now avoid dirt at all) and costs the slider some of
# its low-end travel in dirt country. A driver who wants that back sets
# `avoid_unpaved=0`.
#
# Note also that the `before` arm does not reproduce this file's own 0.32/0.15
# either. That table was fitted on a Massachusetts build several rebuilds ago —
# c_forest, the relief rescale and the New England extract have all landed
# since. **Do not re-fit against 0.32/0.15 without first re-deriving it**, and
# see docs/unpaved-and-urban-verdict.md for what a real re-fit needs.

# --- Travel time --------------------------------------------------------------
# `graph_edges.minutes` is free-flow — length over the speed limit, with nothing
# charged for stopping. Measured against two recorded drives it ran 22% short of
# the clock. The two corrections below close that, and are applied here at load
# rather than baked into the parquet for two reasons: `tools/analyze_trace.py`
# reads `length_m / minutes` as the speed the graph assumed, so a pre-corrected
# column would have every future drive report a factor of 1.00 whether or not
# the correction was any good; and re-fitting these as drives accumulate is then
# a constant and a restart, not a 135 s rebuild plus copying 80 MB to the
# serving box. See docs/junction-timing-plan.md §5.

# Measured moving speed over the posted limit, with stopped time excluded (that
# is priced separately below). From 63 km of trace on 2026-08-14.
#
# **One number, because the roads agree.** Fitted per class, the surface roads
# came out 0.86 / 0.89 / 0.93 for primary / tertiary / secondary, which looks
# like three facts about three kinds of road. It was not: `SPEED_KMH` had the
# posted limits wrong by different amounts on each, and the factor was quietly
# absorbing that. With the limits read off the tagged roads instead (see
# graph.py) the same traces give **0.94 / 0.94 / 0.95** — the same number three
# times, from 52 km of driving.
#
# That is worth more than three fitted constants. It generalises: `residential`
# has 0.9 km of trace behind it and 41,348 km of network, and a rule backed by
# every class that *was* measured is better evidence for it than its own noise
# (which says 1.16, and which is a tenth of a percent of the drive). Anything
# not named below gets the surface-road number.
#
# Motorway is the one real exception and stays measured: drivers exceed the
# posted limit by 16%, and 97% of motorway km carry a real `maxspeed` tag, so
# that is measured against the sign rather than against a fallback. An ETA
# predicts what the driver will do, not what the sign says.
SPEED_FACTOR = {"motorway": 1.16}
SURFACE_SPEED_FACTOR = 0.95

# --- Unpaved roads -----------------------------------------------------------
# Minutes a driver is assumed willing to spend to avoid one km of dirt road, at
# the default setting. Multiplied by the request's `avoid_unpaved` (0..2, 1.0 =
# this number) and added to the Dijkstra weight *outside* the `pref` term.
#
# **Outside `pref` is the whole point.** This used to be `UNPAVED_ADJ = -0.25`
# inside the scenery score, which put it inside
# `pref**PREF_CURVE * BETA * km * (1 - score/10)` — so it charged
# `pref**2 * 8.0 * 0.25` min/km, i.e. **0.00 at pref 0 and 2.00 at pref 1**.
# A driver on the fastest setting got no dirt avoidance at all, and a driver
# asking for maximum beauty got the most: raising the slider from 0.5 to 1.0
# removed two thirds of the dirt from a Vermont loop (23.7% -> 8.2% of loop km,
# 40 km loops from six Vermont starts). Wanting scenery and minding a dirt road
# are different questions and now have different controls.
#
# **1.0 rather than 0.** The beauty evidence says these roads are pretty
# (docs/unpaved-and-urban-verdict.md) but it is evidence about scenery, not
# about what drivers want, and there is not one drive mark on unpaved road in
# `traces/`. So this is set to preserve behaviour, not to change it: over the
# same loops 1.0 min/km gives 15.0% / 17.2% dirt at pref 0.5 / 1.0 against the
# old constant's 23.7% / 8.2% — mean 16.1% against 16.0%. Same average
# exposure, no longer wired to the wrong knob. Re-fit it against marks from
# Vermont dirt country when there are any; that measurement is specified in the
# verdict doc and is the one thing that would justify moving it.
UNPAVED_AVOID_MIN_PER_KM = 1.0

# Ceiling on the request's multiplier. 2.0 reproduces the old constant's
# strength at pref 1.0, which is the hardest this has ever avoided dirt.
MAX_AVOID_UNPAVED = 2.0

# Seconds lost per traffic control *met* — P(stop) and the delay when you do
# stop, folded into the one number a static graph can charge. Fitted by
# `tools/fit_junction_cost.py` over all eight recorded drives: signals were met
# 127 times for 47 stops averaging 31.3 s, stop signs 18 times for 11 stops
# averaging 13.3 s.
#
# These are an average over a quiet hour and a busy one, and that is the most a
# static graph can be. The spread is real and it is the reason to pool: fitting
# a single drive alone gives anywhere from 2.7 s to 16.1 s per signal. The
# location of a signal is structural; the wait at it is not. Left one drive out
# at a time, the pooled figure moves only between 10.0 s and 13.8 s, and the
# fitted model predicts a held-out drive to 15% where free-flow manages 25%.
# See docs/junction-timing-plan.md §10.
#
# Was 9.5 s / 9.3 s, fitted on 2026-08-15 from the first two drives alone. Six
# more drives moved the signal up and the stop sign down. Note that the interim
# figure the fit reported on 2026-08-24 — 18.7 s per signal — was an artifact:
# `fit_junction_cost.py` was not applying the `parked` flag, so one drive's
# 7.1-minute lunch stop was charged to the signal it parked beside. Held out
# properly, the same six drives say 13.0 s and all eight say 11.5 s.
#
# Give-ways are the one number here that is a judgement rather than a
# measurement — eight drives have met none. Half a stop sign, on the grounds
# that yielding is cheaper than stopping and that charging zero is a known error
# in a known direction. Massachusetts has 839 of them against 17,567 stop signs,
# so the choice moves an ETA by well under a tenth of a percent either way.
CONTROL_SECONDS = {"signal": 11.5, "stop": 8.1, "giveway": 4.7}

# The scenery penalty saturates — past a few minutes-per-km the router has taken
# every detour worth taking — which used to leave the slider's top half handing
# back an identical route. Most of that was really the compressed score scale
# (see RAW_BASE in score.py): with the full 0-10 range in play the penalty
# discriminates enough that a near-linear slider already spreads well.
#
# This was 1.3, on a measurement over four routes that exponents above ~1.5
# "overcorrect, trading the dead top for a dead bottom". That was true and it
# was true *of BETA = 7*: a steep curve weakens every pref below 1.0, so with a
# small BETA it empties the bottom. The two constants trade against each other
# exactly that way, and the sweep above BETA settles both at once — at 8.0 a
# curve of 2.0 leaves the bottom alive (32% of the gain at pref 0.25) and is the
# only setting that gets a real share into the back half of the travel.
PREF_CURVE = 2.0

# --- Beauty types -----------------------------------------------------------
# The six *tunable* beauty types — the kinds of scenery a driver would actually
# choose between. One row per type:
#     (api name, display label, edge column, default weight)
# The default weight is the calibrated value from score.py's WEIGHTS, so a user
# weight of 1.0 reproduces the original composite score exactly; >1 leans into
# that type, 0 ignores it.
#
# The display labels are mirrored on the client in ios/Sources/Models.swift
# (RouteProps.sceneryBreakdown) — keep the label set in sync.
BEAUTY_TYPES = [
    ("water",  "water",       "c_water",  WEIGHTS["water"]),
    ("coast",  "coast",       "c_coast",  WEIGHTS["coast"]),
    ("forest", "forest/park", "c_forest", WEIGHTS["forest"]),
    ("hills",  "hills",       "c_relief", WEIGHTS["relief"]),
    ("farm",   "farmland",    "c_farm",   WEIGHTS["farm"]),
    ("town",   "town",        "c_urban",  WEIGHTS["urban"]),
]

# The calibrated weight of each tunable type, in BEAUTY_TYPES order. Its sum is
# the "weight mass" a user's slider settings are renormalized back onto — see
# Router._edge_scores.
DEFAULT_WEIGHTS = np.array([w for *_, w in BEAUTY_TYPES])

# Baseline "quality" signals: always on, not user-tunable. Twistiness, mapped
# viewpoints, and explicit scenic tags form a floor of beauty under every road,
# so a fine road no one toggled on is never scored flat zero.
BASELINE = [
    ("c_curves",     WEIGHTS["curves"]),
    ("c_views",      WEIGHTS["views"]),
    ("c_scenic_tag", WEIGHTS["scenic_tag"]),
]

# Minimum component value for a stretch of road to count toward a beauty type in
# the route summary. It has to sit *below* the smallest partial-credit band any
# component awards, or that band is silently invisible: score.py gives water 0.45
# at 120-350 m and towns 0.5 in a settlement's wider orbit, and the old 0.5
# threshold dropped every metre of the water band — 18% of the network's km — so
# a route hugging a river 200 m away reported "water: 0 mi".
#
# 0.4 rather than exactly 0.45, because graph.py averages components over an
# edge's length: a stretch that is mostly-but-not-entirely in the band lands
# just under it. The cost of the margin is that `hills`, the one component
# that is continuous rather than banded, now counts 40 m of local relief
# instead of 50 m (25.9% of the network's km rather than 16.0%). That is a
# taste call either way; the water band being invisible was not.
# `test_breakdown_threshold_admits_every_partial_credit_band` is the tripwire if
# a DIST band in score.py is ever retuned below this.
BREAKDOWN_MIN = 0.4

# `c_forest` is blended, not banded: 0.5 * (OSM green polygon) + 0.5 * (measured
# tree fraction). An OSM polygon on its own scores 0.5 and still clears
# BREAKDOWN_MIN, so nothing that counted as green before stops counting — but a
# road through genuinely wooded land that OSM never drew a polygon around needed
# a tree fraction of 0.8 to clear 0.4, while the router was already steering
# toward it from 0.0 upward through the continuous pref_matrix. 27% of
# Massachusetts road-km has no polygon at all, so that gap showed as
# "forest/park: 0 mi" on routes picked partly *for* their forest — the same
# complaint d9ea612 fixed for water and town. 0.25 is "no polygon, but the
# ~100 m box around the road is majority canopy", which is what the credit is for.
FOREST_BREAKDOWN_MIN = 0.25

# Per-column cuts for the components that are continuous rather than banded, for
# which BREAKDOWN_MIN — the lowest partial *band* — is not a meaningful boundary.
BREAKDOWN_OVERRIDE = {"c_forest": FOREST_BREAKDOWN_MIN}

# The route-summary breakdown: km of road passing each beauty type. Derived from
# BEAUTY_TYPES so the two never drift.
SCENERY_BREAKDOWN = [(label, col, BREAKDOWN_OVERRIDE.get(col, BREAKDOWN_MIN))
                     for _, label, col, _ in BEAUTY_TYPES]

# The score at or above which a road counts as properly beautiful, for the
# headline "19 of your 40 km" number. Chosen because it separates a scenic loop
# from a fast one 188-fold (18.8 km against 0.1 km at a Needham 40 km target)
# where the means only manage 6.03 against 1.94.
#
# Lives here rather than in looper.py — where it was defined, and is still
# importable from, for `server/app.py` — because point-to-point routes report
# the same number now. One definition, because two would drift and the whole
# point of the threshold is that a loop's "beautiful km" and a route's mean the
# same thing.
BEAUTIFUL_SCORE = 7.0

_TO_M = Transformer.from_crs(4326, CRS_METERS, always_xy=True)

# How much further than the nearest road `snap` will look when it has a
# heading to judge with. Wide enough to reach the carriageway above or below
# at a grade separation, narrow enough that a well-aligned road across the
# street can never win.
SNAP_HEADING_SLACK_M = 20.0
# How far a road may run from the driver's heading and still be the road they
# are on. Compared without direction (a road carries traffic both ways), so
# this is measured on 0..90.
SNAP_HEADING_DEG = 40.0


class Router:
    # Columns the maneuver generator needs, and the graph build that writes
    # them. Checked at load and refused loudly, rather than degraded silently:
    # a graph built before the maneuver rework still *loads* and still routes,
    # so the failure would be a server quietly answering every rotary with a
    # slight right and every exit with nothing — which is the exact
    # silent-disagreement case server/DEPLOY.md exists to warn about.
    REQUIRED_EDGE_COLUMNS = ("junction", "dest_ref", "dest_name", *CONTROL_COLUMNS)
    REQUIRED_NODE_COLUMNS = ("exit_ref",)

    def __init__(self, processed_dir: str):
        d = Path(processed_dir)
        self.edges = gpd.read_parquet(d / "graph_edges.parquet")
        self.nodes = pd.read_parquet(d / "graph_nodes.parquet")
        self.restrictions = self._read_restrictions(d)
        self._require_columns()

        ids = self.nodes["node_id"].to_numpy()
        self.idx = {nid: i for i, nid in enumerate(ids)}
        self.n = len(ids)
        self._nx, self._ny = _TO_M.transform(
            self.nodes["lon"].values, self.nodes["lat"].values
        )

        # Spatial index over the road *geometry*, not just its junctions, so
        # snap() can find the road a point actually sits on — see snap(). Costs
        # ~1 s to project and ~0.1 s to index, once, at startup.
        self._edge_geom_m = self.edges.geometry.to_crs(CRS_METERS).values
        self._edge_tree = STRtree(self._edge_geom_m)

        self._build_directed()
        self._read_access(d)

    def _read_access(self, d: Path):
        """The parking-lot and driveway layer, used only to place destinations.

        Optional, unlike everything above, and for the opposite reason to the
        restriction table. A missing restriction table produces *wrong* routes
        that look right; a missing access layer produces exactly the behaviour
        this router had before the layer existed — a destination in a car park
        snapped to the nearest public road. That is a worse answer, not a silent
        one, and it must not stop a graph built before 2026-08-23 from serving.
        """
        ways, entries = d / "access_ways.parquet", d / "access_entries.parquet"
        if not (ways.exists() and entries.exists()):
            self._access_geom_m = None
            self._access_tree = None
            self._access_component = None
            self._access_entries = {}
            return
        access = gpd.read_parquet(ways)
        self._access_geom_m = access.geometry.to_crs(CRS_METERS).values
        self._access_tree = STRtree(self._access_geom_m)
        self._access_component = access["component"].to_numpy()
        table = pd.read_parquet(entries)
        self._access_entries = {
            int(c): list(zip(g["lat"].to_numpy(), g["lon"].to_numpy()))
            for c, g in table.groupby("component")
        }

    def access_point(self, lat: float, lon: float):
        """Where a driver actually enters the place at `lat, lon`, or None.

        None means the point is beside a public road already and needs no help:
        either there is no access layer loaded, or the nearest service way is
        further off than the nearest road, which is what a roadside address
        looks like.

        Otherwise the point is inside something — a car park, a campus, a long
        drive — and the answer is the nearest node its service network shares
        with the drivable graph. That shared node is the entrance in the literal
        sense: it is the one place the two geometries meet.

        The defect this exists for, measured on 2026-08-22. Three of five
        destinations were inside mapped car parks. Two snapped to a road with no
        connection to the park at all — one to a cul-de-sac 102 m away whose
        park's only entrance was a secondary road 226 m away in the other
        direction, so the router sent the driver on a 3.2 km loop while they
        drove straight past the entrance they wanted, and re-planned it ten
        times in 160 seconds when they carried on. Snapping *nearest* is the
        bug: what a driver needs is not the closest road but the road they can
        get in from.
        """
        if self._access_tree is None:
            return None
        x, y = _TO_M.transform(lon, lat)
        point = shapely.Point(x, y)
        way = int(self._access_tree.nearest(point))
        to_access = self._access_geom_m[way].distance(point)
        edge = int(self._edge_tree.nearest(point))
        if to_access >= self._edge_geom_m[edge].distance(point):
            return None
        entries = self._access_entries.get(int(self._access_component[way]))
        if not entries:
            # A car park mapped with no connection to any drivable way. There is
            # nothing better to offer than the nearest road, which is what the
            # caller already does.
            return None
        ex, ey = _TO_M.transform([e[1] for e in entries], [e[0] for e in entries])
        best = int(np.argmin((np.asarray(ex) - x) ** 2 + (np.asarray(ey) - y) ** 2))
        return entries[best]

    def snap_destination(self, lat: float, lon: float) -> tuple[int, float]:
        """Snap somewhere a driver is trying to *get to*, via its entrance.

        Separate from `snap` rather than a flag on it, because the two answer
        different questions and only one of them is about where the car is.
        `snap` places a car that is on a road; this places a doorway that may
        be nowhere near one.

        No heading, deliberately: a destination is not travelling anywhere, and
        the entrance is a junction of the drivable network either way.

        The distance returned is still measured from the *pin*, not from the
        entrance, so `SNAP_MAX_M` keeps meaning "is this anywhere near our road
        network" rather than silently becoming "did we find an entrance".
        """
        pin = self.snap(lat, lon)
        entry = self.access_point(lat, lon)
        if entry is None:
            return pin
        node, _ = self.snap(*entry)
        return node, pin[1]

    @staticmethod
    def _read_restrictions(d: Path):
        """The turns OSM forbids, or a loud failure.

        Refused rather than defaulted to empty for the same reason the columns
        above are: a graph with no restriction table routes beautifully and
        tells roughly one long route in five to make a turn that is illegal,
        with every test green and nothing in any log.
        """
        path = d / "turn_restrictions.parquet"
        if not path.exists():
            raise RuntimeError(
                f"{path.name} is missing — this graph was built before the "
                "router read turn restrictions, so it cannot tell a legal turn "
                "from an illegal one. Rerun pipeline/graph.py and copy ALL "
                "THREE parquets over; see server/DEPLOY.md."
            )
        return pd.read_parquet(path)

    def _require_columns(self):
        missing = [c for c in self.REQUIRED_EDGE_COLUMNS
                   if c not in self.edges.columns]
        missing += [c for c in self.REQUIRED_NODE_COLUMNS
                    if c not in self.nodes.columns]
        if missing:
            raise RuntimeError(
                f"the graph is missing {', '.join(missing)} — it was built "
                "before turn-by-turn maneuvers and junction timing needed those "
                "tags. Rerun pipeline/graph.py and copy ALL THREE parquets "
                "over; see server/DEPLOY.md."
            )

    @cached_property
    def is_roundabout(self):
        """Per undirected edge, whether it is part of a rotary."""
        j = self.edges["junction"].astype(str).str.lower()
        return j.isin(("roundabout", "circular")).to_numpy()

    def _count_exits_at_node(self):
        """Per node, how many roads leave it that are not part of a rotary.

        Counted over *outgoing* directed edges, not attached ones: a one-way
        street pointing into a rotary is an entrance, and counting it would put
        "take the 2nd exit" one exit early for every driver who passed one.

        Taken before the graph is expanded for turn restrictions, and kept.
        Afterwards a junction's roads are duplicated once per approach that has
        a restriction, so counting then would report a rotary with five exits as
        having nine and send the driver round it twice.
        """
        leaves = ~self.is_roundabout[self.eidx]
        return np.bincount(self.tail[leaves], minlength=self.n)

    @cached_property
    def maneuver_context(self):
        exit_refs = {}
        refs = self.nodes["exit_ref"].astype(str).to_numpy()
        for i, ref in enumerate(refs):
            if ref and ref != "nan":
                exit_refs[i] = ref
        return ManeuverContext(exit_refs, self.exits_at_node,
                               self.departure_bearings)

    def _apply_turn_restrictions(self, banned):
        """Split each junction that forbids a turn into one node per approach.

        A turn restriction is a property of the *pair* (road you came in on,
        road you leave by), and a Dijkstra over nodes has no memory of the
        first — arriving at a junction, it knows only where it is. Measured
        2026-08-15, that cost 7 of 40 random long Massachusetts routes at least
        one movement the map explicitly forbids.

        The textbook fix is to edge-expand the whole graph: every directed edge
        becomes a node and every turn an edge. That is ruled out here — it
        roughly triples E on a router whose latency scales as E^1.20. But the
        restrictions are sparse, so only the junctions that carry one need
        splitting, and the other 300,000-odd nodes are left exactly as they are.

        For a junction V and an approach `s` that forbids something, a copy of V
        is made, `s` is pointed at the copy instead, and the copy is given only
        the exits `s` is allowed to take. A driver arriving any other way still
        arrives at V itself and can still go anywhere, which is what makes this
        cheap: V keeps its own edges and is still a perfectly good place to
        start a route from.

        **The ordering below is load-bearing.** Every approach is redirected
        before any copy is given its exits, so that a copy's exits inherit the
        redirected head rather than the original node. Done the other way round,
        a driver who reached V through a restricted approach would leave along a
        *duplicate* edge — one that no restriction is keyed to — and evade the
        restriction at the *next* junction along. That is a bug that only shows
        up two junctions away from the thing being fixed.
        """
        # Identity until proven otherwise, so callers never have to ask whether
        # the graph was expanded.
        self.real_node = np.arange(self.n)
        self.node_copies = {}
        if banned.empty:
            return

        by_edge = self._group(self.eidx, len(self.edges))
        by_tail = self._group(self.tail, self.n)

        forbidden = {}
        for via, from_edge, to_edge in banned[["via_node", "from_edge",
                                               "to_edge"]].itertuples(index=False):
            v = self.idx.get(via)
            if v is None:
                continue
            arriving = self._slot(by_edge, from_edge, self.head, v)
            leaving = self._slot(by_edge, to_edge, self.tail, v)
            if arriving is None or leaving is None:
                continue
            forbidden.setdefault(arriving, set()).add(leaving)

        # Phase 1: decide the copies, before anything is rewired.
        plan, next_idx = [], self.n
        for arriving, blocked in forbidden.items():
            v = int(self.head[arriving])
            exits = self._members(by_tail, v)
            legal = [o for o in exits if o not in blocked]
            # Nothing left to take, or nothing actually blocked — either way a
            # copy would only add edges. Stranding an approach with no exit
            # would be worse than the illegal turn: it makes roads unreachable.
            if not legal or len(legal) == len(exits):
                continue
            plan.append((arriving, next_idx, legal))
            next_idx += 1

        if not plan:
            return
        # ...and then thinned, because a split that enforces a turn can also
        # take away the last way into somewhere. See _keep_network_reachable.
        plan = self._keep_network_reachable(plan, by_tail)
        if not plan:
            return
        next_idx = self.n + len(plan)

        real = np.arange(next_idx)
        for arriving, copy, _ in plan:
            real[copy] = self.head[arriving]
        # ...every redirect first (see the docstring), then every exit.
        for arriving, copy, _ in plan:
            self.head[arriving] = copy

        tails, heads, eidx, flip = [], [], [], []
        for _, copy, legal in plan:
            for o in legal:
                tails.append(copy)
                heads.append(self.head[o])      # already redirected if it had to be
                eidx.append(self.eidx[o])
                flip.append(self.flip[o])

        self.tail = np.concatenate([self.tail, np.array(tails, dtype=self.tail.dtype)])
        self.head = np.concatenate([self.head, np.array(heads, dtype=self.head.dtype)])
        self.eidx = np.concatenate([self.eidx, np.array(eidx, dtype=self.eidx.dtype)])
        self.flip = np.concatenate([self.flip, np.array(flip, dtype=bool)])
        self.d_minutes = np.concatenate([self.d_minutes,
                                         self.d_minutes[np.array([o for _, _, legal
                                                                  in plan for o in legal])]])
        self.real_node = real
        self.n = next_idx

        # Which node indices stand for the same junction. A route *to* a split
        # junction may legitimately end at any of them — arriving is never the
        # forbidden part, only continuing — so `route` takes the cheapest.
        copies = {}
        for _, copy, _ in plan:
            copies.setdefault(int(real[copy]), []).append(copy)
        self.node_copies = {v: np.array([v] + c) for v, c in copies.items()}

    def _candidate_ends(self, plan):
        """The (tail, head) the graph would have if `plan` were applied.

        Built off to the side so a plan can be tested before anything is
        committed, and in the same order the commit relies on: every approach
        redirected first, then each copy's exits read the redirected heads.
        """
        head = self.head.copy()
        for arriving, copy, _ in plan:
            head[arriving] = copy
        tails, heads = [], []
        for _, copy, legal in plan:
            for o in legal:
                tails.append(copy)
                heads.append(head[o])
        return (np.concatenate([self.tail, np.array(tails, dtype=self.tail.dtype)]),
                np.concatenate([head, np.array(heads, dtype=head.dtype)]))

    def _strands(self, entry, by_tail, stranded):
        """Whether this split is why some junction lost its last way in."""
        arriving, _, legal = entry
        v = int(self.head[arriving])
        kept = set(legal)
        return any(int(self.head[o]) in stranded
                   for o in self._members(by_tail, v) if o not in kept)

    def _keep_network_reachable(self, plan, by_tail):
        """Drop the splits that would cut part of the network off.

        `graph.py` runs `largest_component` and guarantees the graph it writes
        is one strongly connected piece — but it runs that check on the
        *unexpanded* graph, so it says nothing about the graph this class
        actually routes over. A split takes exits away from an approach, and
        taking exits away can leave a pocket of road with no legal way in.

        Measured on the Massachusetts graph, the unchecked expansion turned 1
        strongly connected component into 120 and left 5 junctions that `snap`
        still returns and `route` then cannot reach — which `server/app.py`
        answers as "no route found between those points" for an address
        plainly on the map, at a snap offset of 0.0 m. That is verbatim the
        failure `largest_component` exists to prevent, re-opened one layer
        below it with nothing checking.

        Same trade-off the per-approach guard above already makes, widened
        from one junction to the whole network: an illegal turn left in is
        cheaper than a road nobody can drive to. On the current graph it gives
        up 7 of 3,795 splits to buy back all 5 junctions, converges in two
        rounds, and costs one strong-components pass each — 90 ms on a 2.0 s
        load, paid once at startup.
        """
        # The copy indices have to stay contiguous from self.n, because the
        # candidate graph is sized off len(plan) and `real` below is an arange
        # over them — so renumber after every thinning, not just at the end.
        renumber = lambda p: [(arriving, self.n + i, legal)
                              for i, (arriving, _, legal) in enumerate(p)]
        plan = renumber(plan)
        for _ in range(8):
            tail, head = self._candidate_ends(plan)
            size = self.n + len(plan)
            g = csr_matrix((np.ones(len(tail)), (tail, head)), shape=(size, size))
            n_comp, label = connected_components(g, directed=True,
                                                 connection="strong")
            if n_comp == 1:
                break
            biggest = np.bincount(label).argmax()
            # A junction every one of whose approaches was redirected keeps no
            # edges under its own index, so it always falls out of the largest
            # component — but `route` arrives at it through one of its copies
            # (see `node_copies`), so it is only really stranded if none of
            # those made it either. Counting those as strandings is counting
            # the mechanism working.
            stands_for = {}
            for arriving, copy, _ in plan:
                stands_for.setdefault(int(self.head[arriving]), []).append(copy)
            stranded = {int(v) for v in np.flatnonzero(label[:self.n] != biggest)
                        if all(label[c] != biggest
                               for c in stands_for.get(int(v), ()))}
            if not stranded:
                break
            kept = [p for p in plan if not self._strands(p, by_tail, stranded)]
            if len(kept) == len(plan):
                # Nothing in the plan explains the strandings, so there is no
                # subset to fall back to. Enforce none of them rather than
                # serve a graph with roads no route can reach.
                #
                # Said out loud, because the consequence is invisible: every
                # restriction in the state goes unenforced and routes go back to
                # instructing turns the map forbids, while the graph still loads
                # and every test still passes. `_strands` only blames a split
                # whose removed exit points *directly* at a stranded node, so a
                # pocket stranded two hops out lands here rather than being
                # thinned. If this ever prints, that one-hop attribution is the
                # thing to widen.
                warnings.warn(
                    f"turn restrictions disabled entirely: {len(stranded)} "
                    "junction(s) lost their last way in and no split in the "
                    "plan explains it, so none of the "
                    f"{len(plan)} splits could be applied. Routes may now "
                    "contain turns OSM forbids.", RuntimeWarning, stacklevel=2)
                return []
            plan = renumber(kept)
        else:
            # Fell out of the loop with the last thinning untested. Returning
            # `plan` here would ship exactly the disconnected graph this
            # function exists to prevent, so check it once more and give the
            # restrictions up if it is still stranding.
            tail, head = self._candidate_ends(plan)
            size = self.n + len(plan)
            g = csr_matrix((np.ones(len(tail)), (tail, head)), shape=(size, size))
            if connected_components(g, directed=True,
                                    connection="strong")[0] != 1:
                warnings.warn(
                    "turn restrictions disabled entirely: the split plan still "
                    "cuts the network into more than one strongly connected "
                    "piece after 8 thinning rounds.", RuntimeWarning,
                    stacklevel=2)
                return []
        return plan

    @cached_property
    def _by_tail(self):
        return self._group(self.tail, self.n)

    @cached_property
    def _dep_bearing(self):
        """Per directed slot, its departure bearing, or NaN until first asked.

        float64 and not float32 deliberately. `ManeuverContext.fork_side`
        identifies the road the route itself takes by `abs(delta - wanted) <
        1e-6`, and float32 carries about 1e-5 degrees here — enough to miss that
        test, stop excluding the route's own road, and turn it into its own
        "straighter rival". The 3 MB saved would buy a spurious fork. At float64
        the cached value is bit-identical to the one computed in place, so
        memoizing changes nothing but the time.
        """
        return np.full(len(self.tail), np.nan)

    def outgoing_slots(self, node_idx):
        """Every directed edge leaving a junction, as slot indices.

        For inspecting a junction rather than routing over it — what a driver
        can see leaving it, which is what `tools/audit_directions.py` compares
        the instructions against.
        """
        return self._members(self._by_tail, node_idx)

    def slot_coords(self, slot):
        """A slot's geometry as [lon, lat] in the direction it is driven."""
        coords = shapely.get_coordinates(self._geom[self.eidx[slot]])
        return coords[::-1] if self.flip[slot] else coords

    def departure_bearings(self, node_idx):
        """The compass bearing of every road leaving a junction.

        What the step generator needs to know whether "carry on" is an
        instruction or a trap. Computed on demand rather than precomputed for
        all 750,000 directed edges, which would cost seconds at every startup to
        answer questions almost none of it is ever asked.

        Memoized per slot, though, because "on demand" was being re-paid on
        every request for values that cannot change: a slot's first
        `TURN_CHORD_M` is a property of the graph, not of the trip. Measured on
        one Boston-Pittsfield request, 1,586 calls here drove 3,590
        `shapely.get_coordinates` calls and 29 ms — 58-60% of all step
        generation, and paid again for the fastest result and the scenic one. It
        matters most on a reroute, where the junctions are the ones already
        asked about: the junction set of a mid-drive reroute overlapped the route
        it replaced 1,026 of 1,026. The table is one float per slot (6 MB, see
        `_dep_bearing` for why not half that) and starts empty, so nothing is
        paid at startup for roads never driven.
        """
        slots = self.outgoing_slots(node_idx)
        cache = self._dep_bearing
        out = []
        for slot in slots:
            cached = cache[slot]
            if not math.isnan(cached):
                out.append(float(cached))
                continue
            coords = self.slot_coords(slot)
            if len(coords) < 2:
                continue        # no bearing to have; not cacheable as a number
            bearing = _bearing_out(coords)
            cache[slot] = bearing
            out.append(bearing)
        return out

    @staticmethod
    def _group(key, size):
        """CSR-style grouping of slot indices by `key`, without building a dict.

        Same shape as the node-pair index below it, and for the same reason: a
        dict keyed by boxed ints over three quarters of a million slots costs
        hundreds of megabytes on a box that already holds the graph twice.
        """
        order = np.argsort(key, kind="stable")
        return order, np.searchsorted(key[order], np.arange(size + 1))

    @staticmethod
    def _members(grouped, key):
        order, start = grouped
        return order[start[key]:start[key + 1]]

    def _slot(self, grouped, edge_row, ends, node):
        """The directed slot of `edge_row` whose `ends` array meets `node`.

        A two-way road expands into two slots; the restriction means the one
        pointing the right way. `ends` is `self.head` to find the approach into
        a junction and `self.tail` to find the exit out of it.
        """
        for s in self._members(grouped, edge_row):
            if ends[s] == node:
                return int(s)
        return None

    def _driving_minutes(self) -> np.ndarray:
        """Per undirected edge, free-flow time corrected to real moving speed.

        Divided rather than multiplied: `SPEED_FACTOR` is measured speed over
        assumed speed, and a road driven at 0.93 of its limit takes 1/0.93 as
        long to cover.

        Applied to the computed `minutes` and not to `graph.py`'s `SPEED_KMH`
        fallback table, which is the trap this whole correction is arranged
        around. The table is consulted only where OSM has no `maxspeed` tag, so
        scaling it would move 3% of motorway km and 60% of secondary — landing
        least on the classes whose factor is best measured, and saying nothing
        about it.
        """
        factor = (self.edges["highway"].map(SPEED_FACTOR)
                  .fillna(SURFACE_SPEED_FACTOR).to_numpy())
        return self.edges["minutes"].to_numpy() / factor

    def _control_minutes(self) -> tuple[np.ndarray, np.ndarray]:
        """Per undirected edge, minutes lost to traffic controls each way.

        `graph.py` charges each control to the edge whose node list contains it
        and to the direction it faces, so the forward and reverse counts differ:
        a stop sign facing northbound traffic is on the southbound driver's road
        and costs them nothing.
        """
        out = []
        for direction in ("fwd", "rev"):
            seconds = sum(cost * self.edges[f"n_{kind}_{direction}"].to_numpy()
                          for kind, cost in CONTROL_SECONDS.items())
            out.append(seconds / 60.0)
        return out[0], out[1]

    def _build_directed(self):
        e = self.edges
        # Resolved once. Reaching it as `self.edges.geometry.values` costs a
        # pandas __getitem__ and a geopandas _get_geometry per call, and
        # slot_coords/_collect between them make a few thousand of those per
        # route — measured at 86% of slot_coords' own cost, all of it spent
        # re-finding the column rather than reading geometry.
        self._geom = e.geometry.values
        ui = e["u"].map(self.idx).to_numpy()
        vi = e["v"].map(self.idx).to_numpy()
        # Kept per undirected edge (not per directed slot) so snap() can pick
        # between the two ends of the road segment it landed on.
        self.edge_u_idx, self.edge_v_idx = ui, vi
        minutes = self._driving_minutes()
        control_fwd, control_rev = self._control_minutes()
        ow = e["oneway"].astype(str).str.lower()

        fwd_ok = ~ow.isin(ONEWAY_REV).to_numpy()
        rev_ok = ~ow.isin(ONEWAY_FWD).to_numpy()

        tails, heads, eidx, flip = [], [], [], []
        for ok, t, hh, f in [(fwd_ok, ui, vi, False), (rev_ok, vi, ui, True)]:
            sel = np.where(ok)[0]
            tails.append(t[sel]); heads.append(hh[sel])
            eidx.append(sel); flip.append(np.full(len(sel), f))
        self.tail = np.concatenate(tails)
        self.head = np.concatenate(heads)
        self.eidx = np.concatenate(eidx)        # back-reference to undirected edge row
        self.flip = np.concatenate(flip)
        self.km = e["length_m"].to_numpy() / 1000.0   # per undirected edge
        # The travel time Dijkstra optimises and RouteResult reports: driving
        # time, plus whatever stopping the direction of travel is charged for.
        # One array, used for both, so the ETA cannot describe a different
        # journey from the one that was chosen.
        self.d_minutes = minutes[self.eidx] + np.where(
            self.flip, control_rev[self.eidx], control_fwd[self.eidx])

        # Counted on the plain graph, before junctions start being duplicated.
        self.exits_at_node = self._count_exits_at_node()
        # ...and then duplicated, which is what makes turn restrictions
        # expressible. Everything below reads tail/head/eidx/flip, so it has to
        # come after this and not before.
        self._apply_turn_restrictions(self.restrictions)

        # Parallel edges: more than one directed edge can join the same
        # (tail, head) — parallel roads between the same two junctions. scipy's
        # csr_matrix *sums* duplicate coordinates, which would over-charge those
        # hops, so route() collapses each node-pair to its single cheapest edge.
        # Precompute the unique pairs and a slot -> pair-index map once here.
        pairs = np.stack([self.tail, self.head], axis=1)
        unique_pairs, slot_pair = np.unique(pairs, axis=0, return_inverse=True)
        self.u_tail, self.u_head = unique_pairs[:, 0], unique_pairs[:, 1]
        self.slot_pair = slot_pair.ravel()
        self.n_pairs = len(unique_pairs)

        # Inputs for re-scoring each edge live under a user's beauty weights (see
        # _edge_scores). We split the score.py formula into pieces that let us
        # recompute it as one matrix-vector product per request:
        #   base_score   : the always-on baseline blend (curves/views/scenic tag)
        #   pref_matrix  : column k = default_weight_k * component_k, so scaling
        #                  column k by the user's weight leans into that type
        #   score_adj    : the road-class penalty, re-added after stretch
        self.unpaved_frac, self.score_adj = self._load_unpaved(e)
        self.base_score = sum(w * e[col].to_numpy() for col, w in BASELINE)
        self.pref_matrix = np.column_stack(
            [w * e[col].to_numpy() for _, _, col, w in BEAUTY_TYPES]
        )

        # (tail, head) -> directed-edge slots, used in _collect to map a node
        # path back to edges. Parallel roads mean a pair can hold several slots,
        # so this is a CSR-style grouping: the slots for pair p live in
        # _pair_slots[_pair_start[p]:_pair_start[p + 1]], and _pair_key holds
        # each pair's (tail, head) folded into one sorted integer to bisect on.
        #
        # A dict keyed by (tail, head) tuples is the obvious way to write this
        # and cost ~200 MB of the server's ~1 GB — three quarters of a million
        # boxed tuples, boxed ints and one-element lists — plus the eager Python
        # loop that built them at every startup. These arrays are ~18 MB.
        order = np.argsort(self.slot_pair, kind="stable")
        self._pair_slots = order
        self._pair_start = np.searchsorted(self.slot_pair[order],
                                           np.arange(self.n_pairs + 1))
        self._pair_key = self.u_tail.astype(np.int64) * self.n + self.u_head

    def _load_unpaved(self, e):
        """Per-edge unpaved share, and a `score_adj` with no surface term left.

        Two graph vintages. One built since 2026-08-29 carries `unpaved_frac`
        and a `score_adj` that is road class alone, and there is nothing to do.
        One built before it folded `LEGACY_UNPAVED_ADJ` into `score_adj` and
        kept no surface column at all — and re-deriving it there is worth the
        twenty lines, because `data/processed-ne` is 364 MB behind a home
        tunnel and this turns the fix into a restart.

        **The recovery is exact, not an estimate.** `CLASS_ADJ` is a pure
        function of `highway`, which is on every edge, and the old `score_adj`
        was exactly `class_adj + LEGACY_UNPAVED_ADJ * unpaved` — so the residual
        over that constant *is* the fraction, and subtracting it back off leaves
        the class term. Measured on all 998,252 edges of the New England build:
        none outside [0, 1], 94.6% exactly 0 and 5.4% exactly 1 with **nothing
        in between**, per-class shares within 0.2 pp of the `surface` tags in
        `scored_chunks.parquet`. The nearest-chunk join in `graph.py` can in
        principle hand an edge a neighbouring way's `score_adj`, which would
        land off those two values; on this build it never does.

        Fragile in one specific way, which is why `graph.py` now writes the
        column properly: this holds only while `score_adj` is exactly
        `class_adj` plus a surface term. A third addend would corrupt it
        silently, so the clip is a guard rail rather than decoration — and the
        whole branch should be deleted once no deployed graph predates it.
        """
        if "unpaved_frac" in e.columns:
            return e["unpaved_frac"].to_numpy(), e["score_adj"].to_numpy()

        adj = e["score_adj"].to_numpy()
        class_adj = e["highway"].map(CLASS_ADJ).fillna(0.0).to_numpy()
        frac = np.clip((adj - class_adj) / LEGACY_UNPAVED_ADJ, 0.0, 1.0)
        adj = adj - LEGACY_UNPAVED_ADJ * frac
        # The stored `score` column was written with the penalty in it, and
        # `RouteResult.mean_score` and `looper.beautiful_km` both fall back to
        # it. Left alone it would report the old number for a route chosen on
        # the new one — the exact disagreement `_edge_scores` exists to prevent.
        # Rewriting the in-memory frame (never the parquet) keeps every reader
        # on one scale.
        e["score"] = composite(blend(e[components(e)]).to_numpy(), adj)
        return frac, adj

    def _edge_scores(self, weights: dict) -> np.ndarray:
        """Per *undirected* edge 0-10 scenic score under the given beauty weights.

        `weights` maps a BEAUTY_TYPES api-name to a multiplier (1.0 = the
        calibrated default, >1 leans in, 0 ignores); missing types default to
        1.0. Blending the components is the only part done here — the 0-10
        transform is score.py's `composite`, imported rather than re-derived so
        the live score and the precomputed column cannot drift apart. At
        all-1.0 weights this equals the precomputed `score` column exactly.

        The weight vector is renormalized to hold the *total* tunable weight
        constant, so these knobs change the scenery mix and `pref` alone sets
        the strength — which is what the tune screen tells the user they do.
        Without it the sliders quietly destroy the scale they feed: pushing all
        six to the app's maximum pinned 17% of the state's road-km at exactly
        10.0, and the router's penalty is `km * (1 - score/10)`, so every pinned
        road became free and indistinguishable from every other. Cranking
        everything then returned a route no more scenic than neutral and 5 km
        longer — the "more of everything" request making the result worse.
        Renormalizing maps that request back to "no preference", which is what
        it means. All-1.0 is a fixed point, so the precomputed column is
        untouched.
        """
        w = np.array([weights.get(name, 1.0) for name, *_ in BEAUTY_TYPES], float)
        mass = float(w @ DEFAULT_WEIGHTS)
        if mass > 0:
            w = w * (DEFAULT_WEIGHTS.sum() / mass)
        raw = self.base_score + self.pref_matrix @ w
        return composite(raw, self.score_adj)

    def _weights(self, pref: float, scores: np.ndarray,
                 avoid_unpaved: float = 1.0) -> np.ndarray:
        """Directed-edge Dijkstra weights: travel time, a scenery detour cost,
        and a surface avoidance.

        `scores` is the per-undirected-edge 0-10 score from `_edge_scores`,
        passed in rather than recomputed so the route is reported on exactly the
        scale it was optimized against (see `route`).

        The two costs are deliberately not the same shape. Scenery scales with
        `pref`, because that is what `pref` means. Surface does not: whether a
        driver minds a dirt road is a fact about their car and their day, not
        about how much scenery they asked for, and folding it into `pref` made
        the beauty slider double as a dirt-avoidance slider running backwards.
        """
        penalty = self.km * (1.0 - scores / 10.0)           # km of "unscenic" road
        # Clamped because a negative pref raised to a fractional power is a
        # *complex* number in Python ((-0.5) ** 1.3), which would silently poison
        # the whole cost matrix. The API clamps too; this keeps the class safe
        # for its other caller, the CLI.
        strength = max(0.0, min(1.0, pref)) ** PREF_CURVE
        w = self.d_minutes + strength * BETA * penalty[self.eidx]

        avoid = max(0.0, min(MAX_AVOID_UNPAVED, avoid_unpaved))
        if avoid:
            dirt_km = self.km * self.unpaved_frac
            w = w + avoid * UNPAVED_AVOID_MIN_PER_KM * dirt_km[self.eidx]
        return w

    def snap(self, lat: float, lon: float,
             heading: float | None = None) -> tuple[int, float]:
        """Routable node for a lat/lon: (node index, meters to the road).

        Finds the nearest road *segment* first, then takes one of that segment's
        two ends — rather than going straight to the nearest junction. The
        difference is not cosmetic. Graph nodes are junctions, and a house
        mid-block is routinely closer, in a straight line, to a junction on the
        street behind it than to either end of its own street. Snapping to the
        nearest junction outright therefore started 23% of sampled residential
        addresses on a road the driver was not on (measured over 400 blocks; the
        drivers' complaint was "it starts me on a different road than I live
        on"). Going via the segment makes that 0%: both ends of the nearest
        segment are, by construction, on the road you are standing on.

        Which end depends on whether the caller knows where the driver is
        pointing. Planning a trip from a parked car, there is no travel
        direction and the *nearer* end is right. Mid-drive there is, and the
        nearer end is as often as not the junction just passed — so a reroute
        computed from it can legitimately open by sending the driver back the
        way they came, which the first test drive did in fact do. Given
        `heading` (course over ground in degrees, 0=N, clockwise) the end that
        lies more nearly *ahead* is chosen instead.

        Pass `heading` only while actually moving. CoreLocation reports a course
        of -1 when it has no opinion and its course is noise at walking pace; a
        confidently wrong heading is worse here than none, because it points the
        route at the wrong end of the road with no distance check to catch it.
        Out-of-range values are therefore ignored rather than trusted.

        The distance returned is to the road itself, so callers can reject
        points that aren't on the network at all — e.g. a request from outside
        Massachusetts would otherwise silently snap to a border town and return
        a nonsense route.
        """
        x, y = _TO_M.transform(lon, lat)
        point = shapely.Point(x, y)
        aiming = heading if heading is not None and 0.0 <= heading < 360.0 else None
        e = int(self._edge_tree.nearest(point))
        if aiming is not None:
            e = self._aligned_edge(point, e, aiming)
        u, v = int(self.edge_u_idx[e]), int(self.edge_v_idx[e])
        if aiming is None:
            du = (self._nx[u] - x) ** 2 + (self._ny[u] - y) ** 2
            dv = (self._nx[v] - x) ** 2 + (self._ny[v] - y) ** 2
            node = u if du <= dv else v
        else:
            node = self._forward_end(self._edge_geom_m[e], point, u, v, aiming)
        return node, float(self._edge_geom_m[e].distance(point))

    def _aligned_edge(self, point, nearest: int, heading: float) -> int:
        """Which road the driver is on, among the ones they may be standing over.

        `nearest` is picked in plan view from centrelines carrying no z and no
        layer test, so at a grade separation it can name the road *underneath*:
        a driver on I-93 crossing above Albany Street snaps to Albany Street
        whenever its centreline is the closer of the two in plan view. Their
        heading tells the two apart cleanly — the bearings differ by about 90
        degrees — and it is already in hand, so `route` no longer plans from a
        node on a road the car is not on.

        Deliberately conservative in both directions. Only edges within
        `SNAP_HEADING_SLACK_M` of the nearest are looked at, so a well-aligned
        road further away can never win; and the nearest edge is kept unless it
        is *itself* misaligned, so an ordinary snap on an ordinary street does
        not change at all.
        """
        off = self._misalignment(nearest, point, heading)
        if off <= SNAP_HEADING_DEG:
            return nearest
        reach = self._edge_geom_m[nearest].distance(point) + SNAP_HEADING_SLACK_M
        best, best_off = nearest, off
        for c in self._edge_tree.query(point.buffer(reach)):
            c = int(c)
            if c == nearest or self._edge_geom_m[c].distance(point) > reach:
                continue
            c_off = self._misalignment(c, point, heading)
            if c_off < best_off:
                best, best_off = c, c_off
        return best if best_off <= SNAP_HEADING_DEG else nearest

    def _misalignment(self, e: int, point, heading: float) -> float:
        """How far edge `e` runs from `heading` where the driver stands, 0..90.

        Undirected: a road carries traffic both ways, so a tangent 180 degrees
        from the heading is the same road driven the other way. A geometry with
        no tangent to take scores as maximally misaligned rather than winning
        by default.
        """
        bearing = _tangent_at(self._edge_geom_m[e], point)
        if bearing is None:
            return 90.0
        delta = abs(_turn_delta(heading, bearing))
        return min(delta, 180.0 - delta)

    def _forward_end(self, line, point, u: int, v: int,
                     heading: float) -> int:
        """Whichever of a segment's two ends lies ahead *along the road* for a
        driver at `point` travelling on `heading`.

        Compared against the road's own tangent where the driver is standing,
        not against the straight line to each end. Those agree on a straight
        segment and part company on a curved one: a loop ramp that turns
        through more than 90 degrees has its far end *behind* the driver as the
        crow flies while still being the end they are driving toward. Measured
        over 3,857 mid-edge samples, three chose the junction just passed —
        among them a 635 m motorway_link loop — which is the reroute-turns-you-
        around failure `heading` exists to prevent.

        Bearings are taken in projected metres rather than on the sphere.
        `CRS_METERS` is a conformal conic, so a grid bearing differs from a true
        one by the convergence angle — under 1.5 degrees anywhere in
        Massachusetts, against a decision that is 180 degrees wide. The
        approximation is nowhere near the margin.
        """
        tangent = _tangent_at(line, point)
        if tangent is None:
            return u
        # The geometry runs u -> v, so agreeing with the tangent means heading
        # for v.
        return v if abs(_turn_delta(heading, tangent)) <= 90.0 else u

    def route(self, src_idx: int, dst_idx: int, pref: float, weights: dict = None,
              heading: float | None = None, avoid_unpaved: float = 1.0):
        # Scored once, then used for both jobs: choosing the route and reporting
        # it. They used to disagree — the router optimized the live re-blend
        # while RouteResult.mean_score read the stored neutral column, so a user
        # who asked for coast was shown a number computed as though they hadn't
        # (measured 1.9 points apart on a 0-10 scale). That number is the whole
        # output of the tune screen.
        scores = self._edge_scores(weights or {})
        w = self._weights(pref, scores, avoid_unpaved)
        # Collapse parallel edges to the cheapest weight per node-pair, so the
        # cost matrix has one entry per pair (no summed duplicates).
        pair_w = np.full(self.n_pairs, np.inf)
        np.minimum.at(pair_w, self.slot_pair, w)
        g = csr_matrix((pair_w, (self.u_tail, self.u_head)), shape=(self.n, self.n))
        dist, pred = dijkstra(g, directed=True, indices=src_idx,
                              return_predecessors=True)
        # A junction split for turn restrictions stands at several indices, one
        # per approach that forbids something. Any of them is a legitimate place
        # to *arrive* — the restriction is on continuing through, and a route
        # that ends here does not continue — so take whichever is cheapest.
        # The source needs no such treatment: the original index keeps all of
        # the junction's exits, which is right for a driver setting off from it
        # with no direction of arrival to be restricted by.
        targets = self.node_copies.get(dst_idx)
        if targets is not None:
            dst_idx = int(targets[np.argmin(dist[targets])])
        if not np.isfinite(dist[dst_idx]):
            return None
        # reconstruct node path
        path = []
        cur = dst_idx
        while cur != src_idx and cur >= 0:
            path.append(cur)
            cur = pred[cur]
        if cur < 0:
            return None
        path.append(src_idx)
        path.reverse()
        return self._collect(path, w, scores, heading)

    def _collect(self, path, w, scores, heading=None):
        """Turn a Dijkstra node path into the chosen edges, in travel order.

        Dijkstra hands back a sequence of node indices. For each hop (a -> b) we
        look up the directed edge(s) joining them and keep the one the cost
        actually used — the cheapest under the same weights `w` Dijkstra saw, so
        the drawn geometry and scenery match the chosen parallel road — then
        gather that edge's row and geometry (reversed if we drove it backwards).
        The edges come out in travel order, which is exactly what the
        turn-by-turn step list walks over to emit "turn onto X" maneuvers.
        """
        hops = np.asarray(path, dtype=np.int64)
        keys = hops[:-1] * self.n + hops[1:]
        pairs = np.searchsorted(self._pair_key, keys)

        chosen = []
        for hop, pair in enumerate(pairs):
            if pair >= self.n_pairs or self._pair_key[pair] != keys[hop]:
                # Every (tail, head) Dijkstra can traverse was indexed from the
                # same arrays, so this is unreachable. Skipping the hop silently
                # would be the worst way to be wrong about that: the drawn line
                # would jump the gap and the distance would be under-reported,
                # with nothing anywhere saying so.
                raise RuntimeError(f"no directed edge for hop {hops[hop]} -> "
                                   f"{hops[hop + 1]}; graph index is inconsistent")
            slots = self._pair_slots[self._pair_start[pair]:self._pair_start[pair + 1]]
            chosen.append(int(slots[np.argmin(w[slots])]))

        # The undirected edge rows (stats, names, geometry) in travel order.
        edge_rows = [self.eidx[k] for k in chosen]
        rows = self.edges.iloc[edge_rows]

        # Stitch the per-edge geometries into one line, flipping any edge we
        # traversed against its stored direction so the points run start -> end.
        coords = []
        for k in chosen:
            c = shapely.get_coordinates(self._geom[self.eidx[k]])
            if self.flip[k]:
                c = c[::-1]
            coords.append(c)
        # `coords` is the per-edge geometry in travel order; the steps generator
        # uses it (with the edge names) to build maneuvers. `path` goes with it
        # because two of those maneuvers are properties of the *junctions*
        # rather than the roads: which numbered exit this is, and how many roads
        # you pass going round a rotary.
        #
        # The per-hop travel time Dijkstra actually weighted, carried rather
        # than re-derived. Summing the edge table's `minutes` instead would
        # report a free-flow, direction-blind number for a route chosen on a
        # corrected, directional one — the same silent disagreement that once
        # had `mean_score` reporting the neutral score for a route optimised
        # under the user's beauty weights.
        # Back to real junctions before anything reads them. `path` may run
        # through the per-approach copies that turn restrictions created, and
        # everything downstream — exit numbers, rotary exit counts — is keyed by
        # the junction itself.
        real = [int(self.real_node[p]) for p in path]
        return RouteResult(rows, stitch(coords), coords, scores[edge_rows],
                           nodes=real, context=self.maneuver_context,
                           edge_minutes=self.d_minutes[chosen],
                           start_heading=heading)


def stitch(coord_arrays):
    if not coord_arrays:
        return None
    out = [coord_arrays[0]]
    for c in coord_arrays[1:]:
        out.append(c[1:] if len(c) > 1 else c)
    return shapely.LineString(np.vstack(out))


# --- Turn-by-turn maneuver helpers ------------------------------------------

_COMPASS = ["north", "northeast", "east", "southeast",
            "south", "southwest", "west", "northwest"]


def _bearing(p, q):
    """Compass bearing in degrees (0=N, 90=E) along the ground from lon/lat
    point p to point q."""
    lon1, lat1, lon2, lat2 = map(math.radians, [p[0], p[1], q[0], q[1]])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _compass(bearing):
    """Nearest of the 8 compass directions for a bearing."""
    return _COMPASS[int((bearing + 22.5) % 360 // 45)]


def _tangent_at(line, point):
    """Bearing of `line` where `point` sits on it, or None if it has none.

    The vertex pair the driver is standing between, in projected metres:
    easting/northing, so a compass bearing is atan2(east, north). `CRS_METERS`
    is a conformal conic, so a grid bearing differs from a true one by under
    1.5 degrees anywhere in Massachusetts.
    """
    coords = shapely.get_coordinates(line)
    if len(coords) < 2:
        return None
    step = np.hypot(np.diff(coords[:, 0]), np.diff(coords[:, 1]))
    along = np.concatenate([[0.0], np.cumsum(step)])
    k = int(np.searchsorted(along, line.project(point), side="right")) - 1
    k = min(max(k, 0), len(coords) - 2)
    dx, dy = coords[k + 1] - coords[k]
    if dx == 0.0 and dy == 0.0:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _turn_delta(bearing_in, bearing_out):
    """Signed heading change in degrees, -180..180. Positive = right turn
    (bearings increase clockwise)."""
    return (bearing_out - bearing_in + 180) % 360 - 180


# How far back from a junction the approach heading is measured, and how far
# past it the departure heading is.
#
# Not the adjacent vertex pair, which is what this used to use. OSM carries a
# vertex roughly every 19 m but packs them far tighter through a junction, to
# shape the corner: measured across the MA graph, 29% of edge ends have their
# last two vertices under 10 m apart and 10% under 5 m. A bearing taken over
# 4 m of geometry is mostly digitizing noise — the same defect that had
# curvature rating cul-de-sacs above the Mohawk Trail, in a function that never
# got the fix.
#
# And the noise is *biased*, which is what made it a user-visible bug rather
# than jitter: the approach geometry curves into the turn, so the final few
# metres already point round the corner, the measured heading change comes out
# too small, and a real turn was announced as "Continue". A chord long enough
# to clear the corner rounding, short enough not to swallow the turn itself.
TURN_CHORD_M = 25.0

# How far the first step may depart from the driver's own course before it is
# described as a maneuver rather than as a compass heading, in degrees.
#
# 45 and not lower because `_bearing_out` takes its chord over the first 25 m of
# the route, and on a curving road that chord is a few degrees off the tangent
# the car is actually on. Announcing "Slight right onto Bedford Street" to a
# driver already on Bedford Street would be a false instruction bought for
# nothing — at 45 the modifier is a real turn (`_turn_modifier`'s "left",
# "right", "sharp" and "uturn" bands all sit at or above it) and the compass
# form covers everything gentler.
DEPART_TURN_DEGREES = 45.0

# How much road behind a junction is gathered before that chord is taken off it.
#
# The chord alone is not enough, because the road it is measured on may be
# shorter than the chord: `build_edges` splits a way at every junction, so a
# short block between two of them is a whole edge, and `_bearing_in` falls back
# to whatever it has — which on a 10 m block is the corner rounding again. That
# does not matter much for deciding whether a turn is "slight"; it matters a
# great deal for `fork_side`, which compares the route's heading against every
# other road at the junction and is deciding whether to say anything at all.
# Measured, taking the heading over one short edge missed forks on 12% of
# routes that gathering 60 m of approach then finds.
APPROACH_M = 60.0

# Two instructions closer together than this cannot both be acted on. At 50 km/h
# a driver covers 14 m a second, and hearing an instruction, finding the road and
# moving across for it needs several of those.
#
# This does not mean a route never has two junctions that close — plenty do, and
# both maneuvers are real. It means the *router* must not manufacture the
# crowding by describing one junction twice.
MIN_INSTRUCTION_GAP_M = 60.0


def _dist_m(p, q):
    """Metres between two [lon, lat] points, on the flat.

    Called per vertex over a few tens of metres, where a spherical formula buys
    millimetres and costs a trig call per candidate.
    """
    mid_lat = math.radians((p[1] + q[1]) / 2.0)
    return math.hypot((q[0] - p[0]) * 111320.0 * math.cos(mid_lat),
                      (q[1] - p[1]) * 110540.0)


def _bearing_in(coords):
    """Heading on arrival at the end of `coords`, over a `TURN_CHORD_M` chord."""
    end = coords[-1]
    # Walked by index: the scan stops after a few vertices, and `coords` can be
    # a merged leg of several hundred, so slicing and reversing it copies the
    # whole thing twice to read three of it.
    for k in range(len(coords) - 2, -1, -1):
        if _dist_m(coords[k], end) >= TURN_CHORD_M:
            return _bearing(coords[k], end)
    # Shorter than the chord: the whole leg is the best baseline there is.
    return _bearing(coords[0], end)


def _approach(legs):
    """The road leading into the seam after `legs`, back at least APPROACH_M.

    Walks back across leg boundaries rather than trusting the last one to be
    long enough. See APPROACH_M.
    """
    coords = list(legs[-1]["coords"])
    k = len(legs) - 2
    while k >= 0 and _dist_m(coords[0], coords[-1]) < APPROACH_M:
        coords = list(legs[k]["coords"])[:-1] + coords
        k -= 1
    return coords


def _bearing_out(coords):
    """Heading on departure from the start of `coords`, over the same chord."""
    start = coords[0]
    for k in range(1, len(coords)):
        if _dist_m(start, coords[k]) >= TURN_CHORD_M:
            return _bearing(start, coords[k])
    return _bearing(start, coords[-1])


def _turn_modifier(bearing_in, bearing_out):
    """Which way the road turns, as a maneuver modifier.

    The vocabulary is OSRM's and Valhalla's, not prose — see `RouteResult.steps`
    for why the wire format is a type plus a modifier rather than a sentence.
    """
    delta = _turn_delta(bearing_in, bearing_out)
    magnitude = abs(delta)
    if magnitude < 20:
        return "straight"
    side = "right" if delta > 0 else "left"
    if magnitude < 45:
        return f"slight {side}"
    if magnitude < 120:
        return side
    if magnitude < 160:
        return f"sharp {side}"
    return "uturn"


_MODIFIER_PHRASE = {
    "straight": "Continue",
    "slight left": "Slight left", "left": "Turn left", "sharp left": "Sharp left",
    "slight right": "Slight right", "right": "Turn right",
    "sharp right": "Sharp right", "uturn": "Make a U-turn",
}


# The classes you "exit" rather than "turn off", and "merge" onto rather than
# "continue" onto.
_GRADE_SEPARATED = ("motorway", "trunk")


def _leg_kind(highway: str, junction: str) -> str:
    """How a stretch of road behaves for the purpose of describing it.

    A rotary and a slip road are not turns onto a differently-named street, and
    describing them as though they were is what produced "Slight right" for a
    motorway exit and nothing at all for a rotary.

    Only a *grade-separated* link is an exit, though. OSM tags every slip lane
    `*_link`, and 18% of the ones a route uses are 40-90 m at-grade connectors
    between ordinary streets — a corner cut across, not a junction you leave
    the highway by. Called ramps they came out as "Take the exit onto Ocean
    Street" over a `sharp left`, which is both the wrong words and, because
    `_describe_ramp` keeps the modifier, the wrong arrow beside them.
    """
    if junction in ("roundabout", "circular"):
        return "roundabout"
    if highway.endswith("_link") and highway[:-len("_link")] in _GRADE_SEPARATED:
        return "ramp"
    return "road"

_ORDINALS = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"]


def _ordinal(n: int) -> str:
    """'2nd', for counting rotary exits. Beyond the table, a rotary with nine
    exits is a roundabout interchange and the number is more use than the word.
    """
    return _ORDINALS[n] if 0 <= n < len(_ORDINALS) else f"{n}th"


class ManeuverContext:
    """What the step generator needs to know about the graph beyond the route.

    Two questions the route alone cannot answer. Which numbered exit a junction
    is — that lives on the mainline node, not on any edge of the route. And how
    many roads leave each node of a rotary — which needs the *whole* graph's
    adjacency, since the roads you pass without taking are by definition not on
    your route, and they are exactly what "take the 2nd exit" counts.
    """

    def __init__(self, exit_ref: dict, exits_at_node, departures=None):
        self.exit_ref = exit_ref                # node index -> "26", "13A"
        self.exits_at_node = exits_at_node      # node index -> outgoing non-rotary roads
        # node index -> bearings of every road leaving it. A callable rather
        # than a table; see Router.departure_bearings.
        self.departures = departures

    def numbered_exit(self, node_idx):
        return self.exit_ref.get(node_idx, "")

    def leaves_the_rotary(self, node_idx):
        return bool(self.exits_at_node[node_idx])

    def fork_side(self, node_idx, arrival, taken):
        """Which way to keep when a *straighter* road leaves the same junction.

        Returns "left", "right", or "" when carrying straight on is right.

        This is the failure the route itself cannot see. Every instruction can
        be correct and the driver still end up on the wrong road, because a road
        that keeps its name is allowed to bend at a junction while a side road
        carries on dead ahead — and a driver who is told nothing does what the
        wheel is already doing. Measured over 60 routes before this existed, it
        happened on 78% of them.

        Only a *straighter* rival counts. A road peeling off at 40 degrees while
        the route goes straight needs no instruction; nobody drifts onto it.
        """
        if self.departures is None:
            return ""
        wanted = _turn_delta(arrival, taken)
        best = None
        for bearing in self.departures(node_idx):
            delta = _turn_delta(arrival, bearing)
            if abs(delta) > 150:
                continue                      # the road you arrived on
            if abs(delta - wanted) < 1e-6:
                continue                      # the road the route takes
            if abs(delta) < abs(wanted) and (best is None or abs(delta) < abs(best)):
                best = delta
        if best is None:
            return ""
        return "left" if wanted < best else "right"


class RouteResult:
    def __init__(self, edge_rows: gpd.GeoDataFrame, line, edge_coords=None,
                 scores=None, nodes=None, context: "ManeuverContext" = None,
                 edge_minutes=None, start_heading=None):
        self.edges = edge_rows
        self.line = line
        # Per-edge travel time in travel order, as weighted. None falls back to
        # the stored free-flow column, which is right only for a result built by
        # hand — see `minutes`.
        self.edge_minutes = edge_minutes
        # Per-edge [lon, lat] arrays in travel order (parallel to edge_rows),
        # used to build turn-by-turn steps. None for callers that don't need them.
        self.edge_coords = edge_coords or []
        # Per-edge 0-10 score under the beauty weights this route was chosen
        # with, aligned to edge_rows. None falls back to the stored neutral
        # column, which is right only when no weights were applied.
        self.scores = scores
        # Node indices in travel order, one longer than `edges`: the junction
        # each edge starts at, plus the destination. Exit numbers hang off
        # these, so without them a route still describes its turns but never
        # names an exit.
        self.nodes = list(nodes) if nodes is not None else []
        self.context = context
        # The driver's course when this route was asked for, or None if it was
        # planned from a standstill. Only the first step reads it — see
        # `steps` — and only to say what the driver has to *do*, which a
        # compass departure cannot express to a car already moving.
        self.start_heading = start_heading
        self._steps = None      # memo for steps(); see there

    def _column(self, name):
        """A route column, or blanks if this result was built without it.

        The Router refuses to load a graph missing any of these (see
        `REQUIRED_EDGE_COLUMNS`), so in production they are always present.
        Tests build a `RouteResult` straight from a handful of geometries to
        exercise the turn logic, and should not have to carry every tag to do
        it.
        """
        if name in self.edges.columns:
            return [("" if v is None or (isinstance(v, float) and math.isnan(v))
                     else str(v)) for v in self.edges[name]]
        return [""] * len(self.edges)

    @cached_property
    def km(self):
        return self.edges["length_m"].sum() / 1000.0

    @cached_property
    def minutes(self):
        """How long the drive takes — the number Dijkstra minimised.

        Driving time at the class's measured speed plus the controls the
        traversed direction is charged for, summed over the route. The edge
        table's own `minutes` column is free-flow and direction-blind, and is
        used only by a `RouteResult` built directly in a test.
        """
        if self.edge_minutes is None:
            return self.edges["minutes"].sum()
        return float(np.sum(self.edge_minutes))

    @cached_property
    def mean_score(self):
        L = self.edges["length_m"].to_numpy()
        s = self.edges["score"].to_numpy() if self.scores is None else self.scores
        return float((s * L).sum() / max(L.sum(), 1))

    @cached_property
    def beautiful_km(self):
        """Kilometres of this route on roads scoring `BEAUTIFUL_SCORE` or better.

        The legible half of the pair. `mean_score` is length-weighted across the
        whole trip, so the unavoidable arterial at each end drags a genuinely
        lovely middle down and a driver cannot tell 4.2 from 5.1; this counts
        the road that is actually worth driving and leaves the rest out of it.
        Measured on loops, it separates a scenic drive from a fast one of the
        same length 188-fold where the means manage about 3-fold.

        Read off the *user-weighted* scores when there are any, exactly as
        `mean_score` is, so the fastest and scenic arms of one response are on
        one scale — `server/app.py` scores both with the caller's weights for
        precisely this reason. The corollary is that this is not comparable
        across different weight settings: turning a beauty type off moves the
        scale, so a rise here between two settings is not evidence of a better
        route. Compare `scenery_km`, which is thresholded on raw components, or
        the clock.
        """
        L = self.edges["length_m"].to_numpy()
        s = (self.edges["score"].to_numpy() if self.scores is None
             else np.asarray(self.scores))
        return float(L[s >= BEAUTIFUL_SCORE].sum() / 1000.0)

    def scenery_km(self):
        """Kilometers of this route that pass each kind of scenery (the labels
        in SCENERY_BREAKDOWN), for the breakdown bars in the app."""
        length_km = self.edges["length_m"] / 1000.0
        out = {}
        for label, column, threshold in SCENERY_BREAKDOWN:
            out[label] = float(length_km[self.edges[column] >= threshold].sum())
        return out

    def _legs(self):
        """Group the route's edges into stretches worth one instruction each.

        Consecutive edges merge when they are the same *kind* of road, carry the
        same label, and do not turn sharply at the seam. Each of those three
        conditions is load-bearing:

        - Kind, because a rotary and a slip road are not streets. Merging a
          rotary into the road that fed it is how a rotary produced no
          instruction at all, and merging the segments of a rotary *together*
          is what makes counting its exits possible.
        - Label, because a turn onto a differently-named road is a maneuver.
        - The seam, because a road can turn hard at a junction while keeping its
          name, and a silent merge there swallows a real turn.

        The rotary is the exception to the seam rule: it is a circle, so of
        course it turns, and breaking it at every few degrees would emit a
        stream of slight rights — which is exactly what the first test drive
        got.
        """
        names, refs = self._column("name"), self._column("ref")
        highways, junctions = self._column("highway"), self._column("junction")
        dest_refs, dest_names = self._column("dest_ref"), self._column("dest_name")
        lengths = self.edges["length_m"].to_numpy()
        label = lambda i: names[i] or refs[i] or ""

        legs = []
        for i, pts in enumerate(self.edge_coords):
            kind = _leg_kind(highways[i], junctions[i])
            coords = pts.tolist()
            fork, arrival = "", None
            if legs:
                prev = legs[-1]
                # One arrival heading for the seam, gathered back over
                # APPROACH_M and reused by everything that asks about it: the
                # fork test here, the merge test below, and the turn modifier
                # in `steps`. They used to take their own, and the other two
                # took it over the previous leg alone — which on a block
                # shorter than the chord is the corner rounding TURN_CHORD_M
                # exists to get away from. Measured over 150 routes, that had
                # five turns announced to the wrong side.
                arrival = _bearing_in(_approach(legs))
                departure = _bearing_out(coords)
                # Whether a straighter road leaves this seam — the thing that
                # turns a silent merge into a wrong turn. Asked before merging,
                # because if the answer is yes then merging is exactly what must
                # not happen, however gentle the bend and however unchanged the
                # street name.
                #
                # Not asked when leaving a rotary. The straighter road there is
                # the rotary itself, which the driver has just been told to
                # leave, so the answer is always yes and always useless: 12 of
                # 150 routes said "Take the 1st exit ... onto X" and then, two
                # metres later, "Keep right onto X".
                if (kind != "roundabout" and prev["kind"] != "roundabout"
                        and self.context is not None):
                    fork = self.context.fork_side(
                        self.nodes[i] if i < len(self.nodes) else -1,
                        arrival, departure)
                mergeable = prev["kind"] == kind and (
                    kind == "roundabout" or prev["label"] == label(i))
                if mergeable and not fork and (kind == "roundabout"
                                               or abs(_turn_delta(arrival,
                                                                  departure)) < 45):
                    prev["coords"].extend(coords[1:])      # drop the shared vertex
                    prev["length_m"] += float(lengths[i])
                    prev["end"] = i + 1
                    prev["dest_ref"] = prev["dest_ref"] or dest_refs[i]
                    prev["dest_name"] = prev["dest_name"] or dest_names[i]
                    continue
            legs.append({
                "kind": kind, "label": label(i), "highway": highways[i],
                "fork": fork,
                # The heading the driver arrives on at this leg's start, over
                # APPROACH_M of road behind it. None on the first leg, which
                # nobody arrives at. See the comment above.
                "arrival": arrival,
                # The OSM `name` on its own, without the `ref` fallback. A
                # rotary carrying only a route number is not a rotary *called*
                # that: "Take the 1st exit at MA 122" reads as though the
                # roundabout were named after the highway crossing it.
                "name": names[i],
                "coords": coords, "length_m": float(lengths[i]),
                "start": i, "end": i + 1,
                "dest_ref": dest_refs[i], "dest_name": dest_names[i],
            })
        return legs

    def _roundabout_exit(self, leg):
        """Which exit of a rotary this leg leaves by, or 0 if it can't be known.

        Counted over the nodes the route actually traverses around the circle.
        `build_edges` splits a way at every node two ways share, so every road
        meeting the rotary is a node on it and none can be skipped — which is
        what makes the count trustworthy rather than an estimate.

        The entry node is deliberately excluded: the road you arrived on is not
        one of the exits you pass, and counting it would put every instruction
        one exit late.
        """
        if not self.context or not self.nodes:
            return 0
        passed = 0
        for node_idx in self.nodes[leg["start"] + 1:leg["end"] + 1]:
            if self.context.leaves_the_rotary(node_idx):
                passed += 1
        return passed

    def _exit_number(self, leg):
        """The signed exit number for a ramp leg, if the junction carries one."""
        if not self.context or not self.nodes:
            return ""
        return self.context.numbered_exit(self.nodes[leg["start"]])

    @staticmethod
    def _destination(leg):
        """Where a ramp says it goes, as it would read on the sign.

        OSM separates multiple destinations with semicolons and splits the road
        number (`destination:ref`, "I 93 North") from the places it serves
        (`destination`, "Cambridgeport;Brookline"). Rendered here rather than in
        graph.py so the wording stays with the other labels, and so re-wording
        it never costs a graph rebuild.

        Truncated, deliberately. A big interchange lists everything it serves —
        Massachusetts' worst reads "I 93: South Station / Concord New Hampshire
        / Quincy" — which is a sign you read at 60 mph, not a sentence anyone
        can follow spoken aloud. The road number is the part a driver matches
        against the overhead gantry, so it survives at the expense of the
        places.
        """
        split = lambda s: [p.strip() for p in s.split(";") if p.strip()]
        refs = split(leg["dest_ref"])[:2]
        # One place alongside a road number, two when the number is all we have
        # to go on.
        names = split(leg["dest_name"])[:1 if refs else 2]
        parts = [" / ".join(p) for p in (refs, names) if p]
        return ": ".join(parts)

    def steps(self):
        """Turn-by-turn maneuvers for the client to follow.

        Each step is a *structured* maneuver — a `type`, a `modifier`, and
        whichever of `exit_ref` / `destination` / `roundabout_exit` that type
        needs — with `instruction` as the rendered English alongside it, not
        instead of it. Three reasons the wire format is not just the sentence:
        the app can style an exit differently from a turn, voice guidance needs
        the parts separately ("in 500 feet, take exit 26"), and the vocabulary
        is deliberately OSRM's and Valhalla's, so swapping the routing engine
        later would not move the client.

        The first step sets off, the last announces arrival, and each carries
        the coordinate of its maneuver plus how far that instruction then
        carries you.

        Memoised, because at pref=0 `server/app.py` hands the same RouteResult
        back under both the fastest and the scenic key, and `geojson()` would
        otherwise build the whole list twice off identical data.
        """
        if self._steps is not None:
            return self._steps
        if not self.edge_coords:
            self._steps = []
            return self._steps
        legs = self._legs()
        steps = []

        for i, leg in enumerate(legs):
            pts = leg["coords"]
            previous = legs[i - 1] if i else None
            step = {"type": "continue", "modifier": "straight",
                    "name": leg["label"], "exit_ref": "", "destination": "",
                    "roundabout_exit": 0}

            if previous is None:
                self._describe_start(step, leg, pts)
            else:
                # The arrival heading `_legs` gathered for this seam, not one
                # taken over the previous leg alone — see `_legs`.
                modifier = _turn_modifier(leg["arrival"], _bearing_out(pts))
                step["modifier"] = modifier
                if leg["kind"] == "roundabout":
                    self._describe_roundabout(step, leg, legs[i + 1:])
                elif leg["kind"] == "ramp":
                    self._describe_ramp(step, leg, legs[i + 1:], modifier)
                elif (previous["kind"] == "ramp"
                        and leg["highway"] in _GRADE_SEPARATED):
                    step["type"] = "merge"
                    onto = f" onto {leg['label']}" if leg["label"] else ""
                    step["instruction"] = f"Merge{onto}"
                elif leg.get("fork") and modifier in ("straight", "slight left",
                                                      "slight right"):
                    # A gentle bend past a road that runs straighter. "Continue"
                    # is true and useless here — the driver has to be told which
                    # side to hold. A sharper turn needs no help: "Turn left" is
                    # already unambiguous, so it falls through to the usual
                    # wording below.
                    self._describe_fork(step, leg, previous)
                else:
                    self._describe_turn(step, leg, previous, modifier)

            # Two instructions the driver cannot act on separately, describing
            # one junction. A rotary already names the road you leave on and an
            # exit already names where the ramp goes, so "Continue on X" a few
            # metres later says nothing and arrives mid-manoeuvre — but the rule
            # is about the *gap*, not about what kind of road preceded it. Gated
            # on the previous leg being a rotary or a ramp, it missed the
            # commonest shape of all: a corner cut across a slip lane, emitting
            # "Turn right onto Plymouth Street" and then, 8 m on, "Turn right to
            # stay on Plymouth Street".
            #
            # The same is true when the follow-on is a slight turn rather than a
            # continue — "Take the exit onto Saint James Street" then, 40 m on,
            # "Slight right onto Saint James Street". Measured over 120 routes,
            # 61 of the 408 instruction pairs that land closer together than a
            # driver can act on were this: the same road, named twice.
            #
            # Bounded by the length of the leg being left, because the merge at
            # the end of a two-kilometre ramp is a real event and folding it
            # would leave the driver with nothing to follow for two kilometres.
            # And never for a sharp turn, where which way you go is the
            # instruction and the earlier one did not say.
            if previous is not None:
                # The bound is on the leg being left, and it applies to both
                # ways of folding — a "Continue" is only redundant when the
                # instruction it would follow is still on screen. Measured over
                # 150 routes, charging it to `repeats` alone folded away 104
                # follow-ons after a leg of 60 m or more, one of them a 759 m
                # ramp and one leaving 1,636 m of road with nothing to follow.
                crowded = previous["length_m"] < MIN_INSTRUCTION_GAP_M
                # A "Continue" is not an action, so it earns its place only
                # with room on both sides: enough road behind it that the
                # rotary or exit instruction has been dealt with, and enough
                # ahead that it is not itself superseded before the driver has
                # read it. Folding on the near side alone swaps one crowded
                # pair for another a few metres further on.
                idle = (step["type"] == "continue"
                        and (crowded or leg["length_m"] < MIN_INSTRUCTION_GAP_M))
                # ...but never a fork. That one exists precisely because the
                # road ahead is not the road to take, so folding it back into
                # "the same road, already named" is the bug it was written to
                # fix. Measured: dropping this clause put one misleading fork
                # back into 120 routes.
                repeats = (step["type"] != "fork"
                           and step["name"] and step["name"] == steps[-1]["name"]
                           and step["modifier"] not in ("sharp left", "sharp right",
                                                        "uturn"))
                if idle or (crowded and repeats):
                    steps[-1]["distance_m"] += round(leg["length_m"])
                    continue

            step["lat"] = round(pts[0][1], 6)
            step["lon"] = round(pts[0][0], 6)
            step["distance_m"] = round(leg["length_m"])
            steps.append(step)

        end = legs[-1]["coords"][-1]
        steps.append({"instruction": "Arrive at your destination",
                      "type": "arrive", "modifier": "straight", "name": "",
                      "exit_ref": "", "destination": "", "roundabout_exit": 0,
                      "lat": round(end[1], 6), "lon": round(end[0], 6),
                      "distance_m": 0})
        self._steps = steps
        return steps

    def _describe_roundabout(self, step, leg, following):
        """'Take the 2nd exit at Reid Rotary onto Elm Street'."""
        step["type"] = "roundabout"
        # Going round is not a turn; the exit number is the instruction, and a
        # modifier here would fight it.
        step["modifier"] = "straight"
        nth = self._roundabout_exit(leg)
        step["roundabout_exit"] = nth
        onto = next((f["label"] for f in following if f["kind"] != "roundabout"), "")
        step["name"] = onto or leg["label"]

        where = f" at {leg['name']}" if leg["name"] else ""
        # 693 of MA's 1,234 rotaries are named, so the fallback is common
        # enough to have to read well on its own.
        if nth:
            instruction = f"Take the {_ordinal(nth)} exit{where}"
        else:
            instruction = f"At the roundabout{where}, take your exit"
        if onto:
            instruction += f" onto {onto}"
        step["instruction"] = instruction

    def _describe_ramp(self, step, leg, following, modifier):
        """'Take exit 26 toward I 93 North: Boston'.

        The fallback order is the one that never invents anything: the signed
        exit number if the junction carries one (74% of Massachusetts' numbered
        junctions do), then where the ramp says it goes, then the road it
        actually joins, and only then a bare instruction. A ramp described as
        "Slight right" — which is what every exit used to get, because 96% of
        ramps carry no name and the label fell through to nothing — is
        geometrically true and useless.
        """
        step["type"] = "exit"
        number = self._exit_number(leg)
        destination = self._destination(leg)
        joins = next((f["label"] for f in following if f["kind"] != "ramp"), "")
        step["exit_ref"] = number
        step["destination"] = destination
        step["name"] = leg["label"] or joins

        side = "right" if "right" in modifier else "left" if "left" in modifier else ""
        if number:
            step["instruction"] = f"Take exit {number}"
            if destination:
                step["instruction"] += f" toward {destination}"
        elif destination:
            step["instruction"] = f"Take the exit toward {destination}"
        elif joins:
            step["instruction"] = f"Take the exit onto {joins}"
        else:
            step["instruction"] = f"Take the exit on the {side}" if side else "Take the exit"

    @staticmethod
    def _describe_fork(step, leg, previous):
        """'Keep left to stay on Washington Street'.

        The OSRM vocabulary calls this a fork and pairs it with a slight-side
        modifier, which is what the app needs to draw the right arrow — the
        maneuver is not a turn, and an arrow that says one would be worse than
        none at a junction the driver is already worried about.
        """
        side = leg["fork"]
        step["type"] = "fork"
        step["modifier"] = f"slight {side}"
        label = leg["label"]
        if label and previous and label == previous["label"]:
            step["instruction"] = f"Keep {side} to stay on {label}"
        elif label:
            step["instruction"] = f"Keep {side} onto {label}"
        else:
            step["instruction"] = f"Keep {side}"

    def _describe_start(self, step, leg, pts):
        """The first step: a compass departure, or a maneuver when we know
        which way the car is already pointing.

        "Head southeast on Bedford Street" is the right thing to say to a parked
        driver and useless to a moving one. A reroute is computed from where the
        car is *now*, and the road back to the destination is often the way it
        came — so the opening instruction is routinely a turn, and phrasing it
        as a compass heading leaves the driver no way to know they have to act.

        Measured on the drives of 2026-08-22: across two drives, 15 of 27
        reroutes opened against the driver's own course by more than 90 degrees
        and 9 of them by more than 175 — a U-turn, announced as "Head southeast
        on The Great Road" to a car doing 70 km/h northwest. The driver kept
        going, went off the new route within four seconds, and the reroute fired
        again. Ten times in 160 seconds on one drive.

        `start_heading` is only ever set when the caller sent one, which the app
        does only while actually moving (see `usableHeading` in
        NavigationModel.swift), so a trip planned from a standstill still gets
        the compass form. Below `DEPART_TURN_DEGREES` it gets it too: the route
        opens along the road the car is on, and naming the compass direction
        there is both true and less fussy than "Continue".

        The wire vocabulary is unchanged — `turn` plus an existing modifier, as
        OSRM would send it — so a client built before this renders it correctly
        with no update.
        """
        heading = self.start_heading
        delta = (None if heading is None
                 else _turn_delta(heading, _bearing_out(pts)))
        if delta is None or abs(delta) < DEPART_TURN_DEGREES:
            step["type"] = "depart"
            step["modifier"] = "straight"
            where = f" on {leg['label']}" if leg["label"] else ""
            step["instruction"] = f"Head {_compass(_bearing_out(pts))}{where}"
            return

        modifier = _turn_modifier(heading, _bearing_out(pts))
        step["type"] = "turn"
        step["modifier"] = modifier
        phrase = _MODIFIER_PHRASE[modifier]
        if not leg["label"]:
            step["instruction"] = phrase
        elif modifier == "uturn":
            # You turn around *on* a road, not onto one: a U-turn leaves you on
            # the same carriageway you were already driving.
            step["instruction"] = f"{phrase} on {leg['label']}"
        else:
            step["instruction"] = f"{phrase} onto {leg['label']}"

    def _describe_turn(self, step, leg, previous, modifier):
        phrase = _MODIFIER_PHRASE[modifier]
        if modifier == "straight":
            step["type"] = "continue"
            step["instruction"] = (f"Continue onto {leg['label']}" if leg["label"]
                                   else "Continue")
        else:
            step["type"] = "turn"
            if not leg["label"]:
                step["instruction"] = phrase           # no useful name to offer
            elif leg["label"] == previous["label"]:
                step["instruction"] = f"{phrase} to stay on {leg['label']}"
            else:
                step["instruction"] = f"{phrase} onto {leg['label']}"

    def geojson(self):
        return {
            "type": "Feature",
            "geometry": json.loads(shapely.to_geojson(self.line)) if self.line else None,
            "properties": {
                "km": round(self.km, 1), "minutes": round(self.minutes, 1),
                "mean_score": round(self.mean_score, 2),
                # The headline the app leads with: "31 of your 50 miles". A
                # 0-10 mean is a number nobody has a feel for, and the two arms
                # of one response are scored on one scale, so the pair reads as
                # a plain comparison. `beautiful_score` travels with it so the
                # client can say what the bar was without hardcoding 7.0.
                "beautiful_km": round(self.beautiful_km, 1),
                "beautiful_score": BEAUTIFUL_SCORE,
                "scenery_km": {k: round(v, 1) for k, v in self.scenery_km().items()},
                "steps": self.steps(),
            },
        }


def main(processed_dir, a, b, pref=1.0):
    r = Router(processed_dir)
    (lat1, lon1), (lat2, lon2) = parse_ll(a), parse_ll(b)
    (s, s_off), (t, t_off) = r.snap(lat1, lon1), r.snap(lat2, lon2)
    print(f"snapped to node idx {s} ({s_off:.0f} m off) -> {t} ({t_off:.0f} m off)")

    fast = r.route(s, t, 0.0)
    scenic = r.route(s, t, float(pref))
    if fast is None or scenic is None:
        print("no route found (disconnected)"); return

    out = Path("out"); out.mkdir(exist_ok=True)
    for name, res in [("fastest", fast), ("scenic", scenic)]:
        (out / f"route_{name}.geojson").write_text(json.dumps(res.geojson()))

    fj, sj = fast.geojson()["properties"], scenic.geojson()["properties"]
    print(f"\nFASTEST:  {fj['km']} km, {fj['minutes']} min, "
          f"mean scenic {fj['mean_score']}")
    print(f"SCENIC :  {sj['km']} km, {sj['minutes']} min, "
          f"mean scenic {sj['mean_score']}")
    dt = sj["minutes"] - fj["minutes"]
    print(f"\n+{dt:.0f} min ({100*dt/max(fj['minutes'],1):.0f}% longer), "
          f"scenic {fj['mean_score']} -> {sj['mean_score']}")
    print(f"{'feature':12s} {'fastest':>8s} {'scenic':>8s}   (km near feature)")
    for k in sj["scenery_km"]:
        print(f"   {k:12s} {fj['scenery_km'][k]:8.1f} {sj['scenery_km'][k]:8.1f}")
    print("\nwrote out/route_fastest.geojson, out/route_scenic.geojson")


def parse_ll(s):
    lat, lon = s.split(",")
    return float(lat), float(lon)


if __name__ == "__main__":
    pref = sys.argv[4] if len(sys.argv) > 4 else 1.0
    main(sys.argv[1], sys.argv[2], sys.argv[3], pref)
