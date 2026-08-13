"""Shared test setup: put pipeline/ and tools/ on the path, locate the built data.

The pure-function tests run anywhere. The calibration and routing tests need a
built graph, which is large and gitignored, so they skip cleanly when it's
absent (a fresh clone, or CI) instead of failing.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))

DATA = Path(os.environ.get("SCENIC_DATA", ROOT / "data" / "processed"))


def _require(*files):
    missing = [f for f in files if not (DATA / f).exists()]
    if missing:
        pytest.skip(f"built data missing ({', '.join(missing)}) — run the pipeline first")
    return DATA


@pytest.fixture(scope="session")
def chunks():
    """The scored road chunks (output of score.py)."""
    import geopandas as gpd
    return gpd.read_parquet(_require("scored_chunks.parquet") / "scored_chunks.parquet")


@pytest.fixture(scope="session")
def edges():
    """The routable graph edges (output of graph.py)."""
    import geopandas as gpd
    return gpd.read_parquet(_require("graph_edges.parquet") / "graph_edges.parquet")


@pytest.fixture(scope="session")
def router():
    """A loaded Router. Slow to build, so it is shared across the session."""
    _require("graph_edges.parquet", "graph_nodes.parquet")
    from router import Router
    return Router(str(DATA))
