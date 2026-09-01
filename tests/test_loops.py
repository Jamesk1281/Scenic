"""Loop-route tests: the tree accumulation (exact) and real loops over the graph.

The accumulation test comes first on purpose. Everything in `looper.py` rests on
recovering the km and scenic-km of a path from a Dijkstra predecessor tree
instead of re-running a search, and if that arithmetic is wrong every other
number the module produces is quietly wrong with it. `route()` is the
independent instrument: it walks the same edges the slow way.

The rest assert what a driver would notice — the loop comes back to where it
started, it does not drive the same road twice, it is roughly the length that was
asked for, and a start on a cul-de-sac still gets an answer.

These need the built graph and skip without it, like tests/test_routing.py.
"""

import numpy as np
import pytest

from looper import (BEAUTIFUL_SCORE, CANDIDATE_TOLERANCE, MAX_TARGET_KM,
                    MIN_TARGET_KM, PENALTY, SECTORS, LoopPlanner, _accumulate,
                    _weights_key)

# The three starts every measurement in docs/loop-routes-design.md used, chosen
# to span the geography: dense, suburban, and rural where the retrace problem is
# at its worst.
NEEDHAM = (42.2809, -71.2378)
CONCORD = (42.4604, -71.3489)
PETERSHAM = (42.4879, -72.1889)
BOSTON = (42.3554, -71.0640)


@pytest.fixture(scope="module")
def planner(router):
    return LoopPlanner(router)


class TestAccumulation:
    """Pointer doubling up a tree, against arithmetic that can be checked by eye."""

    def test_a_straight_chain_sums_along_itself(self):
        # 0 <- 1 <- 2 <- 3, root self-loops. Each edge is worth its own index.
        parent = np.array([0, 0, 1, 2])
        km = np.array([0.0, 1.0, 2.0, 3.0])
        assert _accumulate(parent, km).tolist() == [0.0, 1.0, 3.0, 6.0]

    def test_two_quantities_accumulate_together(self):
        parent = np.array([0, 0, 1])
        got_km, got_scen = _accumulate(parent, np.array([0.0, 2.0, 4.0]),
                                       np.array([0.0, 10.0, 20.0]))
        assert got_km.tolist() == [0.0, 2.0, 6.0]
        assert got_scen.tolist() == [0.0, 10.0, 30.0]

    def test_a_detached_node_contributes_nothing(self):
        # An unreachable node self-loops, so it stays at zero rather than
        # poisoning the sum or looping forever.
        parent = np.array([0, 0, 2])
        assert _accumulate(parent, np.array([0.0, 5.0, 7.0])).tolist() == [0.0, 5.0, 0.0]

    def test_a_root_carrying_a_value_does_not_get_doubled_into_nonsense(self):
        """The one way to misuse this that is silent and enormous. Left to the
        caller, a root holding 1.0 comes back holding 2^32."""
        parent = np.array([0, 0, 1])
        assert _accumulate(parent, np.array([1.0, 2.0, 4.0])).tolist() == [0.0, 2.0, 6.0]

    def test_a_deep_chain_still_terminates(self):
        # 5,000 hops needs 13 rounds of doubling; the loop's cap is 32.
        n = 5000
        parent = np.concatenate([[0], np.arange(n - 1)])
        assert _accumulate(parent, np.ones(n))[-1] == pytest.approx(n - 1)


class TestFieldsMatchTheRouter:
    """The load-bearing equality: the field's km is the route's km."""

    def test_accumulated_km_and_scenery_match_route(self, router, planner):
        start, _ = router.snap(*NEEDHAM)
        fields = planner._fields(start, 1.0, {})
        rng = np.random.default_rng(7)
        far = np.where(fields.reachable & (fields.out.km > 5)
                       & (fields.out.km < 60))[0]
        for node in rng.choice(far, 6, replace=False):
            result = router.route(start, int(node), 1.0)
            assert result is not None
            assert fields.out.km[node] == pytest.approx(result.km, abs=1e-6)
            # Same trick, different quantity: scenic-km over km is the route's
            # own length-weighted mean score.
            mean = fields.out.scen[node] / fields.out.km[node]
            assert mean == pytest.approx(result.mean_score, abs=1e-6)

    def test_the_way_home_is_computed_and_not_mirrored(self, router, planner):
        """A reverse field that just copied the outbound one would pass every
        other test in this file. The graph is directed, so it must not."""
        start, _ = router.snap(*NEEDHAM)
        fields = planner._fields(start, 1.0, {})
        far = fields.reachable & (fields.out.km > 5)
        difference = fields.back.km[far] - fields.out.km[far]
        assert np.abs(difference).max() > 1.0
        # ...and it is a genuine route home, not noise: for a sample of nodes the
        # reverse cost equals what the router charges for driving back.
        rng = np.random.default_rng(11)
        for node in rng.choice(np.where(far)[0], 4, replace=False):
            back = router.route(int(node), start, 1.0)
            assert back is not None
            assert fields.back.km[node] == pytest.approx(back.km, abs=1e-6)


class TestLoopsAreLoops:
    @pytest.mark.parametrize("place", [NEEDHAM, PETERSHAM, BOSTON])
    def test_a_loop_returns_to_where_it_started(self, router, planner, place):
        start, _ = router.snap(*place)
        loop = planner.plan(start, 40.0)
        assert loop is not None
        coords = np.asarray(loop.route.line.coords)
        gap_m = np.hypot(*(coords[0] - coords[-1]) * [82_000, 111_000])
        # The line closes on the start junction itself, or on a split copy of it
        # standing at the same place, so this is metres and not kilometres.
        assert gap_m < 50.0

    @pytest.mark.parametrize("place", [NEEDHAM, PETERSHAM, BOSTON])
    def test_a_loop_does_not_drive_the_same_road_twice(self, router, planner, place):
        """The measured reason `_build` exists. Without the penalty this is 26%,
        50% and 18% of the loop's length at these three starts."""
        start, _ = router.snap(*place)
        loop = planner.plan(start, 40.0)
        assert loop is not None
        # Measured 1.1% on average and 3.4% at worst over three starts and three
        # targets, against 26%/50%/18% with the penalty switched off.
        assert loop.repeated_fraction < 0.06

    def test_the_penalty_is_what_removes_the_retrace(self, router, planner):
        """Neutralise the one parameter and the defect comes back — otherwise
        this suite would pass just as well with `_build` doing nothing."""
        start, _ = router.snap(*PETERSHAM)
        fields = planner._fields(start, 1.0, {})
        candidates = planner.candidates(fields, 40.0)
        turnaround = int(candidates[np.argmax(
            fields.loop_scen[candidates] / fields.loop_km[candidates])])
        penalised = planner._build(fields, turnaround, PENALTY)
        plain = planner._build(fields, turnaround, 1.0)
        with_it = penalised.repeated_km / penalised.km
        without = plain.repeated_km / plain.km

        # The guard is the *separation*, which is what "the penalty is what
        # removes the retrace" actually says. Measured on Massachusetts:
        # 50.0% without (18.28 of 36.57 km) against 5.2% with (2.14 of 41.12),
        # a 9.6x cut. Asserted at 5x, which no do-nothing `_build` can reach.
        #
        # The absolute bound was 0.05 and is now 0.06, matching
        # `test_a_loop_does_not_drive_the_same_road_twice` above rather than
        # sitting 1 point stricter than it for no stated reason. It moved
        # because the surface work removed the -0.25 unpaved penalty from
        # `score_adj`, which changes which loop rural Massachusetts picks —
        # Petersham went from just under the old line to 5.2%. That is the
        # intended effect of that change landing on a threshold calibrated
        # before it, not a regression in `_build`: the separation it guards
        # is unmoved.
        assert without > 0.30
        assert with_it < 0.06
        assert without / with_it > 5.0, (
            f"the penalty only cut the retrace {without / with_it:.1f}x "
            f"({without:.1%} -> {with_it:.1%}); `_build` may be doing nothing")

    def test_the_loop_is_a_drivable_route_with_turn_by_turn(self, router, planner):
        start, _ = router.snap(*NEEDHAM)
        loop = planner.plan(start, 40.0)
        steps = loop.route.steps()
        assert len(steps) > 5
        assert steps[0]["type"] == "depart"
        assert steps[-1]["type"] == "arrive"
        assert all(step["instruction"] for step in steps)
        # geojson() is what the API returns and the app decodes; a loop must not
        # need a different shape from a point-to-point route.
        feature = loop.route.geojson()
        assert feature["geometry"]["type"] == "LineString"
        for key in ("km", "minutes", "mean_score", "scenery_km", "steps"):
            assert key in feature["properties"]


class TestTheDistanceSlider:
    @pytest.mark.parametrize("target", [20.0, 40.0, 80.0])
    def test_it_lands_near_what_was_asked(self, router, planner, target):
        start, _ = router.snap(*NEEDHAM)
        loop = planner.plan(start, target)
        assert loop is not None
        # 3 picks measured -7%..+6% over three starts and three targets; 12%
        # leaves room for the graph moving under us without hiding a regression.
        assert abs(loop.error) < 0.12

    def test_it_is_clamped_to_the_sliders_ends(self, router, planner):
        start, _ = router.snap(*NEEDHAM)
        assert planner.plan(start, 1.0).target_km == MIN_TARGET_KM
        assert planner.plan(start, 10_000.0).target_km == MAX_TARGET_KM

    def test_longer_targets_give_longer_loops(self, router, planner):
        start, _ = router.snap(*NEEDHAM)
        lengths = [planner.plan(start, t).km for t in (20.0, 40.0, 80.0)]
        assert lengths[0] < lengths[1] < lengths[2]

    def test_a_target_the_geography_cannot_meet_admits_it(self, router, planner):
        """A very short loop from a rural start is not impossible, it is bad —
        within 2.5 km of Petersham there is one road, so the loop has to double
        back. The module returns the best available and says how much of it is
        repeated; refusing outright is the API's call, not this layer's."""
        start, _ = router.snap(*PETERSHAM)
        # Not one target, because which short lengths happen to have a clean loop
        # is a fact about that parish's roads and not about this code: at
        # Petersham 5 km repeats 9%, 6 km repeats 30% and 8 km repeats 35%. The
        # claim is about the range, so the test is too.
        cramped = [planner.plan(start, t) for t in (5.0, 6.0, 8.0, 10.0)]
        assert all(loop is not None for loop in cramped)
        assert max(loop.repeated_fraction for loop in cramped) > 0.20
        # Give it room and the same start comes back clean.
        roomy = planner.plan(start, 40.0)
        assert roomy.repeated_fraction < 0.06

    def test_it_can_report_the_nearest_length_that_works(self, router, planner):
        start, _ = router.snap(*PETERSHAM)
        nearest = planner.nearest_length(start, 1.0)
        assert nearest is not None
        assert MIN_TARGET_KM <= nearest <= MAX_TARGET_KM


class TestRegenerate:
    def test_the_sectors_offered_are_the_ones_that_have_loops(self, router, planner):
        start, _ = router.snap(*BOSTON)
        available = planner.sectors(start, 20.0)
        assert available
        assert set(available) <= set(SECTORS)
        # Every direction offered has to actually produce a loop, or the app
        # shows the user a button that fails.
        for name in available:
            assert planner.plan(start, 20.0, sector=name) is not None
        # ...and hold enough candidates to be a direction rather than a single
        # drive wearing one. This used to assert that a coastal start could not
        # fill all eight octants, which was true of the Massachusetts graph and
        # is an accident of where that extract was clipped: on New England,
        # Boston fills all eight, south-east with exactly **one** candidate that
        # does route. So the emptiness was never the property worth guarding —
        # sufficiency is.
        #
        # 5 written out rather than imported from `looper`, deliberately. It is
        # `SPAN_PICKS`, the number of band slices `plan` fills, so a direction
        # holding fewer cannot honour the distance slider — but a test that
        # imports the constant it asserts against passes at any value, including
        # the 1 this exists to reject.
        for name, count in available.items():
            assert count >= 5, (
                f"{name} offered on {count} candidate(s); regenerate would "
                "return the same drive every press")

    def test_different_sectors_are_different_drives(self, router, planner):
        """The product claim behind the regenerate button: eight loops from one
        start shared a median 1% of their roads."""
        start, _ = router.snap(*NEEDHAM)
        loops = []
        for name in planner.sectors(start, 40.0):
            loop = planner.plan(start, 40.0, sector=name)
            if loop is not None:
                loops.append(set(loop.route.edges.index))
        assert len(loops) >= 5
        overlaps = [len(a & b) / len(a | b)
                    for i, a in enumerate(loops) for b in loops[i + 1:]]
        assert np.median(overlaps) < 0.20
        assert max(overlaps) < 0.60

    def test_a_requested_sector_is_the_sector_returned(self, router, planner):
        start, _ = router.snap(*NEEDHAM)
        for name in list(planner.sectors(start, 40.0))[:3]:
            assert planner.plan(start, 40.0, sector=name).sector == name


class TestHardStarts:
    def test_a_cul_de_sac_start_still_gets_a_loop(self, router, planner):
        """17.2% of junctions are dead ends. A hard no-repeat rule returns
        nothing for every one of them, which is why the penalty is soft."""
        degree = np.bincount(
            np.concatenate([router.edge_u_idx, router.edge_v_idx]),
            minlength=len(router.nodes))
        dead_ends = np.where(degree == 1)[0]
        assert len(dead_ends) > 10_000
        rng = np.random.default_rng(0)
        repeated = []
        for start in rng.choice(dead_ends, 8, replace=False):
            loop = planner.plan(int(start), 20.0)
            assert loop is not None, "a cul-de-sac must still get a loop"
            repeated.append(loop.repeated_fraction)
        # The stub itself has to be driven twice and no penalty can change that,
        # so this is not zero. Over 30 samples: median 3.4%, p90 10%, worst 27%.
        # The bound is on the shape of that distribution, not on one draw.
        assert np.median(repeated) < 0.15
        assert max(repeated) < 0.35

    def test_a_banned_return_leg_is_not_a_crash(self, router, planner):
        """An infinite penalty is exactly the hard constraint that fails on a
        dead end. `_build` must return None rather than raise or hand back a
        broken path."""
        degree = np.bincount(
            np.concatenate([router.edge_u_idx, router.edge_v_idx]),
            minlength=len(router.nodes))
        start = int(np.where(degree == 1)[0][0])
        fields = planner._fields(start, 1.0, {})
        candidates = planner.candidates(fields, 20.0)
        if not len(candidates):
            pytest.skip("this dead end has no 20 km candidate to try")
        assert planner._build(fields, int(candidates[0]), np.inf) is None


class TestQualityNumbers:
    def test_beautiful_km_counts_only_beautiful_roads(self, router, planner):
        start, _ = router.snap(*NEEDHAM)
        loop = planner.plan(start, 40.0)
        assert 0.0 <= loop.beautiful_km <= loop.km
        scores = np.asarray(loop.route.scores)
        length = loop.route.edges["length_m"].to_numpy()
        expected = length[scores >= BEAUTIFUL_SCORE].sum() / 1000.0
        assert loop.beautiful_km == pytest.approx(expected)

    def test_a_scenic_loop_beats_a_fast_one_at_the_same_length(self, router, planner):
        """Measured 5.74 against 4.76 at a Needham 40 km target, and 8.7 km on
        roads scoring 7+ against 2.3 km. If this ever narrows, the feature has
        stopped doing its job.

        The gap is narrower than the 6.03-against-1.94 in the design doc, and
        that is not a regression — it is that `plan` ranks candidate turnarounds
        by scenery whatever `pref` is, so pref 0 still sends the driver somewhere
        worth going and only takes efficient roads to get there. Worth knowing
        before the loop tab offers a pref slider: with the length pinned by the
        distance slider, pref has much less left to trade than it does
        point-to-point.
        """
        start, _ = router.snap(*NEEDHAM)
        scenic = planner.plan(start, 40.0, pref=1.0)
        fast = planner.plan(start, 40.0, pref=0.0)
        assert scenic.mean_score > fast.mean_score + 0.5
        # The stronger signal, and the one the app should show: 8.7 km of
        # properly beautiful road against 2.3 km.
        assert scenic.beautiful_km > 2.5 * fast.beautiful_km

    def test_the_middle_of_the_pref_slider_is_not_monotone(self, router, planner):
        """A wart, asserted so that fixing it fails loudly rather than silently.

        pref 0.5 comes back *worse* than pref 0.0 — 4.99 against 5.86 at a
        Concord 40 km target. Two things combine: `plan` ranks candidate
        turnarounds by scenery no matter what pref is, so a middling pref picks
        a different turnaround than pref 0 without gaining the routing to
        justify it; and the final choice among built loops is on distance and
        repeated road with no scenery term, which at pref 1.0 is harmless
        (every candidate is pretty) and below it is not.

        Concord rather than Needham because *which* pref dips is a property of
        the start, not of the slider. This test used to pin Needham at pref
        0.25, which dipped 5.00 → 3.58 under the old designation-only `c_green`
        and does not dip at all now that forest is half measured tree cover.
        Concord dips at 0.5 on both scorings, so it is the more durable sample —
        but it is still a sample, and a failure here means the dip has moved
        again, not necessarily that it is fixed. Sweep the other starts before
        concluding anything.

        The endpoints are what matter and they behave. Until someone sweeps this
        properly, the loop tab should pin pref at 1.0 rather than offer a slider
        — which is also what the field cache wants, since pref is the one
        parameter that invalidates it.
        """
        start, _ = router.snap(*CONCORD)
        scores = {pref: planner.plan(start, 40.0, pref=pref).mean_score
                  for pref in (0.0, 0.5, 1.0)}
        assert scores[1.0] > scores[0.0]
        assert scores[0.5] < scores[0.0], \
            "the dip in the middle of the slider has moved or gone — sweep " \
            "pref from several starts, then update this test and the table in " \
            "looper.py's docstring"

    def test_the_reported_numbers_describe_the_drawn_line(self, router, planner):
        """One instrument, not two: km/minutes/mean_score come off the route
        rather than off the candidate estimate, which is a few percent out."""
        start, _ = router.snap(*NEEDHAM)
        loop = planner.plan(start, 40.0)
        assert loop.km == loop.route.km
        assert loop.minutes == loop.route.minutes
        assert loop.mean_score == loop.route.mean_score
        assert loop.km == pytest.approx(
            loop.route.edges["length_m"].sum() / 1000.0)


class TestCaching:
    def test_shuffling_from_one_start_reuses_the_passes(self, router, planner):
        p = LoopPlanner(router, field_cache=2)
        start, _ = router.snap(*NEEDHAM)
        first = p._fields(start, 1.0, {})
        assert p._fields(start, 1.0, {}) is first
        # Moving pref changes every edge weight, so it cannot be a cache hit.
        assert p._fields(start, 0.5, {}) is not first
        # ...and the cache does not grow without bound.
        for lat in (42.1, 42.2, 42.3, 42.4):
            p._fields(router.snap(lat, -71.2)[0], 1.0, {})
        assert len(p._fields_by_key) <= 2

    def test_the_weight_key_ignores_ordering(self):
        assert _weights_key({"coast": 2.0, "farm": 0.0}) == \
               _weights_key({"farm": 0.0, "coast": 2.0})
        assert _weights_key(None) == _weights_key({})


class TestSurfaceAvoidanceReachesTheLoops:
    """`avoid_unpaved` moves every edge weight, so it has to be part of the
    cache keys. A cost model or field set cached under a different setting
    answers a question nobody asked — and the loop planner caches both."""

    def test_the_cost_cache_is_keyed_on_the_avoidance(self, planner):
        loose = planner._cost(1.0, (), 0.0)
        tight = planner._cost(1.0, (), 2.0)
        assert loose is not tight
        if not (planner.router.unpaved_frac > 0).any():
            pytest.skip("no unpaved road in this build")
        assert not np.array_equal(loose.w_slot, tight.w_slot)

    def test_asking_again_with_the_same_avoidance_hits_the_cache(self, planner):
        first = planner._cost(1.0, (), 0.5)
        assert planner._cost(1.0, (), 0.5) is first

    def test_the_field_cache_is_keyed_on_the_avoidance(self, router, planner):
        start = router.snap(*PETERSHAM)[0]
        assert (planner._fields(start, 1.0, {}, 0.0)
                is not planner._fields(start, 1.0, {}, 2.0))
        assert (planner._fields(start, 1.0, {}, 0.0)
                is planner._fields(start, 1.0, {}, 0.0))
