"""Pure-function tests for the scoring maths. No built data needed.

The curvature cases below are the ones that were silently wrong: the old metric
summed heading change between raw ~20 m OSM vertices, so digitizing jitter on a
straight road read as extreme twistiness, and a short stub divided its way to
thousands of deg/km.
"""

import numpy as np
import pytest
import shapely

from score import (CLASS_ADJ, CURVE_FULL, RAW_BASE, STRETCH, UNPAVED,
                   WEIGHTS, composite, curvature_deg_per_km)


def line(points):
    return np.array([shapely.LineString(points)])


def straight(length=1000.0, spacing=20.0):
    n = int(length / spacing) + 1
    return [(i * spacing, 0.0) for i in range(n)]


def jittered(length=1000.0, spacing=20.0, amplitude=2.5, seed=0):
    """A straight road digitized with a couple of metres of position error —
    exactly what OSM road geometry looks like."""
    rng = np.random.default_rng(seed)
    return [(x, rng.normal(0, amplitude)) for x, _ in straight(length, spacing)]


def arc(radius, sweep_deg, spacing=20.0):
    sweep = np.radians(sweep_deg)
    n = max(int(radius * sweep / spacing), 2)
    a = np.linspace(0, sweep, n + 1)
    return list(zip(radius * np.sin(a), radius * (1 - np.cos(a))))


class TestCurvature:
    def test_perfectly_straight_road_has_no_curvature(self):
        assert curvature_deg_per_km(line(straight()))[0] == 0.0

    def test_digitizing_jitter_does_not_compete_with_a_real_curve(self):
        """The original bug: metre-scale vertex noise on a dead-straight road
        integrated to hundreds of deg/km, outscoring genuine switchbacks."""
        noise = max(curvature_deg_per_km(line(jittered(amplitude=1.0, seed=s)))[0]
                    for s in range(5))
        curve = curvature_deg_per_km(line(arc(200, 90)))[0]
        assert noise < 0.3 * curve, f"jitter scored {noise:.0f} vs curve {curve:.0f}"
        assert noise < CURVE_FULL

    def test_short_stub_cannot_explode(self):
        """A 25 m cul-de-sac stub with a hard bend used to reach >20,000 deg/km
        purely by dividing by its own tiny length."""
        stub = line([(0, 0), (12, 0), (12, 12), (0, 12)])
        assert curvature_deg_per_km(stub)[0] < 5 * CURVE_FULL

    def test_a_real_curve_still_registers(self):
        """A 200 m-radius 90-degree bend is genuinely twisty and must score high."""
        value = curvature_deg_per_km(line(arc(200, 90)))[0]
        assert value >= CURVE_FULL

    def test_tighter_curves_score_higher(self):
        gentle = curvature_deg_per_km(line(arc(800, 90)))[0]
        sharp = curvature_deg_per_km(line(arc(200, 90)))[0]
        assert sharp > 2 * gentle

    def test_short_line_is_still_measured(self):
        """A line shorter than two sampling steps must not silently score zero —
        the first cut of this metric discarded its final partial chord."""
        assert curvature_deg_per_km(line(arc(150, 45)))[0] > 0

    def test_scales_with_turning_not_with_length(self):
        """deg/km is a rate: two identical curves back to back should score about
        the same as one, not double."""
        one = curvature_deg_per_km(line(arc(300, 60)))[0]
        pts = arc(300, 60)
        shifted = [(x, y) for x, y in arc(300, 60)]
        last = pts[-1]
        two = curvature_deg_per_km(
            line(pts + [(last[0] + x, last[1] + y) for x, y in shifted[1:]])
        )[0]
        assert one == 0 or 0.5 < two / one < 2.0


class TestComposite:
    def test_uses_the_full_range(self):
        """A road with nothing going for it and one with everything must land at
        opposite ends of 0-10; the old calibration bunched everything into 0-6."""
        worst = composite(np.array([0.0]), np.array([0.0]))[0]
        best = composite(np.array([sum(WEIGHTS.values())]), np.array([0.0]))[0]
        assert worst < 3.0
        assert best >= 9.5

    def test_clamped_to_scale(self):
        assert composite(np.array([5.0]), np.array([0.0]))[0] == 10.0
        assert composite(np.array([0.0]), np.array([-9.0]))[0] == 0.0

    def test_monotonic_in_raw(self):
        raw = np.linspace(0, 0.8, 40)
        out = composite(raw, np.zeros_like(raw))
        assert np.all(np.diff(out) >= 0)

    def test_motorway_penalty_sinks_an_otherwise_average_road(self):
        raw = np.array([0.25, 0.25])
        adj = np.array([0.0, CLASS_ADJ["motorway"]])
        plain, motorway = composite(raw, adj)
        assert motorway < plain
        assert motorway < 1.0

    def test_matches_the_documented_formula(self):
        raw, adj = 0.3, -0.05
        expected = 10.0 * ((raw + RAW_BASE) * STRETCH + adj)
        assert composite(np.array([raw]), np.array([adj]))[0] == expected


class TestSurfaceIsNotBeauty:
    """`UNPAVED_ADJ` used to take 2.5 points off a dirt road. It measured
    mapping diligence and contradicted this file's own components, so surface
    left the score for a minutes-priced avoidance in router.py. See
    docs/unpaved-and-urban-verdict.md."""

    def test_the_score_makes_no_claim_about_surface(self):
        """Written against the class table directly rather than by importing
        whatever `score_adj` happens to be built from — the point is that a
        surface term cannot creep back in unnoticed."""
        for highway, expected in (("residential", -0.05), ("motorway", -0.45),
                                  ("unclassified", 0.0), ("tertiary", 0.0)):
            assert CLASS_ADJ[highway] == expected
        # And the composite itself: a -0.25 surface term would move this.
        assert composite(0.30, CLASS_ADJ["unclassified"]) == pytest.approx(
            10.0 * ((0.30 + RAW_BASE) * STRETCH))

    def test_compacted_is_unpaved(self):
        """The omission that let 2,258 km escape, 1,125 km of it in Maine —
        the state already escaping most through untagged roads."""
        assert "compacted" in UNPAVED

    def test_the_unpaved_set_is_osms_unpaved_family(self):
        """Spot values, written out rather than imported, so a careless edit to
        the set fails here instead of silently re-scoring a state."""
        for value in ("unpaved", "gravel", "dirt", "ground", "compacted",
                      "fine_gravel", "pebblestone", "sand", "grass", "mud"):
            assert value in UNPAVED, value

    def test_sealed_and_laid_surfaces_are_not_unpaved(self):
        """Rough is not the same as unpaved. A cobbled lane is a paved road."""
        for value in ("asphalt", "concrete", "paved", "chipseal", "sett",
                      "paving_stones", "cobblestone"):
            assert value not in UNPAVED, value
