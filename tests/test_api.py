"""API contract tests, driven through Flask's test client (no live server).

These pin the shape of the JSON the iOS app decodes: a field renamed here is a
silent decode failure on the phone.
"""

import sys

import pytest

from conftest import DATA, ROOT, ROUTER_DATA


@pytest.fixture(scope="session")
def client():
    # The whole set: importing `app` builds a Router at module scope, and that
    # raises on any one of them missing rather than degrading.
    missing = [f for f in ROUTER_DATA if not (DATA / f).exists()]
    if missing:
        pytest.skip(f"built graph missing ({', '.join(missing)}) — "
                    "run the pipeline first")
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


def test_root_describes_the_service(client):
    """A bare visit must not 404 — that reads as a broken deploy when checking
    a tunnel from a browser."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_json()
    assert body["service"] == "scenic-api"
    assert "/api/route" in body["endpoints"]
    assert "coast" in body["beauty_types"]


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


def test_both_routes_are_scored_on_the_same_scale(client):
    """The app shows "scenery 4.1 -> 6.3" side by side, so the two numbers have
    to be measured the same way. Weighting only the scenic one made the
    comparison meaningless — and the fastest route's own path must not move,
    since pref 0 zeroes the scenery term whatever the weights say."""
    plain = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.7").get_json()
    tuned = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.7"
                       "&w_coast=4&w_town=0&w_farm=0").get_json()

    # same path for "fastest" either way...
    assert tuned["fastest"]["geometry"] == plain["fastest"]["geometry"]
    # ...but rescored, along with the scenic route, on the user's own scale
    assert tuned["fastest"]["properties"]["mean_score"] != \
        plain["fastest"]["properties"]["mean_score"]


def test_tuning_changes_the_reported_score(client):
    """A tune slider that moves the route but not its reported score is the
    defect this guards."""
    plain = client.get(f"/api/route?from={BOSTON}&to=41.6362,-70.9342"
                       "&pref=0.8").get_json()["scenic"]["properties"]
    coastal = client.get(f"/api/route?from={BOSTON}&to=41.6362,-70.9342"
                         "&pref=0.8&w_coast=4&w_town=0&w_farm=0"
                         ).get_json()["scenic"]["properties"]
    assert coastal["mean_score"] != plain["mean_score"]
    assert coastal["scenery_km"]["coast"] > plain["scenery_km"]["coast"]


def test_weights_are_clamped_not_rejected(client):
    for query in ("w_coast=-3", "w_coast=99", "w_coast=1e999"):
        r = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.5&{query}")
        assert r.status_code == 200, query


def test_unparseable_weight_is_a_bad_request(client):
    r = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&w_coast=abc")
    assert r.status_code == 400


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


def test_a_heading_is_accepted(client):
    r = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.5&heading=90")
    assert r.status_code == 200
    assert r.get_json()["scenic"]["properties"]["km"] > 0


@pytest.mark.parametrize("heading", ["-1", "400", "-720", ""])
def test_an_unusable_heading_is_ignored_not_wrapped(client, heading):
    """The dangerous one is -1: CoreLocation reports it for "no opinion", and
    normalising the range would turn that into a confident due north, because
    `-1 % 360` is 359. That points the start at the wrong end of the road with
    nothing to catch it — a silently worse route, not an error. So an unusable
    heading has to behave exactly as if none had been sent.
    """
    plain = client.get(
        f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.5").get_json()
    given = client.get(
        f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.5&heading={heading}"
    ).get_json()
    assert given["scenic"]["geometry"] == plain["scenic"]["geometry"]


def test_an_unparseable_heading_is_a_bad_request(client):
    """Out of range is a client with no fix; non-numeric is a client with a
    bug, and those should be loud."""
    r = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&heading=abc")
    assert r.status_code == 400
