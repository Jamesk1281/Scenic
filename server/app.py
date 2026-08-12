"""Scenic routing API (backend for the iOS app).

Loads the routing graph once at startup and serves:
  GET  /api/route?from=LAT,LON&to=LAT,LON&pref=0.5[&w_<type>=...]
                              -> {"fastest": <GeoJSON Feature>,
                                  "scenic":  <GeoJSON Feature>}
  GET  /api/health

`pref` (0..1) is the overall scenery strength. Each beauty type can also be
weighted with w_<type> (e.g. w_coast=2&w_town=3&w_farm=0); each defaults to 1.0
(neutral) and is clamped to a sane range. The tunable types are listed by
BEAUTY_TYPES in router.py.

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

# Reject a request whose endpoint snaps farther than this from any road node —
# it's outside the covered region (currently Massachusetts), and the "nearest"
# node would be an arbitrary border town, yielding a nonsense route.
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
print(f"ready: {ROUTER.n:,} nodes")


def _parse_ll(s: str):
    lat, lon = (float(x) for x in s.split(","))
    return lat, lon


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
        weights = _parse_weights(request.args)
    except (KeyError, ValueError):
        return jsonify(error="need from=lat,lon&to=lat,lon[&pref=0..1][&w_<type>=...]"), 400

    s, s_off = ROUTER.snap(*a)
    t, t_off = ROUTER.snap(*b)
    if max(s_off, t_off) > SNAP_MAX_M:
        return jsonify(error="point is outside the covered road network "
                             "(currently Massachusetts)"), 400
    if s == t:
        return jsonify(error="start and end snap to the same point"), 400

    # Fastest ignores beauty weights (pref 0 zeroes the scenery term anyway);
    # the scenic route applies the user's overall strength and per-type weights.
    # At pref 0 the scenic weights collapse to the fastest ones, so reuse that
    # result instead of running Dijkstra twice — this halves the latency of
    # mid-drive "switch to fastest" reroutes.
    pref = max(0.0, min(1.0, pref))
    fastest = ROUTER.route(s, t, 0.0)
    scenic = fastest if pref == 0.0 else ROUTER.route(s, t, pref, weights)
    if fastest is None or scenic is None:
        return jsonify(error="no route found between those points"), 404
    return jsonify(fastest=fastest.geojson(), scenic=scenic.geojson())


@app.get("/api/health")
def health():
    return jsonify(status="ok", nodes=ROUTER.n)


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
        nodes=ROUTER.n,
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
