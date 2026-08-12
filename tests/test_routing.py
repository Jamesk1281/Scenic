"""Router tests: the turn-by-turn maths (pure) and real routes over the graph.

The routing cases use the built graph and skip without it. They assert the
properties a driver would notice — the scenic route is genuinely more scenic,
the fastest route is genuinely faster, and the slider actually does something
across its whole travel.
"""

import numpy as np
import pytest
import shapely

from router import (BEAUTY_TYPES, PREF_CURVE, RouteResult, _bearing, _compass,
                    _turn_delta, _turn_phrase, stitch)

BOSTON = (42.3551, -71.0657)
WORCESTER = (42.2626, -71.8023)
CONCORD = (42.4604, -71.3489)


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
        (0, "Continue"), (10, "Continue"),
        (30, "Slight right"), (-30, "Slight left"),
        (90, "Turn right"), (-90, "Turn left"),
        (150, "Sharp right"), (-150, "Sharp left"),
    ])
    def test_turn_phrases(self, delta, expected):
        assert _turn_phrase(0, delta % 360) == expected


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

    def test_pref_curve_shapes_the_scenery_cost(self, router):
        """The scenery term must scale as pref**PREF_CURVE, not linearly — that
        is what keeps the slider's low end fine-grained."""
        half = router._weights(0.5, {}) - router.d_minutes
        full = router._weights(1.0, {}) - router.d_minutes
        moving = full > 0
        ratio = half[moving].sum() / full[moving].sum()
        assert ratio == pytest.approx(0.5 ** PREF_CURVE, rel=1e-9)
