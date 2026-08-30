"""Route a stratified sample of town-to-town trips and record what each one traded.

    .venv/bin/python tools/route_census.py --processed data/processed-ne

Three product decisions — where a "little scenery to offer" warning should
fire, whether `pref=0.5` is a sensible default, and whether dropping the `town`
beauty weight helps — were being made from a dozen hand-picked routes, on which
beautiful-miles-per-extra-minute ranged 0.13 to 2.71. This walks ~1,000 realistic
pairs instead and writes the raw per-route numbers, so the distribution can be
re-analysed without re-routing. See docs/route-distribution-study.md.

Deliberately *not* an HTTP client. `/api/route` serialises a 4,600-point
geometry and 86 turn instructions per arm, none of which this reads, so it calls
`Router.route` directly. That is worth about 1.5x, not the order of magnitude
one might expect: scipy's Dijkstra is single-source-to-every-node with no early
exit, so the graph's size sets the cost and the JSON never was the bottleneck.
Single-process on purpose — `server/serve.py` measured four concurrent routes at
1.08x serial, because that Dijkstra does not usefully release the GIL.

Two measurement traps this is built around, both of which have already produced
a wrong answer on this codebase:

1. `Router.route` re-blends every edge score from the caller's beauty weights,
   so `mean_score` and beautiful-km are on a *different scale* under a different
   weight vector. Comparing them across weight settings compares two rulers and
   reverses conclusions. Both are still recorded — they are the right instrument
   *within* one weight setting — but the cross-weight comparison must use
   `scenery_km`, which is thresholded on the raw `c_*` columns and so is
   scale-free, plus minutes and km. Comparing across `pref` at fixed weights is
   fine: `pref` enters the cost, not the score blend.

2. The fastest arm is routed once per weight setting rather than shared, even
   though `pref=0` zeroes the scenery term and the path cannot depend on the
   weights. It costs ~4 minutes and buys a check on 1,000 inputs that the
   assumption holds — `fastest_path_differs` in the summary — instead of an
   argument from reading the source.

Failures are recorded, not dropped: a pair that cannot route because a point is
off the network, because the two ends snap to one node, or because no path
exists is a fact about the product's coverage. The islands are genuinely
unreachable by road and should show up.
"""

import argparse
import csv
import math
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402

from looper import BEAUTIFUL_SCORE  # noqa: E402
from router import BEAUTY_TYPES, SCENERY_BREAKDOWN, Router  # noqa: E402

# Matches server/app.py. A pair whose start or destination pin is further than
# this from any road is refused there, so it has to be refused here too or the
# census would describe trips the product declines to plan.
SNAP_MAX_M = 5000.0

# Great-circle bands, in km. The time-for-scenery trade almost certainly depends
# on how far the driver is going — an hour of detour means something different
# on a 15 km errand than on a 150 km day out — so nothing is pooled across these
# in the headline.
BANDS = [(10.0, 25.0), (25.0, 50.0), (50.0, 100.0), (100.0, 200.0)]

# The two weight vectors under test. "shipped" is what the iOS client actually
# sends today: every tunable type at 1.0, the calibrated default (see
# BeautyType.all / RouteModel.weights). "town_off" is the proposed change.
SHIPPED_WEIGHTS = {name: 1.0 for name, *_ in BEAUTY_TYPES}
TOWN_OFF_WEIGHTS = dict(SHIPPED_WEIGHTS, town=0.0)
WEIGHT_SETS = [("shipped", SHIPPED_WEIGHTS), ("town_off", TOWN_OFF_WEIGHTS)]

# server/app.py's default for /api/route, and the value question 4 is asked at.
DEFAULT_PREF = 0.5
# Question 3's sweep. 0.0 is definitional rather than measured: at pref 0 the
# scenery term is zeroed, so the "scenic" route *is* the fastest one by
# construction, and a sweep that reports a perfect match there has measured
# nothing. It is routed anyway, as a check that the sweep reproduces the
# separately-routed fastest arm.
PREF_SWEEP = [0.0, 0.25, 0.5, 0.75, 1.0]

SCENERY_COLUMNS = [(label, "sc_" + label.replace("/", "_"))
                   for label, _, _ in SCENERY_BREAKDOWN]

PAIR_FIELDS = ["pair_id", "band", "src_lat", "src_lon", "dst_lat", "dst_lon",
               "gc_km", "status", "snap_src_m", "snap_dst_m", "in_sweep"]
ROUTE_FIELDS = (["pair_id", "band", "gc_km", "arm", "pref", "weights",
                 "km", "minutes", "mean_score", "beautiful_km"]
                + [col for _, col in SCENERY_COLUMNS])

EARTH_KM = 6371.0088


def great_circle_km(lat1, lon1, lat2, lon2):
    """Haversine distance in km, vectorised over numpy arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2) - np.radians(lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def sample_pairs(lat, lon, per_band, seed, batch=200_000):
    """`per_band` ordered place-node pairs in each distance band, deterministically.

    Rejection sampling rather than a distance matrix: 5,421 place points is 29
    million ordered pairs, and all that is wanted is a uniform draw from the few
    thousand of them that land in each band. Draws are consumed in generated
    order, so the seed alone reproduces the sample.

    Uniform random *points* in a bounding box would be the obvious alternative
    and is the trap: they land in the ocean and over the state line, they
    over-sample the empty north, and nobody drives between two of them. OSM
    place nodes are towns and villages, so a pair of them approximates a trip
    somebody would actually take.
    """
    rng = np.random.default_rng(seed)
    want = {band: per_band for band in BANDS}
    picked = {band: [] for band in BANDS}
    seen = set()
    draws = 0
    while any(want[b] for b in BANDS):
        i, j = rng.integers(0, len(lat), batch), rng.integers(0, len(lat), batch)
        d = great_circle_km(lat[i], lon[i], lat[j], lon[j])
        draws += batch
        for k in range(batch):
            a, b = int(i[k]), int(j[k])
            if a == b or (a, b) in seen:
                continue
            for band in BANDS:
                if want[band] and band[0] <= d[k] < band[1]:
                    seen.add((a, b))
                    picked[band].append((a, b, float(d[k])))
                    want[band] -= 1
                    break
            if not any(want[b] for b in BANDS):
                break
        if draws > 200 * batch:
            raise RuntimeError(f"cannot fill bands {[b for b in BANDS if want[b]]} "
                               f"from {len(lat)} place points")
    return picked, draws


def measure(result, weights_name, pair, arm, pref):
    """One CSV row for a routed arm.

    `beautiful_km` is computed the way `looper._beautiful_km` does, off the
    same imported constant, so the two cannot drift. It reads the *re-blended*
    per-edge scores, which is the point — within one weight setting that is the
    right ruler, and across weight settings it is the wrong one and the
    `sc_*` columns are there instead.
    """
    scores = (result.edges["score"].to_numpy() if result.scores is None
              else np.asarray(result.scores))
    length_m = result.edges["length_m"].to_numpy()
    breakdown = result.scenery_km()
    row = {
        "pair_id": pair["pair_id"], "band": pair["band"], "gc_km": pair["gc_km"],
        "arm": arm, "pref": pref, "weights": weights_name,
        "km": round(result.km, 4), "minutes": round(result.minutes, 4),
        "mean_score": round(result.mean_score, 4),
        "beautiful_km": round(
            float(length_m[scores >= BEAUTIFUL_SCORE].sum() / 1000.0), 4),
    }
    for label, col in SCENERY_COLUMNS:
        row[col] = round(breakdown[label], 4)
    return row


def run(args):
    processed = Path(args.processed).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    places = gpd.read_parquet(processed / "place_points.parquet")
    lat = places.geometry.y.to_numpy()
    lon = places.geometry.x.to_numpy()
    print(f"{len(places)} place points from {processed}", file=sys.stderr)

    picked, draws = sample_pairs(lat, lon, args.per_band, args.seed)
    print(f"sampled {sum(len(v) for v in picked.values())} pairs "
          f"from {draws} draws, seed {args.seed}", file=sys.stderr)

    t0 = time.perf_counter()
    router = Router(str(processed))
    load_s = time.perf_counter() - t0
    print(f"router loaded in {load_s:.1f}s: {router.n} nodes, "
          f"{len(router.edges)} edges", file=sys.stderr)

    pair_rows, route_rows = [], []
    sweep_per_band = args.sweep_pairs // len(BANDS)
    counts = {"ok": 0, "snap_far": 0, "same_node": 0, "no_path": 0}
    band_counts = {f"{a:g}-{b:g}": dict(counts) for a, b in BANDS}
    fastest_path_differs = 0
    routed = 0
    t0 = time.perf_counter()

    for band in BANDS:
        label = f"{band[0]:g}-{band[1]:g}"
        sweep_left = sweep_per_band
        for n, (i, j, gc) in enumerate(picked[band]):
            pair_id = f"{label}:{n:04d}"
            pair = {"pair_id": pair_id, "band": label, "gc_km": round(gc, 3)}
            row = dict(pair, src_lat=round(float(lat[i]), 6),
                       src_lon=round(float(lon[i]), 6),
                       dst_lat=round(float(lat[j]), 6),
                       dst_lon=round(float(lon[j]), 6),
                       status="ok", snap_src_m="", snap_dst_m="", in_sweep=0)

            # Snapped exactly as server/app.py does it: the start is a car on a
            # road, the destination is a pin that may be inside a car park and
            # has to become the road you can get in from.
            src, src_off = router.snap(float(lat[i]), float(lon[i]))
            dst, dst_off = router.snap_destination(float(lat[j]), float(lon[j]))
            row["snap_src_m"] = round(src_off, 1)
            row["snap_dst_m"] = round(dst_off, 1)
            if max(src_off, dst_off) > SNAP_MAX_M:
                row["status"] = "snap_far"
            elif src == dst:
                row["status"] = "same_node"

            if row["status"] == "ok":
                arms, failed = {}, False
                for wname, weights in WEIGHT_SETS:
                    fastest = router.route(src, dst, 0.0, weights)
                    scenic = router.route(src, dst, DEFAULT_PREF, weights)
                    routed += 2
                    if fastest is None or scenic is None:
                        failed = True
                        break
                    arms[wname] = (fastest, scenic)
                    route_rows.append(measure(fastest, wname, pair, "fastest", 0.0))
                    route_rows.append(measure(scenic, wname, pair, "scenic",
                                              DEFAULT_PREF))
                if failed:
                    row["status"] = "no_path"
                    route_rows = [r for r in route_rows if r["pair_id"] != pair_id]
                else:
                    # pref 0 zeroes the scenery term, so the fastest path cannot
                    # depend on the beauty weights. Checked rather than assumed.
                    a, b = arms["shipped"][0], arms["town_off"][0]
                    if abs(a.km - b.km) > 1e-6 or abs(a.minutes - b.minutes) > 1e-6:
                        fastest_path_differs += 1
                    if sweep_left:
                        row["in_sweep"] = 1
                        sweep_left -= 1
                        for pref in PREF_SWEEP:
                            r = router.route(src, dst, pref, SHIPPED_WEIGHTS)
                            routed += 1
                            if r is not None:
                                route_rows.append(
                                    measure(r, "shipped", pair, "sweep", pref))

            counts[row["status"]] += 1
            band_counts[label][row["status"]] += 1
            pair_rows.append(row)
            if len(pair_rows) % 50 == 0:
                el = time.perf_counter() - t0
                print(f"  {len(pair_rows)} pairs, {routed} routes, {el:.0f}s "
                      f"({el / max(routed, 1):.3f}s/route)", file=sys.stderr)

    elapsed = time.perf_counter() - t0
    stem = out_dir / args.prefix
    with open(f"{stem}-pairs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, PAIR_FIELDS)
        w.writeheader()
        w.writerows(pair_rows)
    with open(f"{stem}-routes.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ROUTE_FIELDS)
        w.writeheader()
        w.writerows(route_rows)

    graph = processed / "graph_edges.parquet"
    summary = {
        "processed_dir": str(processed),
        "graph_built": time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(graph.stat().st_mtime)),
        "place_points": int(len(places)),
        "seed": args.seed, "per_band": args.per_band,
        "sweep_pairs_per_band": sweep_per_band,
        "beautiful_score": BEAUTIFUL_SCORE,
        "default_pref": DEFAULT_PREF,
        "weight_sets": {n: w for n, w in WEIGHT_SETS},
        "pref_sweep": PREF_SWEEP,
        "snap_max_m": SNAP_MAX_M,
        "status_counts": counts,
        "status_by_band": band_counts,
        "fastest_path_differs_across_weights": fastest_path_differs,
        "routes": len(route_rows), "routes_attempted": routed,
        "router_load_s": round(load_s, 1),
        "elapsed_s": round(elapsed, 1),
        "s_per_route": round(elapsed / max(routed, 1), 4),
    }
    with open(f"{stem}-summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2), file=sys.stderr)
    print(f"wrote {stem}-pairs.csv, {stem}-routes.csv, {stem}-summary.json",
          file=sys.stderr)



# --- analysis ---------------------------------------------------------------
# Split from the routing half on purpose: routing 4,300 shortest paths is half
# an hour and the questions are seconds, so a threshold can be re-cut off the
# CSVs without touching Dijkstra again.

MI = 0.621371
# Extra-minutes floor for the efficiency ratio. A scenic route that costs
# nothing — the same path, or a rounding away from it — has an undefined
# miles-per-extra-minute. Flooring the denominator scores "gained nothing for
# nothing" at 0, which fires, and "gained 5 miles for nothing" at 50, which
# never does. That is the behaviour a warning wants; an unguarded division
# gives a ZeroDivisionError on the first pair and a NaN that quietly sorts
# to the bottom on the rest.
MIN_EXTRA = 0.1
BAND_LABELS = [f"{a:g}-{b:g}" for a, b in BANDS]
SC = [col for _, col in SCENERY_COLUMNS]
GAIN_T = [0.25, 0.5, 1.0, 2.0, 3.0]     # miles of beautiful road gained
RATIO_T = [0.05, 0.1, 0.2, 0.3, 0.5]    # beautiful miles per extra minute
FRAC_T = [0.10, 0.20, 0.30, 0.40]       # beautiful share of the scenic route


def load(stem):
    with open(f"{stem}-pairs.csv") as f:
        pairs = list(csv.DictReader(f))
    with open(f"{stem}-routes.csv") as f:
        routes = list(csv.DictReader(f))
    for r in routes:
        for k in ["gc_km", "pref", "km", "minutes", "mean_score",
                  "beautiful_km"] + SC:
            r[k] = float(r[k])
    idx = {}
    for r in routes:
        e = idx.setdefault(r["pair_id"], {})
        if r["arm"] == "sweep":
            e.setdefault("sweep", {})[r["pref"]] = r
        else:
            e[(r["arm"], r["weights"])] = r
    return pairs, routes, idx


def pcts(v):
    v = np.asarray(v, float)
    if not len(v):
        return {k: None for k in ("n", "min", "p10", "p50", "p90", "max", "mean")}
    q = np.percentile(v, [10, 50, 90])
    return {"n": len(v), "min": float(v.min()), "p10": float(q[0]),
            "p50": float(q[1]), "p90": float(q[2]), "max": float(v.max()),
            "mean": float(v.mean())}


def wilson(k, n, z=1.96):
    """95% CI for a proportion.

    Printed beside every fire-rate because a rate off 60 pairs and a rate off
    1,000 read identically on the page, and the whole point of this study is
    that the twelve-route pilot could not site a threshold.
    """
    if not n:
        return (0.0, 1.0)
    p, d = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def boot_median(v, seed=1, reps=4000):
    v = np.asarray(v, float)
    if len(v) < 3:
        return (None, None)
    rng = np.random.default_rng(seed)
    m = np.median(rng.choice(v, (reps, len(v))), axis=1)
    return (float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)))


def sign_test(d, tol=1e-6):
    """Paired sign test. Ties are dropped, which is the point: most of these
    comparisons are between two routes that are the same road."""
    d = np.asarray(d, float)
    pos, neg = int((d > tol).sum()), int((d < -tol).sum())
    n = pos + neg
    if n == 0:
        return pos, neg, len(d), 1.0
    z = (max(pos, neg) - 0.5 - n / 2) / math.sqrt(n / 4)
    return pos, neg, len(d) - n, min(1.0, math.erfc(z / math.sqrt(2)))


def trade(idx, pairs, wname):
    """Per band, the fastest-versus-scenic comparison at one weight setting.

    Both arms come from the same weight vector, so `beautiful_km` is one ruler
    over two routes and the difference means what it looks like. Across weight
    settings it would not — see the module docstring.
    """
    out = {b: [] for b in BAND_LABELS}
    for p in pairs:
        if p["status"] != "ok":
            continue
        e = idx.get(p["pair_id"], {})
        f, s = e.get(("fastest", wname)), e.get(("scenic", wname))
        if not f or not s:
            continue
        extra = s["minutes"] - f["minutes"]
        gain_km = s["beautiful_km"] - f["beautiful_km"]
        out[p["band"]].append({
            "pair_id": p["pair_id"], "extra_min": extra,
            "extra_km": s["km"] - f["km"], "gain_mi": gain_km * MI,
            "ratio": (gain_km * MI) / max(extra, MIN_EXTRA),
            "scenic_beaut_frac": s["beautiful_km"] / max(s["km"], 1e-9),
            "fast_min": f["minutes"], "fast_beaut_km": f["beautiful_km"],
            "scenic_beaut_km": s["beautiful_km"],
            "score_gain": s["mean_score"] - f["mean_score"],
            # The shipped "Same as the fastest route" test, replayed exactly:
            # RouteResults.isSameDrive compares the minutes and the score at the
            # precision the cards *print* them to, not at full precision.
            "same_drive": (round(s["minutes"]) - round(f["minutes"])) <= 0
                          and abs(s["mean_score"] - f["mean_score"]) < 0.05,
        })
    return out


def _five(d, p=2):
    if not d["n"]:
        return "n/a"
    return (f"{d['min']:.{p}f} / {d['p10']:.{p}f} / {d['p50']:.{p}f} / "
            f"{d['p90']:.{p}f} / {d['max']:.{p}f}")


def _rate(k, n):
    lo, hi = wilson(k, n)
    return f"{100 * k / max(n, 1):.1f}% ({k}/{n}, 95% CI {100 * lo:.1f}–{100 * hi:.1f})"


def report_failures(pairs, summary, out):
    out.append("\n## Coverage — what failed to route\n")
    out.append("| band | ok | snap > 5 km | same node | no path |")
    out.append("|---|---|---|---|---|")
    for b in BAND_LABELS:
        c = summary["status_by_band"][b]
        out.append(f"| {b} km | {c['ok']} | {c['snap_far']} | {c['same_node']} "
                   f"| {c['no_path']} |")
    c = summary["status_counts"]
    out.append(f"| **all** | {c['ok']} | {c['snap_far']} | {c['same_node']} "
               f"| {c['no_path']} |")
    bad = [p for p in pairs if p["status"] != "ok"]
    if bad:
        out.append("\n| failed pair | status | from | to | gc km | snap src m | snap dst m |")
        out.append("|---|---|---|---|---|---|---|")
        for p in bad[:40]:
            out.append(f"| {p['pair_id']} | {p['status']} | {p['src_lat']},{p['src_lon']} "
                       f"| {p['dst_lat']},{p['dst_lon']} | {float(p['gc_km']):.1f} "
                       f"| {p['snap_src_m']} | {p['snap_dst_m']} |")
        if len(bad) > 40:
            out.append(f"\n…and {len(bad) - 40} more, in the pairs CSV.")


def report_trade(idx, pairs, wname, out):
    t = trade(idx, pairs, wname)
    out.append(f"\n## Q1/Q2 — the trade at weights={wname}, pref={DEFAULT_PREF:g}\n")
    out.append("| band | n | extra minutes | beautiful mi gained | mi per extra min |")
    out.append("|---|---|---|---|---|")
    for b in BAND_LABELS:
        rows = t[b]
        if rows:
            out.append(f"| {b} km | {len(rows)} "
                       f"| {_five(pcts([r['extra_min'] for r in rows]), 1)} "
                       f"| {_five(pcts([r['gain_mi'] for r in rows]))} "
                       f"| {_five(pcts([r['ratio'] for r in rows]))} |")
    out.append("\n(cells are min / p10 / median / p90 / max)\n")
    out.append("| band | extra km (p10/p50/p90) | scenic beautiful share (p10/p50/p90) "
               "| median beautiful mi, fastest → scenic | median % longer in time |")
    out.append("|---|---|---|---|---|")
    for b in BAND_LABELS:
        rows = t[b]
        if not rows:
            continue
        ek = pcts([r["extra_km"] for r in rows])
        fr = pcts([r["scenic_beaut_frac"] for r in rows])
        pc = np.median([r["extra_min"] / max(r["fast_min"], 1e-9) for r in rows])
        out.append(
            f"| {b} km | {ek['p10']:.1f} / {ek['p50']:.1f} / {ek['p90']:.1f} "
            f"| {100 * fr['p10']:.0f}% / {100 * fr['p50']:.0f}% / {100 * fr['p90']:.0f}% "
            f"| {np.median([r['fast_beaut_km'] for r in rows]) * MI:.1f} → "
            f"{np.median([r['scenic_beaut_km'] for r in rows]) * MI:.1f} "
            f"| {100 * pc:.0f}% |")

    out.append("\n### Candidate warning rules — fire-rate by band, with 95% CI\n")
    for name, key, ts, pct in [
            ("A — gained beautiful miles below T", "gain_mi", GAIN_T, False),
            ("B — beautiful miles per extra minute below T", "ratio", RATIO_T, False),
            ("C — beautiful share of the scenic route below T",
             "scenic_beaut_frac", FRAC_T, True)]:
        out.append(f"\n**Rule {name}**\n")
        out.append("| T | " + " | ".join(f"{b} km" for b in BAND_LABELS) + " | all |")
        out.append("|---" * (len(BAND_LABELS) + 2) + "|")
        for T in ts:
            cells, ak, an = [], 0, 0
            for b in BAND_LABELS:
                rows = t[b]
                k = sum(1 for r in rows if r[key] < T)
                ak, an = ak + k, an + len(rows)
                lo, hi = wilson(k, len(rows))
                cells.append(f"{100 * k / max(len(rows), 1):.0f}% "
                             f"({100 * lo:.0f}–{100 * hi:.0f})")
            lo, hi = wilson(ak, an)
            out.append(f"| {100 * T:.0f}% |" if pct else f"| {T:g} |")
            out[-1] += " " + " | ".join(cells) + \
                f" | {100 * ak / max(an, 1):.0f}% ({100 * lo:.0f}–{100 * hi:.0f}) |"
    return t


def report_pref(idx, pairs, out):
    """Question 3. Comparing across pref is valid at fixed weights: pref enters
    the edge cost, not the score blend, so all five routes of one pair are
    measured with the same ruler."""
    sw = [(p, idx[p["pair_id"]]["sweep"]) for p in pairs
          if p["in_sweep"] == "1" and "sweep" in idx.get(p["pair_id"], {})]
    out.append(f"\n## Q3 — is `pref` monotone? ({len(sw)} pairs, shipped weights)\n")
    if not sw:
        return sw
    TOL = 0.05      # km / minutes; below this is tie-breaking, not a reversal
    steps = range(len(PREF_SWEEP) - 1)
    bad_b = [p["pair_id"] for p, s in sw if any(
        s[PREF_SWEEP[k + 1]]["beautiful_km"] < s[PREF_SWEEP[k]]["beautiful_km"] - TOL
        for k in steps)]
    bad_m = [p["pair_id"] for p, s in sw if any(
        s[PREF_SWEEP[k + 1]]["minutes"] < s[PREF_SWEEP[k]]["minutes"] - TOL
        for k in steps)]
    out.append(f"- beautiful-km **not** non-decreasing in pref: {_rate(len(bad_b), len(sw))}")
    out.append(f"- minutes **not** non-decreasing in pref: {_rate(len(bad_m), len(sw))}")

    def same(a, b):
        return abs(a["km"] - b["km"]) < 1e-6 and abs(a["minutes"] - b["minutes"]) < 1e-6

    out.append(f"- identical route at pref 0 and {DEFAULT_PREF:g}: "
               f"{_rate(sum(1 for _, s in sw if same(s[0.0], s[DEFAULT_PREF])), len(sw))}")
    out.append("\n| step | strength (pref²) | cum. extra min vs pref 0 (p50) "
               "| cum. beautiful mi vs pref 0 (p50) | marginal mi per marginal min (p50) "
               "| step changes nothing |")
    out.append("|---|---|---|---|---|---|")
    for k, pr in enumerate(PREF_SWEEP):
        if k == 0:
            out.append("| 0 (definitional) | 0.00 | 0.0 | 0.00 | — | — |")
            continue
        prev = PREF_SWEEP[k - 1]
        em = [s[pr]["minutes"] - s[0.0]["minutes"] for _, s in sw]
        gb = [(s[pr]["beautiful_km"] - s[0.0]["beautiful_km"]) * MI for _, s in sw]
        marg = [((s[pr]["beautiful_km"] - s[prev]["beautiful_km"]) * MI)
                / max(s[pr]["minutes"] - s[prev]["minutes"], MIN_EXTRA) for _, s in sw]
        nc = sum(1 for _, s in sw if same(s[prev], s[pr]))
        out.append(f"| {prev:g} → {pr:g} | {pr ** 2:.2f} | {np.median(em):.1f} "
                   f"| {np.median(gb):.2f} | {np.median(marg):.2f} "
                   f"| {100 * nc / len(sw):.0f}% |")

    out.append("\n**What each half of the slider's travel is worth.** Aggregate is "
               "total miles over total minutes across the pairs, which is what a "
               "product decision spends; the per-pair median is what one driver "
               "sees.\n")
    out.append("| span | extra min (p50) | beautiful mi (p50) "
               "| per-pair mi/min (p50) | aggregate mi/min |")
    out.append("|---|---|---|---|---|")
    lo_hi = [(0.0, DEFAULT_PREF), (DEFAULT_PREF, 1.0), (0.0, 1.0)]
    for a, b in lo_hi:
        dm = np.array([s[b]["minutes"] - s[a]["minutes"] for _, s in sw])
        dg = np.array([(s[b]["beautiful_km"] - s[a]["beautiful_km"]) * MI for _, s in sw])
        out.append(f"| pref {a:g} → {b:g} | {np.median(dm):.1f} | {np.median(dg):.2f} "
                   f"| {np.median(dg / np.maximum(dm, MIN_EXTRA)):.2f} "
                   f"| {dg.sum() / max(dm.sum(), MIN_EXTRA):.2f} |")
    share = [(s[DEFAULT_PREF]["beautiful_km"] - s[0.0]["beautiful_km"])
             / (s[1.0]["beautiful_km"] - s[0.0]["beautiful_km"]) for _, s in sw
             if s[1.0]["beautiful_km"] - s[0.0]["beautiful_km"] > 0.1]
    tshare = [(s[DEFAULT_PREF]["minutes"] - s[0.0]["minutes"])
              / (s[1.0]["minutes"] - s[0.0]["minutes"]) for _, s in sw
              if s[1.0]["minutes"] - s[0.0]["minutes"] > 0.1]
    out.append(f"\nThe default at pref {DEFAULT_PREF:g} takes "
               f"**{100 * np.median(share):.0f}%** of what pref 1.0 gains "
               f"(n={len(share)}) for **{100 * np.median(tshare):.0f}%** of what "
               f"pref 1.0 spends (n={len(tshare)}) — both medians over pairs where "
               f"pref 1.0 moves at all.")
    if bad_b:
        out.append("\nBeautiful miles by pref on every pair that goes backwards:\n")
        out.append("| pair | " + " | ".join(f"pref {q:g}" for q in PREF_SWEEP) + " |")
        out.append("|---" * (len(PREF_SWEEP) + 1) + "|")
        for pid in bad_b:
            row = next(s for p_, s in sw if p_["pair_id"] == pid)
            out.append(f"| {pid} | " + " | ".join(
                f"{row[q]['beautiful_km'] * MI:.2f}" for q in PREF_SWEEP) + " |")
    return sw


def report_town(idx, pairs, out):
    """Question 4. `beautiful_km` and `mean_score` are on a different scale
    under each weight vector, so the verdict is taken on `scenery_km` — which
    thresholds the raw c_* columns and so is scale-free — plus minutes and km.
    The re-blended metrics are still printed, as the size of the trap."""
    d, ruler, n, ident = {}, {"beautiful_km": [], "mean_score": []}, 0, 0
    for p in pairs:
        if p["status"] != "ok":
            continue
        e = idx.get(p["pair_id"], {})
        s0, s1 = e.get(("scenic", "shipped")), e.get(("scenic", "town_off"))
        f0, f1 = e.get(("fastest", "shipped")), e.get(("fastest", "town_off"))
        if not (s0 and s1 and f0 and f1):
            continue
        n += 1
        if abs(s0["km"] - s1["km"]) < 1e-6 and abs(s0["minutes"] - s1["minutes"]) < 1e-6:
            ident += 1
        for c in ["minutes", "km"] + SC:
            d.setdefault(c, []).append(s1[c] - s0[c])
        # The fastest arm's path cannot depend on the weights, so every
        # difference here is the ruler moving and none of it is the road.
        for c in ruler:
            ruler[c].append(f1[c] - f0[c])

    out.append(f"\n## Q4 — turning the `town` weight off ({n} pairs, "
               f"pref {DEFAULT_PREF:g})\n")
    out.append(f"- scenic route **identical** with town on and off: {_rate(ident, n)}")
    out.append("\n**Scale-free comparison, paired (town_off − shipped)**\n")
    out.append("| metric | median Δ | 95% CI on median | up / down / tied | sign-test p |")
    out.append("|---|---|---|---|---|")
    for c in ["minutes", "km"] + SC:
        v = d[c]
        lo, hi = boot_median(v)
        pos, neg, tie, pv = sign_test(v)
        ci = f"{lo:+.3f} … {hi:+.3f}" if lo is not None else "n/a"
        out.append(f"| {c} | {np.median(v):+.3f} | {ci} | {pos} / {neg} / {tie} "
                   f"| {pv:.2g} |")
    changed = [i for i in range(n)
               if abs(d["km"][i]) > 1e-6 or abs(d["minutes"][i]) > 1e-6]
    tied = 100 * (n - len(changed)) / max(n, 1)
    out.append(f"\nEvery median above is 0.000 because {tied:.0f}% "
               f"of these routes do not move at all, which is itself the first "
               f"half of the answer. Among the {len(changed)} that do move:\n")
    out.append("| metric | median Δ | 95% CI on median | mean Δ | p10 | p90 |")
    out.append("|---|---|---|---|---|---|")
    for c in ["minutes", "km"] + SC:
        v = np.array([d[c][i] for i in changed])
        lo, hi = boot_median(v)
        ci = f"{lo:+.3f} … {hi:+.3f}" if lo is not None else "n/a"
        out.append(f"| {c} | {np.median(v):+.3f} | {ci} | {v.mean():+.3f} "
                   f"| {np.percentile(v, 10):+.2f} | {np.percentile(v, 90):+.2f} |")

    out.append(f"\n**The trap, measured.** The fastest arm's *path* cannot depend on "
               f"the beauty weights, so across these {n} identical routes every "
               f"difference below is the ruler moving:\n")
    out.append("| metric, on one identical path | median Δ | p10 | p90 | largest |Δ| |")
    out.append("|---|---|---|---|---|")
    for c, v in ruler.items():
        st = pcts(v)
        out.append(f"| {c} | {st['p50']:+.3f} | {st['p10']:+.3f} | {st['p90']:+.3f} "
                   f"| {max(abs(x) for x in v):.3f} |")
    return d, ruler


def report(stem):
    pairs, routes, idx = load(stem)
    with open(f"{stem}-summary.json") as f:
        summary = json.load(f)
    out = [f"Built from `{summary['processed_dir']}` "
           f"(graph tables written {summary['graph_built']}), "
           f"{summary['place_points']} place points, seed {summary['seed']}, "
           f"{summary['routes']} routes in {summary['elapsed_s']:.0f}s "
           f"({summary['s_per_route']:.3f}s/route), BEAUTIFUL_SCORE="
           f"{summary['beautiful_score']}."]
    report_failures(pairs, summary, out)
    t = report_trade(idx, pairs, "shipped", out)
    report_trade(idx, pairs, "town_off", out)
    report_pref(idx, pairs, out)
    report_town(idx, pairs, out)

    allr = [r for b in BAND_LABELS for r in t[b]]
    out.append("\n## Pooled, for the prose only — the bands do not agree, "
               "so nothing here belongs in a headline\n")
    for k, lab in [("extra_min", "extra minutes"), ("gain_mi", "beautiful mi gained"),
                   ("ratio", "beautiful mi per extra min")]:
        out.append(f"- {lab}: {_five(pcts([r[k] for r in allr]))} (min/p10/p50/p90/max)")
    free = [r for r in allr if r["extra_min"] < MIN_EXTRA and r["gain_mi"] > 0.1]
    none = [r for r in allr if r["extra_min"] < MIN_EXTRA and r["gain_mi"] < 0.1]
    out.append(f"- scenery for free (under {MIN_EXTRA} extra min, over 0.1 mi "
               f"gained): {_rate(len(free), len(allr))}")
    out.append(f"- nothing offered at all (under {MIN_EXTRA} extra min, under "
               f"0.1 mi gained): {_rate(len(none), len(allr))}")
    # The scenery penalty is km * (1 - score/10), so it is proportional to
    # length: at any pref > 0 the router is also, quietly, a shortest-distance
    # router. That is how a "scenic" route comes back with fewer beautiful miles
    # than the fastest one — shorter, better per km, worse in total.
    worse = [r for r in allr if r["gain_mi"] < -0.01 and r["extra_min"] > MIN_EXTRA]
    out.append(f"- strictly worse (slower *and* fewer beautiful miles): "
               f"{_rate(len(worse), len(allr))}")
    if worse:
        by_band = {b: sum(1 for r in worse if r["pair_id"].startswith(b + ":"))
                   for b in BAND_LABELS}
        out.append(f"  - median {np.median([r['gain_mi'] for r in worse]):.2f} beautiful mi "
                   f"lost, worst {min(r['gain_mi'] for r in worse):.2f}; median "
                   f"{np.median([r['extra_min'] for r in worse]):.1f} extra minutes; "
                   f"median {np.median([r['extra_km'] for r in worse]):.1f} km "
                   f"*shorter*")
        out.append(f"  - by band: " + ", ".join(f"{b} km: {c}" for b, c in by_band.items()))
        rose = sum(1 for r in worse if r["score_gain"] > 0)
        out.append(f"  - and `mean_score` — the number the app puts on screen — "
                   f"**rose on {rose} of {len(worse)}** of them (median "
                   f"{np.median([r['score_gain'] for r in worse]):+.2f} on a 0-10 "
                   f"scale), so the app reports these as an improvement")
    # A warning already ships. Measuring the proposed rules without measuring it
    # would price the whole decision against nothing.
    sd = [r for r in allr if r["same_drive"]]
    none_ = [r for r in allr if r["extra_min"] < MIN_EXTRA and r["gain_mi"] < 0.1]
    caught = sum(1 for r in none_ if r["same_drive"])
    under1 = [r for r in allr if r["gain_mi"] < 1.0]
    out.append("\n## The warning that already ships\n")
    out.append("`RouteResults.isSameDrive` prints \"Same as the fastest route at "
               "this setting\" when the rounded minutes do not rise and the two "
               "printed scores are within 0.05. Replayed over this sample:\n")
    out.append("| band | " + " | ".join(f"{b} km" for b in BAND_LABELS) + " | all |")
    out.append("|---" * (len(BAND_LABELS) + 2) + "|")
    cells = []
    for b in BAND_LABELS:
        k = sum(1 for r in t[b] if r["same_drive"])
        cells.append(f"{100 * k / max(len(t[b]), 1):.1f}% ({k}/{len(t[b])})")
    out.append("| fires on | " + " | ".join(cells)
               + f" | {100 * len(sd) / len(allr):.1f}% ({len(sd)}/{len(allr)}) |")
    out.append(f"\n- it catches **{caught} of the {len(none_)}** trips where the "
               f"scenic arm really is the fastest arm")
    if sd:
        out.append(f"- and nothing else: the most any trip it fires on gains is "
                   f"{max(r['gain_mi'] for r in sd):.2f} beautiful miles for "
                   f"{max(r['extra_min'] for r in sd):.2f} extra minutes")
    out.append(f"- but **{len(under1) - sum(1 for r in under1 if r['same_drive'])} "
               f"trips ({100 * (len(under1) - sum(1 for r in under1 if r['same_drive'])) / len(allr):.1f}%)** "
               f"gain under a mile of beautiful road and are told nothing — that "
               f"gap, not the rule's accuracy, is what a new threshold would buy")

    out.append("\n## Instrument checks — each must come out at 0\n")
    out.append(f"- pairs whose fastest arm moved when the beauty weights changed: "
               f"**{summary['fastest_path_differs_across_weights']}** of "
               f"{summary['status_counts']['ok']}")
    # The sweep routes each pair a second time, through the same call with the
    # same arguments. Where its pref coincides with an arm already routed, the
    # two must land on the same road; anything else would mean `route` is not a
    # function of its arguments, and every number above would be noise.
    dis0 = dis5 = swn = 0
    for p_ in pairs:
        e = idx.get(p_["pair_id"], {})
        sw = e.get("sweep")
        if not sw:
            continue
        swn += 1
        f, sc = e.get(("fastest", "shipped")), e.get(("scenic", "shipped"))
        if f and abs(sw[0.0]["km"] - f["km"]) > 1e-6:
            dis0 += 1
        if sc and abs(sw[DEFAULT_PREF]["km"] - sc["km"]) > 1e-6:
            dis5 += 1
    out.append(f"- sweep at pref 0 disagreeing with the fastest arm: **{dis0}** of {swn}")
    out.append(f"- sweep at pref {DEFAULT_PREF:g} disagreeing with the scenic arm: "
               f"**{dis5}** of {swn}")
    print("\n".join(out))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--processed",
                   help="processed data dir (data/processed-ne is what the "
                        "deployed server runs; it lives in the main checkout)")
    p.add_argument("--per-band", type=int, default=250,
                   help="pairs drawn per distance band (default 250, x4 bands)")
    p.add_argument("--sweep-pairs", type=int, default=60,
                   help="pairs given the full pref sweep, split across bands")
    p.add_argument("--seed", type=int, default=20260829)
    p.add_argument("--out-dir", default="docs/route-census")
    p.add_argument("--prefix", default="census")
    p.add_argument("--report-only", action="store_true",
                   help="skip routing; re-analyse the CSVs already in --out-dir")
    args = p.parse_args()
    if args.report_only:
        report(str(Path(args.out_dir).resolve() / args.prefix))
    elif not args.processed:
        p.error("--processed is required unless --report-only is given")
    else:
        run(args)
        report(str(Path(args.out_dir).resolve() / args.prefix))


if __name__ == "__main__":
    main()
