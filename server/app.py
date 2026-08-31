"""Scenic routing API (backend for the iOS app).

Loads the routing graph once at startup and serves:
  GET  /api/route?from=LAT,LON&to=LAT,LON&pref=0.5[&heading=DEG][&via=LAT,LON]
                                            [&avoid_unpaved=0..2]
                 [&w_<type>=...]
                              -> {"fastest": <GeoJSON Feature>,
                                  "scenic":  <GeoJSON Feature>}
  GET  /api/loop?from=LAT,LON&km=40[&pref=1.0][&sector=NE][&w_<type>=...]
                              -> {"loop": <GeoJSON Feature>, "meta": {...},
                                  "alternatives": [...], "note": null|str}
  GET  /api/health

`pref` (0..1) is the overall scenery strength. Each beauty type can also be
weighted with w_<type> (e.g. w_coast=2&w_town=3&w_farm=0); each defaults to 1.0
(neutral) and is clamped to a sane range. The tunable types are listed by
BEAUTY_TYPES in router.py.

`via` pins a route through a waypoint. It exists for one caller: a driver who
has gone off a *loop* and needs to rejoin it. A loop ends where it began, so
asking for a route to its destination hands back the short way home and deletes
the rest of the drive — the waypoint is the loop's far point, and pinning through
it is what makes the replacement a continuation rather than an abandonment.

`/api/loop` takes one endpoint and a length instead of two endpoints, and
returns a closed scenic drive of about that length. `sector` (a compass octant)
is what a regenerate button varies; the populated ones come back in
`alternatives`, and asking is the point — a coastal start has fewer than eight.
The loop-specific numbers live in `meta` rather than in the Feature's
properties, so the same client type decodes a loop and a route.

`heading` (0..360, 0=N, clockwise) is the driver's course over ground, and
applies to `from` only — a destination has no travel direction. With it, the
start snaps to the end of its road that lies ahead rather than the nearer one,
so a mid-drive reroute doesn't open by turning the driver around. Send it only
while moving; omit it when planning from a parked car.

Run:  python server/app.py [processed_dir]   (default: data/processed)
"""

import os
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, request
from flask_compress import Compress
from flask_cors import CORS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from looper import (BEAUTIFUL_SCORE, MAX_TARGET_KM, MIN_TARGET_KM, SECTORS,
                    LoopPlanner)  # noqa: E402
from router import (BEAUTY_TYPES, MAX_AVOID_UNPAVED, Router)  # noqa: E402

# How far a user may push a single beauty type. 0 ignores it; the upper bound
# keeps one cranked slider from completely swamping the others.
WEIGHT_MIN, WEIGHT_MAX = 0.0, 4.0

# Above this share of a loop spent re-driving a road already driven, the answer
# stops being a loop and the response says so instead of pretending. Normal is
# under 0.06; the cases that trip this are short loops from rural starts, where
# within a couple of km there is genuinely one road (a 8 km loop from Petersham
# comes back at 0.35). See looper.Loop.repeated_fraction.
LOOP_RETRACE_NOTE = 0.15

# What the built graph actually covers, named in one place because it appears in
# three error messages and in the index response. It must be changed with the
# parquets, not with the code: a server telling a driver in Vermont that the
# region is Massachusetts is wrong in the one direction that costs a bug report,
# and it was wrong that way from the moment the New England graph was copied
# over. Read from the environment so a rollback to a different region's parquets
# does not need a code change to stay honest.
REGION = os.environ.get("SCENIC_REGION", "New England")

# Reject a request whose endpoint lies farther than this from any road — it's
# outside the covered region (see REGION), and the "nearest" road would be in an
# arbitrary border town, yielding a nonsense route.
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
# Loops are served from the same graph. The planner is cheap to construct — it
# holds a reference and nothing else — but it caches two ~25 MB Dijkstra passes
# per (start, pref, weights) so that pressing regenerate does not repeat them.
#
# One lock around every call, because waitress is threaded and those caches are
# plain dicts. Serialising is also the right answer on merit: a loop is ~0.7 s of
# CPU-bound numpy, so two at once would contend for the same cores and finish no
# sooner, and the second request is almost always the same user pressing the
# button again.
LOOPER = LoopPlanner(ROUTER)
LOOP_LOCK = threading.Lock()

# Built loops, keyed by the whole request. The planner's caches make a *new* loop
# cost ~0.65 s; this makes an *identical* request cost nothing, which is the
# common interaction and not an edge case — shuffling forward through the
# directions and then back to the one you liked is how this button gets used. A
# cached entry can never go stale: the graph is loaded once for the life of the
# process, so the same request has the same answer.
LOOP_RESULTS = {}
LOOP_RESULTS_MAX = 16

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


def _parse_avoid_unpaved(args):
    """How hard to steer around dirt roads, as a multiple of the calibrated
    default (`router.UNPAVED_AVOID_MIN_PER_KM`). 0 accepts them freely, 1.0 is
    the default, `MAX_AVOID_UNPAVED` is as hard as the old fixed penalty ever
    pushed.

    Separate from `w_<type>` on purpose. Those six compete for a fixed pot of
    scenery weight and are renormalised against each other, so an avoidance
    jammed in among them would quietly turn every attraction down. This one is
    priced in minutes and does not touch the beauty blend at all.
    """
    value = float(args.get("avoid_unpaved", 1.0))
    return max(0.0, min(MAX_AVOID_UNPAVED, value))


def _parse_weights(args):
    """Read the per-beauty-type weights (w_<type>) from the query string. Each
    defaults to 1.0 (neutral) and is clamped to [WEIGHT_MIN, WEIGHT_MAX]."""
    weights = {}
    for name, *_ in BEAUTY_TYPES:
        value = float(args.get(f"w_{name}", 1.0))
        weights[name] = max(WEIGHT_MIN, min(WEIGHT_MAX, value))
    return weights


def _no_worse_than_fastest(fastest, scenic):
    """The scenic route, or the fastest one when "scenic" came back scoring lower.

    The detour cost is `km * (1 - score/10)` (`Router._weights`) — proportional
    to *length* — so at any pref above 0 the router is also, quietly, a
    shortest-distance router, and a shorter route can carry less total penalty
    while being uglier per kilometre. Dijkstra then returns it, correctly by its
    own objective and wrongly by the driver's.

    Measured over 983 sampled trips at the shipped defaults, that happens on 6
    of them (0.6%; 9 with `town` off). One came back 4.8 km shorter, 0.3 minutes
    slower, and scoring 5.21 against the fastest route's 5.79 — and the app
    dutifully rendered it as "adds 1 min and raises scenery 5.8 -> 5.2".
    Handing back the fastest route instead is better on both numbers the app
    prints, costs nothing because it is already computed, and makes the screen
    say "Same as the fastest route at this setting", which is true.

    Deliberately *not* a fix to the cost function. Making the penalty
    proportional to minutes rather than km would remove the cause, and would
    also invalidate the BETA/PREF_CURVE calibration those two constants share —
    a sweep, not a bug fix. See docs/route-distribution-study.md.
    """
    return fastest if scenic.mean_score < fastest.mean_score else scenic


@app.get("/api/route")
def api_route():
    try:
        a = _parse_ll(request.args["from"])
        b = _parse_ll(request.args["to"])
        pref = float(request.args.get("pref", 0.5))
        avoid_unpaved = _parse_avoid_unpaved(request.args)
        heading = _parse_heading(request.args)
        weights = _parse_weights(request.args)
        raw_via = request.args.get("via")
        via = _parse_ll(raw_via) if raw_via else None
    except (KeyError, ValueError):
        return jsonify(error="need from=lat,lon&to=lat,lon[&pref=0..1]"
                             "[&heading=0..360][&via=lat,lon][&w_<type>=...]"), 400

    # Heading applies to the start only: it says which way the driver is
    # travelling, and a destination isn't travelling anywhere.
    s, s_off = ROUTER.snap(*a, heading=heading)
    # The destination goes through the access layer: a pin on a building inside
    # a car park has to become the road you can get in from, not the nearest
    # road as the crow flies, which is routinely the wrong side of the building.
    t, t_off = ROUTER.snap_destination(*b)
    if max(s_off, t_off) > SNAP_MAX_M:
        return jsonify(error="point is outside the covered road network "
                             f"(currently {REGION})"), 400
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
    if via is not None:
        # A waypoint is a point on a road, so it snaps like a start and not like
        # a destination — there is no building to find the car park entrance for.
        w, w_off = ROUTER.snap(*via)
        if w_off > SNAP_MAX_M:
            return jsonify(error="that waypoint is outside the covered road "
                                 f"network (currently {REGION})"), 400
        # Under the loop planner's lock: `resume` reads the same cached cost
        # models that `/api/loop` fills, and they are plain dicts.
        with LOOP_LOCK:
            fastest = LOOPER.resume(s, w, t, 0.0, weights, avoid_unpaved)
            scenic = (fastest if pref == 0.0
                      else LOOPER.resume(s, w, t, pref, weights,
                                         avoid_unpaved))
        # `heading` is deliberately dropped here. It picks which end of the
        # driver's road to leave from, and this caller is mid-drive, so it
        # matters — but honouring it would mean starting the search from a
        # different node than the one `snap` returned above, which `resume` has
        # no way to express. A rejoin that opens by turning the car around is
        # worth fixing; see docs/loop-routes-design.md.
        if fastest is None or scenic is None:
            return jsonify(error="no route found through that waypoint"), 404
        scenic = _no_worse_than_fastest(fastest, scenic)
        return jsonify(fastest=fastest.geojson(), scenic=scenic.geojson())

    fastest = ROUTER.route(s, t, 0.0, weights, heading=heading,
                           avoid_unpaved=avoid_unpaved)
    scenic = (fastest if pref == 0.0
              else ROUTER.route(s, t, pref, weights, heading=heading,
                                avoid_unpaved=avoid_unpaved))
    if fastest is None or scenic is None:
        return jsonify(error="no route found between those points"), 404
    scenic = _no_worse_than_fastest(fastest, scenic)
    return jsonify(fastest=fastest.geojson(), scenic=scenic.geojson())


@app.get("/api/loop")
def api_loop():
    """A closed scenic drive of about `km` from one point, and the directions
    that hold another one.

    There is no destination to take, which is the whole feature: choosing where
    to go is the term that decides whether a drive is good, and here the server
    owns it. See docs/loop-routes-design.md.
    """
    try:
        start_ll = _parse_ll(request.args["from"])
        target_km = float(request.args.get("km", 40.0))
        # Loops default to full scenery where routes default to 0.5. With the
        # length already pinned by the slider, pref has little left to trade,
        # and the middle of its travel is not monotone for loops — see the
        # docstring in pipeline/looper.py. Clients should leave this alone.
        pref = max(0.0, min(1.0, float(request.args.get("pref", 1.0))))
        weights = _parse_weights(request.args)
        avoid_unpaved = _parse_avoid_unpaved(request.args)
    except (KeyError, ValueError):
        return jsonify(error="need from=lat,lon[&km=5..200][&pref=0..1]"
                             "[&sector=N|NE|E|SE|S|SW|W|NW][&w_<type>=...]"), 400

    sector = request.args.get("sector") or None
    if sector is not None and sector not in SECTORS:
        return jsonify(error=f"sector must be one of {', '.join(SECTORS)}"), 400

    # A loop is planned from a standstill by definition — the driver is choosing
    # a drive, not already on one — so no heading, and `snap` takes the nearer
    # end of the road they are on. No `snap_destination` either: there is no
    # destination pin to pull out of a car park.
    start, offset = ROUTER.snap(*start_ll)
    if offset > SNAP_MAX_M:
        return jsonify(error="point is outside the covered road network "
                             f"(currently {REGION})"), 400

    target_km = max(MIN_TARGET_KM, min(MAX_TARGET_KM, target_km))
    key = (start, round(target_km, 1), round(pref, 4), sector,
           tuple(sorted(weights.items())), round(avoid_unpaved, 4))
    with LOOP_LOCK:
        cached = LOOP_RESULTS.pop(key, None)
        if cached is not None:
            LOOP_RESULTS[key] = cached          # move to the warm end
            return jsonify(cached)
        loop = LOOPER.plan(start, target_km, pref, weights, sector=sector,
                           avoid_unpaved=avoid_unpaved)
        if loop is None:
            # Only reachable when the geography has nothing at all in that
            # direction at that length, since `plan` returns the best available
            # rather than holding out for a good one.
            nearest = LOOPER.nearest_length(start, target_km, pref, weights,
                                            avoid_unpaved)
            hint = (f" The nearest loop from here is about {nearest:.0f} km."
                    if nearest else "")
            return jsonify(error="no loop of that length from there." + hint), 404
        available = LOOPER.sectors(start, loop.target_km, pref, weights,
                                   avoid_unpaved)

    note = None
    if loop.repeated_fraction > LOOP_RETRACE_NOTE:
        share = round(100 * loop.repeated_fraction)
        note = (f"The roads here don't really make a loop this short — "
                f"{share}% of this one doubles back. Try a longer distance.")

    body = dict(
        loop=loop.route.geojson(),
        meta={
            "target_km": round(float(loop.target_km), 1),
            "km": round(float(loop.km), 1),
            "minutes": round(float(loop.minutes), 1),
            "mean_score": round(float(loop.mean_score), 2),
            # The legible number: "16 of your 40 km". Measured to separate a
            # scenic loop from a fast one of the same length 5-fold, where the
            # means only manage 5.8 against 5.0.
            "beautiful_km": round(float(loop.beautiful_km), 1),
            "beautiful_score": BEAUTIFUL_SCORE,
            # The loop-specific defect, always reported. It is what tells a
            # driver their 40 km drive is really a 20 km drive twice.
            "repeated_km": round(float(loop.repeated_km), 1),
            "turnaround": [round(loop.turnaround[0], 6),
                           round(loop.turnaround[1], 6)],
            "sector": loop.sector,
        },
        # Which way else the user could be sent, so the app can offer real
        # directions instead of a blind shuffle. Free — it falls out of the same
        # cached passes.
        alternatives=[{"sector": name, "candidates": count}
                      for name, count in available.items()],
        note=note,
    )
    with LOOP_LOCK:
        LOOP_RESULTS[key] = body
        while len(LOOP_RESULTS) > LOOP_RESULTS_MAX:
            LOOP_RESULTS.pop(next(iter(LOOP_RESULTS)))
    return jsonify(body)


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
            "/api/route": ("from=LAT,LON&to=LAT,LON[&pref=0..1][&via=LAT,LON]"
                           "[&avoid_unpaved=0..2]"
                           "[&w_<type>=0..4]"),
            "/api/loop": (f"from=LAT,LON&km={MIN_TARGET_KM:.0f}..{MAX_TARGET_KM:.0f}"
                          "[&sector=NE][&pref=0..1][&w_<type>=0..4]"
                          "[&avoid_unpaved=0..2]"),
            "/api/health": "liveness check",
        },
        loop_sectors=list(SECTORS),
        beauty_types=[name for name, *_ in BEAUTY_TYPES],
        region=REGION,
    )


if __name__ == "__main__":
    # 0.0.0.0 listens on all interfaces so a phone on the same Wi-Fi can reach
    # this dev server. (127.0.0.1 would only be reachable from this Mac.)
    app.run(host="0.0.0.0", port=5057, debug=False)
