"""Tests for the graph build — chiefly how a chunk's score reaches an edge.

Edges are split at junctions, chunks every 400 m, and the two grids do not line
up. The build used to read one chunk at each edge's midpoint, which discarded
the rest of a long edge: 7.7% of edges are longer than a chunk and they carry
35% of the state's road-km. The cases below pin the sampler that replaced it.
"""

import geopandas as gpd
import numpy as np
import pytest
import shapely

from common import CRS_METERS
from graph import SAMPLE_STEP_M, attach_scores, sample_offsets, parse_maxspeed
from score import WEIGHTS, blend, components, composite

ALL_COMPONENTS = [f"c_{k}" for k in WEIGHTS]


class TestSampleOffsets:
    @pytest.mark.parametrize("length,expected_pieces", [
        (10.0, 1), (100.0, 1), (100.1, 2), (350.0, 4), (1000.0, 10),
    ])
    def test_piece_count(self, length, expected_pieces):
        row, _, _ = sample_offsets(np.array([length]), step=100.0)
        assert len(row) == expected_pieces

    def test_pieces_tile_the_edge_exactly(self):
        lengths = np.array([37.0, 250.0, 999.0, 19_184.0])
        row, along, piece = sample_offsets(lengths, step=100.0)
        for i, length in enumerate(lengths):
            mine = row == i
            # every metre of the edge is accounted for exactly once...
            assert piece[mine].sum() == pytest.approx(length)
            # ...and every reading is taken strictly inside it
            assert (along[mine] > 0).all()
            assert (along[mine] < length).all()

    def test_readings_are_evenly_spaced_midpoints(self):
        _, along, piece = sample_offsets(np.array([1000.0]), step=100.0)
        assert along == pytest.approx(np.arange(50.0, 1000.0, 100.0))
        assert piece == pytest.approx(np.full(10, 100.0))

    def test_short_edges_keep_the_old_single_midpoint_reading(self):
        """Anything up to one step behaves exactly as the midpoint sample did,
        so the fix cannot perturb the 92% of edges that were already right."""
        for length in (1.0, 50.0, SAMPLE_STEP_M):
            _, along, _ = sample_offsets(np.array([length]), step=SAMPLE_STEP_M)
            assert len(along) == 1
            assert along[0] == pytest.approx(length / 2)


def _chunks(values_by_offset):
    """Chunks tiling a straight 1000 m line east from the origin.

    `values_by_offset` is [(start, end, component_value), ...]; every component
    takes that same value, which keeps the arithmetic in the tests obvious.
    """
    rows, geoms = [], []
    for start, end, value in values_by_offset:
        geoms.append(shapely.LineString([(start, 0.0), (end, 0.0)]))
        rows.append({**{c: float(value) for c in ALL_COMPONENTS}, "score_adj": 0.0})
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=CRS_METERS)
    gdf["score"] = composite(blend(gdf[ALL_COMPONENTS]).to_numpy(),
                             gdf["score_adj"].to_numpy())
    return gdf


def _edge(start, end):
    return gpd.GeoDataFrame(
        {"length_m": [float(end - start)]},
        geometry=[shapely.LineString([(start, 0.0), (end, 0.0)])],
        crs=CRS_METERS,
    )


class TestAttachScores:
    def test_long_edge_averages_the_chunks_it_covers(self):
        """The defect this guards: an edge spanning a dull half and a lovely
        half took whichever one happened to sit under its midpoint."""
        chunks = _chunks([(0, 500, 0.0), (500, 1000, 1.0)])
        edges = _edge(0, 1000)
        attach_scores(edges, chunks, step=100.0)
        for component in ALL_COMPONENTS:
            assert edges[component][0] == pytest.approx(0.5)

    def test_weighting_follows_length_not_chunk_count(self):
        """Three quarters of the edge is scenic, so the answer is 0.75 — not
        0.5, which is what counting chunks rather than metres would give."""
        chunks = _chunks([(0, 250, 0.0), (250, 1000, 1.0)])
        edges = _edge(0, 1000)
        attach_scores(edges, chunks, step=50.0)
        assert edges["c_water"][0] == pytest.approx(0.75, abs=0.01)

    def test_short_edge_is_unchanged_by_the_sampler(self):
        chunks = _chunks([(0, 500, 0.2), (500, 1000, 0.9)])
        edges = _edge(0, 80)
        attach_scores(edges, chunks, step=100.0)
        assert edges["c_water"][0] == pytest.approx(0.2)

    def test_score_is_recomputed_from_the_averaged_components(self):
        """Averaging the composite instead of rebuilding it from the averaged
        components would break the identity the router's live re-blend needs,
        because `composite` clips."""
        chunks = _chunks([(0, 500, 0.0), (500, 1000, 1.0)])
        edges = _edge(0, 1000)
        attach_scores(edges, chunks, step=100.0)
        raw = blend(edges[ALL_COMPONENTS]).to_numpy()
        assert edges["score"].to_numpy() == pytest.approx(
            composite(raw, edges["score_adj"].to_numpy()))

    def test_components_stay_inside_the_unit_range(self):
        chunks = _chunks([(0, 400, 1.0), (400, 700, 0.0), (700, 1000, 1.0)])
        edges = _edge(0, 1000)
        attach_scores(edges, chunks, step=100.0)
        for component in ALL_COMPONENTS:
            assert 0.0 <= edges[component][0] <= 1.0

    def test_every_component_column_is_carried(self):
        chunks = _chunks([(0, 1000, 0.5)])
        edges = _edge(0, 1000)
        written = attach_scores(edges, chunks, step=100.0)
        assert set(written) == set(ALL_COMPONENTS)
        assert set(ALL_COMPONENTS) <= set(edges.columns)


class TestMaxspeed:
    @pytest.mark.parametrize("tag,expected", [
        ("", None), ("none", None), ("walk", None), ("RU:urban", None),
        ("50", 50.0), ("30 km/h", 30.0),
        ("55 mph", pytest.approx(88.5, abs=0.1)),
    ])
    def test_parses_or_declines(self, tag, expected):
        assert parse_maxspeed(tag) == expected


class TestOnRealData:
    def test_the_blend_covers_every_component_the_graph_carries(self, edges):
        """A c_ column with no WEIGHTS entry would be silently unweighted in
        score.py and a KeyError in graph.py — catch the mismatch here instead."""
        assert set(components(edges)) == set(ALL_COMPONENTS)

    def test_long_edges_no_longer_read_a_single_point(self, edges):
        """A midpoint reading makes an edge's score exactly equal to some
        chunk's score. Averaging makes that a coincidence rather than a rule,
        and the long edges are where it shows."""
        long = edges[edges["length_m"] > 1200]
        if len(long) < 100:
            pytest.skip("no long edges in this extract")
        # c_water only ever takes 0, 0.45 or 1 on a chunk; an averaged edge
        # lands between those.
        blended = ~np.isin(np.round(long["c_water"].to_numpy(), 4), [0.0, 0.45, 1.0])
        assert blended.mean() > 0.05, "long edges still look point-sampled"
