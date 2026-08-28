"""Scenic loops: one start point, a distance target, and a different loop each press.

The user gives a start and a length. There is no destination, so this module's
whole job is choosing the geography — which `docs/loop-routes-design.md` measures
as the term that decides whether a drive is good.

Why this is not `router.route(start, start)`
--------------------------------------------
That returns a zero-length route, and correctly so: the router minimises a sum
that accumulates with distance, so the cheapest way back to where you started is
not to leave. Hitting a distance *target* while collecting scenery is the
Orienteering Problem, which is NP-hard. What makes it tractable here is the
router's biggest inefficiency:

    `route()` runs a full-graph Dijkstra with no target early-exit.

For point-to-point that is pure waste. Here it is the entire architecture. One
pass from the start settles every node in the state, and — the measured result
this module is built on — the shortest-path *tree* it hands back can be walked to
recover the exact km and collected scenic-km to all 313,950 nodes, for about
80 ms of vectorised arithmetic. See `_accumulate`. So one pass yields 313,950
costed candidate turnarounds, and re-filtering all of them when the user moves
the distance slider takes 1.1 ms.

The shape of a loop
-------------------
1. Cache two fields per (start, pref, weights): the cost/km/scenic-km *out* to
   every node, and the same *back* from every node (§`_fields`). Two passes,
   about half a second.
2. Filter and rank candidate turnarounds — free, 1.1 ms (§`candidates`).
3. For a chosen turnaround, reuse the cached outbound path and run **one** more
   Dijkstra home with the roads just driven made three times more expensive
   (§`_build`). About 140 ms.

Step 3 is the one that makes this a loop rather than an out-and-back. Measured
over three starts at a 40 km target, the share of loop km spent driving a road
already driven:

    plain out-and-back      Needham 26%   Petersham 50%   Boston 18%
    with the penalty        Needham  0%   Petersham  0%   Boston  0%

Petersham at 50% is the arithmetic maximum — a rural start where the way home is
the way out, reversed. One extra pass removes it entirely for about 0.15 points
of mean score.

Those three are one turnaround each, chosen for scenery. What `plan` actually
ships is chosen for *distance*, which is a different candidate: over three starts
and three targets it comes out at 1.1% repeated road on average and 3.4% at
worst. Not zero, and the difference is `SPAN_PICKS` — see there.

The penalty is soft (a multiplier) and never a ban, and that is not a
preference. 53,216 of 310,162 junctions — **17.2%** — are dead ends, and a house
on a cul-de-sac has *no* loop that avoids driving its own street twice. A hard
edge-disjointness constraint, which is the textbook way to get a non-retracing
circuit, returns "no loop" for every one of them. Sampled five at random: all
five failed hard and all five worked at x3.

What `pref` does here, and why the tab should not offer it
----------------------------------------------------------
Less than it does point-to-point, and not monotonically. Measured at 40 km
targets from two starts:

    pref      0.00   0.25   0.50   0.75   1.00
    Needham   4.76   5.41   5.67   5.73   5.74     score
              2.3    4.1    7.0    8.7    8.7      km >= 7
    Concord   5.86   5.86   4.99   6.15   6.19     score
              9.2    9.2    7.6   11.3    9.9      km >= 7

The endpoints behave — pref 1.0 is clearly better than pref 0.0, on both
measures, from both starts. The dip in the middle is real, and *which* pref
dips depends on the start: Concord loses 0.87 at pref 0.5, and Needham used to
lose 1.42 at pref 0.25 before the forest component was rebuilt on measured tree
cover (it read 5.00 / 3.58 / 5.43 / 5.84 under the old `c_green`). Do not read
the dip as living at a particular slider position. It has two causes, both
structural rather than a bug to chase: candidate turnarounds are ranked by
scenery whatever `pref` is, so a middling pref moves the choice of *where to go*
without buying the routing that would justify it; and the final choice among the
built loops is made on distance and repeated road with no scenery term (see
`_miss`), which is harmless at pref 1.0 where every candidate is pretty and is
not below it.

There is also a cache reason: `pref` is the one parameter that invalidates the
two cached passes, where the distance slider does not. So the loop tab should
pin pref at 1.0 and let the distance slider and the regenerate button be the
controls. Anyone who wants to expose it should sweep it first.

Nothing here changes the router or its objective. Every number above is at the
shipped `BETA` and `PREF_CURVE`. The distance target does quietly repair the
objective's known defect, though, and it is worth knowing why: at *fixed* total
km, minimising the router's `sum of km*(1 - score/10)` is exactly maximising
`sum of km*score`, because `sum of km` is a constant. The length-shrinking
pathology in `docs/scenery-cap-options.md` is a property of letting length float.
Here the slider pins it. Only at candidate selection, though, not inside the
search — which is why these loops reach far out and come back rather than
rambling in a circle.
"""

from dataclasses import dataclass, field as _dc_field

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

# How much more expensive a road becomes on the way home once it has been driven
# on the way out. Measured at 3, 10 and infinity over three starts: all three
# gave 0% repeated road, so the retrace is bought at x3 and the rest is free.
# x3 is chosen because it stays closest to the requested distance (45.7 km
# against 50.4 km at a Petersham 40 km target) and because it degrades where a
# ban cannot: on the 17.2% of starts that are dead ends it still returns a loop.
PENALTY = 3.0

# Compass octants, from due north, clockwise. What the regenerate button varies:
# eight loops built one per sector from a Needham start shared a median of 1% of
# their roads, so these really are different drives and not jitters of one.
SECTORS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# How far from the requested distance a candidate may sit and still be
# considered. Wider than the accuracy we ship, on purpose: the estimate below is
# exact for the *mirror* loop, but the penalised leg home is a different path and
# comes out anywhere from 13% short to 14% long, so the band has to be loose
# enough to contain the answer and the final choice is made on built lengths.
CANDIDATE_TOLERANCE = 0.10

# How many candidates spanning the band get built before one is kept. Each costs
# one Dijkstra (~140 ms). Measured error against the requested distance:
#
#     picks              1              3              5
#     worst error        +21%           -7%            +5%
#     worst repeated      --            5.0%           3.4%
#     mean repeated       --            1.7%           1.1%
#
# 5, and the second row is why. At 3 picks the slice holding the target can offer
# only a candidate that doubles back — at a Needham 40 km target it chose a
# 39.7 km loop repeating 2.0 km over a 43.2 km loop repeating none. Narrower
# slices fix that without touching the selection rule, which is the cheaper fix:
# weighting repeated road more heavily instead was measured and cost 14% of
# distance accuracy to save 0.4% of retrace.
#
# Do *not* try to converge by re-scaling the target from a built length instead —
# it jumps to a different candidate with a different stretch and oscillates
# (measured +5% to -19% on one Petersham target).
SPAN_PICKS = 5

# A turnaround closer than this makes a loop that leaves and returns along the
# same few metres of road, which no penalty can fix because there is nothing else
# there yet.
MIN_LEG_KM = 0.5

# The slider's ends. Not graph limits — the largest available loop is 516 km from
# Needham, 622 km from Petersham — but the range where the answer is a drive.
MIN_TARGET_KM, MAX_TARGET_KM = 5.0, 200.0

# The score at or above which a road counts as properly beautiful, for the
# headline "19 of your 40 km" number. Chosen because it separates a scenic loop
# from a fast one 188-fold (18.8 km against 0.1 km at a Needham 40 km target)
# where the means only manage 6.03 against 1.94.
BEAUTIFUL_SCORE = 7.0


@dataclass
class Loop:
    """One built loop: the route itself, plus what is loop-specific about it.

    `km`, `minutes` and `mean_score` are read off `route` rather than off the
    candidate estimate, so every number shown describes the line that is
    actually drawn. The estimate is a filter and is not reported.
    """

    route: object                       # router.RouteResult
    turnaround_idx: int
    turnaround: tuple                   # (lat, lon)
    sector: str
    target_km: float
    repeated_km: float
    beautiful_km: float

    @property
    def km(self):
        return self.route.km

    @property
    def minutes(self):
        return self.route.minutes

    @property
    def mean_score(self):
        return self.route.mean_score

    @property
    def repeated_fraction(self):
        """Share of the loop spent on a road it has already driven.

        Worth surfacing rather than hiding: it is the number that tells a user
        their 40 km drive is really a 20 km drive twice. Normally under 0.03. It
        goes high where the geography has no answer — a 5 km loop from a rural
        start comes back at 0.30, because within 2.5 km of the start there is
        only one road — and that is the cue to say so rather than to pretend.
        """
        return self.repeated_km / self.km if self.km else 0.0

    @property
    def error(self):
        """Signed fraction by which the built loop misses the request, or None
        when it was built directly rather than asked for (see `_build`)."""
        if not self.target_km:
            return None
        return (self.route.km - self.target_km) / self.target_km


@dataclass
class _CostModel:
    """Edge weights and per-node-pair lookups for one (pref, weights) setting.

    Held apart from the fields below because it does not depend on the start, so
    a user shuffling loops from one place reuses it for every press. About 30 MB.
    """

    scores: np.ndarray          # per undirected edge, 0-10, under the user's weights
    w_slot: np.ndarray          # per directed slot, the Dijkstra weight
    pair_w: np.ndarray          # per unique (tail, head), the cheapest slot's weight
    pair_km: np.ndarray         # ...and that slot's road length, scenic-km, edge id
    pair_scen: np.ndarray
    pair_eidx: np.ndarray


@dataclass
class _Field:
    """One Dijkstra pass from (or to) the start, with km and scenic-km recovered.

    `km` and `scen` are meaningless where `cost` is infinite — an unreachable
    node self-loops in the predecessor tree and accumulates zero. Callers must
    mask on `np.isfinite(cost)`; `_Fields.reachable` does it once.
    """

    cost: np.ndarray
    km: np.ndarray
    scen: np.ndarray
    pred: np.ndarray


@dataclass
class _Fields:
    """The two cached passes for one (start, pref, weights). About 25 MB.

    Carries the `_CostModel` they were built from, so a loop built off these
    fields cannot be priced under different weights than the ones that chose the
    turnaround.
    """

    start: int
    cost: _CostModel
    out: _Field
    back: _Field
    reachable: np.ndarray = _dc_field(default=None, repr=False)

    def __post_init__(self):
        self.reachable = np.isfinite(self.out.cost) & np.isfinite(self.back.cost)

    @property
    def loop_km(self):
        """Estimated length of an out-and-back loop through each node.

        Exact for the mirror loop — verified against `route().km` to four
        decimals — and an estimate for the loop we actually build, because the
        penalised leg home is a different path. Good enough to filter on, not
        good enough to report.
        """
        return self.out.km + self.back.km

    @property
    def loop_scen(self):
        return self.out.scen + self.back.scen


class LoopPlanner:
    """Builds scenic loops over an already-loaded `Router`.

    Holds no reference to the router's internals beyond what it reads, and
    changes nothing on it, so it is safe to construct alongside ordinary routing
    and to throw away.

    The two caches are sized for the serving box, not for a workstation: the
    process runs around 1 GB on a laptop behind a tunnel, and a field set is
    ~25 MB while a cost model is ~30 MB. Four starts and two settings is ~160 MB.
    Both are plain insertion-ordered dicts trimmed from the front, which is a
    true LRU only if callers touch entries by going through `plan`. They do.
    """

    def __init__(self, router, field_cache=4, cost_cache=2):
        self.router = router
        self._field_cache_size = field_cache
        self._cost_cache_size = cost_cache
        self._fields_by_key = {}
        self._costs_by_key = {}

    # ------------------------------------------------------------------ public

    def sectors(self, start: int, target_km: float, pref: float = 1.0,
                weights: dict = None):
        """Which compass directions actually hold a loop of about this length.

        Returns `{sector: candidate count}` for the populated ones only, which is
        what lets the app offer real directions instead of a blind shuffle. It
        must be asked rather than assumed: a Boston start has **zero** candidates
        due east at 20 km and zero north-east at 40 km — that is the harbour and
        the ocean — and a Petersham start has 9 south-west against 289
        north-west. Costs nothing beyond the cached passes.
        """
        fields = self._fields(start, pref, weights)
        idx = self.candidates(fields, target_km)
        if not len(idx):
            return {}
        codes = self._sector_codes(fields.start, idx)
        counts = np.bincount(codes, minlength=len(SECTORS))
        return {SECTORS[i]: int(c) for i, c in enumerate(counts) if c}

    def candidates(self, fields: _Fields, target_km: float,
                   tolerance: float = CANDIDATE_TOLERANCE,
                   sector: str = None):
        """Node indices that could be the turnaround of a loop of this length.

        The whole of step 2, and the reason the distance slider is free: this is
        array work over every node in the state, measured at 1.1 ms.
        """
        km = fields.loop_km
        ok = (fields.reachable
              & (np.abs(km - target_km) <= tolerance * target_km)
              & (fields.out.km >= MIN_LEG_KM))
        idx = np.where(ok)[0]
        if sector is not None and len(idx):
            want = SECTORS.index(sector)
            idx = idx[self._sector_codes(fields.start, idx) == want]
        return idx

    def plan(self, start: int, target_km: float, pref: float = 1.0,
             weights: dict = None, sector: str = None,
             picks: int = SPAN_PICKS, penalty: float = PENALTY):
        """A scenic loop of about `target_km` from `start`, or None.

        None means the geography cannot answer — a rural start asked for a loop
        shorter than its road spacing allows. Callers should say so and offer the
        nearest length that works, rather than showing an error: at a Petersham
        start a 10 km request has only 21 candidates, and below about 15 km in
        open country there is genuinely nothing to return.

        `sector` is what the regenerate button varies. Leave it None for the
        first loop and the best-scoring direction wins.

        Costs `picks` Dijkstra passes on a warm cache (~140 ms each), plus two
        (~0.5 s) the first time this start, pref and weight set are seen.
        """
        target_km = float(np.clip(target_km, MIN_TARGET_KM, MAX_TARGET_KM))
        fields = self._fields(start, pref, weights)
        idx = self.candidates(fields, target_km, sector=sector)
        if not len(idx):
            return None

        built = [loop for loop in
                 (self._build(fields, int(v), penalty)
                  for v in self._spread(fields, idx, target_km, picks))
                 if loop is not None]
        if not built:
            return None
        # Kept on built length and repeated road, not on estimated length and
        # never on score. Building more and keeping the *best-scoring* one was
        # measured and is worthless: 40 passes and six seconds buy 0.02 points
        # over one, because the top of the band is flat. Passes spent on getting
        # the distance right are the only ones that pay.
        best = min(built, key=lambda loop: _miss(loop, target_km))
        best.target_km = target_km
        return best

    def resume(self, src: int, via: int, dst: int, pref: float = 1.0,
               weights: dict = None):
        """A drive from `src` to `dst` that still goes round by way of `via`.

        What a loop's reroute needs, and it cannot be had from `route()`. A loop
        ends where it began, so a driver who misses a turn and asks for a route
        to their destination is asking for a route to the place they are trying
        to get away from — and gets the short way home, which deletes the rest of
        the drive. Measured on a 24 mi Needham loop, a missed turn two miles in
        would have replaced 22 remaining miles with about three.

        So until the driver has passed the far point of the loop, the replacement
        has to be pinned through it. Two Dijkstras rather than one, and no
        candidate filtering — the geography was already chosen when the loop was
        generated, and this is only the way back onto it.

        Returns a single `RouteResult`, so the caller cannot tell it was built
        from two searches and the turn-by-turn reads continuously across the
        waypoint.
        """
        cost = self._cost(round(float(pref), 4), _weights_key(weights))
        out = self._leg(cost, int(src), int(via))
        if out is None:
            return None
        # Threaded through whichever index of `via` the first leg actually
        # arrived at, not through the junction's original index. If `via` is a
        # junction split for turn restrictions, arriving on a restricted
        # approach lands on a copy whose exits are the legal ones — so
        # continuing from that copy is what keeps the second leg from taking a
        # turn the first leg's arrival forbids.
        for arrival in out:
            back = self._leg(cost, arrival[-1], int(dst))
            if back is not None:
                # The cheapest way home from here; unlike the waypoint, the end
                # of the route has nothing to continue into, so any arrival at
                # it will do and the first is the best.
                path = arrival + back[0][1:]
                return self.router._collect(path, cost.w_slot, cost.scores)
        return None

    def _leg(self, cost: _CostModel, src: int, dst: int):
        """Node paths from `src` to `dst`, cheapest arrival first.

        A list rather than one path, because a split junction can be arrived at
        several ways and only the caller knows whether the cheapest one can be
        continued from.
        """
        r = self.router
        g = csr_matrix((cost.pair_w, (r.u_tail, r.u_head)), shape=(r.n, r.n))
        dist, pred = dijkstra(g, directed=True, indices=src,
                              return_predecessors=True)
        ends = self._arrival_indices(dst)
        ends = ends[np.isfinite(dist[ends])]
        if not len(ends):
            return None
        paths = []
        for end in ends[np.argsort(dist[ends])]:
            path = _tree_path(pred, src, int(end))
            if path is not None:
                paths.append(path)
        return paths or None

    def nearest_length(self, start: int, target_km: float, pref: float = 1.0,
                       weights: dict = None):
        """The closest loop length that has any candidate at all, or None.

        For the message shown when `plan` returns None. Reuses the cached
        passes, so it is free.
        """
        fields = self._fields(start, pref, weights)
        ok = fields.reachable & (fields.out.km >= MIN_LEG_KM)
        km = fields.loop_km[ok]
        km = km[(km >= MIN_TARGET_KM) & (km <= MAX_TARGET_KM)]
        if not len(km):
            return None
        return float(km[np.argmin(np.abs(km - target_km))])

    # ----------------------------------------------------------------- picking

    def _spread(self, fields, idx, target_km, picks):
        """Best-ranked candidate from each of `picks` slices across the band.

        Spread across *length* rather than taking the top `picks` by score,
        because the band's job is distance accuracy — the scoring is flat across
        the top and the lengths are not. Ranking inside a slice is by estimated
        scenic-km per km, which is a good filter and a poor tie-breaker
        (Spearman +0.24 against built score at Needham, -0.21 at Petersham) —
        and it does not matter, because trusting rank 1 costs 0.01 points.
        """
        km = fields.loop_km
        rate = fields.loop_scen[idx] / np.maximum(km[idx], 1e-9)
        lo = target_km * (1.0 - CANDIDATE_TOLERANCE)
        hi = target_km * (1.0 + CANDIDATE_TOLERANCE)
        out = []
        for a, b in zip(np.linspace(lo, hi, picks + 1)[:-1],
                        np.linspace(lo, hi, picks + 1)[1:]):
            inside = (km[idx] >= a) & (km[idx] < b)
            if inside.any():
                out.append(int(idx[np.where(inside)[0][np.argmax(rate[inside])]]))
        # A band narrower than one slice, or all candidates bunched at one
        # length, leaves nothing above. Fall back to the best-ranked candidate so
        # a legal request never returns empty for a bookkeeping reason.
        return out or [int(idx[np.argmax(rate)])]

    # ---------------------------------------------------------------- building

    def _build(self, fields, turnaround: int, penalty: float):
        """Out along the cached tree, home along a Dijkstra that avoids it.

        The penalty is applied to the *undirected* edge, not to the directed
        slot, which is the whole point: penalising one direction would leave the
        way home free to drive the same road backwards, which is the failure
        being fixed.
        """
        r = self.router
        cost = fields.cost
        out_path = _tree_path(fields.out.pred, fields.start, turnaround)
        if out_path is None:
            return None
        out_edges = self._path_edges(cost, out_path)

        # Re-collapse from slot level rather than scaling the per-pair weights
        # directly. Where two parallel roads join the same junctions and only one
        # was driven, only that one may be penalised, and the cheapest weight for
        # the pair may now belong to the other road.
        w_slot = cost.w_slot.copy()
        w_slot[np.isin(r.eidx, out_edges)] *= penalty
        pair_w = np.full(r.n_pairs, np.inf)
        np.minimum.at(pair_w, r.slot_pair, w_slot)

        g = csr_matrix((pair_w, (r.u_tail, r.u_head)), shape=(r.n, r.n))
        dist, pred = dijkstra(g, directed=True, indices=turnaround,
                              return_predecessors=True)

        # Closing the loop means *arriving* at the start, and a junction split for
        # turn restrictions stands at several indices — any of which is a legal
        # way to arrive, since a restriction forbids continuing through and a
        # route that ends here does not continue. Same rule `route()` applies to
        # a destination.
        homes = self._arrival_indices(fields.start)
        reachable = homes[np.isfinite(dist[homes])]
        if not len(reachable):
            return None
        home = int(reachable[np.argmin(dist[reachable])])
        back = _tree_path(pred, turnaround, home)
        if back is None:
            return None

        path = out_path + back[1:]
        # The route is reported under the *real* weights, not the penalised ones:
        # the penalty shaped which roads were chosen and has no business in the
        # drawn geometry or the ETA. `_collect` uses them only to pick between
        # parallel roads on a hop, and `repeated_km` below would expose a
        # divergence as a road driven twice.
        route = r._collect(path, cost.w_slot, cost.scores)
        return Loop(
            route=route,
            turnaround_idx=turnaround,
            turnaround=self._latlon(turnaround),
            sector=SECTORS[int(self._sector_codes(fields.start,
                                                  np.array([turnaround]))[0])],
            target_km=0.0,          # set by `plan`, which knows what was asked
            repeated_km=_repeated_km(route),
            beautiful_km=_beautiful_km(route),
        )

    def _path_edges(self, cost, path):
        """Undirected edge ids for the hops of a node path."""
        hops = np.asarray(path, dtype=np.int64)
        keys = hops[:-1] * self.router.n + hops[1:]
        pair = np.searchsorted(self.router._pair_key, keys)
        if (pair >= self.router.n_pairs).any() or \
                (self.router._pair_key[pair] != keys).any():
            # Every hop came out of a Dijkstra over these same arrays, so this
            # cannot happen. Saying so loudly beats a silently short loop.
            raise RuntimeError("loop hop has no directed edge; graph index is "
                               "inconsistent")
        return cost.pair_eidx[pair]

    def _arrival_indices(self, node: int):
        copies = self.router.node_copies.get(node)
        if copies is None:
            return np.array([node], dtype=np.int64)
        return np.unique(np.asarray(copies, dtype=np.int64))

    # ------------------------------------------------------------------ fields

    def _fields(self, start: int, pref: float, weights: dict):
        key = (int(start), round(float(pref), 4), _weights_key(weights))
        hit = self._fields_by_key.pop(key, None)
        if hit is not None:
            self._fields_by_key[key] = hit          # move to the warm end
            return hit
        cost = self._cost(key[1], key[2])
        fields = _Fields(start=int(start), cost=cost,
                         out=self._pass(cost, int(start), reverse=False),
                         back=self._pass(cost, int(start), reverse=True))
        self._fields_by_key[key] = fields
        while len(self._fields_by_key) > self._field_cache_size:
            self._fields_by_key.pop(next(iter(self._fields_by_key)))
        return fields

    def _pass(self, cost: _CostModel, start: int, reverse: bool):
        """One Dijkstra, plus the km and scenic-km of every path it found.

        Reverse runs on the **transposed** graph, which gives the least-cost
        legal drive *to* the start rather than a mirror of the drive out. That
        distinction is not pedantic: the graph is directed and carries turn
        restrictions, and only 0.8% of nodes have a way home the same length as
        the way out (p5 of the difference is -6.4 km).

        It is also multi-sourced over the start's split copies, using scipy's
        `min_only` so that costs one pass and not one per copy.
        """
        r = self.router
        if reverse:
            g = csr_matrix((cost.pair_w, (r.u_head, r.u_tail)), shape=(r.n, r.n))
            sources = self._arrival_indices(start)
            dist, pred, _ = dijkstra(g, directed=True, indices=sources,
                                     return_predecessors=True, min_only=True)
        else:
            g = csr_matrix((cost.pair_w, (r.u_tail, r.u_head)), shape=(r.n, r.n))
            dist, pred = dijkstra(g, directed=True, indices=start,
                                  return_predecessors=True)

        nodes = np.arange(r.n, dtype=np.int64)
        seen = pred >= 0
        if reverse:
            # On the transpose, `pred[v]` is the next node *after* v on the way
            # home, so the arc that belongs to v is the original (v -> pred[v]).
            keys = nodes[seen] * r.n + pred[seen].astype(np.int64)
        else:
            keys = pred[seen].astype(np.int64) * r.n + nodes[seen]
        pair = np.searchsorted(r._pair_key, keys)
        if (pair >= r.n_pairs).any() or (r._pair_key[pair] != keys).any():
            raise RuntimeError("shortest-path tree holds an edge the pair index "
                               "does not; graph index is inconsistent")

        # The root and every unreachable node point at themselves and carry zero,
        # so the accumulation below terminates and leaves them at zero.
        parent = np.where(seen, pred, nodes).astype(np.int64)
        edge_km = np.zeros(r.n)
        edge_scen = np.zeros(r.n)
        edge_km[seen] = cost.pair_km[pair]
        edge_scen[seen] = cost.pair_scen[pair]
        km, scen = _accumulate(parent, edge_km, edge_scen)
        return _Field(cost=dist, km=km, scen=scen, pred=pred)

    # -------------------------------------------------------------- cost model

    def _cost(self, pref: float, weights_key: tuple):
        hit = self._costs_by_key.pop(weights_key + (pref,), None)
        key = weights_key + (pref,)
        if hit is not None:
            self._costs_by_key[key] = hit
            return hit
        r = self.router
        scores = r._edge_scores(dict(weights_key))
        w_slot = r._weights(pref, scores)
        pair_w = np.full(r.n_pairs, np.inf)
        np.minimum.at(pair_w, r.slot_pair, w_slot)
        # Which slot won each pair, so the km and score reported for a hop are
        # the road Dijkstra actually priced rather than an arbitrary parallel one.
        best = np.full(r.n_pairs, -1, dtype=np.int64)
        winners = np.where(w_slot == pair_w[r.slot_pair])[0]
        best[r.slot_pair[winners]] = winners
        edge = r.eidx[best]
        model = _CostModel(scores=scores, w_slot=w_slot, pair_w=pair_w,
                           pair_km=r.km[edge],
                           pair_scen=r.km[edge] * scores[edge],
                           pair_eidx=edge)
        self._costs_by_key[key] = model
        while len(self._costs_by_key) > self._cost_cache_size:
            self._costs_by_key.pop(next(iter(self._costs_by_key)))
        return model

    # ---------------------------------------------------------------- geometry

    def _latlon(self, node: int):
        real = int(self.router.real_node[node])
        row = self.router.nodes.iloc[real]
        return (float(row["lat"]), float(row["lon"]))

    def _sector_codes(self, start: int, nodes: np.ndarray):
        """Which compass octant each node sits in, seen from the start.

        A local flat-earth projection, which is ample: the answer is one of
        eight 45-degree buckets, and Massachusetts is 300 km across.
        """
        r = self.router
        lat = r.nodes["lat"].to_numpy()
        lon = r.nodes["lon"].to_numpy()
        origin = int(r.real_node[start])
        real = r.real_node[np.asarray(nodes)]
        scale = np.cos(np.radians(lat[origin]))
        north = lat[real] - lat[origin]
        east = (lon[real] - lon[origin]) * scale
        bearing = (np.degrees(np.arctan2(east, north)) + 360.0) % 360.0
        # +22.5 so "N" spans -22.5..+22.5 rather than 0..45.
        return (((bearing + 22.5) % 360.0) / 45.0).astype(int)


# ------------------------------------------------------------- free functions

def _accumulate(parent, *edge_values):
    """Sum each edge value along every node's path to the root of a tree.

    The measured trick this module rests on. A shortest-path tree is handed back
    by `dijkstra` for free, and any quantity that sums along a path can be
    totalled up it by **pointer doubling**: repeatedly replace each node's parent
    by its grandparent while adding the two partial sums. After k rounds each
    node holds the sum over its nearest 2^k ancestors, so ~14 rounds of two
    gathers cover a road network's tree depth.

    Roots and unreachable nodes self-loop in `parent`, which makes them fixed
    points and lets the loop stop as soon as the parent array stops changing.
    Their edge value is forced to zero here rather than being required of the
    caller, because getting it wrong is silent and enormous: a root left holding
    a value of 1 comes back holding 2^32, and nothing about the number says
    where it came from.

    Exact, not approximate: it re-sums the same per-edge lengths `_collect`
    sums, and the km it produces matches `route().km` to four decimal places.
    That equality is the test worth writing first — see tests/test_loops.py.
    """
    A = parent.astype(np.int64).copy()
    totals = [np.asarray(v, dtype=np.float64).copy() for v in edge_values]
    rooted = A == np.arange(len(A))
    for t in totals:
        t[rooted] = 0.0
    for _ in range(32):
        nxt = A[A]
        for t in totals:
            t += t[A]
        if np.array_equal(nxt, A):
            break
        A = nxt
    return totals if len(totals) > 1 else totals[0]


def _tree_path(pred, start, node):
    """Node path start -> node, walking a forward predecessor tree."""
    out, cur = [], int(node)
    while cur != start:
        out.append(cur)
        cur = int(pred[cur])
        if cur < 0:
            return None
    out.append(start)
    out.reverse()
    return out


def _miss(loop, target_km):
    """How badly a built loop answers the request, in kilometres.

    Distance error and repeated road are added with equal weight because they
    are the same unit and the same complaint: a kilometre the driver did not ask
    for. That leaves no exchange rate to tune, and measurement says none is
    needed — at `SPAN_PICKS` slices the ranking barely moves between weighting
    repeated road at 1x and at 2x, and weighting it at 4x starts trading real
    distance accuracy away.
    """
    return abs(loop.route.km - target_km) + loop.repeated_km


def _repeated_km(route):
    """Kilometres of a loop spent on a road it has already driven.

    The loop-specific defect, and the number that tells a user their 40 km drive
    is really a 20 km drive twice. Taken off the built route's own edge rows, so
    it describes the line that gets drawn: `_collect` gathers those rows in
    travel order, so a road driven again appears twice and `duplicated()` marks
    every appearance after the first.
    """
    repeats = route.edges.index.duplicated()
    if not repeats.any():
        return 0.0
    return float(route.edges["length_m"].to_numpy()[repeats].sum() / 1000.0)


def _beautiful_km(route):
    """Kilometres of a loop on roads scoring `BEAUTIFUL_SCORE` or better."""
    scores = (route.edges["score"].to_numpy() if route.scores is None
              else np.asarray(route.scores))
    length = route.edges["length_m"].to_numpy()
    return float(length[scores >= BEAUTIFUL_SCORE].sum() / 1000.0)


def _weights_key(weights):
    """A hashable, order-independent cache key for a beauty-weight dict."""
    return tuple(sorted((str(k), round(float(v), 4))
                        for k, v in (weights or {}).items()))
