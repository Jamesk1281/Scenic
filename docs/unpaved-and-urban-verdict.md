# Verdict: surface is a preference, not a scenery measurement

**Status: measured, then implemented, 2026-08-29.** The measurement was done first with no
source file touched; the recommendation below was then implemented in the same
branch. `WEIGHTS["urban"]` is **unchanged** at 0.14 (`score.py`) — see the
`c_urban` section for why. All measurements are on the live build
`data/processed-ne` (942,448 chunks, 998,252 graph edges).

**What shipped is recorded in "Implementation" at the end of this file.**

Answers `docs/unpaved-and-urban-brief.md`. Reading order: that file first.

---

## The verdict in five sentences

1. **Surface does not belong in the scenery score.** In all six states the
   model's own beauty measurement rates unpaved roads *at or above* paved ones —
   including in Massachusetts, where the constant was calibrated.
2. **The preference it encodes is real, and should be moved, not deleted** — to a
   pref-independent, user-tunable avoidance.
3. **The strongest argument is not "dirt roads are pretty" — it is that the
   penalty is wired to the wrong knob.** It is scaled by `pref**PREF_CURVE`, so
   raising the beauty slider from 0.5 to 1.0 removes **two thirds** of the dirt
   road from a Vermont loop (23.7% -> 8.2%).
4. **The whole change is restart-only.** Contrary to
   `docs/driver-preferences-study.md`, per-edge surface *is* recoverable exactly
   from the shipped graph. No rebuild, no 364 MB redeploy.
5. **`c_urban` is not enough to act on.** Its 0.35 clears a one-tailed test by a
   hair (p=0.030) and fails a two-tailed one (p=0.061), from one driver, one day
   — and users already have a slider for it.

---

## What I verified before building on it

The brief's numbers **reproduce exactly** on the current build, which is not the
build the brief was written against (942,448 chunks now vs 940,420 then — the
`c_forest` promotion landed 2026-08-29 at 14:26). Per-state means, raw-component
decomposition, unpaved shares and tagging coverage all match to the digit.

Its **code** citations drift by a few lines but are correct in substance:
`score_adj = class_adj + unpaved_adj` is `score.py:352` (brief says 290),
`UNPAVED_ADJ` is `score.py:89` (brief says 86). Confirmed load-bearing:

- `router.py:893` calls `composite(raw, self.score_adj)` **inside** the live
  re-blend, so `score_adj` is in the cost function, not a display transform.
- `score_adj` survives the user's beauty sliders untouched — `_edge_scores`
  renormalises the six tunable weights but re-adds `score_adj` flat afterwards.
  It is the one term no user can reach.
- `SPEED_FACTOR`/`SURFACE_SPEED_FACTOR` (`router.py:108-109`) make **no
  paved/unpaved distinction**. Note the name is a trap: `SURFACE_SPEED_FACTOR`
  means *surface street* (as against motorway), not road surface material.

Two integrity checks passed, and the analysis below depends on both:
`score_adj == class_adj + unpaved_adj` to 0.0 residual on all 942,448 chunks,
and `composite(raw, score_adj) == score` to 0.0.

---

## Finding 1 — the penalty contradicts the model's own beauty measurement

The brief measured Vermont only (unpaved raw 0.358 vs 0.355 overall) and called
it "statistically identical". Measured across all six states, the case is
**stronger than the brief claims**. Length-weighted mean raw beauty, unpaved
chunks vs everything else in the same state:

| | unpaved raw | rest | delta | unpaved km |
|---|---|---|---|---|
| CT | 0.380 | 0.308 | **+0.071** | 308 |
| **MA** | 0.359 | 0.296 | **+0.063** | 4,055 |
| RI | 0.390 | 0.338 | +0.052 | 132 |
| ME | 0.318 | 0.273 | +0.046 | 7,655 |
| NH | 0.361 | 0.329 | +0.033 | 5,165 |
| VT | 0.358 | 0.352 | +0.006 | 12,796 |

**The sign is positive in every state.** And it survives controlling for road
class — within `residential` alone: MA +0.058 (3,755 km), CT +0.064, ME +0.051,
NH +0.023, VT +0.013. Within `unclassified`: MA +0.082, ME +0.038, NH +0.040.

Vermont is the *weakest* case, not the strongest, and for a structural reason
worth stating: at 45% of the network Vermont's dirt roads largely *are* the
network, so there is little left to contrast them against.

**The decisive line is Massachusetts.** `UNPAVED_ADJ = -0.25` was calibrated on
MA, and in MA the model's own components rate unpaved road **+0.063 raw** above
the rest — worth about +0.76 points of composite after `STRETCH` — and the
constant then subtracts 2.5. Net -1.74 points on roads the model just called
prettier. The penalty is not correcting a beauty over-estimate. It contradicts
the beauty measurement everywhere it fires.

The honest counter, which I cannot refute with this data: `c_forest`, `c_relief`
and `c_water` are *correlated* with remoteness, so of course dirt roads collect
them. The penalty may encode something real that no component measures —
washboard, dust, mud, no shoulder, no centre line. That is a coherent position.
It is a position about **driving comfort**, not about scenery, which is exactly
why the constant should move rather than shrink.

---

## Finding 2 — the category error, and the measurement that shows it

`score_adj` sits inside `weight = minutes + pref**PREF_CURVE * BETA * km * (1 -
score/10)`. Differentiating, a -0.25 shift in `score_adj` costs
`strength * BETA * 0.25` minutes per km. So the shipped constant charges:

| pref | strength | dirt avoidance |
|---|---|---|
| 0.0 | 0.000 | **0.00 min/km** |
| 0.25 | 0.062 | 0.12 min/km |
| 0.5 | 0.250 | 0.50 min/km |
| 0.75 | 0.562 | 1.12 min/km |
| 1.0 | 1.000 | **2.00 min/km** |

A driver on the fastest setting gets **no dirt avoidance at all** — the setting
where a dirt road actually costs them time and comfort. A driver asking for
maximum beauty gets the **maximum** — the one most likely to accept a dirt road
for the view. If the constant encodes "some drivers don't want dirt roads," it is
attached to a knob that has nothing to do with that preference, and runs
backwards along it.

**This is visible in the product.** 40 km loops, 8 starts (6 Vermont), dirt as a
share of loop km — Vermont starts only:

| variant | pref 0.5 | pref 1.0 |
|---|---|---|
| **shipped -0.25 (pref-coupled)** | **23.7%** | **8.2%** |
| neutralised, avoid 0.00 min/km | 26.9% | 26.7% |
| neutralised, avoid 0.25 | 26.4% | 22.0% |
| neutralised, avoid 0.50 | 24.2% | 22.0% |
| neutralised, avoid 1.00 | 15.0% | 17.2% |
| neutralised, avoid 2.00 | 10.1% | 13.3% |

**Turning the beauty slider from half to maximum deletes two thirds of the dirt
road from a Vermont loop.** No pref-independent variant does anything like it.

A correction to my own working, because it changes how this should be tested:
I first ran this on eleven 60 km inter-city routes and found dirt at ~0% under
*every* pref, concluding the effect was absent. It was the wrong instrument —
on long A-to-B routes dirt roads are never time-competitive and never enter the
solution at any setting. The effect is large and it lives on **loops**, which is
Scenic's flagship surface (`/api/loop`). A route-only test of this defect will
report nothing, the same way a Massachusetts-only test does.

---

## Finding 3 — the asymmetry, quantified as a counterfactual

The brief establishes that the penalty fires only where a mapper tagged surface.
Here is what that is worth, in points. For each state I imputed untagged roads
the unpaved rate of *tagged* roads of the same class in the same state
(missing-at-random within class), and re-scored:

| | tagged | charged unpaved | est. **true** unpaved | score now | if fully tagged | **hidden** |
|---|---|---|---|---|---|---|
| **ME** | 35.9% | 12.8% | **35.4%** | 4.31 | 3.75 | **0.57** |
| MA | 46.8% | 6.0% | 16.6% | 4.58 | 4.31 | 0.26 |
| NH | 73.5% | 16.7% | 25.1% | 4.81 | 4.60 | 0.21 |
| **VT** | 90.1% | 45.1% | 52.0% | 4.38 | 4.21 | **0.17** |
| RI | 44.8% | 1.3% | 4.1% | 5.14 | 5.07 | 0.07 |
| CT | 37.8% | 0.8% | 2.3% | 4.75 | 4.71 | 0.04 |

**Maine is nearly as dirt-heavy as Vermont — an estimated 35.4% against 52.0% —
and is charged for 12.8% of it.** The "hidden" column is the score each state is
spared purely by incomplete mapping: Maine keeps 0.57 points that Vermont, at
90% tagged, cannot. This is `c_green` measuring land designation, again.

**But — and the brief does not say this — completing the tagging does not fix
Vermont.** Under full tagging the order is RI > CT > NH > MA > VT > ME: Vermont
is still fifth, Maine still last. These are two separable defects:

- **The asymmetry** is an unfairness (Maine escapes). Fixing it makes things
  *worse* everywhere, Maine most.
- **The magnitude** is what puts Vermont last, and it is a separate lever.

Only the second one moves Vermont. Both argue for the same remedy, for different
reasons, which is worth knowing if only one can be shipped.

---

## Finding 4 — the `UNPAVED` set is itself incomplete, and skewed the same way

`UNPAVED` (`score.py:88`) omits `compacted`, which is crushed stone and is not
pavement. **2,258 km escape the penalty**, and the miss is largest exactly where
the asymmetry already bites:

| | escaping km | % of network | currently penalised |
|---|---|---|---|
| ME | 1,125 | 1.88% | 12.8% |
| CT | 492 | 1.26% | 0.8% |
| VT | 346 | 1.22% | 45.1% |
| NH | 159 | 0.51% | 16.7% |
| MA | 88 | 0.13% | 6.0% |
| RI | 48 | 0.45% | 1.3% |

Mostly `compacted` (2,211 km), plus `grave3` (a typo for gravel, 16 km, all in
Maine), `dirt/sand`, `pebblestone`. Connecticut is the sharp case: adding
`compacted` would more than double its charged unpaved share. Whatever happens to
the constant, this list is wrong and independently worth fixing.

---

## The option table

Length-weighted mean score per state, whole region, by constant:

| `UNPAVED_ADJ` | RI | NH | CT | MA | VT | ME | ranking | region |
|---|---|---|---|---|---|---|---|---|
| **-0.25 (shipped)** | 5.14 | 4.81 | 4.75 | 4.58 | **4.38** | 4.31 | RI>NH>CT>MA>VT>ME | 4.570 |
| -0.20 | 5.14 | 4.90 | 4.75 | 4.61 | 4.60 | 4.37 | RI>NH>CT>MA>VT>ME | 4.634 |
| -0.15 | 5.15 | 4.98 | 4.76 | 4.64 | **4.83** | 4.44 | RI>NH>**VT**>CT>MA>ME | 4.697 |
| -0.10 | 5.16 | 5.06 | 4.76 | 4.67 | 5.05 | 4.50 | RI>NH>**VT**>CT>MA>ME | 4.761 |
| -0.05 | 5.16 | 5.15 | 4.76 | 4.70 | 5.28 | 4.57 | **VT**>RI>NH>CT>MA>ME | 4.825 |
| 0.00 | 5.17 | 5.23 | 4.77 | 4.73 | **5.50** | 4.63 | **VT**>NH>RI>CT>MA>ME | 4.888 |

Vermont crosses into third at -0.15 and takes first at -0.05. Note the ranking
is nearly inert for RI and CT — they have almost no dirt, so this constant is
close to a Vermont/New-Hampshire/Maine-only lever.

**On real routes**, which is what the brief asked for. 12 starts x {40, 80} km
loops at pref 1.0:

| variant | dirt % | km | min | mean score |
|---|---|---|---|---|
| A shipped -0.25 | 6.3% | 63.7 | 75.1 | 6.76 |
| B reduced -0.15 | 7.9% | 63.5 | 75.3 | 6.74 |
| C removed 0.00 | 17.2% | 62.1 | 76.8 | 6.91 |

Vermont starts only: dirt **8.1% -> 10.8% -> 25.1%**, mean score 7.00 -> 6.95 ->
7.22, minutes 76.2 -> 76.3 -> 78.6. Removing the penalty roughly **triples** the
dirt on a Vermont loop and costs **2.3 minutes on 76**. Even at 25% the loops
still under-represent dirt against its 45% share of Vermont's network.

Caveat, stated because per-start numbers below are noisy: `LoopPlanner.plan` is a
heuristic over `SPAN_PICKS` candidate turnarounds, not a global optimum, so
individual starts move non-monotonically (VT Chelsea scores *higher* under A than
under C). Only the aggregates carry weight; the per-start tables are in
`/tmp/loops.csv` for anyone who wants to argue with them.

On 11 inter-city routes (60 km mean, pref 0.6) the variants are nearly
indistinguishable — dirt 0.02% -> 0.19% -> 2.03%, mean score 6.64 -> 6.71. As
above: that instrument cannot see this defect.

---

## Recommendation

**Move surface out of the scenery score and into a pref-independent, user-tunable
avoidance. Default it to 1.0 min/km of unpaved road.**

The mechanism, matching option A of `docs/driver-preferences-study.md` (a second
group of sliders outside the renormalised blend — an avoidance must not compete
for the attraction pot, or turning up "avoid dirt" quietly turns down water):

- `score_adj` keeps `CLASS_ADJ` and drops `UNPAVED_ADJ`. The 0-10 score becomes a
  statement about beauty only.
- The router adds `avoid_unpaved * km_unpaved` minutes to the Dijkstra weight,
  **outside** the `pref**PREF_CURVE` term.

**Why 1.0 and not 0.** The default is the product; most users never open a
settings screen. 1.0 min/km reproduces today's *average* dirt exposure while
removing the pref coupling: on Vermont loops it gives 15.0% / 17.2% at pref
0.5 / 1.0, against the shipped 23.7% / 8.2% — mean 16.1% against 16.0%. It is the
smallest change that fixes the defect without also shipping an unevidenced
behaviour change. Defaulting to 0 is defensible on the beauty evidence and I am
not recommending it, because the beauty evidence is not evidence about what
drivers *want*, and we have zero marks on dirt road. Slider range 0 to 2.0 min/km
covers "happy on dirt" to "harder than today's maximum".

**What this does not do:** it does not conflate slow with ugly. If unpaved roads
should be *slower*, that belongs in `SPEED_FACTOR` (`router.py:108`), which today
makes no surface distinction at all. That is a separate, unmeasured gap and I am
not proposing a number for it — nobody has driven a traced Vermont dirt road.

**On the asymmetry.** Moving surface out of the score removes mapping diligence
from the *beauty* claim completely, which is the larger win: Maine's 64%
untagged roads stop distorting what the product asserts about scenery. The
avoidance itself still fires only on tagged roads, so a driver who asks to avoid
dirt gets it honoured in Vermont and under-honoured in Maine. That residual is
real and must not be papered over. The remedy, and it is now cheap because it is
a preference rather than a truth claim: apply the avoidance to an **estimated
unpaved probability** — 1.0 for tagged-unpaved, 0.0 for tagged-paved, and the
class-and-state rate from the table in Finding 3 for untagged. Applying a
preference to a probability is defensible in a way that applying a beauty penalty
to a probability never was. Fix the `UNPAVED` set (Finding 4) at the same time.

---

## The cost claim in `driver-preferences-study.md` is wrong

That study states:

> **`surface` is NOT on the edges.** `score.py` consumed it into `score_adj` and
> discarded it. Making anything surface-based tunable requires `graph.py` to
> carry a new column, i.e. a full rebuild and redeploy.

**Measured otherwise.** `CLASS_ADJ` is a pure function of `highway`, which *is*
on every edge, and `score_adj = class_adj + unpaved_adj` is an exact identity.
So per-edge unpaved fraction is recoverable from the shipped graph:

```python
f = (edges["score_adj"] - edges["highway"].map(CLASS_ADJ).fillna(0.0)) / -0.25
```

Validated on all **998,252 edges** of `data/processed-ne/graph_edges.parquet`:

- **0 edges** fall outside [0, 1].
- `f` is **binary** — 94.6% exactly 0, 5.4% exactly 1, **0.0% strictly between**.
  Cross-way contamination from the nearest-chunk join (`graph.py:580`) does not
  touch it.
- Recovered share per class matches chunk ground truth to **within 0.2 pp**
  (residential 12.74% vs 12.90%, unclassified 51.42% vs 51.39%, tertiary 5.89%
  vs 5.91%).
- Recovered total 29,242 km against 30,120 km in chunks; the gap is the 3,757 km
  of chunk length that is not in the routable graph, not a recovery error.

Consequence: **the entire recommendation is restart-only.** The router can derive
`f` at load, neutralise the baked-in penalty (`score_adj + 0.25 * f`), and apply
its own avoidance — no `score.py` change, no rebuild, no 364 MB redeploy. Every
measurement in this document was produced that way, against the live build,
without writing a byte to `data/`.

The caveat, which belongs in the commit that uses it: this is a *derived*
quantity, exact only while `score_adj` has exactly two additive terms and
`CLASS_ADJ` stays a pure `highway` lookup. Adding a third term to `score_adj`
silently corrupts it. So ship the tunable on the derived fraction now, and have
`graph.py` carry an explicit `unpaved_frac` column at the next scheduled
rebuild — cost then is zero, rather than gating the fix on a redeploy today.

**`BETA` / `PREF_CURVE`.** Removing the penalty from the score raises the
region's length-weighted mean from 4.570 to 4.888, which shrinks the mean
scenery penalty `(1 - score/10)` by **5.9%**. `BETA` is that much weaker for the
same slider position and needs re-sweeping upward by order 6% using the existing
procedure at `router.py:60-73` — swept *with* `PREF_CURVE`, never alone. This is
an order-of-magnitude figure from the network distribution, not a fitted one.

---

## `c_urban`: real, but not enough to act on

Confirmed independently: 76 marks over four drives on 2026-08-25, **59 nice / 17
dull**, composite separation 0.72 against a null ceiling of 0.63.

At those sample sizes the Mann-Whitney null SE is 0.0800, giving a one-in-twenty
null band of **[0.368, 0.632]**. So:

| | separation | z | p (1-tail) | p (2-tail) |
|---|---|---|---|---|
| `c_curves` | 0.81 | +3.88 | <0.001 | <0.001 |
| class rank | 0.74 | +3.00 | 0.001 | 0.003 |
| composite | 0.72 | +2.75 | 0.003 | 0.006 |
| **`c_urban`** | **0.35** | **-1.88** | **0.030** | **0.061** |

`c_urban` is the only one that is genuinely marginal: it sits 0.018 below the
band's floor, clears a one-tailed 5% test and fails a two-tailed one. With nine
components examined, the chance that *at least one* reaches p=0.030 by luck alone
is about 24%. From one driver, one day, eastern Massachusetts.

**That is not enough to change a calibrated weight**, and the brief's own sweep
agrees it would not fix the ranking anyway (removing it leaves RI first and VT
last). What the loops add:

| variant (40 km, pref 1.0, 10 starts) | town km as % of loop | mean score |
|---|---|---|
| U1 shipped (town = 1.0) | **31.3%** | 6.49 |
| U2 user sets `w_town=0` | 23.8% | 6.73 |
| U3 `WEIGHTS["urban"]` removed | 25.1% | 6.05 |

Two things follow. First, the defect is real in the product: at maximum beauty
the shipped default sends a Montpelier loop through **60.9% town**, which drops
to 24.3% when the user turns the slider off — and the model's *own* score of the
result goes **up** (6.49 -> 6.73). A default that a user can improve by
switching it off is a bad default. Second, `town` is already tunable
(`BEAUTY_TYPES`, `router.py:164`), so this is a **default** question, not a
mechanism question, and nothing is blocking a user today.

**Recommendation: do not change the weight on this evidence. Collect the marks
that would settle it** (below). If something must ship sooner, changing the
client's *default* town slider is reversible, server-compatible and needs no
rebuild — unlike changing `WEIGHTS["urban"]`, which moves the stored `score`
column and forces the full rebuild plus a `BETA` re-sweep.

---

## What cannot be settled without drive marks on northern roads

Everything above rests on the model's own components and on route geometry.
Neither can answer *what a driver wants*, and the 79 eastern-Massachusetts marks
physically cannot: that region is 6% unpaved and 53% untagged for surface.

**1. Do drivers like or dislike unpaved road, holding scenery constant?**
This decides whether the default avoidance is 0.0 or 1.0 min/km, and it is the
only thing that would overturn the recommendation.
- *Where:* Vermont dirt country — Craftsbury and Peacham (Caledonia/Orleans),
  Chelsea (Orange County), and the Middlebury-Bristol corridor. These are the
  starts used above; all four return loops that are 22-45% dirt with the penalty
  neutralised.
- *Design:* paired marks within a single drive — a dirt stretch and a paved
  stretch of similar modelled beauty, so the verdict difference is surface and
  not scenery.
- *How many:* to distinguish a true separation of 0.35 from a coin at 80% power
  needs SE <= 0.054, i.e. **~58 marks of each verdict**. The binding constraint
  is *dull* marks — the 2026-08-25 drives yielded 17 dull from 76 (22%), so at
  that rate this is ~260 marks, or several drives. A useful first read comes at
  ~40 marks on unpaved road with at least 15 of each verdict.
- *Ideally more than one driver.* Every separation number in this project comes
  from one person.

**2. Is `c_urban` really inverted, or is 0.35 the 24%?** Same arithmetic:
~100 marks of each verdict, or — cheaper and more informative — a **second
driver** replicating it on the same eastern-Massachusetts roads. Replication
beats sample size against a multiple-comparisons worry.

**3. Are unpaved roads slower, and by how much?** Needed before anything is put
in `SPEED_FACTOR`. No traced drive in `traces/` is on unpaved road. One recorded
Vermont dirt drive would produce the factor the same way the 0.94/0.94/0.95
surface-road numbers were produced from 52 km.

**4. Does Maine's untagged road behave like its tagged road?** The Finding 3
counterfactual assumes missing-at-random within class. It is the weakest
assumption in this document. A drive on untagged Maine `unclassified` road,
recording actual surface, would replace an assumption with a rate.

---

## Reproducing this

No repo file was modified. Every result came from scripts in the session
scratchpad against `data/processed-ne`, read-only, with constants overridden in
memory:

- per-edge unpaved fraction: `(score_adj - CLASS_ADJ[highway]) / -0.25`
- score sweep: `composite(raw, class_adj + k * is_unpaved)`
- route/loop variants: assign `router.score_adj`, monkeypatch `Router._weights`
  for the avoidance arm, fresh `LoopPlanner` per variant so its caches cannot
  leak across arms
- state attribution: `/tmp/way_state.json` (564,580 ways)

Intermediates left at `/tmp/loops.csv`, `/tmp/avoid.csv`, `/tmp/r1.csv`,
`/tmp/r2.csv`. No scratch build was made, so there is nothing to delete from
`data/`.

---

## Implementation

Shipped in the same branch as this document, after the measurements above.

| | before | after |
|---|---|---|
| surface in the 0-10 score | `UNPAVED_ADJ = -0.25` inside `score_adj` | nothing |
| surface in the cost function | `pref**2 * BETA * 0.25` min/km (0.00 to 2.00) | `avoid_unpaved * 1.0` min/km, flat |
| user control | none | `avoid_unpaved=0..2`, default 1.0 |
| `WEIGHTS["urban"]` | 0.14 | 0.14, unchanged |

- **`score.py`** — `score_adj` is road class alone. `UNPAVED` is now OSM's
  unpaved family taken whole rather than hand-picked, which adds `compacted`
  (the 2,258 km of Finding 4). Chunks carry `unpaved` as a 0/1 column.
- **`graph.py`** — edges carry `unpaved_frac`, length-averaged like a component.
- **`router.py`** — `UNPAVED_AVOID_MIN_PER_KM = 1.0`, added in `_weights`
  outside the `pref**PREF_CURVE` term; `MAX_AVOID_UNPAVED = 2.0`.
- **`looper.py`** — `avoid_unpaved` threaded through `plan`/`sectors`/`resume`/
  `nearest_length` **and into both cache keys**, since it moves every edge
  weight.
- **`server/app.py`** — `avoid_unpaved` on `/api/route` and `/api/loop`,
  clamped, and in the loop result cache key. Not a `w_<type>`: those six are
  renormalised against each other, so an avoidance among them would quietly
  turn every attraction down.
- **iOS is untouched.** The server defaults to 1.0, so the current app keeps
  working and gets the fix; exposing the slider is a separate client release.

### It did not need a rebuild

As predicted in the cost section, and this is the part worth keeping:
`Router._load_unpaved` recovers per-edge surface from a legacy graph exactly,
undoes the baked-in penalty, and rewrites the in-memory `score` column so every
reader stays on one scale. Verified against the live build: the recovered
fraction is **identical** to the independent measurement (29,242 km), the
migrated `score_adj` equals the class table to 3e-15, and the live re-blend
still matches the stored column exactly.

**The one thing the restart cannot buy:** a legacy graph can only give back the
surfaces that were penalised when it was built, so `compacted` is invisible to
the recovery until a rebuild (1.0 pp of residential km). Recorded as
`LEGACY_UNPAVED` in `score.py` and pinned by
`test_a_legacy_graph_cannot_see_compacted`.

### Measured on the shipped code

Vermont 40 km loops, dirt as a share of loop km — the prediction the 1.0
default was chosen against, and what the code actually does:

| | pref 0.5 | pref 1.0 | mean | loop score | minutes |
|---|---|---|---|---|---|
| before (penalty in the score) | 23.7% | 8.2% | 15.98% | 6.71 | 48.9 |
| **after, `avoid_unpaved=1`** | **15.0%** | **17.2%** | **16.11%** | **7.21** | **48.0** |
| after, `avoid_unpaved=0` | 26.9% | 26.7% | 26.80% | 7.10 | 51.3 |
| after, `avoid_unpaved=2` | 10.1% | 13.3% | 11.68% | 7.05 | 48.9 |

Same average dirt exposure (16.11% against 15.98%) and the same driving time,
but flat across `pref` instead of collapsing by two thirds. The reported score
rises 6.71 -> 7.21 because it has stopped docking roads for their surface.

### `BETA` was re-swept and deliberately not changed

Full reasoning is in the comment under `BETA` in `router.py`. In short: on
Massachusetts routes the change moves the slider's shape by **nothing**,
identically to two decimals; the shift (bottom 0.21 -> 0.14) is confined to
routes with dirt on them, and BETA is a global instrument that would re-shape
six states to compensate for two. Also worth knowing before anyone tries:
**`router.py`'s own 0.32/0.15 sweep target does not reproduce on this build even
before the change** — that table was fitted on a Massachusetts build several
rebuilds ago. Re-derive it before re-fitting against it.

### Tests

`SCENIC_DATA=data/processed-ne .venv/bin/python -m pytest tests/` — **325 pass,
0 skipped**, up from 294. The new coverage is deliberately shaped so the
original defect cannot come back quietly:

- `TestSurfaceAvoidanceIsNotAScenerySetting` asserts the per-km charge is
  **identical at pref 0, 0.25, 0.5, 0.75 and 1.0**. Putting the constant back
  inside the scenery term collapses the pref-0 charge to zero and fails it.
- Ratios, not imported constants, wherever the calibrated value could drift —
  `test_the_multiplier_scales_the_charge_linearly` holds whatever the default
  is, and the pref-0 charge is asserted `> 0` so zeroing the constant fails.
- `test_pref_curve_shapes_the_scenery_cost` now passes `avoid_unpaved=0` to
  isolate what it names, and `test_the_surface_cost_is_what_lifts_the_ratio`
  pins the other half. It failed honestly when first run — `_weights` minus
  travel time is no longer purely the scenery term — which is the change
  showing up exactly where it should.
- `TestSurfaceAvoidanceReachesTheLoops` covers the cache-key trap on both the
  cost and field caches.

### Still open, in priority order

1. **The drive marks in section "What cannot be settled".** The 1.0 default is
   set to preserve behaviour, not because anyone has evidence about what
   drivers want on dirt. This is the only measurement that can move it.
2. **A rebuild**, whenever one is convenient — it makes `unpaved_frac` a real
   column, lets the `_load_unpaved` legacy branch and `LEGACY_UNPAVED` be
   deleted, and brings `compacted` into the avoidance.
3. **The untagged-road imputation** (Finding 3's rates), so the avoidance stops
   under-firing in Maine. Deliberately not shipped here: it applies a
   preference to a probability, which is defensible, but it needs a product
   decision and per-region rates the pipeline does not currently carry.
4. **Expose the slider in the iOS client.**
5. **`SPEED_FACTOR` makes no surface distinction.** Unmeasured; needs a traced
   drive on unpaved road.
