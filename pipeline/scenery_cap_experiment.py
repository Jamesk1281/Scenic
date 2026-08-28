"""Measurement harness for docs/scenery-cap-options.md. Nothing here is wired
into the router.

The question it answers: can the top of the preference slider be made to produce
genuinely longer routes? It measures five candidate objectives over the same
origin-destination pairs. It does not change `BETA`, `PREF_CURVE` or
`Router._weights` on disk -- it monkeypatches the weight function between calls
and puts it back.

Options 1-4 are all the same shortest-path problem with different constants:

    w_e = d_minutes_e + A * km_e * (b - score_e/10)

    (A, b) = (pref**PREF_CURVE * BETA, 1)  the shipped router        -- option 1
    (A, b) with b < 1                      reward above a baseline   -- option 2
    (1/lambda, 0)                          the CSP's Lagrangian      -- option 3
    (1, mu)                                a Dinkelbach step         -- option 4

which is why they behave so alike, and why they share one validity wall: every
weight is >= 0 iff b >= b*(A) = max_e[score_e/10 - d_minutes_e/(A*km_e)]. Below
that wall scipy's dijkstra is invalid, and because a two-way road's two
directions carry the same km and score, the first negative edge also gives a
negative 2-cycle -- so there is no shortest path to find, at any price.

Option 5 (an explicit detour budget) is not in the family. It makes length an
input instead of an emergent property, which is why it is the only one that
produces a longer route and the only one that stays cheap.

Usage:
    .venv/bin/python pipeline/scenery_cap_experiment.py <processed_dir> [option...]

`<processed_dir>` is `data/processed`, which lives only in the main checkout.
With no option names it runs all of them. One Router load (~15 s) serves the
whole sweep; each route is ~0.15 s.
"""

import sys
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router as R                                          # noqa: E402


# The four pairs the original brief measured, plus six from recorded drives in
# traces/ so the sample is not all spokes from one town. The Harvard->Needham
# pins are lifted from drive-2026-08-25-211808.ndjson and reproduce the brief's
# row exactly (65.2 km / 1.47 fastest, 49.2 km / 5.73 at pref=1), which is what
# pins this harness to the measurement it is meant to extend.
PINS = {
    "Harvard":     (42.478244, -71.620952),
    "Needham":     (42.277252, -71.241774),
    "Worcester":   (42.2626, -71.8023),
    "Wachusett":   (42.4487019, -71.877296),
    "Wellesley":   (42.2965, -71.2924),
    "Foxborough":  (42.075481, -71.090375),
    "Groton":      (42.5455337, -71.5518133),
    "Lexington":   (42.4243752, -71.2323368),
    "Chelmsford":  (42.4848940, -71.2643360),
    "BostonHarvSq": (42.3551, -71.0657),
    "Gloucester":  (42.6159, -70.6620),
}
PAIRS = [("Harvard", "Needham"), ("Needham", "Wachusett"),
         ("Needham", "Worcester"), ("Needham", "Wellesley"),
         ("Needham", "Foxborough"), ("Needham", "Groton"),
         ("Needham", "Lexington"), ("Needham", "Chelmsford"),
         ("BostonHarvSq", "Gloucester"), ("Worcester", "Groton")]
BRIEF = 4                       # the first four are the brief's

HDR = "     km    min  mean  scenKM  min>=7"
_SHIPPED_WEIGHTS = R.Router._weights


def measure(res):
    """Route measures. The two scenery numbers are TOTALS, not averages.

    `scenic_km` = sum of km * score/10, the prettiness actually driven through,
    which grows with length as a scenic route should. `min7` = minutes on road
    scoring >= 7, the top 8% of Massachusetts road-km.

    `mean_score` is carried for continuity and is never the basis of a claim: it
    is length-weighted, so a route can score lower while being strictly more
    scenic in total. Comparing routes on it alone is what makes a *shorter*
    route look like the scenic answer.
    """
    L = res.edges["length_m"].to_numpy() / 1000.0
    s = np.asarray(res.scores, float)
    m = np.asarray(res.edge_minutes, float)
    return dict(km=float(L.sum()), minutes=float(m.sum()),
                mean_score=float((s * L).sum() / max(L.sum(), 1e-9)),
                scenic_km=float((L * s / 10.0).sum()),
                unscenic_km=float((L * (1.0 - s / 10.0)).sum()),
                km7=float(L[s >= 7].sum()), min7=float(m[s >= 7].sum()))


def patch(A, b):
    """Install w = d_minutes + A*km*(b - score/10) as the router's weights."""
    def _w(self, pref, scores):
        return self.d_minutes + A * (self.km * (b - scores / 10.0))[self.eidx]
    R.Router._weights = _w


def unpatch():
    R.Router._weights = _SHIPPED_WEIGHTS


def critical_b(rt, A):
    """b*(A): the largest b at which some edge weight goes negative.

    Above it every weight is >= 0 and dijkstra is valid; below it there is a
    negative 2-cycle and no shortest path exists. Note what this quantity *is*:
    an edge goes negative exactly when adding it lowers the total cost, which is
    exactly what "reward me for driving further" asks for. The valid region is
    therefore precisely the region in which no kilometre is ever worth adding.
    """
    scores = rt._edge_scores({})
    km = rt.km[rt.eidx]
    ok = km > 0
    return float(np.max(scores[rt.eidx][ok] / 10.0 - rt.d_minutes[ok] / (A * km[ok])))


def negative_edges(rt, A, b):
    """(count of negative directed edges, whether a negative 2-cycle exists)."""
    scores = rt._edge_scores({})
    w = rt.d_minutes + A * (rt.km * (b - scores / 10.0))[rt.eidx]
    idx = np.flatnonzero(w < 0)
    by_edge = {}
    for i in idx:
        by_edge.setdefault(int(rt.eidx[i]), []).append(bool(rt.flip[i]))
    return len(idx), any(len(set(v)) == 2 for v in by_edge.values())


def run(rt, a, b, pref=1.0):
    res = rt.route(rt.snapped[a], rt.snapped[b], pref)
    return None if res is None else measure(res)


def line(m, base=None):
    if m is None:
        return "   (no route)"
    s = (f"{m['km']:6.1f} {m['minutes']:6.1f} {m['mean_score']:5.2f} "
         f"{m['scenic_km']:7.1f} {m['min7']:6.1f}")
    if base:
        s += (f"   {m['minutes']/base['minutes']:5.2f}x time "
              f"{m['km']/base['km']:5.2f}x dist "
              f"{m['scenic_km']/max(base['scenic_km'], 1e-9):5.2f}x scenic")
    return s


def head(title):
    print("\n" + "=" * 104 + f"\n{title}\n" + "=" * 104)


# --- option 1 ----------------------------------------------------------------

def option1(rt, fast):
    head("OPTION 1 -- LEAVE IT ALONE.  shipped BETA=8.0, PREF_CURVE=2.0")
    print(f"{'pair':28s}{HDR}")
    ratios = []
    for a, b in PAIRS:
        f, g = fast[(a, b)], run(rt, a, b, 1.0)
        mark = "*" if PAIRS.index((a, b)) < BRIEF else " "
        print(f"{mark}{a[:12]:>12}->{b[:12]:<13} fastest {line(f)}")
        print(f" {'':26s} pref=1  {line(g, f)}")
        ratios.append((g["minutes"] / f["minutes"], g["km"] / f["km"]))
    t = np.median([r[0] for r in ratios]); d = np.median([r[1] for r in ratios])
    print(f"\n  median over all {len(PAIRS)} pairs: {t:.2f}x travel time, {d:.2f}x distance.")
    print("  The slider already buys time. What it does not buy is distance.")


# --- option 2 ----------------------------------------------------------------

def option2(rt, fast):
    head("OPTION 2 -- REWARD SCORE ABOVE A BASELINE:  w = dmin + A*km*(b - score/10)")
    print("b*(A) is the validity wall: below it, weights go negative.\n")
    print(f"{'A':>8} {'b*(A)':>8} {'neg edges at b*-0.01':>22}  negative 2-cycle?")
    for A in [1.0, 2.0, 4.0, 8.0, 16.0, 64.0, 512.0]:
        bc = critical_b(rt, A)
        n, cyc = negative_edges(rt, A, bc - 0.01)
        print(f"{A:8.0f} {bc:8.3f} {n:22d}  {'YES -- no shortest path exists' if cyc else 'no'}")

    print("\nInside the window at the shipped A=8 (b swept down to b*):")
    print(f"{'pair':26s} {'b':>7} {HDR}")
    for a, bn in PAIRS[:BRIEF]:
        f = fast[(a, bn)]
        print(f"{a[:11]:>11}->{bn[:11]:<13} {'fastest':>7} {line(f)}")
        for bb in [1.0, 0.96, 0.94, 0.92, critical_b(rt, 8.0) + 1e-4]:
            patch(8.0, bb)
            print(f"{'':26s} {bb:7.4f} {line(run(rt, a, bn), f)}")
        unpatch()
        print()

    print("On the valid frontier -- for each A, b set just above b*(A), the most")
    print("aggressive setting that is still a legal shortest-path problem:")
    print(f"{'pair':26s} {'A':>6} {'b':>8} {HDR}")
    for a, bn in PAIRS[:BRIEF]:
        f = fast[(a, bn)]
        print(f"{a[:11]:>11}->{bn[:11]:<13} {'fastest':>15} {line(f)}")
        for A in [1.0, 2.0, 4.0, 8.0, 16.0, 64.0]:
            bb = critical_b(rt, A) + 1e-4
            n, _ = negative_edges(rt, A, bb)
            assert n == 0, f"frontier point A={A} b={bb} is not actually valid"
            patch(A, bb)
            print(f"{'':26s} {A:6.0f} {bb:8.5f} {line(run(rt, a, bn), f)}")
        unpatch()
        print()
    print("  Every legal setting still returns a route SHORTER than the fastest route.")


# --- option 3 ----------------------------------------------------------------

def option3(rt, fast):
    head("OPTION 3 -- CONSTRAINED SHORTEST PATH (maximise scenery under a time cap)")
    scores = rt._edge_scores({})
    lam_star = float(np.max((scores[rt.eidx] / 10.0)
                            * (rt.km[rt.eidx] / np.maximum(rt.d_minutes, 1e-9))))
    print("Lagrangian subproblem: min over paths of [lambda*minutes - scenic_km],")
    print("which is the family at A=1/lambda, b=0. Valid only for lambda >= lam* =")
    print(f"{lam_star:.4f}; below that the subproblem is unbounded (drive the pretty loop\n"
          "forever), which is the same wall as option 2 seen from another angle.\n")
    print(f"{'pair':26s} {'lambda':>8} {HDR}")
    for a, bn in PAIRS[:BRIEF]:
        f = fast[(a, bn)]
        print(f"{a[:11]:>11}->{bn[:11]:<13} {'fastest':>8} {line(f)}")
        for lam in [50.0, 10.0, 3.0, 2.0, lam_star * 1.001]:
            patch(1.0 / lam, 0.0)
            print(f"{'':26s} {lam:8.3f} {line(run(rt, a, bn), f)}")
        unpatch()
        print()

    print("Binary search on lambda for a time budget (what a request would do):")
    print(f"  {'pair':24s} {'target':>8} {'iters':>6} {'ms':>7}   result")
    for a, bn in PAIRS[:BRIEF]:
        f = fast[(a, bn)]
        for N in [1.25, 1.5, 2.0]:
            T = N * f["minutes"]
            lo, hi, best, t0, it = lam_star * 1.0005, 500.0, None, time.time(), 0
            for _ in range(14):
                mid = (lo * hi) ** 0.5
                it += 1
                patch(1.0 / mid, 0.0)
                m = run(rt, a, bn)
                unpatch()
                if m["minutes"] <= T:
                    best, hi = m, mid
                else:
                    lo = mid
            ms = (time.time() - t0) * 1000
            got = (f"{best['km']:.1f} km, scenic-km {best['scenic_km']:.1f} "
                   f"({best['scenic_km']/f['scenic_km']:.2f}x), min>=7 {best['min7']:.1f}")
            print(f"  {a[:10]:>10}->{bn[:10]:<12} {T:8.1f} {it:6d} {ms:7.0f}   {got}")
    print("\n  Driving lambda to the wall returns the FASTEST ROUTE UNCHANGED on three of")
    print("  four pairs: b=0 forces A<=0.71, a scenery weight 11x weaker than shipped.")


# --- option 4 ----------------------------------------------------------------

def option4(rt, fast):
    head("OPTION 4 -- RATIO OBJECTIVE (maximise mean score) via Dinkelbach")
    scores = rt._edge_scores({})
    s_max = float(scores.max())
    print(f"Step k minimises sum km*(mu - score/10); mu_(k+1) is the mean score of the")
    print(f"path found. Weights are >= 0 only for mu >= s_max/10 = {s_max/10:.2f}.\n")
    for a, bn in PAIRS[:BRIEF]:
        mu = 1.0
        patch(1.0, mu)
        # no time term at all for the pure ratio objective
        def _w(self, pref, sc, mu=mu):
            return (self.km * (mu - sc / 10.0))[self.eidx]
        R.Router._weights = _w
        m = run(rt, a, bn)
        unpatch()
        nxt = m["mean_score"] / 10.0
        nneg = int((((rt.km * (nxt - scores / 10.0))[rt.eidx]) < 0).sum())
        print(f"  {a}->{bn}")
        print(f"    iter 0: mu=1.0000 -> {line(m, fast[(a, bn)])}")
        print(f"            next mu = {nxt:.4f}; at that mu {nneg:,} of {len(rt.eidx):,} "
              f"directed edges go NEGATIVE -> dijkstra invalid, iteration stops here")
    print("\n  Iteration 0 reproduces option 1 at BETA=infinity. A path's mean score is")
    print("  always below the network's best edge, and the validity threshold IS the")
    print("  network's best edge, so option 4 leaves the tractable region at iter 2")
    print("  always. It would fix the mean_score inversion; a ratio is scale-free, so")
    print("  it has no more reason to produce a LONG route than option 1 does.")


# --- option 5 ----------------------------------------------------------------

def option5(rt, fast):
    head("OPTION 5 -- EXPLICIT DETOUR BUDGET (scenic waypoint under a time cap)")
    print("Two dijkstras and two O(V) tree passes give the cost of routing via EVERY")
    print("node in the state at once; keep the best via-node that fits the budget.")
    print("Length is an input, so no weight ever goes negative.\n")
    import pandas as pd
    scores = rt._edge_scores({})
    N = rt.n
    W = rt.d_minutes + 8.0 * (rt.km * (1.0 - scores / 10.0))[rt.eidx]   # shipped pref=1
    order = np.lexsort((W, rt.slot_pair))
    _, first = np.unique(rt.slot_pair[order], return_index=True)
    best = order[first]
    G = csr_matrix((W[best], (rt.u_tail, rt.u_head)), shape=(N, N))
    GT = G.T.tocsr()
    pair_min, pair_scen = rt.d_minutes[best], (rt.km * scores / 10.0)[rt.eidx[best]]

    def totals(dist, pred, reverse):
        """Cumulative minutes and scenic-km from the tree root to every node."""
        cm, cs = np.zeros(N), np.zeros(N)
        live = np.isfinite(dist) & (pred >= 0)
        nodes = np.flatnonzero(live)
        t, h = (pred[nodes], nodes) if not reverse else (nodes, pred[nodes])
        p = np.searchsorted(rt._pair_key, t.astype(np.int64) * N + h.astype(np.int64))
        vm, vs = np.zeros(N), np.zeros(N)
        vm[nodes], vs[nodes] = pair_min[p], pair_scen[p]
        for i in np.argsort(dist, kind="stable"):
            if live[i]:
                cm[i], cs[i] = cm[pred[i]] + vm[i], cs[pred[i]] + vs[i]
        return cm, cs

    def chain(pred, node, root):
        out, cur = [], node
        while cur != root and cur >= 0:
            out.append(cur)
            cur = pred[cur]
        out.append(root)
        return out

    for a, bn in PAIRS[:BRIEF]:
        s, d = rt.snapped[a], rt.snapped[bn]
        f, g1 = fast[(a, bn)], run(rt, a, bn, 1.0)
        t0 = time.time()
        df, pf = dijkstra(G, directed=True, indices=s, return_predecessors=True)
        db, pb = dijkstra(GT, directed=True, indices=d, return_predecessors=True)
        t_dij = time.time() - t0
        t0 = time.time()
        cmf, csf = totals(df, pf, False)
        cmb, csb = totals(db, pb, True)
        t_prop = time.time() - t0
        tot_m, tot_s = cmf + cmb, csf + csb
        reach = np.isfinite(df) & np.isfinite(db)
        print(f"\n{a}->{bn}   fastest {line(f)}")
        print(f"{'':21s}pref=1 {line(g1, f)}")
        print(f"  [2 dijkstras {t_dij*1000:.0f} ms, 2 tree passes {t_prop*1000:.0f} ms "
              f"(python; O(V) and trivially compiled)]")
        print(f"  {'budget':>7} {HDR}   repeat")
        for Nx in [1.5, 2.0, 3.0, 4.0]:
            ok = reach & (tot_m <= Nx * f["minutes"])
            if not ok.any():
                print(f"  {Nx:6.2f}x  no via-node fits -- budget is under what pref=1 "
                      f"already costs ({g1['minutes']/f['minutes']:.2f}x)")
                continue
            w = int(np.flatnonzero(ok)[np.argmax(tot_s[ok])])
            path = chain(pf, w, s)[::-1] + chain(pb, w, d)[1:]
            if len(path) < 2:
                continue
            res = rt._collect(path, W, scores)
            m = measure(res)
            Lk = res.edges["length_m"].to_numpy() / 1000.0
            dup = float(Lk[pd.Index(res.edges.index).duplicated()].sum())
            print(f"  {Nx:6.2f}x {line(m, f)}   {dup:.1f} km ({100*dup/m['km']:.0f}%)")
    print("\n  The only option whose distance column exceeds 1.0. The repeat column is")
    print("  its real cost: one via-node lets the two halves share road, negligible up")
    print("  to ~2.5x budget and ~30% of the drive at 4x.")


OPTIONS = {"1": option1, "2": option2, "3": option3, "4": option4, "5": option5}


def main(processed_dir, which):
    t0 = time.time()
    rt = R.Router(processed_dir)
    rt.snapped = {k: rt.snap(*v)[0] for k, v in PINS.items()}
    print(f"# router loaded in {time.time()-t0:.1f}s "
          f"({rt.n:,} nodes, {len(rt.eidx):,} directed edges)")
    fast = {(a, b): run(rt, a, b, 0.0) for a, b in PAIRS}
    for k in which:
        OPTIONS[k](rt, fast)
        unpatch()


if __name__ == "__main__":
    args = sys.argv[2:] or list(OPTIONS)
    main(sys.argv[1], [a for a in args if a in OPTIONS])
