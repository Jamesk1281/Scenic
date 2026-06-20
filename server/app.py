"""Scenic routing API + static web demo.

Loads the routing graph once at startup and serves:
  GET  /                      the MapLibre web demo (web/index.html)
  GET  /api/route?from=LAT,LON&to=LAT,LON&pref=0.5
                              -> {"fastest": <GeoJSON Feature>,
                                  "scenic":  <GeoJSON Feature>}

Run:  python server/app.py [processed_dir]   (default: data/processed)
"""

import sys
from functools import lru_cache
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from router import Router  # noqa: E402

PROCESSED = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "processed")
WEB = ROOT / "web"

app = Flask(__name__, static_folder=None)
CORS(app)

print(f"loading graph from {PROCESSED} ...")
ROUTER = Router(PROCESSED)
print(f"ready: {ROUTER.n:,} nodes")


def _parse_ll(s: str):
    lat, lon = (float(x) for x in s.split(","))
    return lat, lon


@app.get("/api/route")
def api_route():
    try:
        a = _parse_ll(request.args["from"])
        b = _parse_ll(request.args["to"])
        pref = float(request.args.get("pref", 0.5))
    except (KeyError, ValueError):
        return jsonify(error="need from=lat,lon&to=lat,lon[&pref=0..1]"), 400

    s = ROUTER.snap(*a)
    t = ROUTER.snap(*b)
    if s == t:
        return jsonify(error="start and end snap to the same point"), 400

    fastest = ROUTER.route(s, t, 0.0)
    scenic = ROUTER.route(s, t, max(0.0, min(1.0, pref)))
    if fastest is None or scenic is None:
        return jsonify(error="no route found between those points"), 404
    return jsonify(fastest=fastest.geojson(), scenic=scenic.geojson())


@app.get("/api/health")
def health():
    return jsonify(status="ok", nodes=ROUTER.n)


# Massachusetts bounding box, biases geocoding to local results.
_MA_VIEWBOX = "-73.55,42.92,-69.85,41.18"


@lru_cache(maxsize=512)
def _geocode(q: str):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "us",
                "viewbox": _MA_VIEWBOX, "bounded": 1},
        headers={"User-Agent": "ScenicRoutingDemo/0.1 (scenic-routes demo app)"},
        timeout=10,
    )
    r.raise_for_status()
    return [{"name": d["display_name"], "lat": float(d["lat"]), "lon": float(d["lon"])}
            for d in r.json()]


@app.get("/api/geocode")
def geocode():
    q = request.args.get("q", "").strip()
    if len(q) < 3:
        return jsonify(results=[])
    try:
        return jsonify(results=_geocode(q))
    except requests.RequestException:
        return jsonify(results=[], error="geocoder unavailable"), 502


@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/<path:fname>")
def static_files(fname):
    return send_from_directory(WEB, fname)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5057, debug=False)
