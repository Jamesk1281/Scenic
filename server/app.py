"""Scenic routing API (backend for the iOS app).

Loads the routing graph once at startup and serves:
  GET  /api/route?from=LAT,LON&to=LAT,LON&pref=0.5[&heading=DEG][&w_<type>=...]
                              -> {"fastest": <GeoJSON Feature>,
                                  "scenic":  <GeoJSON Feature>}
  GET  /api/health

`pref` (0..1) is the overall scenery strength. Each beauty type can also be
weighted with w_<type> (e.g. w_coast=2&w_town=3&w_farm=0); each defaults to 1.0
(neutral) and is clamped to a sane range. The tunable types are listed by
BEAUTY_TYPES in router.py.

`heading` (0..360, 0=N, clockwise) is the driver's course over ground, and
applies to `from` only — a destination has no travel direction. With it, the
start snaps to the end of its road that lies ahead rather than the nearer one,
so a mid-drive reroute doesn't open by turning the driver around. Send it only
while moving; omit it when planning from a parked car.

Run:  python server/app.py [processed_dir]   (default: data/processed)
"""

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request
from flask_compress import Compress
from flask_cors import CORS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from router import BEAUTY_TYPES, Router  # noqa: E402

# How far a user may push a single beauty type. 0 ignores it; the upper bound
# keeps one cranked slider from completely swamping the others.
WEIGHT_MIN, WEIGHT_MAX = 0.0, 4.0

# Reject a request whose endpoint lies farther than this from any road — it's
# outside the covered region (currently Massachusetts), and the "nearest" road
# would be in an arbitrary border town, yielding a nonsense route.
SNAP_MAX_M = 5000.0

# Where the prebuilt graph lives. An env var (not a CLI arg) so it works
# identically whether run directly (python server/app.py) or via the waitress
# entrypoint (server/serve.py). Defaults to the repo's data/processed.
PROCESSED = os.environ.get("SCENIC_DATA", str(ROOT / "data" / "processed"))

app = Flask(__name__, static_folder=None)
CORS(app)
# Gzip responses. Route GeoJSON is large and very repetitive (coordinate
# digits), so it compresses ~5x — which directly eases the home-upload
# bottleneck when the server runs on a laptop behind a tunnel.
Compress(app)

print(f"loading graph from {PROCESSED} ...")
ROUTER = Router(PROCESSED)
# len(ROUTER.nodes), not ROUTER.n: the router overwrites `n` with the
# turn-restriction-expanded index count (~+1.2%), so reporting it here made
# /api/health disagree with graph_nodes.parquet after a code-only deploy on
# identical data — a false alarm in the one number a smoke check compares.
print(f"ready: {len(ROUTER.nodes):,} nodes "
      f"({ROUTER.n:,} routing slots after turn-restriction splits)")


def _parse_ll(s: str):
    lat, lon = (float(x) for x in s.split(","))
    return lat, lon


def _parse_heading(args):
    """Read the driver's course over ground, or None if they didn't send a
    usable one.

    Anything outside 0..360 is dropped rather than rejected. CoreLocation
    reports -1 for "no opinion", and a client that forwards it verbatim is
    asking for the default behaviour, not making a bad request — failing the
    whole route over it would turn a missing heading into a failed reroute.

    Dropped, specifically, and never wrapped: `-1 % 360` is 359, so normalising
    a *negative* would turn "I don't know which way I'm facing" into a confident
    due-north, and point the reroute at the wrong end of the road.

    Exactly 360.0 is the one value folded rather than dropped, because it is not
    a client error — it is what rounding a legal course to one decimal produces.
    A driver headed due north reports 359.97, which any `%.1f` formatter sends as
    "360.0"; dropping that fell back to nearer-end snapping precisely when the
    heading was most worth having. Folding is safe here and not above because
    the sign check has already run.
    """
    raw = args.get("heading")
    if raw is None or raw == "":
        return None
    value = float(raw)          # a non-numeric heading is a real bad request
    if value < 0.0 or value > 360.0:
        return None
    # The same 0..360 window `Router.snap` accepts, so both layers agree on
    # what "usable" means rather than each having its own idea.
    return value % 360.0


def _parse_weights(args):
    """Read the per-beauty-type weights (w_<type>) from the query string. Each
    defaults to 1.0 (neutral) and is clamped to [WEIGHT_MIN, WEIGHT_MAX]."""
    weights = {}
    for name, *_ in BEAUTY_TYPES:
        value = float(args.get(f"w_{name}", 1.0))
        weights[name] = max(WEIGHT_MIN, min(WEIGHT_MAX, value))
    return weights


@app.get("/api/route")
def api_route():
    try:
        a = _parse_ll(request.args["from"])
        b = _parse_ll(request.args["to"])
        pref = float(request.args.get("pref", 0.5))
        heading = _parse_heading(request.args)
        weights = _parse_weights(request.args)
    except (KeyError, ValueError):
        return jsonify(error="need from=lat,lon&to=lat,lon[&pref=0..1]"
                             "[&heading=0..360][&w_<type>=...]"), 400

    # Heading applies to the start only: it says which way the driver is
    # travelling, and a destination isn't travelling anywhere.
    s, s_off = ROUTER.snap(*a, heading=heading)
    # The destination goes through the access layer: a pin on a building inside
    # a car park has to become the road you can get in from, not the nearest
    # road as the crow flies, which is routinely the wrong side of the building.
    t, t_off = ROUTER.snap_destination(*b)
    if max(s_off, t_off) > SNAP_MAX_M:
        return jsonify(error="point is outside the covered road network "
                             "(currently Massachusetts)"), 400
    if s == t:
        return jsonify(error="those points are too close together — "
                             "they sit on the same stretch of road"), 400

    # Both routes are scored with the user's beauty weights so the two numbers
    # the app puts side by side ("scenery 4.1 -> 6.3") are on one scale. The
    # weights do not change the *fastest* route itself: pref 0 zeroes the
    # scenery term, so its path is time-only either way.
    # At pref 0 the scenic route collapses to the fastest one, so reuse that
    # result instead of running Dijkstra twice — this halves the latency of
    # mid-drive "switch to fastest" reroutes.
    # `heading` reaches the route as well as the snap. It picks which end of the
    # road to start from (above) and, in RouteResult._describe_start, whether
    # the opening instruction is a compass heading or a turn — a route that has
    # to begin by sending a moving car back the way it came must say so.
    pref = max(0.0, min(1.0, pref))
    fastest = ROUTER.route(s, t, 0.0, weights, heading=heading)
    scenic = (fastest if pref == 0.0
              else ROUTER.route(s, t, pref, weights, heading=heading))
    if fastest is None or scenic is None:
        return jsonify(error="no route found between those points"), 404
    return jsonify(fastest=fastest.geojson(), scenic=scenic.geojson())


@app.get("/api/health")
def health():
    return jsonify(status="ok", nodes=len(ROUTER.nodes),
                   routing_slots=ROUTER.n)


@app.get("/")
def index():
    """Say what this service is, rather than 404ing.

    Flask's default 404 on `/` renders as a page headed "Not Found", which reads
    as a broken deployment when you open the bare hostname in a browser to check
    a tunnel — even though the server is answering perfectly. Returning a small
    description makes a bare visit a useful liveness check and documents the
    query shape for anyone poking at the API by hand.
    """
    return jsonify(
        service="scenic-api",
        status="ok",
        nodes=len(ROUTER.nodes),
        routing_slots=ROUTER.n,
        endpoints={
            "/api/route": "from=LAT,LON&to=LAT,LON[&pref=0..1][&w_<type>=0..4]",
            "/api/health": "liveness check",
        },
        beauty_types=[name for name, *_ in BEAUTY_TYPES],
        region="Massachusetts",
    )


if __name__ == "__main__":
    # 0.0.0.0 listens on all interfaces so a phone on the same Wi-Fi can reach
    # this dev server. (127.0.0.1 would only be reachable from this Mac.)
    app.run(host="0.0.0.0", port=5057, debug=False)
