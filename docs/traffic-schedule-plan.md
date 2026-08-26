# Plan: schedule-based travel times

**Status: a plan, ready to build. Nothing has been implemented and no source
file has been touched by this document.** Survey current as of 2026-08-26.
**Constraint: nothing here costs money.** Every paid product was surveyed, and
every paid product is recorded in §3.5 as eliminated rather than deleted, so the
next person does not re-price them.

---

## 1. The recommendation, up front

**Fit the profile from MassDOT's public traffic-count portal (MS2 TCDS). It is
free, needs no account, and already holds roughly seven years of 15-minute speed
data from 214 permanent stations on Massachusetts Interstates.** Fit an
hour-of-week × road-class speed-factor table from it, ship that table as a
constant in `router.py` applied at load — beside `SPEED_FACTOR`, where the
existing constants already live.

* **Fallback, if scraping TCDS proves impractical or its terms forbid it:**
  MassDOT's GoTime/RTTM API — free to authorized developers, but real-time only,
  so the archive must be self-polled for eight weeks before it can be fitted.
* **No graph rebuild.** `graph_edges.parquet` untouched. The profile multiplies
  the same `minutes` column `SPEED_FACTOR` divides today.
* **`CONTROL_SECONDS` is not touched, and a test asserts it.**

### 1.1 It works — measured, not asserted

Station 10 on I-495 south of I-95 (Mansfield), **Monday 2026-08-24**, 2-way loop
detector, 105,183 vehicles, binned by speed each hour. Converting each hour's
speed distribution to a space-mean speed (harmonic mean over bin midpoints) and
dividing by the 65 mph posted limit:

```
 hour   vehicles   space-mean mph   factor    share < 35 mph
  2 AM       336            69.7     1.07              0.0%
  2 PM     7,677            68.5     1.05              0.4%
  4 PM     8,397            67.9     1.04              0.6%
  5 PM     7,362            45.0     0.69             14.5%
  6 PM     5,174            29.2     0.45             30.8%
  7 PM     3,773            71.4     1.10              0.4%
```

**A 57% peak-to-midday drop, from a free public web page, on the first station
opened.** The 6 PM factor of **0.45** sits beside the project's own independently
measured **0.39** on a congested afternoon (§2) — two unrelated instruments, one
a loop detector and one a phone in a car, landing in the same place. Off-peak
lands at 1.05–1.11 against the 1.16 the router currently believes, which is the
right ballpark and mildly lower, as a 2-way count including trucks should be.

`router.py:108` believes **1.16, every hour of every day**.

Note the shape: 5 PM 0.69 → 6 PM 0.45 → 7 PM 1.10. A single day cannot tell
recurrent congestion from one evening's incident. That is what the seven years
of history are for — the profile is a **median over same-weekday observations**,
never one day (§5.2).

---

### 1.2 How much of a downgrade is free? — the honest answer

**On the defect that actually exists: none.** On future refinement: real but
narrow. The arithmetic that settles it, from §2's per-class table:

| class | km | assumed | measured | free-flow min | actual min | **excess** |
|---|---|---|---|---|---|---|
| motorway | 49.0 | 103 | 40 | 28.5 | 73.5 | **+45.0** |
| secondary | 30.2 | 59 | 58 | 30.7 | 31.2 | +0.5 |
| primary | 24.9 | 60 | 55 | 24.9 | 27.2 | +2.3 |
| tertiary | 23.9 | 47 | 49 | 30.5 | 29.3 | −1.2 |
| residential | 11.8 | 39 | 43 | 18.2 | 16.5 | −1.7 |
| | | | | | **total** | **+44.8** |

**Motorway is 45.0 minutes of a 44.8-minute error. The other four classes
combined are −0.1 minutes.** They cancel. So a data source that covers only
Interstates and principal arterials — which is exactly what a state DOT's
permanent count stations cover — addresses **100% of the measured defect**, and
the coverage that the paid products add is coverage of the part that is already
right to within 7%.

The three things the free path genuinely costs:

1. **Point speeds, not segment travel times — and this is the real one.** A loop
   detector measures speed where it sits. A bottleneck's queue extends *upstream*
   of the sensor, so a station outside a queue reads free-flow while the corridor
   crawls. The bias is **optimistic — the same direction as the bug being
   fixed**, which is the worst direction for it to be in. Mitigations: 214
   stations means many *are* inside queues (station 10 plainly was); and §7
   measures the bias directly rather than hoping, because the eight traces *are*
   segment travel times. **This is the plan's largest unquantified risk and it
   is not resolvable before the fit exists.**
2. **A class-level profile, not a per-segment one.** 214 stations cannot say that
   I-93 southbound differs from I-495. But §5 only ever wanted class-level — 214
   stations is far more than enough for 168 cells — so this costs nothing
   against the plan as designed, and costs a refinement that was never scoped.
3. **No independent confirmation for the surface classes.** A paid area product
   would have confirmed that `secondary`/`tertiary`/`residential` have no
   time-of-day pattern worth modelling. Permanent stations are thin there, so
   "flat profile for surface roads" remains an assumption resting on the eight
   traces rather than a measurement. Given those classes are within 7%, the
   exposure is small — but it is an assumption, and it is labelled as one.

Two things the free path is **better** at than the paid plan it replaces:

* **It is available today** — no purchase, no sales conversation, no 30-day
  trial clock, and no eight-week wait for a self-built archive.
* **It has roughly seven years of history** (2,685 daily speed records at station
  10 alone). Seasonality and day-of-week fall straight out. The GoTime fallback
  could not have answered either question before October.

And the New England problem is mostly solved rather than deferred: **NHDOT runs
the same MS2 TDMS software**, CTDOT publishes continuous-count-station daytime
speeds, and MaineDOT runs a public count portal. Largely the same scraper, per
state, which is a far better position than the MA-only GoTime fallback.

---

## 2. The measured state — quoted, not re-derived

From `tools/analyze_trace.py` over the three drives of 2026-08-25 (147.9 km):

| drive | character | error vs the corrected model |
|---|---|---|
| Harvard→Needham | back roads, pref 1.0 | **+2%** |
| →Harvard | mixed | +38% (+22% excluding a 6.5 min park) |
| Needham→Wachusett | highway, pref 0 | **+242%** |

Pooled: free-flow times are 44% optimistic; 42% holding out parked time.
Measured moving speed by class against what the graph assumed:

```
motorway     49.0 km   assumed 103   measured  40   factor 0.39
secondary    30.2 km   assumed  59   measured  58   factor 0.99
primary      24.9 km   assumed  60   measured  55   factor 0.93
tertiary     23.9 km   assumed  47   measured  49   factor 1.05
residential  11.8 km   assumed  39   measured  43   factor 1.10
```

`router.py:108` says `SPEED_FACTOR = {"motorway": 1.16}` — motorway is driven
16% faster than the limit, always. That afternoon it was 61% slower. Everything
else is already within 7%.

166 stops, 59.0 min, of which 143 (49.7 min) are unexplained by any mapped
signal or sign.

**The product bug this fixes.** `server/app.py:150` computes the fastest route
as `ROUTER.route(s, t, 0.0, ...)` and returns it beside the scenic one. The
scenic ETA is accurate to 2% because scenic routes are made of the classes that
are already right; the fastest ETA is inflated because it is made of the one
class that is wrong. **The app systematically oversells the highway and prices
its own scenic route as more expensive than it is.** A "scenic costs you 25
extra minutes" that should read five is the reason to do this work; the ETA
being wrong is only the mechanism.

### 2.1 What this plan does *not* claim

Of the 49.7 minutes of unexplained stopped time, this plan only claims the part
that happens on congested motorway — where stop-and-go is captured by any
segment-average travel time, because such a measure includes the time stopped.
The part that is a surface-street junction OSM never mapped is a different
defect and is not addressed here.

**Nobody has split those 49.7 minutes by road class, and it should be the first
thing measured** — `analyze_trace.py` already has both the class breakdown and
the stop attribution, so the split is a reporting change, not new work. If most
of the unexplained stopped time turns out to be on surface streets, the ceiling
on this plan is lower than §2 makes it look. That measurement is cheap and it
should happen before the data is bought, not after.

---

## 3. The survey

### 3.1 MassDOT MS2 TCDS permanent count stations — **recommended**

MassDOT publishes its traffic count database at `mhd.public.ms2soft.com` — a
public-facing MS2 TCDS instance, no login. Verified by opening it:

* **454 permanent ("Perm Station") locations**, of which **214 are functional
  class (1) Interstate**.
* Data types per station: **Volume, Speed, Classification**, WIM, gap.
* Speed is stored as **hourly counts across 15 speed bins** (0–20, 20–25, …,
  85–250 mph), selectable at 15- or 60-minute display intervals.
* **~2,685 daily speed records** at the one Interstate station inspected — call
  it seven years — and current to **two days before this survey**.
* Per-report export to Excel; the underlying pages are plain ASP with stable
  query strings (`tcount_gcs.asp?...&count_type=SPEED&speedDate=...`).

**This section is a correction.** The first pass of this survey dismissed MS2 as
"spot speeds, biased optimistic, a cross-check not a source" — reasoning from
how count stations are usually sited, without opening one. Opening one overturned
it: station 10 sat inside a queue and recovered the entire diurnal curve (§1.1).
The siting concern is still real and is now §1.2's risk 1, but it is a bias to
measure, not grounds to discard the source.

**What must still be checked:** the portal's Terms of Service, which returned 403
to an automated fetch and needs reading in a browser, specifically on automated
access. Scraping ~200 stations × N days is a lot of requests against a state
portal; it should be rate-limited, cached locally, and run once rather than
repeatedly. If the ToS forbids automated access, ask MassDOT for a bulk extract
before assuming the fallback — MS2 has an agency-side bulk API and MassDOT can
export.

### 3.2 MassDOT GoTime / RTTM — **named fallback**

Real-Time Travel Time system: 137 signs over 700+ miles of Massachusetts
highway, published as a REST API, **free to authorized developers** via a signup
form at `api-signup.massgotime.com`.

Its one advantage over §3.1 is decisive where it applies: it reports **travel
time over a segment**, which is the quantity the router needs, and is therefore
immune to §1.2's risk 1. Its disadvantages are that it is real-time only — the
archive must be self-polled for ~8 weeks before a fit — Massachusetts only, and
gated behind an access grant a personal project may not receive.

**Ask for the key in week 1 regardless of path**, because it is free, it costs
one form, and it is the only free source that measures segments rather than
points. If it is granted, start the poller immediately even while §3.1 is being
fitted: eight weeks later it becomes the instrument that validates §3.1's
optimistic bias on far more than eight drives.

### 3.3 Boston Region MPO (CTPS) — free, and the independent check

CTPS publishes Express-Highway and Arterial Performance Dashboards showing a
**"speed index" — observed speed over posted speed limit** per segment, with
downloadable tables. That is `SPEED_FACTOR` by another name, published openly.
It is legal to read precisely because an agency did the summarising: the NPMRDS
licence that blocks §3.6 permits agencies to publish data summaries.

Limits: **2019 data, peak-period only, Boston region only.** Pre-pandemic, two
bins rather than a curve.

**Role:** not a source — an independent check. It comes from INRIX probe data
rather than loop detectors, so where it agrees with a TCDS-fitted profile, risk 1
is bounded at that location. Worth an email to `ctps.org/data-resources` asking
whether anything post-2019 exists.

### 3.4 Free, checked, and not useful

* **OpenStreetMap** — carries no traffic data. `maxspeed:conditional` encodes
  *legal* limits that vary by time, not congestion. Confirmed; move on.
* **FHWA Urban Congestion Report / TTI Urban Mobility Report** — free and real,
  but metro-level summary statistics (travel time index, congested hours) for a
  whole urban area. No diurnal curve, no road. Useful as a sanity check on the
  magnitude of a fitted peak, nothing more.
* **Uber Movement** — discontinued, no official archive, newest data ~2020 and
  therefore pre-pandemic, and its Speeds product covered a few cities' streets
  rather than Massachusetts highways. Dead and would not have helped.
* **US DOT ITS DataHub connected-vehicle data** — real probe data, wrong places
  (Wyoming, Tampa, NYC pilots). Nothing for New England.
* **New England 511** (`newengland511.org`, a ME/NH/VT partnership) — real-time
  travel times on a public map, partly Waze-sourced, with **no documented public
  API**. Worth one email per state if the expansion needs it; not a plan.
* **The project's own eight traces** — validation only, per §7. Eight drives, one
  driver, three afternoons: there is no design in which they fit 168 cells.

### 3.5 Eliminated because they cost money — recorded, not deleted

Priced so nobody re-prices them. All were surveyed before the no-cost constraint
and all are genuinely good products; none is needed.

* **TomTom Traffic Stats** — Area Analysis returns per-segment average speed,
  travel time, sample size, posted limit and road class by time bin, delivered as
  shapefiles. Exactly the right shape. There is a **30-day free trial** via the
  MOVE portal, and one Area Analysis would fit inside it — but pricing beyond the
  trial is per directional mile and **quote-only**, and whether the trial licence
  permits shipping derived constants in a public app is **unverified** and sits
  behind the portal. A recommendation resting on an unverified licence for a
  one-shot trial is too fragile to build on. Recorded, not relied on.
* **HERE Traffic Patterns** — average speed for every road, 15-minute intervals
  per day of week, 3-year average. The best-shaped product surveyed and the
  hardest to buy: enterprise, quote-only, no self-service trial found.
* **INRIX MetroLab Challenge** — free INRIX API access for up to a year, but
  needs a local-government collaborator, costs $250 to apply, and **applications
  closed 2026-03-03**. Note it for the next cycle if the academic route revives.
* **StreetLight / Replica** — agency-priced, and the same licence shape as §3.6.

### 3.6 NPMRDS — free, perfect, and **legally unusable**

This is the finding worth keeping even though the answer is no, because NPMRDS
is what everyone reaches for: FHWA probe speeds on the National Highway System,
2016–present, 5-minute bins, supplied by INRIX, **free at the point of use**, and
covering precisely the road class that is broken.

Both blockers were read at source. Either alone is fatal.

1. **The agreement cannot be signed.** The Data Sharing Agreement is executed by
   a "State Department of Transportation or Metropolitan Planning Organization
   receiving federal transportation funds", or their contractors under a named
   contract with an agency point of contact. Universities appear on RITIS's
   eligibility list as bodies that may *fund integration of their own data* —
   a different thing. A `.edu` address is not a route in.
2. **The licence forbids the use.** The DSA grants a "non-exclusive,
   non-transferable, non-sublicensable" licence and **explicitly forbids making
   "data sets or aggregated average travel time databases publicly available"**.
   A speed profile shipped inside a public routing app is exactly that.

**Do not re-derive this.** If he ever works under a MassDOT or MPO contract,
NPMRDS becomes available *for that work* — and still could not ship in the app.

### 3.7 Sampling a routing API offline — eliminated on terms

The shortcut that looks free: routing APIs take a future `departAt` and price it
against historical profiles, so ~3,400 offline requests (20 corridors × 168
hours) would yield the table inside TomTom's free 2,500/day or HERE's free
5,000/month.

It does not survive the terms. Google's Maps Platform terms prohibit caching and
derived datasets; TomTom's Maps API terms bar derivative works and permit
caching only narrowly; Mapbox and HERE restrict storage of directions responses
comparably. Fitting a permanent constant table from responses and shipping it is
derivative-dataset creation under all four. **This is the trap that looks like
cleverness and is a licence breach**, and it is recorded so nobody re-invents it.

---

## 4. The hard question: time-dependent routing

**A 132-minute drive departing at 17:00 finishes in different traffic than it
started in.** Applying the 17:00 factor to the whole trip is an approximation,
and the plan owes an error estimate rather than a shrug.

Three designs, in order of ambition:

* **(A) Departure-time.** `d_minutes` is recomputed for the departure hour. One
  static cost matrix, scipy untouched, zero added cost. Route choice *and* ETA
  both priced at `t₀`.
* **(B) Departure-time choice, integrated ETA.** Route chosen exactly as (A),
  but the reported time is computed by walking the chosen edges in travel order
  and advancing the clock as the driver would. Costs microseconds — the route is
  hundreds of edges, not 750,000.
* **(C) Full time-dependent search.** Edge cost becomes a function of the search
  state. Correct, and a different algorithm.

### 4.1 How wrong is (A)? — measured

Simulated against a bimodal weekday motorway profile anchored on the project's
own two measurements (1.16 off-peak, a parameterised PM trough), integrating the
route forward against applying the departure factor flat. All-motorway, which is
the worst case. "ff min" is free-flow trip minutes; 110 ff-min is about the
132-minute Wachusett drive.

**The profile's *shape* here is assumed, not fitted.** §1.1 now supplies one
real curve — one station, one Monday — and its trough of 0.45 sits between the
"moderate" and "deep" rows below, which is the useful thing to know. But one
station-day is not a fitted profile (§5.2), so the table is swept across three
trough depths rather than quoting one number: the conclusions hold across the
whole range, which is what makes them usable before the fit exists. Re-run this
sweep against the fitted profile afterwards and the numbers become measurements
rather than bounds.

| PM trough | 20 ff-min | 60 ff-min | 110 ff-min |
|---|---|---|---|
| 0.75 (shallow) | −3.1% … +2.9% | −8.4% … +9.6% | −14.5% … **+17.3%** |
| 0.60 (moderate) | −5.7% … +5.8% | −14.4% … +18.1% | −23.5% … **+30.9%** |
| 0.45 (deep) | −10.3% … +11.5% | −22.4% … +33.1% | −34.4% … **+53.2%** |

Three things fall out of that table:

1. **Short trips are fine.** Under half an hour, (A) is within a few percent at
   any plausible profile depth. Most trips are short trips.
2. **Long trips are not.** On a two-hour drive the approximation error reaches
   ±30% at a moderate profile and ±50% at a deep one — **the same order as the
   defect being fixed.** Shipping (A) alone would replace a factor-of-three
   error with a factor-of-1.5 error and call it done.
3. **The sign flips, which is the interesting part.** Depart *into* the peak at
   15:00 and (A) is optimistic by up to 34%. Depart *out of* it at 18:00 and (A)
   is pessimistic by up to 53%. An 18:00 departure is exactly when the app would
   most oversell the highway — the same product bug, one layer down.

### 4.2 The answer: build (B)

Integrating along the chosen route removes essentially all of that error, and
it costs nothing, because **`RouteResult.minutes` (`router.py:1374`) already
sums `self.edge_minutes` — a per-edge array in travel order.** Replacing that
sum with a cumulative walk that advances a clock is a self-contained change to
one property. The edges are already ordered; `_collect` guarantees it.

A midpoint approximation — evaluate the profile at `t₀ + D/2`, iterate to a
fixed point — was also simulated, and it is *worse and longer*: within ±5%
typically, but +18.2% on the 15:00 drive-into-the-peak case, because a midpoint
is a poor summary of a monotonically worsening trip. **Exact integration is
simpler than the approximation to it.** Do not build the midpoint.

What (B) leaves on the table is route *choice*: the router still picks its
fastest route believing the departure hour holds throughout, so on a long trip
it may not avoid a corridor that will be jammed when it gets there. That
residual is much smaller than the one being fixed, for a concrete reason —
**the product bug is a reporting bug.** The screen shows fastest beside scenic;
if both are reported by exact integration, the comparison is honest even when
the chosen fastest route is slightly suboptimal. "We may not have found the
very fastest route" is a far smaller lie than "we misreported its time by 40%".

### 4.3 Why (C) is deferred — measured, not assumed

A time-dependent search cannot use `scipy.sparse.csgraph.dijkstra` at all: the
cost matrix is fixed at call time and time-dependent costs are not. The
relaxation has to move into Python or into a compiled extension written for it.

Benchmarked on a synthetic graph sized like the Massachusetts build (310,807
nodes / 750,000 directed edges, per `docs/new-england-expansion.md`), one
source, 273,517 nodes reached, 660,177 relaxations:

```
scipy,  static            88.6 ms   1.00x   (what router.py runs today)
python, static           319.9 ms   3.61x   (cost of leaving C)
python, time-dependent   391.5 ms   4.42x   (cost of leaving C + time-dependence)
```

**Time-dependence itself adds only 22%. Leaving scipy's C costs 3.61x.** That is
the whole finding, and it inverts the intuition: time-dependent routing is not
expensive, *implementing it in Python* is. Carried through to the real router
(196 ms/request on MA, two Dijkstra runs whenever `pref > 0`, latency scaling as
E^1.20), design (C) lands at roughly **800 ms per Massachusetts request and
~3.3 s on New England** — against a New England baseline already predicted at
800 ms. It compounds with the known open issue that `route()` runs a full-graph
Dijkstra with no target early exit; the fix for that, `dijkstra(..., limit=cost)`,
is a scipy keyword that a Python search would have to reimplement.

So (C) is deferred, and if it is ever wanted the note to leave is: **do not
write it in Python.** Write the search compiled, or don't write it.

### 4.4 One correctness trap for whoever builds (C) later

Time-dependent Dijkstra is only label-setting on a **FIFO** network — leaving
later must never let you arrive earlier. A piecewise-constant profile violates
FIFO at every bin boundary: entering a long edge at 17:59 under a slow bin can
arrive after entering it at 18:01 under a fast one. The standard fixes are to
interpolate linearly between bins and cap the rate of change, or to adopt the
waiting-allowed model. **Design (B) is immune** — it evaluates a fixed profile
along a fixed path and never relies on the search being label-setting — which is
a real, if incidental, argument for it.

---

## 5. The model

A speed factor, per road class, per hour of week:

```python
# router.py, beside SPEED_FACTOR
SPEED_PROFILE = {
    "motorway": [...168 floats...],   # Mon 00:00 -> Sun 23:00
    ...
}
```

**168 cells, hourly.** Valhalla stores 2,016 five-minute cells per edge; that
resolution is not supported by this evidence and would be fitting noise. TCDS
publishes at 15-minute intervals, so finer cells are *available* — they are just
not *justified* by a validation set of eight drives. Aggregate to hourly.

Which classes get a profile:

* **`motorway` — certainly.** 100% of the measured error (§1.2), and the class
  the 214 Interstate stations actually cover.
* **`trunk`, `primary` — from the data.** TCDS functional classes (2) Freeway &
  Expressway and (3) Other Principal Arterial map onto these, and there are
  permanent stations on both. Fit them; ship a profile only if the fit shows
  real diurnal range.
* **Everything else — flat, until something argues otherwise.** `secondary`,
  `tertiary` and `residential` are within 7% and net to −0.1 minutes. Do not
  spend that on 168 free parameters per class because the file has columns for
  them. `SURFACE_SPEED_FACTOR = 0.95` earned its single value by three
  independent classes agreeing; that evidence still stands.

Fallback chain, in Valhalla's order: profile cell → class daily mean →
`SPEED_FACTOR`/`SURFACE_SPEED_FACTOR` exactly as today. A cell with too few
observations falls back rather than shipping noise, and the fitting tool reports
how often it did.

### 5.1 The `CONTROL_SECONDS` firewall

`analyze_trace.py`'s docstring is right and this plan obeys it: the speed factor
scales with distance and the junction cost scales with junction count, so
summing them fits one drive and nothing else.

**The profile multiplies `minutes`. It never touches the junction term.**
`_driving_minutes()` (`router.py:721`) becomes time-parameterised;
`_control_minutes()` (`router.py:739`) is not modified at all. A test asserts
`CONTROL_SECONDS` is invariant to departure time, so a future re-fit cannot
quietly migrate congestion into a per-junction constant where it would be baked
in permanently.

### 5.2 Fitting: three rules that matter more than the estimator

1. **Median across days, never one day.** §1.1's 5 PM → 6 PM → 7 PM shape may be
   one evening's incident. Recurrent congestion is what survives a median over
   every same-weekday observation at that station; incidents do not.
2. **Space-mean, not time-mean.** A detector counts vehicles, so the arithmetic
   mean of its speed bins is a *time*-mean speed, which overestimates the speed
   that produces travel time. Use the harmonic mean over bin midpoints — at
   6 PM in §1.1 the two differ by 54.8 vs 29.2 mph, so this is not a rounding
   detail, it is most of the answer.
3. **Cap the open top bin.** The 85–250 mph bin was held at 90 in §1.1.
   It barely moves an off-peak factor and cannot move a congested one, but it
   should be a named constant in the fitting tool rather than a magic number.

---

## 6. What lands where

No graph rebuild. `graph_edges.parquet` is not regenerated, `pipeline/graph.py`
is not modified, nothing is copied to the serving box.

| file | change |
|---|---|
| `pipeline/router.py:108` | add `SPEED_PROFILE` beside `SPEED_FACTOR`; both stay load-applied |
| `pipeline/router.py:721` | `_driving_minutes(hour_of_week)` — the one place the profile divides |
| `pipeline/router.py:739` | **unchanged**, deliberately, and tested for it |
| `pipeline/router.py:868` | `_weights(...)` takes a departure time and passes it down |
| `pipeline/router.py:967` | `route(...)` takes `depart`; the scipy call is unchanged |
| `pipeline/router.py:1374` | `RouteResult.minutes` walks `edge_minutes` with a clock — design (B) |
| `server/app.py:150` | `/api/route?depart=<ISO8601>`, default now; **both** fastest and scenic priced at it |
| `tools/scrape_tcds.py` | new; polite, rate-limited, resumable, writes one local parquet. Run once |
| `tools/fit_speed_profile.py` | new; the parquet → the table. §5.2's three rules live here |
| `tools/analyze_trace.py` | report error **by departure hour**, and split the 49.7 unexplained stopped minutes by class (§2.1). Still reads no corrected constant — that is what makes it a valid instrument |
| `tests/` | guards, §7.2 |

**Out of scope here, flagged for whoever owns the client:** the iOS app must send
a departure time for a drive planned in advance, and must decide whether a
mid-drive reroute re-prices at the current clock. `ios/` is untouched by
instruction and by good sense — three sessions have changes in flight. Until the
client sends one, `depart` defaults to now, which is correct for the "leaving
now" case that is most of the app's use.

---

## 7. Validation

### 7.1 The eight traces, used correctly

They cannot fit the profile. They can falsify it, and they are the only ground
truth here measured by the actual car on the actual roads — **and, uniquely, they
measure segment travel times, which is the one thing a loop detector cannot.**
That makes them the instrument for §1.2's risk 1, not merely a sanity check.

Each trace carries its own departure timestamp, so each is evaluated **at the
hour it was actually driven**, never pooled. Criteria:

1. **The broken case improves.** Needham→Wachusett: +242% → within ±25%. If the
   fitted profile cannot move that leg, either the profile is wrong or the wrong
   class is being blamed, and the plan has failed its one clear test.
2. **The working case does not regress.** Harvard→Needham stays within ±10%. A
   profile that fixes the highway by making back roads worse has traded one bias
   for another; the +2% is the most valuable number in §2 and the easiest to break.
3. **Off-peak still matches.** The 2026-08-14 traces that fitted `1.16` were
   driven off-peak; the profile's off-peak cells must reproduce them. This is
   the direct check that the fit did not simply shift everything down.
4. **`CONTROL_SECONDS` is unmoved.** Re-run `fit_junction_cost.py` afterwards;
   the pooled 11.5 s / 8.1 s should not move materially. If it does, congestion
   is leaking into the junction term.
5. **The point-vs-segment bias is measured, not assumed.** Compare the profile's
   predicted motorway factor at each trace's hour against that trace's measured
   factor. A systematic optimistic gap **is** risk 1, quantified. If it is large,
   §3.2's GoTime archive stops being a fallback and becomes the correction.

**Stated plainly: three afternoons cannot validate 168 cells.** These criteria
test a handful of cells and the overall direction. Every other cell rests on
TCDS alone. That is a real limitation, it has no fix inside the available data,
and it should not be hidden behind a pooled average that conceals which cells
were exercised.

### 7.2 Tripwires

In `tests/test_calibration.py`'s style — loose guards against a constant drifted
into nonsense, not fitted precision:

* every profile cell within `[0.3, 1.4]`; outside that an ETA is absurd;
* the motorway profile has real diurnal range (peak-to-trough ≥ 20%), so a
  silently-flat table fails loudly instead of reverting to today's bug in silence;
* cells are continuous across bin boundaries within a bounded step — the FIFO
  trap of §4.4, caught at test time rather than in a route;
* `CONTROL_SECONDS` invariant to departure time — the §5.1 firewall;
* a long trip priced by integration differs from the flat departure-time price
  by roughly what §4.1 predicts, so design (B) is provably wired in rather than
  quietly bypassed.

---

## 8. What could not be established

* **The MS2 portal's Terms of Service on automated access.** It returned 403 to
  an automated fetch — which is itself a signal — and needs reading in a browser
  before any scraper runs. If it forbids automated access, ask MassDOT for a
  bulk extract rather than assuming the fallback; MS2 has an agency-side bulk
  API and MassDOT can export.
* **How badly point speeds understate corridor congestion** (§1.2 risk 1). Not
  answerable before the fit exists; §7.1 criterion 5 is the measurement.
* **Whether MassDOT grants GoTime API access to a personal project.** One form
  to find out, and it gates the fallback. Submit it in week 1 either way.
* **Whether CTPS has anything newer than the 2019 dashboards.** The CMP page
  shows 2015 and 2019 and nothing later; one email to `ctps.org/data-resources`.
* **The class split of the 49.7 unexplained stopped minutes** (§2.1). Not
  external — nobody has run it. Cheapest item here and the one that most changes
  the plan's expected ceiling.
* **The §3.7 terms readings are second-hand.** The NPMRDS DSA (§3.6) was read at
  its own source and is quoted; the caching and derivative-work restrictions for
  Google, Mapbox, HERE and TomTom's Maps API were not — TomTom's terms page would
  not render and the rest came from comparison write-ups. The conclusion is not
  close to the line and none is on the recommended path, so the verdict stands;
  but anyone reviving §3.7 must read the contracts rather than trust this.

---

## 9. Sequencing

**Week 1 — the free things that take time to answer, plus the plumbing.**
Submit the GoTime access request and email CTPS the same day; both have latency
measured in weeks and neither blocks anything. Read the MS2 ToS. Run the §2.1
class split.

Then build the machinery against a hand-seeded profile: `SPEED_PROFILE`, the
time-parameterised `_driving_minutes`, integrated `RouteResult.minutes`, the
`depart` parameter, the §7.2 tripwires. Seed it from §1.1's single station and
CTPS's published speed index, label the constants `PROVISIONAL` in the source,
and ship. **The product bug is fixed in week 1, not week 6** — and the real fit
then arrives to a working harness that only needs its constants replaced.

**Week 2 — scrape and fit.** `tools/scrape_tcds.py` over the 214 Interstate
stations plus the Freeway/Expressway and Principal Arterial permanent stations;
rate-limited, resumable, run once to a local parquet. Then
`tools/fit_speed_profile.py` under §5.2's three rules. Replace the provisional
constants, drop the label.

**Week 3 — validate and decide.** Run §7.1 against all eight traces, criterion 5
included, because that is the one that prices the free path's main weakness. Then
the judgement call this plan deliberately leaves open: measure how often route
*choice* differs between design (B) and a design (C) prototype on long trips. If
it is rare, (C) stays deferred permanently and §4.3's benchmark is the reason. If
it is common, (C) gets scoped — compiled, per §4.3, never in Python.

**If risk 1 turns out large:** start the GoTime poller (already keyed from week
1), keep the TCDS profile shipping meanwhile, and re-fit at week ten against
segment travel times. The harness does not change; only the file the fitting tool
reads does. That is the whole point of putting the fitting tool behind a file.
