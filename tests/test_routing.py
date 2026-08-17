"""Router tests: the turn-by-turn maths (pure) and real routes over the graph.

The routing cases use the built graph and skip without it. They assert the
properties a driver would notice — the scenic route is genuinely more scenic,
the fastest route is genuinely faster, and the slider actually does something
across its whole travel.
"""

import numpy as np
import pytest
import shapely

from router import (BEAUTY_TYPES, BREAKDOWN_MIN, PREF_CURVE, ManeuverContext,
                    RouteResult, Router, _bearing, _compass, _turn_delta,
                    _turn_modifier, stitch)

ALL_TYPES = [name for name, *_ in BEAUTY_TYPES]

BOSTON = (42.3551, -71.0657)
WORCESTER = (42.2626, -71.8023)
CONCORD = (42.4604, -71.3489)


class TestClientContract:
    """The iOS app hardcodes these strings. A rename on one side has to fail a
    suite rather than quietly drop a bar from the app's breakdown."""

    def test_the_client_and_server_agree_on_the_scenery_labels(self):
        # Mirrored by `serverLabels` in ios/Tests/ModelsTests.swift and by
        # RouteProps.sceneryBreakdown's displayOrder in ios/Sources/Models.swift.
        from router import SCENERY_BREAKDOWN
        assert [label for label, *_ in SCENERY_BREAKDOWN] == [
            "water", "coast", "forest/park", "hills", "farmland", "town"]

    def test_the_client_and_server_agree_on_the_beauty_type_names(self):
        # Mirrored by BeautyType.all in ios/Sources/BeautyType.swift; these are
        # what the app's `w_<name>` query parameters are built from.
        assert set(ALL_TYPES) == {"water", "coast", "forest", "hills", "farm", "town"}


class TestBearings:
    def test_cardinal_directions(self):
        assert _bearing((0, 0), (0, 1)) == pytest.approx(0, abs=1)      # north
        assert _bearing((0, 0), (1, 0)) == pytest.approx(90, abs=1)     # east
        assert _bearing((0, 0), (0, -1)) == pytest.approx(180, abs=1)   # south
        assert _bearing((0, 0), (-1, 0)) == pytest.approx(270, abs=1)   # west

    def test_compass_names(self):
        assert _compass(0) == "north"
        assert _compass(91) == "east"
        assert _compass(225) == "southwest"
        assert _compass(359) == "north"

    def test_turn_delta_is_signed_and_wraps(self):
        assert _turn_delta(350, 10) == pytest.approx(20)     # right, across north
        assert _turn_delta(10, 350) == pytest.approx(-20)    # left, across north
        assert abs(_turn_delta(0, 180)) == pytest.approx(180)

    @pytest.mark.parametrize("delta,expected", [
        (0, "straight"), (10, "straight"),
        (30, "slight right"), (-30, "slight left"),
        (90, "right"), (-90, "left"),
        (150, "sharp right"), (-150, "sharp left"),
        (180, "uturn"),
    ])
    def test_turn_modifiers(self, delta, expected):
        """The vocabulary is OSRM's and Valhalla's, so the wire format survives
        a change of routing engine."""
        assert _turn_modifier(0, delta % 360) == expected


class TestStitch:
    def test_joins_without_duplicating_the_shared_vertex(self):
        a = np.array([[0.0, 0.0], [1.0, 0.0]])
        b = np.array([[1.0, 0.0], [2.0, 0.0]])
        joined = shapely.get_coordinates(stitch([a, b])).tolist()
        assert joined == [[0, 0], [1, 0], [2, 0]]

    def test_empty_is_none(self):
        assert stitch([]) is None


class TestSteps:
    def _result(self, coords, names):
        import geopandas as gpd
        import pandas as pd
        rows = gpd.GeoDataFrame(
            {"name": names, "ref": [""] * len(names),
             "length_m": [100.0] * len(names),
             "geometry": [shapely.LineString(c) for c in coords]},
            crs=4326,
        )
        return RouteResult(rows, stitch([np.array(c) for c in coords]),
                           [np.array(c) for c in coords])

    def test_first_step_sets_off_and_last_arrives(self):
        steps = self._result([[(0, 0), (0, 0.01)]], ["Main Street"]).steps()
        assert steps[0]["instruction"].startswith("Head north on Main Street")
        assert steps[-1]["instruction"] == "Arrive at your destination"

    def test_same_road_straight_through_does_not_emit_a_turn(self):
        r = self._result([[(0, 0), (0, 0.01)], [(0, 0.01), (0, 0.02)]],
                         ["Main Street", "Main Street"])
        assert len(r.steps()) == 2      # set off + arrive, no spurious maneuver

    def test_a_real_turn_onto_a_new_road_is_announced(self):
        r = self._result([[(0, 0), (0, 0.01)], [(0, 0.01), (0.01, 0.01)]],
                         ["Main Street", "Elm Street"])
        assert "Turn right onto Elm Street" in [s["instruction"] for s in r.steps()]

    def test_sharp_bend_keeping_the_same_name_still_announces(self):
        """A road can turn hard at a junction without changing name; merging
        those into one leg would silently swallow the maneuver."""
        r = self._result([[(0, 0), (0, 0.01)], [(0, 0.01), (0.01, 0.01)]],
                         ["Main Street", "Main Street"])
        assert any("stay on Main Street" in s["instruction"] for s in r.steps())


# Degrees per metre at the equator, where the fixtures live — near enough for
# geometry whose only job is to have the right angles in it.
_DEG_LAT = 1.0 / 110540.0
_DEG_LON = 1.0 / 111320.0


def _point(east_m, north_m):
    return (east_m * _DEG_LON, north_m * _DEG_LAT)


def _along(bearing_deg, metres):
    rad = np.radians(bearing_deg)
    return np.sin(rad) * metres, np.cos(rad) * metres


class TestManeuverGeneration:
    """The four things the first test drive got wrong."""

    def _result(self, legs, nodes=None, context=None):
        """`legs` is a list of (coords, name, highway, junction, dest_ref,
        dest_name)."""
        import geopandas as gpd
        rows = gpd.GeoDataFrame(
            {"name": [x[1] for x in legs],
             "ref": [""] * len(legs),
             "highway": [x[2] for x in legs],
             "junction": [x[3] for x in legs],
             "dest_ref": [x[4] for x in legs],
             "dest_name": [x[5] for x in legs],
             "length_m": [100.0] * len(legs),
             "geometry": [shapely.LineString(x[0]) for x in legs]},
            crs=4326,
        )
        coords = [np.array(x[0]) for x in legs]
        return RouteResult(rows, stitch(coords), coords,
                           nodes=nodes, context=context)

    def test_a_turn_is_not_reported_as_continue_when_the_corner_is_rounded(self):
        """The defect this guards, and the reason it was user-visible.

        `_turn_phrase` used to take its bearings from the two vertices either
        side of the junction. OSM packs vertices tightly through a corner to
        shape it — 29% of edge ends in the MA graph have their last two under
        10 m apart — so that baseline is mostly digitizing noise. And the noise
        is *biased*: the approach already curves into the turn, so the measured
        change comes out too small and a real turn was announced as "Continue".
        A driver on the first test drive was told to continue straight where
        the map plainly showed a turn.

        Here the approach runs due north for 80 m and then rounds 3 m into the
        corner at 30 degrees. Measured across the rounding alone the turn looks
        like 30 degrees (a slight right); measured over a chord that clears it,
        it is the 60 degrees it really is.
        """
        rounding = _along(30, 3)
        approach = [_point(0, 0), _point(0, 40), _point(0, 80),
                    _point(rounding[0], 80 + rounding[1])]
        onward_end = _along(60, 120)
        onward = [approach[-1],
                  _point(rounding[0] + onward_end[0], 80 + rounding[1] + onward_end[1])]

        steps = self._result([
            (approach, "Old Road", "residential", "", "", ""),
            (onward, "New Road", "residential", "", "", ""),
        ]).steps()

        turn = steps[1]
        assert turn["modifier"] == "right", (
            f"a 60-degree turn came out as {turn['modifier']!r} — the bearing "
            "is being measured across the corner rounding again")
        assert turn["instruction"] == "Turn right onto New Road"

    def test_a_rotary_is_counted_not_described_as_slight_rights(self):
        """A rotary used to merge into one unremarkable leg and emit nothing,
        or break into a run of slight rights. It is a distinct maneuver with an
        exit number, and the count comes from the graph rather than any tag."""
        leg = lambda a, b: [a, b]
        legs = [
            (leg(_point(0, 0), _point(0, 100)), "Approach Road", "primary", "", "", ""),
            (leg(_point(0, 100), _point(10, 110)), "Reid Rotary", "primary", "roundabout", "", ""),
            (leg(_point(10, 110), _point(20, 100)), "Reid Rotary", "primary", "roundabout", "", ""),
            (leg(_point(20, 100), _point(30, 90)), "Reid Rotary", "primary", "roundabout", "", ""),
            (leg(_point(30, 90), _point(120, 0)), "Elm Street", "primary", "", "", ""),
        ]
        # Node indices in travel order, one longer than the edge list.
        nodes = [0, 1, 2, 3, 4, 5]
        # Two of the nodes we pass round the circle have a road leaving them:
        # node 3 (one we go by) and node 4 (the one we leave on).
        exits = np.zeros(6, dtype=int)
        exits[3] = 1
        exits[4] = 1
        context = ManeuverContext({}, exits)

        steps = self._result(legs, nodes=nodes, context=context).steps()
        rotary = [s for s in steps if s["type"] == "roundabout"]
        assert len(rotary) == 1, [s["instruction"] for s in steps]
        assert rotary[0]["roundabout_exit"] == 2
        assert rotary[0]["instruction"] == "Take the 2nd exit at Reid Rotary onto Elm Street"

    def test_the_entry_road_is_not_counted_as_a_rotary_exit(self):
        """Counting the node you arrive at would put every rotary instruction
        one exit late."""
        legs = [
            ([_point(0, 0), _point(0, 100)], "Approach Road", "primary", "", "", ""),
            ([_point(0, 100), _point(10, 110)], "Reid Rotary", "primary", "roundabout", "", ""),
            ([_point(10, 110), _point(20, 100)], "Reid Rotary", "primary", "roundabout", "", ""),
            ([_point(20, 100), _point(110, 10)], "Elm Street", "primary", "", "", ""),
        ]
        exits = np.zeros(5, dtype=int)
        exits[1] = 1        # the entry node: the road we arrived on leaves it too
        exits[3] = 1        # the exit we actually take
        steps = self._result(legs, nodes=[0, 1, 2, 3, 4],
                             context=ManeuverContext({}, exits)).steps()
        rotary = [s for s in steps if s["type"] == "roundabout"][0]
        assert rotary["roundabout_exit"] == 1, "the entry road was counted as an exit"

    def test_a_motorway_exit_is_named_not_called_a_slight_right(self):
        """96% of ramps carry no name, so the label fell through and every exit
        in the state was announced as a bare "Slight right"."""
        legs = [
            ([_point(0, 0), _point(0, 200)], "", "motorway", "", "", ""),
            ([_point(0, 200), _point(*_along(30, 150))],
             "", "motorway_link", "", "I 93 North", "Boston;Quincy"),
            ([_point(*_along(30, 150)), _point(200, 400)], "I 93", "motorway", "", "", ""),
        ]
        context = ManeuverContext({1: "26"}, np.zeros(4, dtype=int))
        steps = self._result(legs, nodes=[0, 1, 2, 3], context=context).steps()
        exit_step = [s for s in steps if s["type"] == "exit"][0]
        assert exit_step["exit_ref"] == "26"
        assert exit_step["instruction"] == "Take exit 26 toward I 93 North: Boston"
        assert any(s["type"] == "merge" for s in steps), "joining a motorway is a merge"

    def test_an_unsigned_ramp_falls_back_to_the_road_it_joins(self):
        """The fallback order never invents anything: exit number, then where
        the ramp says it goes, then the road it actually joins."""
        legs = [
            ([_point(0, 0), _point(0, 200)], "", "primary", "", "", ""),
            ([_point(0, 200), _point(*_along(30, 150))], "", "motorway_link", "", "", ""),
            ([_point(*_along(30, 150)), _point(200, 400)], "Chestnut Street", "primary", "", "", ""),
        ]
        steps = self._result(legs, nodes=[0, 1, 2, 3],
                             context=ManeuverContext({}, np.zeros(4, dtype=int))).steps()
        exit_step = [s for s in steps if s["type"] == "exit"][0]
        assert exit_step["instruction"] == "Take the exit onto Chestnut Street"

    def test_a_long_interchange_destination_is_truncated(self):
        """"I 93: South Station / Concord New Hampshire / Quincy" is a gantry
        you read at 60 mph, not a sentence anyone can follow spoken aloud."""
        legs = [
            ([_point(0, 0), _point(0, 200)], "", "motorway", "", "", ""),
            ([_point(0, 200), _point(*_along(30, 150))], "", "motorway_link", "",
             "I 93;I 95;US 1", "South Station;Concord New Hampshire;Quincy"),
            ([_point(*_along(30, 150)), _point(200, 400)], "I 93", "motorway", "", "", ""),
        ]
        steps = self._result(legs, nodes=[0, 1, 2, 3],
                             context=ManeuverContext({}, np.zeros(4, dtype=int))).steps()
        exit_step = [s for s in steps if s["type"] == "exit"][0]
        assert exit_step["destination"] == "I 93 / I 95: South Station"

    def test_every_step_carries_a_type_and_a_modifier(self):
        """The wire format is a structured maneuver, not a sentence: the app
        styles on the type, voice guidance needs the parts separately, and the
        vocabulary is OSRM's so a change of routing engine would not move the
        client."""
        legs = [
            ([_point(0, 0), _point(0, 100)], "Main Street", "residential", "", "", ""),
            ([_point(0, 100), _point(100, 100)], "Elm Street", "residential", "", "", ""),
        ]
        steps = self._result(legs).steps()
        assert [s["type"] for s in steps] == ["depart", "turn", "arrive"]
        for step in steps:
            assert set(step) >= {"instruction", "type", "modifier", "name",
                                 "exit_ref", "destination", "roundabout_exit",
                                 "lat", "lon", "distance_m"}


class TestStaleGraphIsRefused:
    def test_a_graph_without_the_maneuver_tags_is_refused_loudly(self):
        """A graph built before this rework still loads and still routes — it
        would just answer every rotary with a slight right and every exit with
        nothing. That is the silent-disagreement case DEPLOY.md exists to warn
        about, so it has to be a failure to start, not a quieter route.
        """
        import pandas as pd

        router = Router.__new__(Router)
        router.edges = pd.DataFrame({"u": [1], "v": [2], "name": [""]})
        router.nodes = pd.DataFrame({"node_id": [1], "lon": [0.0], "lat": [0.0]})
        with pytest.raises(RuntimeError, match="junction"):
            router._require_columns()

    def test_a_graph_without_the_control_counts_is_refused_loudly(self):
        """Same reasoning, one rework later. A graph built before junction
        timing routes perfectly well and charges nothing for 29,772 traffic
        signals and stop signs — travel times 22% short, with every test green.
        """
        import pandas as pd

        router = Router.__new__(Router)
        router.edges = pd.DataFrame({"u": [1], "v": [2], "junction": [""],
                                     "dest_ref": [""], "dest_name": [""]})
        router.nodes = pd.DataFrame({"node_id": [1], "lon": [0.0], "lat": [0.0],
                                     "exit_ref": [""]})
        with pytest.raises(RuntimeError, match="n_signal_fwd"):
            router._require_columns()


class TestTurnRestrictions:
    """A Dijkstra over nodes cannot say "not from that road", so the junctions
    that need to say it are split into one node per approach."""

    def test_the_graph_carries_restrictions_and_splits_the_junctions(self, router):
        assert len(router.restrictions) > 1000, "Massachusetts has thousands"
        assert len(router.node_copies) > 100
        # Cheap, which is the whole argument for doing it this way rather than
        # edge-expanding a graph whose latency scales as E^1.20.
        assert router.n < len(router.nodes) * 1.05

    def test_a_split_junction_forbids_the_turn_from_the_restricted_approach(self, router):
        """...and only from that one. The copy exists so the driver arriving one
        way is stopped while everyone else carries on as before."""
        banned = router.restrictions
        by_edge = router._group(router.eidx, len(router.edges))
        # The approach's head is a *copy* by now — that is the whole point — so
        # look the junction up through `real_node` rather than expecting the
        # slot to still point at it.
        arrives_at = router.real_node[router.head]
        checked = 0
        for via, from_edge, to_edge in banned[["via_node", "from_edge",
                                               "to_edge"]].itertuples(index=False):
            v = router.idx.get(via)
            if v is None:
                continue
            arriving = router._slot(by_edge, from_edge, arrives_at, v)
            leaving = router._slot(by_edge, to_edge, router.tail, v)
            if arriving is None or leaving is None:
                continue
            copy = int(router.head[arriving])
            if copy == v:
                continue        # no copy was made; see _apply_turn_restrictions
            reachable = {int(router.eidx[s]) for s in router.outgoing_slots(copy)}
            assert to_edge not in reachable, \
                f"the forbidden turn is still available from the copy at {via}"
            # The junction itself is untouched, so anyone else still gets there.
            assert to_edge in {int(router.eidx[s])
                               for s in router.outgoing_slots(v)}
            checked += 1
            if checked >= 200:
                break
        assert checked > 50, "not enough resolved restrictions to check"

    def test_arriving_at_a_split_junction_still_works(self, router):
        """Arriving is never the forbidden part — only continuing through — so
        a route *to* a split junction must find it from any direction."""
        v = next(iter(router.node_copies))
        lat, lon = router.nodes["lat"].iat[v], router.nodes["lon"].iat[v]
        s, _ = router.snap(*BOSTON)
        t, _ = router.snap(lat, lon)
        assert router.route(s, t, 0.0) is not None

    def test_a_graph_without_the_restriction_table_is_refused(self, tmp_path):
        with pytest.raises(RuntimeError, match="turn_restrictions"):
            Router._read_restrictions(tmp_path)


class TestForks:
    """The failure that does not look like one: every instruction correct, and
    the driver still ends up on the wrong road."""

    def test_a_straighter_road_makes_the_bend_an_instruction(self):
        """A road that keeps its name and bends 15 degrees at a junction, past a
        side road that carries straight on. Merged silently, the app says
        nothing and the wheel takes the driver onto the side road.
        """
        context = ManeuverContext({}, np.zeros(4), lambda node: [0.0, 15.0])
        # Arriving due north, the route leaves on 15 and something leaves on 0.
        assert context.fork_side(1, 0.0, 15.0) == "right"
        assert context.fork_side(1, 0.0, 0.0) == ""      # the route *is* straight

    def test_a_road_peeling_off_needs_no_instruction(self):
        """The other side of it: the route goes straight and a side road leaves
        at 40 degrees. Nobody drifts onto that, and announcing it would bury the
        turns that matter."""
        context = ManeuverContext({}, np.zeros(4), lambda node: [0.0, 40.0])
        assert context.fork_side(1, 0.0, 0.0) == ""

    def test_the_road_you_came_in_on_is_not_a_fork(self):
        """It is behind you; leaving by it is a U-turn, not a drift."""
        context = ManeuverContext({}, np.zeros(4), lambda node: [180.0, 30.0])
        assert context.fork_side(1, 0.0, 30.0) == ""


class TestTravelTime:
    """Travel time is no longer free-flow, and the number reported has to be the
    number that was minimised. See docs/junction-timing-plan.md."""

    def test_the_reported_time_is_the_time_dijkstra_minimised(self, router):
        """The tripwire for the whole change. At pref 0 the edge weight *is*
        travel time, so the shortest-path distance to the destination is exactly
        what the route should claim to take. If `RouteResult.minutes` ever goes
        back to summing the edge table's free-flow column, this catches it — the
        codebase has already shipped one bug of exactly this shape, where the
        router optimised a live re-blend while `mean_score` read the stored
        neutral column and the two disagreed by 1.9 points with nothing saying so.
        """
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra

        s, t = self._od(router, BOSTON, WORCESTER)
        result = router.route(s, t, 0.0)

        weights = router._weights(0.0, router._edge_scores({}))
        pair_w = np.full(router.n_pairs, np.inf)
        np.minimum.at(pair_w, router.slot_pair, weights)
        graph = csr_matrix((pair_w, (router.u_tail, router.u_head)),
                           shape=(router.n, router.n))
        shortest = dijkstra(graph, directed=True, indices=s)[t]
        assert result.minutes == pytest.approx(shortest, rel=1e-9)

    def test_the_reported_time_is_not_the_free_flow_column(self, router):
        """The stored `minutes` is what the graph says a road takes if you never
        slow down and never stop, and the correction moves *both ways*.

        A scenic route comes out slower: back roads are driven below their limit
        and carry the stop signs. A motorway route comes out faster, because
        drivers exceed the posted limit by 16% and a motorway carries about one
        signal per 100 km. Worth pinning in both directions — a correction that
        only ever adds time would be a fudge factor rather than a measurement.
        """
        s, t = self._od(router, BOSTON, WORCESTER)
        fast = router.route(s, t, 0.0)
        scenic = router.route(s, t, 1.0)

        assert scenic.minutes > scenic.edges["minutes"].sum()
        assert fast.minutes < fast.edges["minutes"].sum()

    def test_a_road_costs_more_in_the_direction_its_stop_sign_faces(self, router):
        """The reason the counts are per direction rather than per road."""
        edges = router.edges
        asymmetric = np.where(
            (edges["n_stop_fwd"].to_numpy() > edges["n_stop_rev"].to_numpy())
            & (edges["oneway"].astype(str).str.lower() == "").to_numpy())[0]
        if not len(asymmetric):
            pytest.skip("no two-way road with a one-directional stop sign")

        edge = asymmetric[0]
        slots = np.where(router.eidx == edge)[0]
        assert len(slots) == 2, "a two-way road should expand into two slots"
        forward = slots[~router.flip[slots]][0]
        reverse = slots[router.flip[slots]][0]
        assert router.d_minutes[forward] > router.d_minutes[reverse]

    def test_controls_and_speed_are_both_priced_in(self, router):
        """Each term on its own, so a regression says which one broke."""
        from router import CONTROL_SECONDS, SPEED_FACTOR, SURFACE_SPEED_FACTOR

        edges = router.edges
        free = edges["minutes"].to_numpy()
        driving = router._driving_minutes()
        control_fwd, _ = router._control_minutes()

        # Term 1, and it moves both ways. A surface road is driven below its
        # limit, so it takes longer than free-flow...
        assert SURFACE_SPEED_FACTOR < 1.0
        surface = (edges["highway"] == "tertiary").to_numpy()
        assert (driving[surface] > free[surface]).all()
        # ...while a motorway is driven above it and takes less. A correction
        # that only ever added time would be a fudge factor, not a measurement.
        assert SPEED_FACTOR["motorway"] > 1.0
        fast = (edges["highway"] == "motorway").to_numpy()
        assert (driving[fast] < free[fast]).all()
        # A class nobody drove enough of to measure gets the surface number
        # rather than 1.0 — every class that *was* measured agreed on it.
        untouched = (edges["highway"] == "residential").to_numpy()
        assert "residential" not in SPEED_FACTOR
        assert driving[untouched] == pytest.approx(free[untouched]
                                                   / SURFACE_SPEED_FACTOR)

        # Term 2: an edge with one signal on it costs that many seconds.
        one_signal = np.where((edges["n_signal_fwd"].to_numpy() == 1)
                              & (edges["n_stop_fwd"].to_numpy() == 0)
                              & (edges["n_giveway_fwd"].to_numpy() == 0))[0]
        assert len(one_signal), "the graph should carry signals"
        assert control_fwd[one_signal[0]] == pytest.approx(
            CONTROL_SECONDS["signal"] / 60.0)

    def _od(self, router, a, b):
        return router.snap(*a)[0], router.snap(*b)[0]


class TestRoutingOverTheGraph:
    def _od(self, router, a, b):
        return router.snap(*a)[0], router.snap(*b)[0]

    def test_snap_reports_distance_and_rejects_far_points(self, router):
        _, near = router.snap(*BOSTON)
        _, far = router.snap(45.0, -100.0)       # middle of the continent
        assert near < 500
        assert far > 100_000

    def test_snap_lands_on_the_road_you_are_standing_on(self, router):
        """The defect this guards: snapping to the nearest *junction* put a
        mid-block address on a neighbouring street 23% of the time, because the
        closest junction in a straight line is often on the road behind the
        house. Drivers reported starting a drive on a road they don't live on.
        Snapping via the nearest road segment fixes it — both ends of that
        segment carry the road's own name.
        """
        import numpy as np
        from collections import defaultdict

        edges = router.edges
        names = edges["name"].fillna("").to_numpy()
        u, v = edges["u"].to_numpy(), edges["v"].to_numpy()
        roads_at_node = defaultdict(set)
        for i in range(len(edges)):
            roads_at_node[u[i]].add(names[i])
            roads_at_node[v[i]].add(names[i])
        node_id = router.nodes["node_id"].to_numpy()

        # Stand in the middle of a sample of named residential blocks — the
        # stand-in for "outside a house".
        rng = np.random.default_rng(7)
        candidates = np.where((edges["highway"].to_numpy() == "residential")
                              & (edges["length_m"].to_numpy() > 120)
                              & (names != ""))[0]
        wrong = 0
        sample = rng.choice(candidates, size=120, replace=False)
        for i in sample:
            midpoint = edges.geometry.values[i].interpolate(0.5, normalized=True)
            idx, off = router.snap(midpoint.y, midpoint.x)
            assert off < 1.0, "a point on a road should be ~0 m from the network"
            if names[i] not in roads_at_node[node_id[idx]]:
                wrong += 1
        assert wrong == 0, f"{wrong}/{len(sample)} snapped to a different road"

    def test_snap_with_a_heading_takes_the_end_you_are_driving_toward(self, router):
        """The defect this guards: mid-drive, the *nearer* end of the road you
        are on is as often as not the junction you have just passed, so a
        reroute computed from it can legitimately open by sending you back the
        way you came. The first test drive did exactly that. With a heading,
        snap should take the end ahead of the driver instead — and the two
        opposite headings on one road must give the two different ends.

        The heading fed in is the road's own tangent, because that is the
        heading a driver on it actually has. Asking instead for the straight
        line to an endpoint — which is what this test used to do — is both
        unphysical and circular: on a ramp that turns through more than 90
        degrees the far end lies *behind* the driver as the crow flies, so the
        straight-line answer is the junction they just left, and a `snap` that
        agreed with it was agreeing with the bug. The chord below is taken off
        `interpolate`, independently of how `_forward_end` finds its tangent.
        """
        import math

        import numpy as np

        node_id = router.nodes["node_id"].to_numpy()
        edges = router.edges
        u, v = edges["u"].to_numpy(), edges["v"].to_numpy()

        def bearing(from_lat, from_lon, to_lat, to_lon):
            p1, p2 = math.radians(from_lat), math.radians(to_lat)
            dl = math.radians(to_lon - from_lon)
            y = math.sin(dl) * math.cos(p2)
            x = (math.cos(p1) * math.sin(p2)
                 - math.sin(p1) * math.cos(p2) * math.cos(dl))
            return (math.degrees(math.atan2(y, x)) + 360) % 360

        rng = np.random.default_rng(11)
        # Long edges, so the two ends are unambiguously in different directions
        # and the midpoint is far from both.
        candidates = np.where(edges["length_m"].to_numpy() > 200)[0]
        checked = wrong = 0
        for i in rng.choice(candidates, size=150, replace=False):
            line = edges.geometry.values[i]
            mid = line.interpolate(0.5, normalized=True)
            a, b = int(u[i]), int(v[i])
            # Only meaningful where the nearest road really is this one; a
            # parallel service road would otherwise put us on a different edge
            # and compare against the wrong pair of ends.
            if node_id[router.snap(mid.y, mid.x)[0]] not in (a, b):
                continue
            checked += 1
            # Driving the geometry's own direction takes you to v; turn round
            # and it takes you to u.
            back = line.interpolate(0.45, normalized=True)
            ahead = line.interpolate(0.55, normalized=True)
            along = bearing(back.y, back.x, ahead.y, ahead.x)
            got_b = node_id[router.snap(mid.y, mid.x, heading=along)[0]]
            got_a = node_id[router.snap(mid.y, mid.x,
                                        heading=(along + 180.0) % 360.0)[0]]
            if got_a != a or got_b != b:
                wrong += 1
        assert checked > 100, "too few usable samples to conclude anything"
        assert wrong == 0, f"{wrong}/{checked} snapped to the end behind the driver"

    def test_an_unusable_heading_falls_back_to_the_nearer_end(self, router):
        """CoreLocation reports -1 when it has no opinion, and its course is
        noise at a crawl. A heading that cannot be trusted has to behave as
        though none was given, not as though the driver faced north."""
        plain = router.snap(*BOSTON)[0]
        for heading in (-1.0, 360.0, 999.0, -0.0001):
            assert router.snap(*BOSTON, heading=heading)[0] == plain, heading

    def test_fastest_is_faster_and_scenic_is_more_scenic(self, router):
        s, t = self._od(router, WORCESTER, BOSTON)
        fast, scenic = router.route(s, t, 0.0), router.route(s, t, 1.0)
        assert fast.minutes < scenic.minutes
        assert scenic.mean_score > fast.mean_score

    def test_scenery_rises_monotonically_with_preference(self, router):
        """Dragging the slider further must never give you a *less* scenic
        route; small dips are the discrete route swap, so allow a hair."""
        s, t = self._od(router, WORCESTER, BOSTON)
        scores = [router.route(s, t, p).mean_score for p in np.linspace(0, 1, 6)]
        assert all(b >= a - 0.05 for a, b in zip(scores, scores[1:])), scores

    def test_the_whole_slider_does_something(self, router):
        """The defect this guards: the top half of the slider used to return an
        identical route, so half the control was dead."""
        s, t = self._od(router, WORCESTER, BOSTON)
        low = router.route(s, t, 0.0).mean_score
        mid = router.route(s, t, 0.5).mean_score
        high = router.route(s, t, 1.0).mean_score
        gain = high - low
        assert gain > 1.0, "preference barely changes the route at all"
        # the back half of the travel must deliver a real share of the gain
        assert (high - mid) / gain > 0.10, "top half of the slider is inert"

    def test_route_geometry_is_continuous(self, router):
        """Stitched edges must form one unbroken line — a gap means an edge was
        traversed in the wrong direction."""
        s, t = self._od(router, WORCESTER, BOSTON)
        coords = shapely.get_coordinates(router.route(s, t, 0.7).line)
        steps_m = np.hypot(*(np.diff(coords, axis=0) * [82_000, 111_000]).T)
        assert steps_m.max() < 2000, f"gap of {steps_m.max():.0f} m in the route line"

    def test_beauty_weights_shift_the_route(self, router):
        """Asking for coast and nothing else must not return the neutral route."""
        s, t = self._od(router, BOSTON, (41.6362, -70.9342))   # toward Buzzards Bay
        neutral = router.route(s, t, 0.8)
        coastal = router.route(s, t, 0.8, {"coast": 4.0, "town": 0.0, "farm": 0.0})
        assert coastal.scenery_km()["coast"] >= neutral.scenery_km()["coast"]

    def test_neutral_weights_reproduce_the_precomputed_score(self, router):
        """The live re-blend and the stored column must agree exactly, or the
        app shows one number while the router optimizes another."""
        live = router._edge_scores({name: 1.0 for name, *_ in BEAUTY_TYPES})
        assert np.allclose(live, router.edges["score"].to_numpy(), atol=1e-9)

    def test_negative_pref_is_clamped_rather_than_going_complex(self, router):
        """`(-0.5) ** 1.3` is a complex number in Python, which would poison the
        whole cost matrix. The API clamps; the class must too, for the CLI."""
        w = router._weights(-0.5, router._edge_scores({}))
        assert np.isrealobj(w)
        assert np.array_equal(w, router._weights(0.0, router._edge_scores({})))


class TestReportedScenery:
    """The number the app puts on screen has to be the one the router used."""

    def _od(self, router, a, b):
        return router.snap(*a)[0], router.snap(*b)[0]

    def test_mean_score_is_measured_on_the_users_own_scale(self, router):
        """The defect this guards: the router optimized the live re-blend while
        the summary read the stored neutral column, so a user who asked for
        coast was shown a score computed as though they hadn't — 1.9 points
        apart on a 0-10 scale, on the one number the tune screen exists to move.
        """
        weights = {"coast": 4.0, "town": 0.0, "farm": 0.0}
        s, t = self._od(router, BOSTON, (41.6362, -70.9342))
        route = router.route(s, t, 0.8, weights)

        live = router._edge_scores(weights)[route.edges.index.to_numpy()]
        length = route.edges["length_m"].to_numpy()
        expected = (live * length).sum() / length.sum()
        assert route.mean_score == pytest.approx(expected, rel=1e-12)

        # ...and that those are genuinely the user's scores rather than the
        # stored column. Compared per edge, not as an average: a route re-chosen
        # under new weights can average out near the neutral number by
        # coincidence, and this assertion used to make that coincidence a
        # failure — it broke when a travel-time change moved this route to one
        # whose two averages land 0.046 apart. The per-edge arrays cannot
        # coincide, and they are what the defect was about.
        stored = route.edges["score"].to_numpy()
        assert np.abs(live - stored).max() > 0.5, \
            "the reported scores are the stored neutral column, not the user's"

    def test_neutral_requests_still_report_the_precomputed_score(self, router):
        s, t = self._od(router, WORCESTER, BOSTON)
        route = router.route(s, t, 0.7)
        length = route.edges["length_m"].to_numpy()
        stored = route.edges["score"].to_numpy()
        assert route.mean_score == pytest.approx(
            (stored * length).sum() / length.sum(), rel=1e-9)


class TestBeautyWeightsAreWellBehaved:
    """The tune sliders shape *what kind* of scenery; `pref` sets how much."""

    def _od(self, router, a, b):
        return router.snap(*a)[0], router.snap(*b)[0]

    @pytest.mark.parametrize("level", [0.5, 1.5, 2.0, 4.0])
    def test_moving_every_slider_together_means_no_preference(self, router, level):
        """"More of everything" is not a preference, and must not be treated as
        one. It used to be: at the app's maximum it pinned 17% of the state's
        road-km at exactly 10.0, where the router's `1 - score/10` penalty makes
        every road free and indistinguishable."""
        uniform = router._edge_scores({t: level for t in ALL_TYPES})
        assert np.allclose(uniform, router._edge_scores({}), atol=1e-9)

    @pytest.mark.parametrize("weights", [
        {}, {t: 2.0 for t in ALL_TYPES}, {t: 4.0 for t in ALL_TYPES},
        {"coast": 4.0}, {"hills": 4.0, "town": 0.0}, {t: 0.0 for t in ALL_TYPES},
    ])
    def test_no_setting_flattens_the_scale(self, router, weights):
        """However the sliders are set, the score has to keep discriminating
        between roads — a scale pinned at its ceiling is not a scale."""
        scores = router._edge_scores(weights)
        km = router.km
        pinned = km[scores >= 9.99].sum() / km.sum()
        assert pinned < 0.05, f"{100 * pinned:.0f}% of km pinned at 10"
        assert scores.min() >= 0.0 and scores.max() <= 10.0

    def test_cranking_everything_is_not_worse_than_neutral(self, router):
        """It used to be: all sliders at maximum returned a route no more scenic
        than neutral and 5 km longer."""
        s, t = self._od(router, WORCESTER, BOSTON)
        neutral = router.route(s, t, 1.0)
        cranked = router.route(s, t, 1.0, {t_: 2.0 for t_ in ALL_TYPES})
        assert cranked.mean_score >= neutral.mean_score - 1e-9
        assert cranked.km <= neutral.km + 1e-9

    def test_a_single_type_still_leans_the_route(self, router):
        """Renormalizing must not neuter the sliders — asking for coast and
        little else still has to find more coast."""
        s, t = self._od(router, BOSTON, (41.6362, -70.9342))
        neutral = router.route(s, t, 0.8)
        coastal = router.route(s, t, 0.8, {"coast": 4.0, "town": 0.0, "farm": 0.0})
        assert coastal.scenery_km()["coast"] > neutral.scenery_km()["coast"]

    def test_pref_curve_shapes_the_scenery_cost(self, router):
        """The scenery term must scale as pref**PREF_CURVE, not linearly — that
        is what keeps the slider's low end fine-grained."""
        neutral = router._edge_scores({})
        half = router._weights(0.5, neutral) - router.d_minutes
        full = router._weights(1.0, neutral) - router.d_minutes
        moving = full > 0
        ratio = half[moving].sum() / full[moving].sum()
        assert ratio == pytest.approx(0.5 ** PREF_CURVE, rel=1e-9)
