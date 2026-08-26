# Plan: schedule-based travel times

**Status: a plan, ready to build, gated on four emails.** Nothing implemented,
no source file touched. Survey current as of 2026-08-26. **Constraint: nothing
here costs money**, so every paid product is recorded in §5.6 as eliminated
rather than deleted.

**Read §2 before building anything.** The earlier draft of this plan recommended
scraping MassDOT's public count portal. That is expressly forbidden by the
portal's terms of service, in a clause that names "scrape" and "traffic data" in
the same sentence. The data is still obtainable and still free — but by asking
for it, not by taking it.

---

## 1. The recommendation

**Two free, sanctioned sources, requested in parallel in week one, feeding one
constant table.**

1. **Primary — MassDOT GoTime API.** The only source found with an explicit,
   free, written licence that fits this use: "provided free-of-charge to
   authorized developers", the signup form offers **Individual** as an
   organisation type, and the only stated restriction is that data "should only
   be used for transportation purposes" — which a routing app is. It reports
   **travel time between Bluetooth sensor pairs**, i.e. real segment travel
   time, which is better shaped than any detector's point speed. Cost: free.
   Catch: real-time only, so the archive must be self-polled for ~8 weeks.
2. **Depth — MassDOT's own continuous-count speed archive, by request.** The
   MS2 portal holds roughly seven years of 15-minute speed data from 214
   permanent Interstate stations (§5.1). MS2's terms forbid scraping it, but
   MS2's terms also say **the customer owns the data** — so ask MassDOT, who
   own it and can export it, with a public records request as the formal
   fallback. This is what gives day-of-week and seasonal depth that eight weeks
   of GoTime cannot.

Fit an **hour-of-week × road-class × urban/rural** speed-factor table from
whichever arrives, ship it as a constant in `router.py` applied at load.

* **No graph rebuild.** `graph_edges.parquet` untouched. The profile multiplies
  the same `minutes` column `SPEED_FACTOR` divides today.
* **`CONTROL_SECONDS` is not touched, and a test asserts it.**
* **Massachusetts is not blocked on New England.** §6 shows the other five
  states are three different vendors and six different access stories. The table
  is designed so adding a state is adding rows.

### 1.1 The data exists and has the right shape — demonstrated

Viewing one page of the MassDOT portal as any visitor may: station 10 on I-495
south of I-95 (Mansfield), **Monday 2026-08-24**, 2-way loop detector, 105,183
vehicles, hourly counts across fifteen speed bins. Converting each hour's
distribution to a space-mean speed (harmonic mean over bin midpoints) against
the 65 mph posted limit:

```
 hour   vehicles   space-mean mph   factor    share < 35 mph
  2 AM       336            69.7     1.07              0.0%
  2 PM     7,677            68.5     1.05              0.4%
  4 PM     8,397            67.9     1.04              0.6%
  5 PM     7,362            45.0     0.69             14.5%
  6 PM     5,174            29.2     0.45             30.8%
  7 PM     3,773            71.4     1.10              0.4%
```

A 57% peak-to-midday drop. The 6 PM factor of **0.45** sits beside the project's
own independently measured **0.39** (§4) — a loop detector and a phone in a car,
landing in the same place. `router.py:108` believes **1.16, every hour of every
day**.

**Treat these numbers as a feasibility demonstration, not as shippable
constants.** §2's clause bars *using* portal contents without authorization, and
the conservative reading covers figures derived from a page one has merely
viewed. They prove the data exists, is current, and has the right shape — which
is what justifies sending the emails. They are not the fit.

---

## 2. Access and licensing — what may actually be used

This section is the gate. Every conclusion here was read at its own source.

### 2.1 The MS2 portals: scraping is expressly prohibited

`mhd.public.ms2soft.com` is operated by Midwestern Software Solutions (MS2), and
its footer links to terms that state their own scope: *"These Terms of Use apply
to the MS2Soft.com web site **and to the other customer-specific or public web
sites or web pages maintained by MS2**."* That covers the MassDOT portal, and
the Vermont and New Hampshire ones (§6).

The operative clause, verbatim:

> "Contents may be used solely for the furtherance of your relationship with MS2
> and you may not copy, use, modify, distribute, transfer, download, upload,
> **"scrape"**, **"mine"**, resell, or republish any of the contents of this Web
> site, **including without limitation traffic data** and other information
> contained on customer-specific web sites or pages within this site, **without
> the prior written authorization of MS2**."

There is no reading of that under which a scraper is acceptable. It names the
verb and it names the data. **The previous draft of this plan was wrong on this
point and the correction is the main result of this revision.**

### 2.2 …but MS2 does not own the data, and says so

From the same terms:

> "**Ownership of Data**: MS2 customers own all traffic data that they input into
> their customer-specific pages on the MS2 web site and all analyses and reports
> involving only that data."

MassDOT owns the Massachusetts speed data. MS2 operates the website it is
displayed on. Those are different things, and the second does not encumber the
first. MassDOT is a public agency and its records are public under M.G.L. c. 66
§ 10. So the data is obtainable — through its owner.

### 2.3 The four asks, all free, all parallel, all week one

None blocks the others; send all four the same day.

| ask | to | what it gets | likelihood |
|---|---|---|---|
| **GoTime API key** | `api-signup.massgotime.com` | Sanctioned segment travel times, real-time, free. Form explicitly accepts "Individual" | **Good** — the form is built for this |
| **Bulk export of continuous-count speed data** | MassDOT Traffic Data / Highway Division | ~7 years × 214 Interstate stations of 15-minute speed | Moderate; MS2 has an agency-side bulk export, so the ask is easy for them to fulfil |
| **Written authorization** | MS2 (`ms2soft.com`) | Unblocks the portals directly — and MS2 hosts MA, VT **and** NH, so one grant may cover three states | Unknown; costs one email; MS2 will likely defer to each customer agency |
| **Anything post-2019** | CTPS (`ctps.org/data-resources`) | A newer speed index than the 2019 dashboards, which seed the provisional constant (§5.4) | Good; they answer data inquiries as a matter of course |

If the bulk export is ignored, escalate to a formal public records request. It is
free, it has a statutory response deadline, and the data is plainly a public
record. Say what is wanted narrowly — continuous-count *speed* records for
Interstate stations, 2019 onward — because a narrow request is cheap to fulfil
and a broad one attracts a fee estimate.

### 2.4 Sources eliminated on licence, not availability

* **NPMRDS** — free, exactly the right shape, covers precisely the broken class,
  and **legally unusable**. The Data Sharing Agreement is executed only by a
  "State Department of Transportation or Metropolitan Planning Organization
  receiving federal transportation funds" or their contractors under a named
  contract; and it **explicitly forbids making "data sets or aggregated average
  travel time databases publicly available"**, which is what shipping a profile
  in a public app is. Two independent blockers, either fatal. **Do not
  re-derive this.**
* **Sampling a routing API offline** (Google, Mapbox, HERE, TomTom) — ~3,400
  requests would fit inside free tiers, and all four bar caching and derivative
  datasets. Fitting a permanent table from responses is derivative-dataset
  creation. The trap that looks like cleverness.

---
## 3. How much of a downgrade is free?

**On the defect that exists: none. On access latency: real — weeks, not
today.** The earlier draft claimed the free path was "available today". §2 makes
that false, and it is the honest cost of going free.

The coverage question settles cleanly. From §4's per-class table, in minutes:

| class | km | assumed | measured | free-flow min | actual min | **excess** |
|---|---|---|---|---|---|---|
| motorway | 49.0 | 103 | 40 | 28.5 | 73.5 | **+45.0** |
| secondary | 30.2 | 59 | 58 | 30.7 | 31.2 | +0.5 |
| primary | 24.9 | 60 | 55 | 24.9 | 27.2 | +2.3 |
| tertiary | 23.9 | 47 | 49 | 30.5 | 29.3 | −1.2 |
| residential | 11.8 | 39 | 43 | 18.2 | 16.5 | −1.7 |
| | | | | | **total** | **+44.8** |

**Motorway is 45.0 minutes of a 44.8-minute error; the other four combined are
−0.1 minutes.** They cancel. A source covering only Interstates and principal
arterials therefore addresses **100% of the measured defect**, and what the paid
products would add is coverage of the part already right to within 7%.

What free actually costs, in order of severity:

1. **Weeks of latency instead of a download** (§2.3). Three emails, then either
   an eight-week GoTime archive or a fulfilled export request. Mitigated by
   shipping the machinery in week one behind a provisional constant (§12).
2. **Point speeds, if the archive route wins.** A loop detector measures speed
   where it sits; a bottleneck's queue extends *upstream*, so a station outside
   a queue reads free-flow while the corridor crawls. The bias is **optimistic —
   the same direction as the bug**. GoTime does not have this problem, which is
   the main reason it is primary rather than the fallback. §11 measures it.
3. **No confirmation for the surface classes.** A paid area product would have
   confirmed that `secondary`/`tertiary`/`residential` carry no time-of-day
   pattern worth modelling. Neither free source covers them well, so "flat
   profile for surface roads" stays an assumption resting on the eight traces.
   Exposure is small — those classes net to −0.1 minutes — but it is an
   assumption, and it is labelled as one.

What free is **better** at: nothing is being licensed, so nothing expires,
nothing needs renewing, and no clause restricts what the app may ship. That is
worth more than it sounds for a product intended to keep working.

---
## 4. The measured state — quoted, not re-derived

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

### 4.1 What this plan does *not* claim

Of the 49.7 minutes of unexplained stopped time, this plan only claims the part
that happens on congested motorway — where stop-and-go is captured by any
segment-average travel time, because such a measure includes the time stopped.
The part that is a surface-street junction OSM never mapped is a different
defect and is not addressed here.

**Nobody has split those 49.7 minutes by road class, and it should be the first
thing measured** — `analyze_trace.py` already has both the class breakdown and
the stop attribution, so the split is a reporting change, not new work. If most
of the unexplained stopped time turns out to be on surface streets, the ceiling
on this plan is lower than §4 makes it look. That measurement is cheap and it
should happen before the data is bought, not after.

---

## 5. The survey

### 5.1 MassDOT MS2 TCDS — the richest data, behind a request (§2)

Verified by opening `mhd.public.ms2soft.com`:

* **454 permanent stations**, of which **214 are functional class (1)
  Interstate**.
* Per station: Volume, **Speed**, Classification, WIM, gap.
* Speed stored as **hourly counts across fifteen 5 mph bins**, displayable at
  15- or 60-minute intervals.
* **~2,685 daily speed records** at the one Interstate station inspected — about
  seven years — current to two days before this survey.

**This entry is a correction twice over.** The first draft of this plan dismissed
MS2 in a sentence as "spot speeds, a cross-check not a source", reasoning from
how count stations are usually sited without opening one; opening one overturned
that (§1.1). The second draft then recommended scraping it, which §2.1 forbids.
The data is excellent; the route to it is a request.

### 5.2 MassDOT GoTime / RTTM — the sanctioned route, and better shaped

137 signs over 700+ miles of Massachusetts highway. Critically, the signup page
states the mechanism: travel times are generated **"by acquiring data from
Bluetooth sensors"** — device re-identification between sensor pairs, which
measures how long a vehicle *actually took over a segment*. That is the quantity
the router needs, and it is immune to §3's risk 2 by construction.

Licence, quoted from the signup page: *"Data processed by the system is provided
free-of-charge to authorized developers via a RESTful HTTP API"*, with one
restriction: *"Data provided by the GoTime API should only be used for
transportation purposes."* The organisation-type field offers **Public /
Private / Individual**.

Cost: free. Catch: real-time only, so the archive is self-polled — a cron job on
the Windows laptop that already runs the server, five-minute cadence, ~8 weeks
before each (segment × hour-of-week) cell has enough observations to median.

**This is not the live traffic feed the brief ruled out.** The router gains no
runtime dependency, no per-request cost and no latency; a poller writes rows to
a file and a fitting tool reads that file offline, months later.

### 5.3 Federal TMAS — checked, and it does not carry speed

Worth recording because it is the obvious "surely the feds publish this"
thought, and it would have solved all six states at once if true.

Every state submits continuous-count data to FHWA monthly, and the Traffic
Monitoring Guide's submission formats **do** include a speed record (5 mph bins).
But the public release at `fhwa.dot.gov/policyinformation/tables/tmasdata/` is
titled **"U.S. Traffic Volume Data"** and publishes station data plus monthly CCS
files of hourly *volume*; the companion open-data products are **Volume, Class
and Stations**. No speed. A search of ArcGIS Hub for New England DOT speed
datasets returns nothing from any of the six states.

**Verdict: eliminated, definitively. Do not re-check.** Hourly volume alone
cannot substitute: a detector in a jam records *capacity* flow, not demand, so
volume-to-speed conversion breaks down in exactly the congested regime this plan
exists to model.

### 5.4 Boston Region MPO (CTPS) — free, published, and the independent check

CTPS publishes Express-Highway and Arterial Performance Dashboards showing a
**"speed index" — observed speed over posted limit** per segment, with
downloadable tables. That is `SPEED_FACTOR` by another name. It is legal to use
precisely because an agency did the summarising: the NPMRDS licence that blocks
§2.4 expressly permits agencies to publish data summaries.

Limits: **2019, peak-period only, Boston region only.** Pre-pandemic, two bins
rather than a curve.

**Role:** the seed for the provisional constant in week one (§12), and an
independent check afterwards — it derives from INRIX probe data rather than loop
detectors, so where it agrees with a fitted profile, §3's risk 2 is bounded at
that location. Worth an email to `ctps.org/data-resources` asking for anything
post-2019.

### 5.5 Free, checked, not useful

* **OpenStreetMap** — no traffic data. `maxspeed:conditional` encodes *legal*
  limits that vary by time, not congestion. Confirmed; move on.
* **FHWA Urban Congestion Report / TTI Urban Mobility Report** — free and real,
  but metro-level summary statistics. No diurnal curve, no road. A sanity check
  on the magnitude of a fitted peak, nothing more.
* **Uber Movement** — discontinued, no official archive, newest data ~2020 and
  therefore pre-pandemic; its Speeds product covered a few cities' streets, not
  Massachusetts highways. Dead, and would not have helped.
* **US DOT ITS DataHub connected-vehicle data** — real probe data, wrong places
  (Wyoming, Tampa, NYC pilots). Nothing for New England.
* **New England 511** (`newengland511.org`, a ME/NH/VT partnership) — real-time
  travel times on a public map, partly Waze-sourced, **no documented public
  API**. One email per state if the expansion needs it; not a plan.
* **The project's own eight traces** — validation only (§11). Eight drives, one
  driver, three afternoons; there is no design in which they fit 168 cells.

### 5.6 Eliminated because they cost money

Recorded so nobody re-prices them. All are good products; none is needed.

* **TomTom Traffic Stats** — Area Analysis returns per-segment average speed,
  travel time, sample size, posted limit and road class by time bin, as
  shapefiles. Exactly the right shape. A 30-day free trial exists via the MOVE
  portal, but pricing beyond it is per directional mile and quote-only, and
  whether the trial licence permits shipping derived constants is unverified and
  sits behind the portal.
* **HERE Traffic Patterns** — average speed for every road, 15-minute intervals
  per day of week, 3-year average. Best-shaped product surveyed, hardest to buy:
  enterprise, quote-only, no self-service trial found.
* **INRIX MetroLab Challenge** — free INRIX API access for up to a year, but
  needs a local-government collaborator, costs $250 to apply, and applications
  **closed 2026-03-03**. Note for the next cycle.
* **StreetLight / Replica** — agency-priced, same licence shape as NPMRDS.

---

## 6. New England, state by state

`docs/new-england-expansion.md` has all six extracts staged, so the traffic
question has to answer for six states, not one. It does not answer uniformly.

**The earlier draft claimed the other states were "largely the same scraper".
That was wrong on both halves:** scraping is barred everywhere it would have
applied (§2.1), and the six states run **three different vendors**.

| state | portal | vendor | permanent stations | speed data | status |
|---|---|---|---|---|---|
| **MA** | `mhd.public.ms2soft.com` | MS2 | 454, **214 Interstate** | **Verified** — 15-min bins, ~7 yrs, current | Ask MassDOT (§2.3) |
| **NH** | `nhdot.public.ms2soft.com` | MS2 | **167** | **Doubtful** — first permanent station reads "SPEED: No Data"; category "Perm Volume" | Confirm, then ask NHDOT |
| **VT** | `vtrans.public.ms2soft.com` | MS2 | not counted | **Advertised** — VTrans says the portal offers "traffic volume, vehicle classification, vehicle speeds and vehicle weights" | Ask via VTrans' Survey123 data-request form |
| **ME** | MaineDOT interactive map | **Drakewell** | **91 CCS**, hourly, 2008– | Unconfirmed; the programme is volume-led | Ask MaineDOT Traffic Engineering |
| **CT** | `trafficmonitoring.dot.ct.gov` | **Bentley** | **40 ATR** | "Continuous Count Station Daytime Vehicle Speeds" published, format unclear, likely reports | Ask the Traffic Monitoring Section |
| **RI** | RIGIS / ArcGIS | Esri | not published | **None found** — AADT count locations only | Weakest; expect to fall back |

Three observations that matter more than the table:

1. **Coverage is wildly uneven and roughly tracks congestion.** Massachusetts —
   the state with real recurring congestion and 214 Interstate stations — has by
   far the best data. Rhode Island and rural Maine have the least data *and* the
   least congestion. That correlation is lucky, and §6.1 exploits it.
2. **NH is the one real gap.** It has 167 permanent stations and I-93, I-95 and
   I-293 carry genuine Boston-commuter congestion at the Massachusetts line, but
   the first permanent station inspected has no speed data at all. **Confirming
   whether NHDOT collects speed anywhere is the single highest-value New England
   check** and it is not resolved here (§11).
3. **Six asks, not one.** Each state is a separate email to a separate office
   under separate terms. That is the honest scope of "works across New England",
   and it is why §1 puts Massachusetts first rather than waiting for six.

### 6.1 The design answer: key the profile by urban/rural, not by state

The naive generalisation — fit one New England motorway profile — would apply
Boston's 6 PM trough to I-95 in rural Maine, where the road is empty and the
true factor is near free-flow all day. That would make Maine ETAs *pessimistic*,
which is a new bug in the opposite direction, and it is exactly the kind of
error a single pooled number hides.

Keying by **state** would fix it and is the wrong axis: it needs a state
attribute the graph does not carry, it puts a discontinuity at the state line
where none exists, and it splits Boston's suburbs from Boston.

**Key on urban/rural instead.** Congestion is a property of where the road is,
not which state issued the sign, and both halves are already available:

* On the data side, TCDS carries a **Rural/Urban** filter and FHWA functional
  class encodes it, so observations can be binned without extra work.
* On the graph side, `score.py` already computes a per-edge **`c_urban`**
  component ("town"), which `router.py` reads as a beauty type. It is a
  continuous 0–1 measure of how built-up an edge's surroundings are — precisely
  the axis congestion varies along, already computed for all 401,695 edges,
  needing no new pipeline stage and no graph rebuild.

So the table becomes `class × urban-band × hour-of-week`, with two or three
urban bands. A rural Maine motorway then inherits the rural profile — which the
Massachusetts data can fit perfectly well, because Massachusetts has rural
Interstates too — and Boston's peak stays where it belongs. **This is what makes
Massachusetts-only data legitimately generalise to New England**, and it is the
main reason not to block on the other five states' asks.

Risk to name: `c_urban` was calibrated for *scenery*, not congestion, and the
threshold that makes a road "towny" is not necessarily the one that makes it
congested. Fitting will show whether the bands separate; if they do not, fall
back to FHWA's binary urban/rural, which is carried in the count data and can be
joined onto the graph by a spatial overlay of Census urban areas — more work, no
new dependency.

---
## 7. The hard question: time-dependent routing

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

### 7.1 How wrong is (A)? — measured

Simulated against a bimodal weekday motorway profile anchored on the project's
own two measurements (1.16 off-peak, a parameterised PM trough), integrating the
route forward against applying the departure factor flat. All-motorway, which is
the worst case. "ff min" is free-flow trip minutes; 110 ff-min is about the
132-minute Wachusett drive.

**The profile's *shape* here is assumed, not fitted.** §1.1 now supplies one
real curve — one station, one Monday — and its trough of 0.45 sits between the
"moderate" and "deep" rows below, which is the useful thing to know. But one
station-day is not a fitted profile (§8.2), so the table is swept across three
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

### 7.2 The answer: build (B)

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

### 7.3 Why (C) is deferred — measured, not assumed

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

### 7.4 One correctness trap for whoever builds (C) later

Time-dependent Dijkstra is only label-setting on a **FIFO** network — leaving
later must never let you arrive earlier. A piecewise-constant profile violates
FIFO at every bin boundary: entering a long edge at 17:59 under a slow bin can
arrive after entering it at 18:01 under a fast one. The standard fixes are to
interpolate linearly between bins and cap the rate of change, or to adopt the
waiting-allowed model. **Design (B) is immune** — it evaluates a fixed profile
along a fixed path and never relies on the search being label-setting — which is
a real, if incidental, argument for it.

---

## 8. The model

A speed factor, per road class, per urban band, per hour of week:

```python
# router.py, beside SPEED_FACTOR
SPEED_PROFILE = {
    ("motorway", "urban"): [...168 floats...],   # Mon 00:00 -> Sun 23:00
    ("motorway", "rural"): [...168 floats...],
    ...
}
```

**168 cells, hourly.** Valhalla stores 2,016 five-minute cells per edge; both
free sources could support finer bins (TCDS displays at 15 minutes, GoTime polls
at five), but neither the eight-drive validation set nor an eight-week archive
justifies that resolution. Aggregate to hourly.

**Urban banding per §6.1** — two bands to start, from `c_urban`. This is what
lets Massachusetts data generalise across New England without applying Boston's
peak to rural Maine.

Which classes get a profile:

* **`motorway` — certainly.** 100% of the measured error (§3), and the class
  both free sources actually cover.
* **`trunk`, `primary` — from the data.** TCDS functional classes (2) Freeway &
  Expressway and (3) Other Principal Arterial map onto these, and GoTime covers
  some arterials. Fit them; ship a profile only if the fit shows real diurnal
  range.
* **Everything else — flat, until something argues otherwise.** `secondary`,
  `tertiary` and `residential` are within 7% and net to −0.1 minutes (§3). Do
  not spend that on 168 free parameters per class because the file has columns
  for them. `SURFACE_SPEED_FACTOR = 0.95` earned its single value by three
  independent classes agreeing; that evidence still stands.

Fallback chain, in Valhalla's order: profile cell → class/band daily mean →
class mean → `SPEED_FACTOR`/`SURFACE_SPEED_FACTOR` exactly as today. A cell with
too few observations falls back rather than shipping noise, and the fitting tool
reports how often it did — a New England build will lean on that fallback hard
in states whose asks came back empty (§6), and it must be visible when it does.

### 8.1 The `CONTROL_SECONDS` firewall

`analyze_trace.py`'s docstring is right and this plan obeys it: the speed factor
scales with distance and the junction cost scales with junction count, so
summing them fits one drive and nothing else.

**The profile multiplies `minutes`. It never touches the junction term.**
`_driving_minutes()` (`router.py:721`) becomes time-parameterised;
`_control_minutes()` (`router.py:739`) is not modified at all. A test asserts
`CONTROL_SECONDS` is invariant to departure time, so a future re-fit cannot
quietly migrate congestion into a per-junction constant where it would be baked
in permanently.

### 8.2 Fitting: four rules that matter more than the estimator

1. **Median across days, never one day.** §1.1's 5 PM → 6 PM → 7 PM shape may be
   one evening's incident. Recurrent congestion is what survives a median over
   every same-weekday observation; incidents do not.
2. **Space-mean, not time-mean.** A detector counts vehicles, so the arithmetic
   mean of its speed bins is a *time*-mean speed, which overestimates the speed
   that produces travel time. Use the harmonic mean over bin midpoints — at
   6 PM in §1.1 the two differ by 54.8 vs 29.2 mph. This is most of the answer,
   not a rounding detail. **GoTime needs no such correction**: a Bluetooth
   segment time is already a space-mean measurement, which is one more reason it
   is the primary source.
3. **Cap the open top bin.** The 85–250 mph bin was held at 90 in §1.1. It
   barely moves an off-peak factor and cannot move a congested one, but it
   belongs in the fitting tool as a named constant, not a magic number.
4. **Record provenance per cell.** Every cell should carry which source and how
   many observations produced it. With two sources, six states and an uneven
   fallback chain, a table that cannot say where a number came from is a table
   nobody can debug a year from now.

---

## 9. What lands where

No graph rebuild. `graph_edges.parquet` is not regenerated, `pipeline/graph.py`
is not modified, nothing is copied to the serving box.

| file | change |
|---|---|
| `pipeline/router.py:108` | add `SPEED_PROFILE` beside `SPEED_FACTOR`; both stay load-applied |
| `pipeline/router.py:721` | `_driving_minutes(hour_of_week)` — the one place the profile divides |
| `pipeline/router.py:739` | **unchanged**, deliberately, and tested for it |
| `pipeline/router.py:868` | `_weights(...)` takes a departure time and passes it down |
| `pipeline/router.py:967` | `route(...)` takes `depart`; the scipy call is unchanged |
| `pipeline/router.py:1374` | `RouteResult.minutes` walks `edge_minutes` with a clock — design (B), §7.2 |
| `server/app.py:150` | `/api/route?depart=<ISO8601>`, default now; **both** fastest and scenic priced at it |
| `tools/poll_gotime.py` | new; five-minute cron, append-only, resumable. Starts the day the key arrives |
| `tools/fit_speed_profile.py` | new; archive or export → the table. §8.2's four rules live here |
| `tools/analyze_trace.py` | report error **by departure hour**, and split the 49.7 unexplained stopped minutes by class (§4.1). Still reads no corrected constant — that is what makes it a valid instrument |
| `tests/` | guards, §11.2 |

There is deliberately **no scraper in this table.** §2.1 is why.

**Out of scope here, flagged for whoever owns the client:** the iOS app must send
a departure time for a drive planned in advance, and must decide whether a
mid-drive reroute re-prices at the current clock. `ios/` is untouched by
instruction and by good sense — three sessions have changes in flight. Until the
client sends one, `depart` defaults to now, which is correct for the "leaving
now" case that is most of the app's use.

---

## 10. What could not be established

* **Whether MassDOT grants a GoTime key to an individual project.** The form
  accepts "Individual" and asks for intended use, which is encouraging, but the
  answer is theirs. This gates the primary source.
* **Whether MassDOT will export the continuous-count speed archive**, and how
  long a public records request would take if the informal ask is ignored.
* **Whether MS2 grants written authorization**, and whether one grant could
  cover MA, VT and NH since they host all three.
* **Whether NHDOT collects speed data at all** (§6). The first permanent station
  inspected reads "SPEED: No Data" and is categorised "Perm Volume". This is the
  single highest-value New England check and it was not resolved — the portal's
  count-type filter needs driving to completion, or NHDOT needs asking.
* **Whether VT, ME and CT publish speed at usable granularity.** VTrans
  advertises vehicle speeds; MaineDOT is on Drakewell and volume-led; CTDOT
  publishes "daytime vehicle speeds" in a format not established. Three emails.
* **How badly point speeds understate corridor congestion** (§3 risk 2). Not
  answerable before a fit exists; §11.1 criterion 5 is the measurement, and
  GoTime sidesteps it entirely.
* **The class split of the 49.7 unexplained stopped minutes** (§4.1). Not
  external — nobody has run it. Cheapest item here and the one that most changes
  the plan's expected ceiling.
* **The §2.4 routing-API terms are second-hand.** The NPMRDS DSA and the MS2
  terms were both read at their own sources and are quoted verbatim; the
  caching and derivative-work restrictions for Google, Mapbox, HERE and TomTom
  came from comparison write-ups because TomTom's terms page would not render.
  None is on the recommended path, so the verdict stands — but anyone reviving
  that idea must read the contracts rather than trust this.

---

## 11. Validation

### 11.1 The eight traces, used correctly

They cannot fit the profile. They can falsify it, and they are the only ground
truth measured by the actual car on the actual roads. Each carries its own
departure timestamp, so each is evaluated **at the hour it was actually driven**,
never pooled.

1. **The broken case improves.** Needham→Wachusett: +242% → within ±25%. If the
   fitted profile cannot move that leg, either the profile is wrong or the wrong
   class is being blamed, and the plan has failed its one clear test.
2. **The working case does not regress.** Harvard→Needham stays within ±10%. A
   profile that fixes the highway by making back roads worse has traded one bias
   for another; the +2% is the most valuable number in §4 and the easiest to break.
3. **Off-peak still matches.** The 2026-08-14 traces that fitted `1.16` were
   driven off-peak; the profile's off-peak cells must reproduce them. The direct
   check that the fit did not simply shift everything down.
4. **`CONTROL_SECONDS` is unmoved.** Re-run `fit_junction_cost.py` afterwards;
   the pooled 11.5 s / 8.1 s should not move materially. If it does, congestion
   is leaking into the junction term.
5. **The point-vs-segment bias is measured, not assumed.** If the archive route
   won, compare the profile's predicted motorway factor at each trace's hour
   against that trace's measured factor. A systematic optimistic gap **is** §3's
   risk 2, quantified. If it is large, GoTime stops being merely primary and
   becomes the only source.
6. **The urban band separates.** Check that the fitted urban and rural motorway
   profiles actually differ, and that traces on rural motorway are priced by the
   rural one. If the bands do not separate, §6.1's generalisation to New England
   does not hold and the fallback in §6.1 is needed.

**Stated plainly: three afternoons cannot validate 168 cells, still less 168 ×
classes × bands.** These criteria test a handful of cells and the overall
direction. Everything else rests on the source. That is a real limitation with
no fix inside the available data, and it should not be hidden behind a pooled
average that conceals which cells were exercised.

### 11.2 Tripwires

In `tests/test_calibration.py`'s style — loose guards against a constant drifted
into nonsense, not fitted precision:

* every profile cell within `[0.3, 1.4]`; outside that an ETA is absurd;
* the urban motorway profile has real diurnal range (peak-to-trough ≥ 20%), so a
  silently-flat table fails loudly instead of reverting to today's bug in silence;
* cells are continuous across bin boundaries within a bounded step — the FIFO
  trap of §7.4, caught at test time rather than in a route;
* `CONTROL_SECONDS` invariant to departure time — the §8.1 firewall;
* a long trip priced by integration differs from the flat departure-time price
  by roughly what §7.1 predicts, so design (B) is provably wired in rather than
  quietly bypassed;
* every shipped cell carries provenance and an observation count (§8.2 rule 4).

---

## 12. Sequencing

**Week 1 — send the emails, ship the machinery.**
The four asks of §2.3 go out the same day, all free, none blocking: the
**GoTime key**; the **MassDOT bulk speed export**; **MS2 written
authorization**; and **CTPS** for anything post-2019. Then run the §4.1 class split, which needs nobody's
permission.

Then build against a provisional constant: `SPEED_PROFILE`, the
time-parameterised `_driving_minutes`, integrated `RouteResult.minutes`, the
`depart` parameter, the §11.2 tripwires. Seed from **CTPS's published speed
index** — free, legal, agency-published, and independent of every pending ask —
label the constants `PROVISIONAL` in the source, and ship. **The product bug is
fixed in week one, and the real fit lands in a working harness that needs only
its constants replaced.**

**Week 2 — start the clock, chase New England.**
The moment the GoTime key arrives, start `tools/poll_gotime.py`; its value is
purely a function of how early it starts. In parallel, resolve §10's New England
unknowns: confirm whether NHDOT has speed at all, and email VTrans, MaineDOT and
CTDOT. Six states, six answers, no code.

**Weeks 3–10 — fit whatever arrived first.**
If the MassDOT export lands, fit it immediately — seven years beats eight weeks,
and it can be re-fitted against GoTime later. If it does not, the GoTime archive
matures around week ten. Either way, `tools/fit_speed_profile.py` under §8.2's
four rules, replace the provisional constants, drop the label.

**Then — validate and decide.**
Run §11.1 against all eight traces, criteria 5 and 6 included: 5 prices the free
path's main weakness, 6 decides whether New England generalisation holds. Then
the judgement call this plan deliberately leaves open — measure how often route
*choice* differs between design (B) and a design (C) prototype on long trips. If
rare, (C) stays deferred permanently and §7.3's benchmark is the reason. If
common, (C) gets scoped: compiled, per §7.3, never in Python.

**If every ask is refused** — the genuinely bad case — the fallback is the
provisional CTPS-seeded profile, shipped permanently and labelled as
approximate. It is 2019, peak-period-only, Boston-only data. It is also still
enormously better than believing motorway runs at 1.16 of the limit at six in
the evening, which is what the app does today.
