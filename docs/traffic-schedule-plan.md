# Plan: schedule-based travel times

**Status: a plan, ready to build. Nothing has been implemented and no source
file has been touched by this document.** The survey below is current as of
2026-08-26; every access term was read at its source rather than assumed, and
the one that matters most had changed the answer.

---

## 1. The recommendation, up front

**Buy one Massachusetts/New England *Area Analysis* from TomTom Traffic Stats,
fit an hour-of-week × road-class speed-factor table from it, and ship that table
as a constant in `router.py` applied at load — exactly where `SPEED_FACTOR`
already lives. Choose the route with the departure-hour factors; report the ETA
by integrating the profile forward along the chosen edges.**

* **Fallback, if the Traffic Stats quote after the free trial is unaffordable:**
  self-archive MassDOT's free GoTime/RTTM real-time travel-time API for eight
  weeks and fit the same table from it. Same table, same code, worse coverage,
  eight weeks of waiting.
* **No graph rebuild.** `graph_edges.parquet` is untouched. The profile
  multiplies the same `minutes` column `SPEED_FACTOR` divides today.
* **`CONTROL_SECONDS` is not touched, and a test will assert it.**

The reasoning that eliminates everything else is §3. The one finding that would
change this answer is in §3.1: **NPMRDS — the free, obvious, exactly-right
dataset — is ruled out by its licence, not by its availability.** That is the
single most important result of this survey and it is not what the brief
expected.

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

Ten sources, each with what it actually provides, what it costs, its terms as
read today, and a verdict.

### 3.1 NPMRDS via RITIS — *eliminated, on licence*

Exactly the right data: FHWA probe speeds on the National Highway System,
2016–present, 5-minute bins, TMC segments, supplied by INRIX (reselected by
FHWA through 2026), with an expanded all-roads TMC option. Free at the point of
use. The brief guessed that Northeastern affiliation made eligibility
plausible, and asked for the terms to be verified rather than assumed.

**Verified. Two independent blockers, either one fatal.**

*Blocker 1 — he cannot sign the agreement.* The Data Sharing Agreement is
executed by "State Department of Transportation or Metropolitan Planning
Organization receiving federal transportation funds". Contractors and
consultants may be covered, but only by establishing "need to know" with a
named contract and an agency contact. Universities appear on RITIS's eligibility
list as organisations that can *fund the integration of their own data*, which
is a different thing from receiving NPMRDS. A Northeastern email address is not
a route in; a named MassDOT or CTPS contract would be.

*Blocker 2 — the licence forbids the use.* The DSA grants a "non-exclusive,
non-transferable, non-sublicensable" licence, prohibits sharing data with other
entities except to fulfil the stated Purpose, and **explicitly forbids making
"data sets or aggregated average travel time databases publicly available".** A
per-segment or per-class speed profile shipped inside a routing app that serves
the public is an aggregated average travel time database made publicly
available. This is not a grey area that a careful reading softens; it is the
prohibition, stated in the words the product would violate.

This is the finding that reshapes the plan. NPMRDS is free, is the right shape,
covers precisely the road class that is broken, and **cannot be used for this.**
Any future plan that reaches for it should stop here rather than re-derive it.

> Worth keeping: if he ever does work under a MassDOT or MPO contract, NPMRDS
> becomes available *for that work*. It still could not be shipped in the app.

### 3.2 TomTom Traffic Stats — **recommended**

Historical speed and travel time, 10+ year archive, 70+ countries, delivered
through the MOVE portal or the Traffic Stats API. **Area Analysis** returns,
for every segment inside a drawn area, average and median speed, average travel
time, sample size, the posted speed limit, street name and road class, over
date ranges and time bins the caller defines.

That is the required table almost literally: *speed against the posted limit,
by segment, by time bin* — which is the definition of `SPEED_FACTOR`.

* **Cost:** a **30-day free trial** via the MOVE portal, which is enough to run
  the one Area Analysis this plan needs. Beyond the trial, licensing is per
  directional mile and **quote-only — no public price exists** (see §8).
* **Delivery:** zipped shapefiles. A static file. No runtime dependency, no
  API key in the server, nothing for the Cloudflare tunnel to reach.
* **Coverage:** every road class, not just the NHS — so `secondary` through
  `residential` get confirmed rather than assumed, and the New England
  expansion is a bigger drawn box rather than a new integration.
* **Fits the architecture exactly:** a file → a fitting tool → a constant table
  → applied at load. The same shape as `fit_junction_cost.py` → `CONTROL_SECONDS`.

**Note the distinction that makes this legal where §3.9 is not.** Traffic Stats
is a *data product*, licensed for the analysis output it produces. TomTom's
Maps API terms separately bar caching and derivative works, which is what kills
the idea of sampling their *Routing* API for the same numbers. Buy the data
product; do not scrape the routing endpoint.

### 3.3 MassDOT GoTime / RTTM — **named fallback**

MassDOT's Real-Time Travel Time system: 137 digital signs over 700+ miles of
Massachusetts highway, published as a RESTful HTTP API, **free of charge to
authorized developers** via a signup form. Highway-only coverage, which per the
brief's own trap is the *right* half.

It is a real-time feed with no published historical archive, so the archive has
to be built: poll every five minutes, and after eight weeks each
(segment × hour-of-week) cell has roughly eight observations — thin per cell,
but pooled to the class level it is thousands of observations per cell, which is
all the recommended table needs.

**This is not the live traffic feed the brief ruled out.** The router gains no
runtime dependency, no per-request cost and no latency; a cron job on the
Windows laptop writes rows to a file, and a fitting tool reads that file
offline months later. That said, it is an ongoing operational commitment the
recommended path does not have, and it is why it is the fallback.

* **Cost:** free, plus eight weeks of wall clock.
* **Risk:** access is granted to "authorized developers" and a personal scenic-
  routing app may not qualify. Ask early — the form is at
  `api-signup.massgotime.com` — because the answer gates the fallback.
* **Limit:** Massachusetts only. Connecticut, New Hampshire, Vermont, Maine and
  Rhode Island would each be a separate integration, which directly conflicts
  with `docs/new-england-expansion.md`. This is the strongest single argument
  for the recommended path over the fallback.

### 3.4 HERE Traffic Patterns — *right shape, no way in*

Average speed for every road in the HERE map, in 15-minute intervals for each
day of week, from a 3-year average of observations; a separate Speed Data
product offers 5-minute granularity with a 5-year history. Shape-wise this is
the best product surveyed.

It is a premium layer on top of the HERE Map, sold enterprise, quote-only. HERE's
freemium tier (30k/mo base, 5k/mo routing) includes *traffic* in the sense of
the real-time API, not the Traffic Patterns map product, and building a
permanent table out of freemium API responses runs into the same derivative-work
problem as §3.9. Partner resellers (e.g. Korem) offer sample extracts, which is
worth one email but is not a plan.

**Verdict:** functionally interchangeable with TomTom and strictly harder to
buy — no self-service trial was found. Second choice to §3.2, not a third path.

### 3.5 Uber Movement — *dead, and would not have helped*

Discontinued, with no official archive. Third-party mirrors and tutorial
datasets survive; the newest data is ~2020, which is pre-pandemic and therefore
describes a commute pattern that no longer exists. The Speeds product covered a
handful of cities' street networks, never Massachusetts highways well.

**Verdict:** eliminate. Even a clean archive would be the wrong roads at the
wrong time.

### 3.6 MassDOT MS2 traffic counts — *a cross-check, not a source*

MassDOT publishes count data through MS2's TCDS portal, and the accepted
dataset types include **Speed** alongside volume, classification and WIM. Free
and public.

The catch is methodological and it is disqualifying for this use: these are
**spot speeds at a detector**, mostly from short-duration studies, and count
stations are sited for volume counting rather than at bottlenecks. Corridor
delay is concentrated at the bottleneck, so spot speeds sampled away from one
will **understate congestion** — biasing in the same optimistic direction as
the bug being fixed.

**Verdict:** useful to sanity-check a fitted profile at a known location. Not a
source for the profile.

### 3.7 Boston Region MPO (CTPS) — *the interim seed, and an honest check*

CTPS publishes Express-Highway and Arterial Performance Dashboards displaying a
**"speed index" — the ratio of observed speed to the posted speed limit** on a
roadway segment. That is `SPEED_FACTOR` under another name, published openly,
with downloadable accessible tables. It is derived from INRIX data and processed
by CTPS, which is the DSA's permitted "share data summaries publicly" route —
so this is legal to read precisely because an agency did the summarising.

Limits: **2019 data, peak-period only, Boston region only.** Pre-pandemic, and
two bins rather than a curve.

**Verdict:** not the source — but it is free, immediate, and independent of
whatever TomTom returns, which makes it the right thing to seed Phase 0 with
(§6) and the right thing to check the fitted profile against.

### 3.8 OpenStreetMap — *confirmed nothing, as the brief expected*

OSM carries no traffic data of any kind. `maxspeed:conditional` exists and looks
relevant, but it encodes *legal* limits that vary by time (school zones, night
limits), not observed congestion. Confirmed; moving on.

For a reference on the *data model* rather than the data, Valhalla is worth
reading: it stores per-edge predicted speeds as 2,016 five-minute buckets
covering a week, DCT-II compressed, with a documented fallback chain (real-time
→ predicted → constrained/free-flow → speed limit → road class). §5 borrows
that fallback chain and deliberately does not borrow the resolution.

### 3.9 Sampling a routing API offline (Google / Mapbox / TomTom / HERE) — *eliminated, on terms*

The appealing shortcut: routing APIs accept a future `departAt`/`departureTime`
and price it against historical speed profiles, so a few thousand offline
requests over (corridor × hour × day-type) would yield the whole table for free.
The volume needed is trivial — 20 corridors × 168 hours = 3,360 requests, inside
TomTom's free 2,500/day or HERE's 5,000/month routing allowance.

It does not survive the terms. Google's Maps Platform terms prohibit caching and
derived datasets; TomTom's Maps API terms bar derivative works and permit
caching only narrowly, explicitly not for scaling results to multiple users;
Mapbox and HERE restrict storage of Directions responses comparably. Fitting a
permanent constant table from responses and shipping it is derivative-dataset
creation under all four.

**Verdict:** eliminate. This is the trap that looks like cleverness and is
actually a licence breach, and it is worth recording so nobody re-invents it.

### 3.10 The project's own eight traces — *validation only*

Per the brief's trap, and it is correct: eight drives, one driver, clustered on
three afternoons. There is no design under which those fit a 168-cell profile;
there are more free parameters than drives. Their role is §7, where they are
genuinely valuable — they are the only ground truth in the entire plan collected
by the actual vehicle on the actual roads, and they can *falsify* a bought
profile in a way nothing else here can.

### 3.11 Also checked, briefly

* **INRIX MetroLab Challenge** — free INRIX API access for up to a year to
  selected teams. Requires a local-government collaborator, carries a $250
  application fee, and **applications closed 2026-03-03**. Not viable now; worth
  a calendar note for the next cycle if the academic angle is ever revived.
* **FHWA Urban Congestion Report / TTI Urban Mobility Report** — public
  metro-level congestion indices. Real and free, but summary statistics for a
  whole urban area, not a diurnal curve. Folded into the Phase 0 seed (§6).
* **US DOT ITS DataHub connected-vehicle datasets** — real probe data, wrong
  places (Wyoming, Tampa, NYC pilots). Nothing for New England.
* **StreetLight / Replica** — agency-priced, same licence shape as §3.1.

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

**The profile's *shape* is assumed, not measured** — nobody has a diurnal curve
for Massachusetts motorway yet; producing one is the point of §3.2. That is why
the table is swept across three trough depths rather than quoting one number:
the conclusions below hold across the whole range, which is what makes them
usable before the data arrives. Re-run this sweep against the fitted profile
once it exists and the numbers become measurements rather than bounds.

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

**168 cells, hourly, not Valhalla's 2,016 five-minute cells.** The resolution is
chosen to match the evidence: the defect is a factor of three in one class, and
neither the fallback source (~8 observations per segment-hour after eight weeks)
nor the validation set (eight drives) can distinguish 15-minute structure from
noise. Hourly × 7 days is 168 floats per class — a few kilobytes, trivially
diffable in review, and re-fittable as a constant plus a restart.

Which classes get a profile:

* **`motorway` — certainly.** Factor 0.39 measured against 1.16 assumed.
* **`trunk`, `primary` — probably.** Not in the measured table as separately
  broken (primary is 0.93), but they are the classes that carry motorway
  overflow and the fitted data will say.
* **Everything else — a flat profile until the data argues otherwise.** The
  brief's own evidence is that `secondary`/`tertiary`/`residential` are within
  7%, and `SURFACE_SPEED_FACTOR = 0.95` earned its single value by three
  independent classes agreeing. **Do not spend that evidence on 168 free
  parameters per class because the data file happens to have columns for them.**

Fallback chain, borrowed from Valhalla: profile cell → class daily mean →
`SPEED_FACTOR`/`SURFACE_SPEED_FACTOR` as they are today. A class or hour with
too few probe observations falls back rather than shipping a noisy cell, and the
fitting tool records how often it did.

### 5.1 The `CONTROL_SECONDS` firewall

`analyze_trace.py`'s docstring is right and the plan obeys it: the speed factor
scales with distance and the junction cost scales with junction count, so
summing them fits one drive and nothing else.

**The profile multiplies `minutes`. It never touches the junction term.**
Concretely, `_driving_minutes()` (`router.py:721`) becomes time-parameterised
and `_control_minutes()` (`router.py:739`) is not modified at all. A test asserts
that `CONTROL_SECONDS` is invariant to departure time, so that a future fit
cannot quietly migrate congestion into a per-junction constant where it would be
baked in permanently.

---

## 6. What lands where

No graph rebuild. `graph_edges.parquet` is not regenerated, `pipeline/graph.py`
is not modified, and nothing is copied to the serving box.

| file | change |
|---|---|
| `pipeline/router.py:108` | add `SPEED_PROFILE` beside `SPEED_FACTOR`; both stay load-applied |
| `pipeline/router.py:721` | `_driving_minutes(hour_of_week)` — the one place the profile divides |
| `pipeline/router.py:739` | **unchanged**, deliberately, and tested for it |
| `pipeline/router.py:868` | `_weights(...)` takes a departure time and passes it down |
| `pipeline/router.py:967` | `route(...)` takes `depart`; the scipy call is unchanged |
| `pipeline/router.py:1374` | `RouteResult.minutes` walks `edge_minutes` with a clock — design (B) |
| `server/app.py:150` | `/api/route?depart=<ISO8601>`, defaulting to now; **both** fastest and scenic priced at it |
| `tools/fit_speed_profile.py` | new; reads the Traffic Stats shapefile (or the GoTime archive), emits the table |
| `tools/analyze_trace.py` | reports error **by departure hour**, and splits the 49.7 unexplained stopped minutes by class (§2.1). Still reads no corrected constant — that property is what makes it a valid instrument |
| `tests/test_routing.py`, `tests/test_calibration.py` | guards, §7.2 |

**Out of scope for this plan and flagged for whoever owns the client:** the iOS
app must send a departure time for a planned-in-advance drive, and must decide
whether a mid-drive reroute re-prices at the current clock. `ios/` is untouched
here by instruction and by good sense — three sessions have changes in flight.
Until the client sends one, `depart` defaults to now, which is correct for the
"leaving now" case that is most of the app's use.

---

## 7. Validation

### 7.1 The eight traces, used correctly

They cannot fit the profile (§3.10). They can falsify it, and that is worth
more than it sounds, because they are the only ground truth here measured by the
actual car on the actual roads.

Each trace records its own departure timestamp, so each is evaluated **at the
hour it was actually driven** — not pooled. Success criteria:

1. **The broken case improves.** Needham→Wachusett: +242% → within ±25%. If the
   bought profile cannot move that leg, the profile is wrong or the wrong class
   is being blamed, and the plan has failed its one clear test.
2. **The working case does not regress.** Harvard→Needham must stay within ±10%.
   A profile that fixes the highway by making back roads worse has traded one
   bias for another; the +2% is the most valuable number in §2 and it is the
   easiest to break.
3. **Off-peak still matches.** The 2026-08-14 traces that fitted `1.16` were
   driven off-peak. The profile's off-peak cells must reproduce them, which is a
   direct check that the fit did not simply shift everything down.
4. **`CONTROL_SECONDS` is unmoved.** Re-run `fit_junction_cost.py` after the
   profile lands; the pooled 11.5 s / 8.1 s should not move materially. If it
   does, congestion is leaking into the junction term.

**Stated honestly: three afternoons cannot validate 168 cells.** Criteria 1–4
test a handful of cells and the overall direction. Every other cell is on the
bought data's authority alone. That is a real limitation of this plan, it does
not have a fix within the data available, and it should not be papered over
with a pooled average that hides which cells were actually exercised.

### 7.2 Tripwires

In the style of `tests/test_calibration.py` — loose guards against a constant
drifted into nonsense, not fitted precision:

* every profile cell within `[0.3, 1.4]` — outside that, an ETA is absurd;
* the motorway profile has real diurnal range (peak-to-trough ≥ 20%), so a
  silently-flat table fails rather than reverting to today's bug unnoticed;
* profile cells are continuous across bin boundaries within a bounded step —
  the FIFO trap of §4.4, caught at test time rather than in a route;
* `CONTROL_SECONDS` invariant to departure time — the §5.1 firewall;
* a long trip priced by integration differs from the flat departure-time price
  by the amount §4.1 predicts, so design (B) is provably wired in and not
  quietly bypassed.

---

## 8. What could not be established

Recorded plainly, with who to ask:

* **TomTom Traffic Stats pricing beyond the free trial.** Licensing is per
  directional mile and **no public price list exists** — every source, including
  TomTom's own FAQ and their resellers, routes to a sales conversation. The
  recommended path is therefore free for one Area Analysis and unpriced
  thereafter. *This is the single largest open risk in the plan.* Ask TomTom
  sales via the MOVE portal for a New England one-off quote **before** spending
  the 30-day trial, so the trial is not burned discovering the renewal is
  unaffordable.
* **Whether the Traffic Stats trial licence permits shipping derived constants
  in a public app.** The FAQ is silent and the terms are behind the portal.
  This must be read before the fitted table is committed. If the trial is
  evaluation-only, the question becomes what a one-off commercial licence costs
  — the same conversation as above.
* **Whether MassDOT grants GoTime API access to a personal project.** The signup
  form asks for an intended use; "authorized developers" is not defined publicly.
  This gates the entire fallback, costs one form to find out, and should be
  submitted in week one regardless of which path is taken.
* **Whether CTPS has published anything newer than the 2019 dashboards.** The CMP
  page shows 2015 and 2019 and nothing later. CTPS answers data inquiries at
  `ctps.org/data-resources`; worth one email, since a 2024–25 refresh would
  materially improve the Phase 0 seed.
* **The class split of the 49.7 unexplained stopped minutes** (§2.1). Not
  external — nobody has run it. Cheapest item on this list and the one that most
  changes the plan's expected ceiling.
* **The §3.9 terms readings are second-hand.** The NPMRDS DSA (§3.1) was read at
  its own source and is quoted; the caching and derivative-work restrictions for
  Google, Mapbox, HERE and TomTom's *Maps API* were not — TomTom's terms page
  would not render, and the rest came from comparison write-ups rather than the
  contracts. The conclusion is not close to the line and none of these is on the
  recommended path, so this does not change the verdict. It does mean that if
  anyone ever wants to revive §3.9, the terms need reading properly first rather
  than treating this section as settled.

---

## 9. Sequencing

Ordered so the cheap questions answer the expensive ones first, and so nothing
waits on a sales conversation.

**Week 1 — answer the gating questions; ship the plumbing.**
Submit the GoTime access request and the TomTom quote request the same day; both
have latency measured in weeks and neither blocks anything else. Run the §2.1
class split. Then build the *machinery* against a seeded profile: `SPEED_PROFILE`,
the time-parameterised `_driving_minutes`, integrated `RouteResult.minutes`, the
`depart` parameter, and the §7.2 tripwires.

Seed it from §3.7 — CTPS's published speed index pinning the Boston peak, TTI /
FHWA metro indices for the shape, 1.16 off-peak — and label the constants
`PROVISIONAL` in the source. This is not a third option; it is the same table
and the same code path with cruder numbers, and it means the product bug is
fixed in week one rather than week nine. It also means the bought data arrives
to a working harness that only needs its constants replaced.

**Weeks 2–5 — get the real numbers.**
Run the Traffic Stats Area Analysis over the New England bbox
(`elevation.py:31` has the Massachusetts one to widen), fit with
`tools/fit_speed_profile.py`, replace the provisional constants, drop the label.

**Week 6 — validate and decide.**
Run §7.1 against all eight traces. Then make the one judgement call this plan
deliberately leaves open: measure how often route *choice* differs between
design (B) and a design (C) prototype on long trips. If it is rare, (C) stays
deferred permanently and §4.3's benchmark is the reason. If it is common, (C)
gets scoped — as a compiled search, per §4.3, never as a Python one.

**If TomTom comes back unaffordable:** start the GoTime poller immediately, keep
the provisional constants shipping in the meantime, and re-fit at week nine. The
harness does not change; only the file the fitting tool reads does. That is the
whole point of putting the fitting tool behind a file rather than an API.
