"""Router tests: the turn-by-turn maths (pure) and real routes over the graph.

The routing cases use the built graph and skip without it. They assert the
properties a driver would notice — the scenic route is genuinely more scenic,
the fastest route is genuinely faster, and the slider actually does something
across its whole travel.
"""

import numpy as np
import pytest
import shapely

from router import (BEAUTY_TYPES, BREAKDOWN_MIN, PREF_CURVE, RouteResult,
                    _bearing, _compass, _turn_delta, _turn_phrase, stitch)

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

    def test_snap_with_a_heading_takes_the_end_you_are_driving_toward(self, router):
        """The defect this guards: mid-drive, the *nearer* end of the road you
        are on is as often as not the junction you have just passed, so a
        reroute computed from it can legitimately open by sending you back the
        way you came. The first test drive did exactly that. With a heading,
        snap should take the end ahead of the driver instead — and the two
        opposite headings on one road must give the two different ends.
        """
        import math

        import numpy as np

        node_id = router.nodes["node_id"].to_numpy()
        lat = router.nodes["lat"].to_numpy()
        lon = router.nodes["lon"].to_numpy()
        row_of = {int(nid): k for k, nid in enumerate(node_id)}
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
            mid = edges.geometry.values[i].interpolate(0.5, normalized=True)
            a, b = int(u[i]), int(v[i])
            # Only meaningful where the nearest road really is this one; a
            # parallel service road would otherwise put us on a different edge
            # and compare against the wrong pair of ends.
            if node_id[router.snap(mid.y, mid.x)[0]] not in (a, b):
                continue
            checked += 1
            toward_a = bearing(mid.y, mid.x, lat[row_of[a]], lon[row_of[a]])
            toward_b = bearing(mid.y, mid.x, lat[row_of[b]], lon[row_of[b]])
            got_a = node_id[router.snap(mid.y, mid.x, heading=toward_a)[0]]
            got_b = node_id[router.snap(mid.y, mid.x, heading=toward_b)[0]]
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

        stored = route.edges["score"].to_numpy()
        assert route.mean_score != pytest.approx(
            (stored * length).sum() / length.sum(), abs=0.05), \
            "weights that reshape the route must reshape its reported score"

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
