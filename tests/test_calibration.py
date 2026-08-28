"""Distribution guards on the built data.

A scoring constant is a single number that silently reshapes 66,000 km of road.
Every assertion here corresponds to a defect that was live in the pipeline and
invisible until the distributions were plotted: a component pinned at its
ceiling, a component with no range left, a 0-10 scale that only reached 6, and
scenic byways ranking below the Mass Pike.

These are deliberately loose — they catch a constant that has drifted into
nonsense, not small honest retunings.
"""

import numpy as np
import pytest

from score import CURVE_FULL, curvature_deg_per_km

COMPONENTS = ["c_water", "c_coast", "c_forest", "c_curves", "c_relief",
              "c_farm", "c_views", "c_scenic_tag", "c_urban"]


@pytest.fixture(scope="session")
def km(edges):
    return (edges["length_m"] / 1000.0).to_numpy()


def share(km, mask):
    return float(km[mask].sum() / km.sum())


def weighted_mean(values, km, mask=None):
    v, w = (values, km) if mask is None else (values[mask], km[mask])
    return float((v * w).sum() / max(w.sum(), 1e-9))


class TestComponents:
    @pytest.mark.parametrize("component", COMPONENTS)
    def test_within_unit_range(self, edges, component):
        v = edges[component].to_numpy()
        assert v.min() >= 0.0 and v.max() <= 1.0

    @pytest.mark.parametrize("component", COMPONENTS)
    def test_not_pinned_at_the_ceiling(self, edges, km, component):
        """c_curves once sat at 1.0 for half the state's road-km, which makes a
        component a constant rather than a signal."""
        assert share(km, edges[component].to_numpy() >= 0.999) < 0.35

    @pytest.mark.parametrize("component", ["c_water", "c_forest", "c_curves",
                                           "c_relief", "c_urban"])
    def test_broad_components_have_usable_range(self, edges, km, component):
        """c_relief was scaled for alpine terrain, so in Massachusetts it never
        left the bottom of its range and the Hills slider had nothing to grab."""
        v = edges[component].to_numpy()
        assert share(km, v >= 0.5) > 0.02, f"{component} almost never fires"
        assert weighted_mean(v, km) > 0.05

    def test_town_is_not_most_of_the_state(self, edges, km):
        """'Town' covering half the network made both the label and the slider
        meaningless."""
        assert share(km, edges["c_urban"].to_numpy() >= 0.5) < 0.45


class TestScoreScale:
    def test_uses_most_of_the_range(self, edges, km):
        s = edges["score"].to_numpy()
        order = np.argsort(s)
        cw = np.cumsum(km[order]) / km.sum()
        p10, p50, p90 = (np.interp(q, cw, s[order]) for q in (0.1, 0.5, 0.9))
        assert p90 - p10 > 4.0, f"scale compressed: p10 {p10:.1f} p90 {p90:.1f}"
        assert 2.5 < p50 < 6.5, f"median road scores {p50:.1f}"

    def test_not_mostly_clipped(self, edges, km):
        s = edges["score"].to_numpy()
        assert share(km, s <= 0.01) < 0.12
        assert share(km, s >= 9.99) < 0.05

    def test_score_matches_components(self, edges):
        """The stored score must be reproducible from the stored component
        vector — if graph.py ever attaches a chunk's score to the wrong edge,
        this is what catches it."""
        from score import WEIGHTS, composite
        key = {"c_water": "water", "c_coast": "coast", "c_forest": "forest",
               "c_curves": "curves", "c_relief": "relief", "c_farm": "farm",
               "c_views": "views", "c_scenic_tag": "scenic_tag",
               "c_urban": "urban"}
        raw = sum(WEIGHTS[k] * edges[c].to_numpy() for c, k in key.items())
        assert np.allclose(composite(raw, edges["score_adj"].to_numpy()),
                           edges["score"].to_numpy(), atol=1e-9)


class TestBreakdownThreshold:
    def test_breakdown_threshold_admits_every_partial_credit_band(self, chunks):
        """A component's partial-credit band has to clear the route-summary
        threshold, or score.py credits road the app can never show.

        score.py awards water 0.45 out to 350 m and towns 0.5 across a
        settlement's wider orbit. The summary asked for >= 0.5, so every metre
        of the water band — 18% of the network's km — scored for water and
        reported as zero: a route hugging a river 200 m away said "water: 0 mi".
        Retuning a DIST band below BREAKDOWN_MIN would bring that back.
        """
        from router import BREAKDOWN_MIN, SCENERY_BREAKDOWN

        for label, column, threshold in SCENERY_BREAKDOWN:
            values = np.unique(chunks[column].to_numpy())
            # Only the banded components can be checked this way; relief is a
            # continuous measure whose threshold is a genuine "how hilly counts"
            # judgement rather than a band boundary.
            if len(values) > 5:
                continue
            bands = values[values > 1e-9]
            assert bands.min() >= threshold, (
                f"{label}: score.py awards {column}={bands.min()} but the "
                f"summary only counts >= {threshold}, so that band is invisible")


class TestBenchmarkRoads:
    """Named roads whose relative order is not a matter of taste."""

    def _lw(self, edges, km, pattern, column="name"):
        mask = edges[column].fillna("").str.contains(pattern, case=False,
                                                     regex=True).to_numpy()
        if not mask.any():
            pytest.skip(f"no road matching {pattern!r} in the extract")
        return weighted_mean(edges["score"].to_numpy(), km, mask)

    def test_scenic_byways_beat_the_interstates(self, edges, km):
        pike = self._lw(edges, km, r"^I 90$", "ref")
        for road in ["mohawk trail", "notch road|rockwell road"]:
            assert self._lw(edges, km, road) > pike + 2.0, road

    def test_the_mass_pike_scores_poorly(self, edges, km):
        assert self._lw(edges, km, r"^I 90$", "ref") < 2.5

    def test_motorways_score_below_ordinary_roads(self, edges, km):
        s = edges["score"].to_numpy()
        motorway = edges["highway"].to_numpy() == "motorway"
        assert weighted_mean(s, km, motorway) < weighted_mean(s, km, ~motorway) - 2.0


class TestCurvatureOnRealRoads:
    def test_no_physically_absurd_values(self, chunks):
        """The old metric reached 3,500 deg/km — ten full rotations inside one
        kilometre — because it was integrating vertex noise."""
        v = curvature_deg_per_km(chunks.geometry.values)
        assert np.percentile(v, 99.9) < 20 * CURVE_FULL

    def test_interstates_are_straighter_than_hill_roads(self, chunks):
        v = curvature_deg_per_km(chunks.geometry.values)
        pike = chunks["ref"].fillna("").str.fullmatch("I 90").to_numpy()
        hills = chunks["name"].fillna("").str.contains(
            "notch road|rockwell road", case=False, regex=True).to_numpy()
        if not (pike.any() and hills.any()):
            pytest.skip("benchmark roads not present")
        assert np.median(v[hills]) > 3 * np.median(v[pike])
