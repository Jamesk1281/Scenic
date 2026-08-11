"""API contract tests, driven through Flask's test client (no live server).

These pin the shape of the JSON the iOS app decodes: a field renamed here is a
silent decode failure on the phone.
"""

import sys

import pytest

from conftest import DATA, ROOT


@pytest.fixture(scope="session")
def client():
    if not (DATA / "graph_edges.parquet").exists():
        pytest.skip("built graph missing — run the pipeline first")
    import os
    os.environ.setdefault("SCENIC_DATA", str(DATA))
    sys.path.insert(0, str(ROOT / "server"))
    import app as server_app
    server_app.app.config["TESTING"] = True
    return server_app.app.test_client()


BOSTON = "42.3551,-71.0657"
WORCESTER = "42.2626,-71.8023"


def test_health(client):
    body = client.get("/api/health").get_json()
    assert body["status"] == "ok" and body["nodes"] > 0


def test_route_returns_both_options(client):
    body = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.6").get_json()
    assert set(body) == {"fastest", "scenic"}
    for feature in body.values():
        assert feature["geometry"]["type"] == "LineString"
        props = feature["properties"]
        assert {"km", "minutes", "mean_score", "scenery_km", "steps"} <= set(props)
        assert props["km"] > 0 and props["minutes"] > 0
        assert 0 <= props["mean_score"] <= 10
        assert props["steps"][-1]["instruction"] == "Arrive at your destination"


def test_scenic_trades_time_for_scenery(client):
    props = client.get(
        f"/api/route?from={WORCESTER}&to={BOSTON}&pref=1.0"
    ).get_json()
    fast, scenic = props["fastest"]["properties"], props["scenic"]["properties"]
    assert scenic["minutes"] > fast["minutes"]
    assert scenic["mean_score"] > fast["mean_score"]


def test_pref_zero_reuses_the_fastest_route(client):
    body = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0").get_json()
    assert body["scenic"]["properties"] == body["fastest"]["properties"]


def test_beauty_weights_are_accepted(client):
    r = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.7"
                   "&w_coast=4&w_town=0&w_farm=0")
    assert r.status_code == 200


@pytest.mark.parametrize("query", [
    "",                                       # nothing at all
    f"from={WORCESTER}",                      # missing destination
    f"from=garbage&to={BOSTON}",              # unparseable point
    f"from={WORCESTER}&to={BOSTON}&pref=abc",  # unparseable preference
])
def test_bad_requests_are_rejected(client, query):
    r = client.get(f"/api/route?{query}")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_points_outside_the_region_are_rejected(client):
    r = client.get(f"/api/route?from=40.7128,-74.0060&to={BOSTON}")   # New York
    assert r.status_code == 400
    assert "outside" in r.get_json()["error"]


def test_identical_endpoints_are_rejected(client):
    r = client.get(f"/api/route?from={BOSTON}&to={BOSTON}")
    assert r.status_code == 400


def test_preference_is_clamped_not_rejected(client):
    for pref in ("-5", "9"):
        assert client.get(
            f"/api/route?from={WORCESTER}&to={BOSTON}&pref={pref}"
        ).status_code == 200
