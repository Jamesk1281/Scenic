# Plan: time-of-day travel times

**Scope: one coarse peak/off-peak factor on motorway, applied at load. Roughly a
day's work, gated on one lookup that may say don't bother.**

An earlier draft of this file planned a 168-cell hour-of-week profile fitted
from probe data, with agency data requests, an eight-week polling archive and a
six-state New England survey. That was scoped to the wrong question. §7 records
what it ruled out so nobody repeats the survey; everything else is deleted.

---

## 1. Why this might matter

The planning screen shows fastest beside scenic. That comparison — *is the
pretty way worth it* — is the question the app exists to answer, so the delta
between the two is the product's headline number, not an ETA detail.

`router.py:108` says `SPEED_FACTOR = {"motorway": 1.16}`: motorway is driven 16%
over the posted limit, at every hour of every day. Scenic routes barely use
motorway; fastest routes are mostly motorway. So the delta moves with that one
constant, and nothing else in the app does.

Holding the README's measured 71% fastest-vs-scenic gap on 40–90 km trips, a
60-minute fastest route and a 103-minute scenic one — what the app shows as a
**43-minute** penalty is really:

| peak motorway factor | fastest 50% motorway | 70% | 90% |
|---|---|---|---|
| **1.16** (today's model) | 43 min | 43 min | 43 min |
| 1.00 | 39 | 37 | 35 |
| 0.90 | 35 | 32 | 28 |
| 0.80 | 31 | 26 | 21 |
| 0.70 | 26 | 18 | 10 |
| 0.60 | 19 | 8 | −3 |

**Above a peak factor of ~0.9 the app is within a few minutes of honest and this
is not worth building.** Below ~0.7 it is telling users the scenic route costs
four times what it costs. Everything here turns on which is true.

## 2. What is actually known — and what is not

* Off-peak motorway is **1.16**, measured over the 2026-08-14 drives against
  real `maxspeed` tags (97% of motorway km carry one). Not in dispute.
* Pooled travel-time error after the 2026-08-15 corrections was **5.7%** on
  those drives. The model is fine off-peak.
* Surface classes measure within **7%** across the 2026-08-25 drives —
  `secondary` 0.99, `primary` 0.93, `tertiary` 1.05, `residential` 1.10. This is
  why the plan touches motorway and nothing else.
* **The recurrent peak motorway factor is unknown.** Nothing this project has
  measured establishes it.

That last point is the honest state and it is a change from earlier drafts. The
one drive that appeared to measure heavy motorway congestion was a crash — an
incident, not a schedule — and a time-of-day model cannot predict incidents by
definition. It is excluded here and must stay excluded.

The same reasoning disqualifies a single evening at a single count station: a
factor that collapses and recovers within two hours is an incident signature,
and one day cannot tell it from recurrence. **Any number used here must be an
average over many days**, which is precisely what §3 is.

## 3. The gate: one lookup, about an hour

Get the recurrent peak factor from the Boston Region MPO (CTPS) Express-Highway
Performance Dashboard, which publishes a **"speed index" — observed speed over
posted speed limit** per expressway segment, with downloadable tables.

Three reasons this is the right source and the only one needed:

1. It is *literally* `SPEED_FACTOR`. Same definition, no conversion.
2. It is an **annual aggregate**, so it measures recurrent congestion and
   averages incidents out — the exact distinction the crash drive taught.
3. It is free, agency-published and carries no access request. (It is derived
   from INRIX data, which is licence-restricted at source; CTPS publishing a
   summary is expressly permitted, which is what makes reading it clean.)

Limits, stated up front: 2019, peak-period only, Boston region, expressways. All
acceptable — a two-band model needs exactly a peak-period number, and the Boston
expressways are where the app's fastest routes run.

**Then decide, against §1's table:**

* peak factor **≥ 0.9** → stop. Note the number in this file, ship nothing, and
  optionally add the §6 disclaimer. This is a real possible outcome.
* peak factor **< 0.9** → build §4. Half a day.

Worth one email to `ctps.org/data-resources` asking whether anything post-2019
exists, since 2019 is pre-pandemic. Not a blocker — send it and proceed.

## 4. The model

Three bands, motorway only:

```python
# router.py, beside SPEED_FACTOR — same load-time application, no rebuild
MOTORWAY_BY_BAND = {"am_peak": ..., "pm_peak": ..., "off": 1.16}
PEAK_HOURS = {"am_peak": (6, 9), "pm_peak": (15, 19)}   # weekdays only
```

* **Weekdays only.** Weekends and holidays take `off`. Recurrent congestion is a
  commute phenomenon.
* **Motorway only.** Surface classes are within 7% (§2); giving them bands
  spends that evidence on free parameters nothing measured.
* **`trunk` only if CTPS's tables separate it** and the number differs from
  motorway. Otherwise leave it on `SURFACE_SPEED_FACTOR` as today.
* **Ramp the band edges over ~30 minutes** rather than stepping. A cliff at
  19:00 makes leaving at 18:55 arrive later than leaving at 19:05, which is
  absurd on its face and also breaks the assumption a shortest-path search
  relies on.
* **`CONTROL_SECONDS` is not touched.** The band multiplies `minutes`; the
  junction term is untouched, and a test asserts it. Folding congestion into a
  per-junction cost would fit one drive and nothing else — see
  `analyze_trace.py`'s docstring.

## 5. Departure time — the one non-obvious part

A 132-minute drive leaving at 17:00 finishes at 19:12, out of the PM band. So
"apply the departure band to the whole trip" is wrong, and with sharp bands it
is wrong by the full width of the band.

**Route choice** uses the departure band: one static cost matrix, the scipy
Dijkstra at `router.py:982` unchanged, zero added cost.

**The reported ETA** integrates forward along the chosen route instead —
`RouteResult.minutes` (`router.py:1374`) already sums `edge_minutes`, a per-edge
array in travel order, so this is a cumulative walk that advances a clock in one
property. It costs microseconds because a route is hundreds of edges, not
750,000, and it removes essentially all of the approximation error.

Full time-dependent *search* is deliberately not built. Measured on a graph
sized like the Massachusetts build: scipy static 88.6 ms, Python static 319.9 ms,
Python time-dependent 391.5 ms. **Time-dependence itself adds 22%; leaving
scipy's C costs 3.6×.** If it is ever wanted, write it compiled or not at all.
The product defect is a *reported* number, and integration fixes reported
numbers for free.

## 6. What lands where

No graph rebuild. `graph_edges.parquet`, `pipeline/graph.py` and the serving box
are untouched.

| file | change |
|---|---|
| `pipeline/router.py:108` | `MOTORWAY_BY_BAND` + `PEAK_HOURS` beside `SPEED_FACTOR` |
| `pipeline/router.py:721` | `_driving_minutes(depart)` — the one place the band divides |
| `pipeline/router.py:739` | `_control_minutes` **unchanged**, and tested for it |
| `pipeline/router.py:868`, `:967` | `_weights` / `route` take `depart` |
| `pipeline/router.py:1374` | `RouteResult.minutes` walks `edge_minutes` with a clock (§5) |
| `server/app.py:150` | `/api/route?depart=<ISO8601>`, default now; **both** routes priced at it |

`depart` defaulting to now means **no iOS change is required to ship** — "leaving
now" is most of the app's use. Sending a departure time for a trip planned in
advance is a later, optional client change; `ios/` is untouched here.

**Optional, free, and independent of the gate:** a line under the fastest ETA
saying it does not account for traffic and that a mainstream app is better for
time-critical trips. Worth adding either way — but it is not a substitute for
§4, because it disclaims the *comparison*, which is the product's thesis rather
than the part that is unreliable.

## 7. Validation, and what is deliberately not validated

* **Off-peak must not regress.** The 2026-08-14 drives sit at 5.7% pooled error
  and are off-peak; the `off` band is 1.16 precisely so they are unchanged. This
  is the only regression risk the change carries, and it is cheap to check.
* **`CONTROL_SECONDS` must not move.** Re-run `tools/fit_junction_cost.py`; the
  pooled 11.5 s / 8.1 s should hold. If it drifts, congestion is leaking into
  the junction term.
* **The peak band is not validated by anything in the trace set, and cannot be.**
  There is no clean recorded peak-hour motorway drive — the one that looked like
  it was a crash. Say so in the constant's comment.

The cheap fix for that, if the gate passes and the number matters: **record two
or three deliberate drives on the same expressway corridor at 17:30 on
weekdays.** That is the only thing that would turn the peak band from a borrowed
number into a measured one, it costs an afternoon, and it should be scheduled at
the same time the constant lands rather than left implicit.

Tripwires, in `tests/test_calibration.py`'s style: every band within `[0.4, 1.4]`;
the `off` band exactly reproduces today's ETAs; band edges continuous within a
bounded step; `CONTROL_SECONDS` invariant to `depart`; a long trip priced by
integration differs from the flat departure-band price, so §5 is provably wired
in rather than quietly bypassed.

## 8. Ruled out — do not re-survey

Recorded so this ground is not covered twice. All were checked at source on
2026-08-26.

* **NPMRDS** — free and exactly the right shape, and **legally unusable**. Its
  Data Sharing Agreement is signable only by a state DOT or MPO receiving
  federal transportation funds, and forbids making "data sets or aggregated
  average travel time databases publicly available" — which is what shipping a
  speed profile in a public app is. Two independent blockers.
* **Scraping the state count portals** (`*.ms2soft.com`, used by MA, NH and VT).
  MS2's terms of use forbid it in terms that name both the verb and the data:
  no "copy, use … download … 'scrape', 'mine' … including without limitation
  traffic data … without the prior written authorization of MS2", and they state
  that this covers the public agency portals. The data behind it is genuinely
  excellent — MassDOT has 214 permanent Interstate stations with 15-minute speed
  bins and years of history — and MS2's own terms say the *agency* owns it, so
  asking MassDOT is the legitimate route if this scope ever grows.
* **MassDOT GoTime API** — free, sanctioned, accepts individual developers, and
  measures true Bluetooth segment travel times. The right source for a bigger
  version of this feature, and overkill for three constants: it is real-time
  only, so it needs ~8 weeks of self-polling before it can be fitted at all.
* **Federal TMAS** — publishes volume and class for every state, never speed.
  Checked at `fhwa.dot.gov/policyinformation/tables/tmasdata/`.
* **TomTom Traffic Stats, HERE Traffic Patterns** — right shape, quote-only
  pricing. Out on cost.
* **Sampling Google/Mapbox/HERE/TomTom routing APIs offline** — would fit inside
  free tiers, and all four bar caching and derivative datasets. The idea that
  looks like cleverness and is a licence breach.
* **Uber Movement** (discontinued, pre-pandemic, wrong roads), **OpenStreetMap**
  (no traffic data at all), **New England expansion** (three portal vendors
  across six states, six separate asks — not a prerequisite for fixing
  Massachusetts).
* **Fitting anything on this project's own eight drives.** One driver, three
  afternoons, and one of those was an incident. They validate; they do not fit.
