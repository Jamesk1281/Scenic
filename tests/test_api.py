"""API contract tests, driven through Flask's test client (no live server).

These pin the shape of the JSON the iOS app decodes: a field renamed here is a
silent decode failure on the phone.
"""

import sys

import pytest

from conftest import DATA, ROOT, ROUTER_DATA
from router import BEAUTIFUL_SCORE


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
        assert {"km", "minutes", "mean_score", "beautiful_km", "beautiful_score",
                "scenery_km", "steps"} <= set(props)
        assert props["km"] > 0 and props["minutes"] > 0
        assert 0 <= props["mean_score"] <= 10
        assert props["steps"][-1]["instruction"] == "Arrive at your destination"


def test_a_route_reports_its_beautiful_km(client):
    """The headline the app leads with, in place of the 0-10 mean.

    A loop has carried this number since the loop tab shipped; a point-to-point
    route now carries the same one, computed the same way and against the same
    threshold, so "31 of your 50 miles" means one thing across both tabs.
    """
    body = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=1.0").get_json()
    for feature in body.values():
        props = feature["properties"]
        assert 0 <= props["beautiful_km"] <= props["km"]
        # Travels with the number so the client never hardcodes the bar.
        assert props["beautiful_score"] == BEAUTIFUL_SCORE
        # A numpy float here is a 500 that only appears once a real number
        # lands in a field nothing looked at.
        assert isinstance(props["beautiful_km"], float)
        assert isinstance(props["beautiful_score"], float)


def test_scenic_trades_time_for_scenery(client):
    props = client.get(
        f"/api/route?from={WORCESTER}&to={BOSTON}&pref=1.0"
    ).get_json()
    fast, scenic = props["fastest"]["properties"], props["scenic"]["properties"]
    assert scenic["minutes"] > fast["minutes"]
    assert scenic["mean_score"] > fast["mean_score"]
    # The legible half of the same trade, and the one the app now shows. Both
    # arms are scored with the caller's weights, so the pair is on one scale.
    assert scenic["beautiful_km"] > fast["beautiful_km"]


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


def test_a_scenic_route_scoring_below_the_fastest_one_is_not_offered(client):
    """The scenic arm has one job, and a route that scores under the fastest
    one has not done it.

    The detour cost is proportional to length, so a *shorter* route can carry
    less total penalty while being uglier per kilometre — measured on 6 of 983
    sampled trips (docs/route-distribution-study.md), the worst of them 4.8 km
    shorter, slower, and scoring 5.21 against 5.79. The app rendered that as
    "raises scenery 5.8 -> 5.2".
    """
    import app as server_app

    class Arm:
        def __init__(self, score):
            self.mean_score = score

    fastest = Arm(5.794)
    assert server_app._no_worse_than_fastest(fastest, Arm(5.210)) is fastest
    # A tie is not a failure — pref 0 hands the same object in as both arms.
    tie = Arm(5.794)
    assert server_app._no_worse_than_fastest(fastest, tie) is tie
    better = Arm(6.301)
    assert server_app._no_worse_than_fastest(fastest, better) is better


def test_the_route_that_found_this_defect_no_longer_returns_it(client):
    """Hancock -> Shutesbury, the case this was caught on in Massachusetts.

    Without the guard the scenic arm comes back 3.8 km *shorter*, 0.3 minutes
    slower, and scoring 5.98 against the fastest route's 6.07 — the length-
    proportional penalty buying "scenery" credit by cutting distance.
    """
    body = client.get("/api/route?from=42.4443,-73.0787&to=42.4945,-72.4684"
                      "&pref=0.5").get_json()
    fast = body["fastest"]["properties"]
    scenic = body["scenic"]["properties"]
    assert scenic["mean_score"] >= fast["mean_score"]
    # It is the fastest route that comes back, so the app says "same drive".
    assert scenic["mean_score"] == fast["mean_score"]
    assert scenic["minutes"] == fast["minutes"]


@pytest.mark.parametrize("pref", ["0.25", "0.5", "0.8", "1.0"])
@pytest.mark.parametrize("pair", [
    (WORCESTER, BOSTON),
    (BOSTON, "41.6362,-70.9342"),                # Buzzards Bay
    ("42.6334,-71.3162", "42.0834,-72.5866"),    # Lowell -> Springfield
    ("42.4443,-73.0787", "42.4945,-72.4684"),    # Hancock -> Shutesbury
    ("41.9019,-71.0931", "41.9166,-71.1141"),    # a short Attleboro hop
])
def test_the_scenic_arm_never_scores_below_the_fastest_arm(client, pair, pref):
    """The property the guard exists to hold, asked of the real graph. The last
    two pairs are known to have broken it before the guard; the rest are a net."""
    body = client.get(f"/api/route?from={pair[0]}&to={pair[1]}"
                      f"&pref={pref}").get_json()
    fast = body["fastest"]["properties"]["mean_score"]
    scenic = body["scenic"]["properties"]["mean_score"]
    assert scenic >= fast, f"{pair} at pref {pref}: {fast} -> {scenic}"


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


# --- /api/loop ---------------------------------------------------------------
# One endpoint and a length instead of two endpoints. The loop-specific numbers
# live in `meta`, deliberately, so `RouteFeature` on the phone decodes a loop and
# a route with the same type — see ios/Sources/Models.swift.

NEEDHAM = "42.2809,-71.2378"
PETERSHAM = "42.4879,-72.1889"


def test_loop_returns_a_closed_route_and_its_metadata(client):
    body = client.get(f"/api/loop?from={NEEDHAM}&km=40").get_json()
    assert set(body) == {"loop", "meta", "alternatives", "note"}

    feature = body["loop"]
    assert feature["geometry"]["type"] == "LineString"
    # The same properties a point-to-point route carries, so one client type
    # decodes both.
    props = feature["properties"]
    assert {"km", "minutes", "mean_score", "scenery_km", "steps"} <= set(props)
    assert props["steps"][0]["type"] == "depart"
    assert props["steps"][-1]["type"] == "arrive"

    # ...and it comes back to where it started.
    coords = feature["geometry"]["coordinates"]
    assert abs(coords[0][0] - coords[-1][0]) < 0.001
    assert abs(coords[0][1] - coords[-1][1]) < 0.001

    meta = body["meta"]
    assert {"target_km", "km", "minutes", "mean_score", "beautiful_km",
            "beautiful_score", "repeated_km", "turnaround", "sector"} == set(meta)
    assert meta["target_km"] == 40.0
    assert abs(meta["km"] - 40.0) / 40.0 < 0.12
    assert 0 <= meta["beautiful_km"] <= meta["km"]
    assert meta["repeated_km"] / meta["km"] < 0.10
    assert len(meta["turnaround"]) == 2
    assert body["note"] is None


def test_loop_numbers_are_json_and_not_numpy(client):
    """A numpy float64 in the response is a 500, and it is the kind of 500 that
    only shows up once a real number lands in a field a test never looked at."""
    import json
    body = client.get(f"/api/loop?from={NEEDHAM}&km=20").get_json()
    json.dumps(body)   # raises TypeError on anything numpy
    for key, value in body["meta"].items():
        if key not in ("sector", "turnaround"):
            assert isinstance(value, (int, float)), f"{key} is {type(value)}"


def test_loop_offers_the_directions_that_exist(client):
    body = client.get(f"/api/loop?from={NEEDHAM}&km=40").get_json()
    alternatives = body["alternatives"]
    assert alternatives
    assert all(set(a) == {"sector", "candidates"} for a in alternatives)
    assert all(a["candidates"] > 0 for a in alternatives)
    # Every direction offered has to actually deliver, or the app shows a button
    # that fails.
    for alternative in alternatives[:3]:
        other = client.get(f"/api/loop?from={NEEDHAM}&km=40"
                           f"&sector={alternative['sector']}").get_json()
        assert other["meta"]["sector"] == alternative["sector"]


def test_regenerate_gives_a_different_drive(client):
    """The product claim behind the button. Two sectors must not be the same
    roads with a different label."""
    body = client.get(f"/api/loop?from={NEEDHAM}&km=40").get_json()
    sectors = [a["sector"] for a in body["alternatives"]][:2]
    lines = []
    for sector in sectors:
        loop = client.get(f"/api/loop?from={NEEDHAM}&km=40"
                          f"&sector={sector}").get_json()
        lines.append({tuple(c) for c in loop["loop"]["geometry"]["coordinates"]})
    overlap = len(lines[0] & lines[1]) / len(lines[0] | lines[1])
    assert overlap < 0.5


def test_a_loop_too_short_for_the_geography_says_so(client):
    """A rural start has no good 8 km loop. The answer is the best available
    plus a note, not an error and not a silent bad loop."""
    body = client.get(f"/api/loop?from={PETERSHAM}&km=8").get_json()
    assert body["loop"] is not None
    assert body["note"] and "doubles back" in body["note"]


def test_the_distance_is_clamped_not_rejected(client):
    for km, expected in ((1, 5.0), (9999, 200.0)):
        meta = client.get(f"/api/loop?from={NEEDHAM}&km={km}").get_json()["meta"]
        assert meta["target_km"] == expected


@pytest.mark.parametrize("query", [
    "km=40",                                    # no start
    f"from={NEEDHAM}&km=abc",                   # unparseable distance
    f"from={NEEDHAM}&km=40&pref=zzz",           # unparseable preference
])
def test_bad_loop_requests_are_rejected(client, query):
    assert client.get(f"/api/loop?{query}").status_code == 400


def test_an_unknown_sector_is_a_bad_request(client):
    response = client.get(f"/api/loop?from={NEEDHAM}&km=40&sector=NNE")
    assert response.status_code == 400
    assert "sector" in response.get_json()["error"]


def test_a_loop_start_outside_the_region_is_rejected(client):
    response = client.get("/api/loop?from=45.5,-73.6&km=40")   # Montreal
    assert response.status_code == 400
    assert "outside" in response.get_json()["error"]


def test_beauty_weights_reach_the_loop(client):
    """The tune screen has to mean something here too, or a user who asked for
    coast gets a loop chosen as though they hadn't."""
    plain = client.get(f"/api/loop?from={NEEDHAM}&km=40").get_json()
    coastal = client.get(f"/api/loop?from={NEEDHAM}&km=40"
                         "&w_coast=4&w_farm=0").get_json()
    assert (plain["loop"]["geometry"]["coordinates"]
            != coastal["loop"]["geometry"]["coordinates"])


def test_the_index_advertises_the_loop_endpoint(client):
    body = client.get("/").get_json()
    assert "/api/loop" in body["endpoints"]
    assert "NE" in body["loop_sectors"]


def test_an_identical_loop_request_is_served_from_memory(client):
    """Shuffling forward and then back to the one you liked is the common
    interaction, not an edge case, and it should not rebuild anything."""
    import time
    url = f"/api/loop?from={NEEDHAM}&km=35&sector=N"
    first = client.get(url).get_json()
    started = time.perf_counter()
    again = client.get(url).get_json()
    elapsed = time.perf_counter() - started
    assert again == first
    assert elapsed < 0.05, f"a repeat request took {elapsed*1000:.0f} ms"


# --- via, for rejoining a loop ----------------------------------------------
# One caller: a driver who has left a loop. A loop's destination is its origin,
# so a plain reroute would hand back the short way home; `via` pins the
# replacement through the loop's far point so the rest of the drive survives.

def test_via_pins_the_route_through_the_waypoint(client):
    plain = client.get(f"/api/route?from={NEEDHAM}&to={NEEDHAM.replace('42.2809', '42.2909')}"
                       "&pref=1.0").get_json()
    detour = client.get(f"/api/route?from={NEEDHAM}"
                        f"&to={NEEDHAM.replace('42.2809', '42.2909')}"
                        f"&via={WORCESTER}&pref=1.0").get_json()
    # Worcester is 60 km west; a route through it cannot be the direct one.
    assert detour["scenic"]["properties"]["km"] > \
        plain["scenic"]["properties"]["km"] * 5
    # ...and it really passes through, rather than merely being longer.
    line = detour["scenic"]["geometry"]["coordinates"]
    worcester_lat, worcester_lon = (float(x) for x in WORCESTER.split(","))
    closest = min(abs(lon - worcester_lon) + abs(lat - worcester_lat)
                  for lon, lat in line)
    assert closest < 0.02, "the route never gets near the waypoint"


def test_a_via_route_is_one_continuous_set_of_directions(client):
    """Spliced from two searches, but the driver must not be able to tell: one
    depart at the front, one arrive at the back, and nothing in between."""
    body = client.get(f"/api/route?from={NEEDHAM}&to={BOSTON}"
                      f"&via={WORCESTER}&pref=1.0").get_json()
    steps = body["scenic"]["properties"]["steps"]
    assert steps[0]["type"] == "depart"
    assert steps[-1]["type"] == "arrive"
    assert [s["type"] for s in steps].count("depart") == 1
    assert [s["type"] for s in steps].count("arrive") == 1
    # Distances and the drawn line have to agree across the join too.
    assert body["scenic"]["properties"]["km"] > 0
    assert body["scenic"]["properties"]["minutes"] > 0


def test_a_via_route_still_returns_both_options(client):
    body = client.get(f"/api/route?from={NEEDHAM}&to={BOSTON}"
                      f"&via={WORCESTER}&pref=1.0").get_json()
    assert set(body) == {"fastest", "scenic"}
    assert body["scenic"]["properties"]["mean_score"] > \
        body["fastest"]["properties"]["mean_score"]


def test_a_waypoint_outside_the_region_is_rejected(client):
    response = client.get(f"/api/route?from={NEEDHAM}&to={BOSTON}&via=45.5,-73.6")
    assert response.status_code == 400
    assert "waypoint" in response.get_json()["error"]


def test_an_unparseable_waypoint_is_a_bad_request(client):
    assert client.get(f"/api/route?from={NEEDHAM}&to={BOSTON}"
                      "&via=nonsense").status_code == 400


def test_avoid_unpaved_is_accepted_and_clamped(client):
    """The surface preference is its own query parameter, not a `w_<type>`:
    those six are renormalised against each other, so an avoidance among them
    would quietly turn every attraction down."""
    for value in ("0", "1", "2", "-5", "99", "1.5"):
        r = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0.6"
                       f"&avoid_unpaved={value}")
        assert r.status_code == 200, value


def test_avoid_unpaved_rejects_nonsense(client):
    r = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&avoid_unpaved=lots")
    assert r.status_code == 400


def test_avoid_unpaved_does_not_move_the_reported_score(client):
    """It is priced in minutes, not in beauty. Two routes that differ only in
    surface avoidance may take different roads, but neither is *scored* for
    its surface — so the scale the app displays is unchanged."""
    free = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0"
                      "&avoid_unpaved=0").get_json()["scenic"]["properties"]
    firm = client.get(f"/api/route?from={WORCESTER}&to={BOSTON}&pref=0"
                      "&avoid_unpaved=2").get_json()["scenic"]["properties"]
    # pref=0 is the fastest route, which no scenery setting may move.
    assert free["km"] == firm["km"]
    assert free["mean_score"] == firm["mean_score"]


def test_loop_accepts_the_surface_preference(client):
    r = client.get(f"/api/loop?from={NEEDHAM}&km=40&avoid_unpaved=0")
    assert r.status_code == 200
    assert "loop" in r.get_json()
