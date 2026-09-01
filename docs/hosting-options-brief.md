# Where the Scenic API should live, and whether it can be free

**Status: requirements measured 2026-08-31, nothing changed.** No source file,
no config, no deploy was touched. The task is a **recommendation and a plan**,
not a migration — the migration needs account credentials nobody but the owner
has.

The goal, in the owner's words: get the API off the laptop, **prioritising free
if it is genuinely possible**. "Free" is a real constraint here, not a
preference — treat a paid recommendation as something you have to justify
against a free option that works.

---

## Why this is being asked now

The API is self-hosted on a spare Windows laptop behind a Cloudflare tunnel at
`api.jameskouvlis.com` (`server/DEPLOY.md` Option A). It has been observed down
**twice in three days**: HTTP 530 / Cloudflare 1033 on 2026-08-29
(`docs/consumer-polish-brief.md` item 1) and again on 2026-08-31. That is the
only uptime data that exists and it is two-for-two on unannounced outages.

`DEPLOY.md` §7 already specifies the full hardening — Task Scheduler entries for
both Flask and `cloudflared` at startup with restart-on-failure, `powercfg` to
disable standby/hibernate and neutralise the lid, Windows Update active hours.
**So either that was never applied or it is not holding, and which one it is
changes the answer.** Establishing that is part of this task, not a detail.

---

## The measured requirements

All measured on this Mac on 2026-08-31, against `main` at `efbbd28`, on the
**New England** build `data/processed-ne` (998,252 edges / 794,685 nodes) which
is what is actually served:

| | measured |
|---|---|
| `Router` peak RSS, access layers loaded | **3.53 GB** cold, 3.85 GB after touching them |
| access layers actually loaded? | **yes** — 691,319 entries |
| `Router` load time | **42.6 s** |
| `route()`, fastest arm (Boston → Augusta ME, 262 km) | **470 ms** |
| `route()`, scenic arm | **365 ms** |
| a full API request (two Dijkstras) | **~835 ms** |
| serving payload, required three parquets | **214 MB** |
| ...plus the optional access layers | **382 MB** total |
| concurrent users | **1** |

**The single most important number, and the reason this brief exists in this
form: `docs/new-england-rollout.md` Phase 6 projects 6–6.5 GB resident, and the
measurement is 3.53 GB.** That projection is a linear extrapolation from one
Massachusetts measurement and it overestimates by roughly 40%. Phase 5's ~1.27 s
per request is likewise pessimistic against a measured ~835 ms.

**Do not size a box from the rollout doc.** The requirement is a machine with
**≥4 GB usable RAM** (6–8 GB for comfort), one reasonable core, ~1 GB of disk,
always on, never sleeping. That is a much cheaper machine than the doc implies,
and it is what puts a free tier in play at all.

---

## The candidates, and what has to be established about each

### The one that could actually be free: Oracle Cloud Always Free

Ampere A1 (ARM) instances, advertised as up to 4 OCPU / 24 GB RAM, always free
with no 12-month clock. On the measured 3.53 GB this fits with enormous margin
and is the only mainstream free tier that does. **It is the option to
investigate first and hardest.** Four things must be settled before recommending
it, and none of them should be assumed:

1. **ARM64 wheels.** The serving path needs what is in
   `server/requirements-serve.txt` — check that list specifically, not the
   pipeline's. `numpy`, `scipy`, `pandas`, `pyarrow` and `geopandas` all publish
   aarch64 wheels, but *verify* rather than assume, and note anything that would
   have to build from source on a free-tier CPU.
2. **A1 capacity.** Ampere A1 is widely reported as "out of capacity" in popular
   regions for extended periods. A recommendation that cannot actually be
   provisioned is not a recommendation — establish current reality and name a
   region.
3. **Idle reclaim and account termination.** Oracle's free tier has provisions
   for reclaiming idle resources. Read the actual current terms. A box that
   disappears in 30 days is worse than the laptop, because it will fail silently.
4. **Terms of service** for serving a public API off it.

### Disqualify quickly, with a reason

- **AWS / GCP / Azure free tiers** — 1 GB class instances, and time-limited to
  12 months. Fails on RAM before anything else.
- **Render / Railway / Fly.io free allowances** — small, and **they sleep**.
  See trap 2: a 42.6 s cold start is disqualifying on its own, independent of
  RAM.

### The incumbent, which is also free

**Keep the laptop and make it reliable.** Zero migration risk, zero cost, and
`DEPLOY.md` §7 already specifies how. This brief does **not** presume migration
is the right answer, and a recommendation to stay put — with the §7 gap closed,
a watchdog, and external monitoring — is a legitimate outcome if the evidence
supports it. What it must not be is the *default* answer arrived at by not
investigating the alternatives.

### The paid fallback, for calibration

Hetzner's cost-optimised CX line, US regions available (Ashburn VA, Hillsboro
OR). CX43 (8 vCPU / 16 GB) is €15.99/mo as of the June 2026 price increase.
**But the requirement is ~4 GB, not 16** — so price the smaller CX tiers too and
report the cheapest that clears the measured footprint with headroom. Note that
Hetzner's *dedicated*-vCPU lines (CPX, CCX) rose by up to 176% in June 2026 and
are poor value for a single-user workload.

---

## Traps

1. **Sizing from `new-england-rollout.md` Phase 6.** Its 6–6.5 GB is an
   extrapolation; the measurement is 3.53 GB. Using the doc's number inflates
   the recommendation into a needlessly expensive tier and may rule out the free
   option entirely — which would be the wrong answer for the wrong reason.

2. **Anything that sleeps or cold-starts is disqualified.** Load time is
   **42.6 s**. That is tolerable once at boot and unacceptable mid-drive: a
   reroute that takes 43 seconds has already failed. This also rules out
   serverless (Lambda, Cloud Run) regardless of price — the working set is
   ~3.5 GB and the load cost is paid per cold container.

3. **Assuming ARM "just works".** It probably does, but the free recommendation
   rests on it, so check the wheels rather than asserting them.

4. **Region.** Route *planning* at ~835 ms absorbs 100 ms of extra RTT without
   anyone noticing; a **mid-drive reroute** is the case that does not. Prefer
   US-East. If you recommend a non-US region, state what it costs in latency
   rather than leaving it implicit.

5. **Code and parquets must come from the same commit.** `router.py` re-blends
   scores live using `score.py`'s `WEIGHTS`, so old code meeting new parquets
   returns subtly wrong routes *with no error at all*.
   `test_neutral_weights_reproduce_the_precomputed_score` is the tripwire — run
   the suite on the box after copying. `DEPLOY.md` records a real instance of
   this costing time on 2026-08-29.

6. **382 MB over a home upload link.** Stage it as a resumable transfer, not one
   long copy. `DEPLOY.md` already flags an 80 MB copy as worth noting; this is
   nearly five times that.

7. **Do not propose building the graph on the server.** The pipeline needs the
   OSM PBF, elevation tiles and rasterio; the serving box needs three parquet
   files and `requirements-serve.txt`. That split is deliberate.

8. **Free tiers that require a credit card can still bill you.** If a
   recommendation has any path to a surprise charge, say so plainly and say how
   to cap it.

---

## Done looks like

1. **A recommendation**: named provider, plan, region, and monthly cost —
   including **$0** if that is genuinely achievable and survives the four Oracle
   questions above.
2. **Evidence for the free option**, specifically: the ARM64 wheel question
   answered against `server/requirements-serve.txt`, and a statement on capacity
   and idle-reclaim taken from the provider's current terms rather than from
   memory or a blog post.
3. **A verdict on the incumbent** — does hardening the laptop beat migrating?
   Answer it on evidence, including whether `DEPLOY.md` §7 was ever applied.
   "Stay on the laptop" is an acceptable conclusion; "we didn't look" is not.
4. **A migration plan**: what to copy, in what order, how to verify it works,
   and how to roll back. Note that rollback is one environment variable —
   `SCENIC_DATA` — which is why the deploy risk is low.
5. **A measurement plan for the box**: peak RSS, load time, and one real route,
   compared against the table in this brief. The 3.53 GB figure is from an Apple
   Silicon Mac and will not transfer exactly to ARM Linux or x86 — expect it to
   move and say by how much.
6. **Uptime**: what monitors it, and what restarts it when it dies. The current
   answer is "the owner finds out when someone curls it", and that has to change
   whatever host wins.
7. **Or a statement of why this brief is wrong**, quoting what proves it. A
   refuted assumption is a good outcome; a silently skipped question is not.

---

## Out of scope

- **Performing the migration.** It needs accounts and credentials. Produce the
  plan; the owner executes it.
- **Buying anything**, or signing up for anything that requires payment details.
- **Router performance work.** Phase 5 of the rollout doc already measured the
  candidate optimisations (`limit=`, A*, degree-2 contraction) at ~1.2x each and
  recommended deferring. Do not reopen it; ~835 ms is not the problem here.
- **The domain and tunnel.** `api.jameskouvlis.com` through Cloudflare works and
  is free. A new host may not need the tunnel at all, but keeping the same
  hostname is a requirement — it is baked into the app bundle at
  `ios/project.yml` (`ScenicAPIBaseURL`), so changing it means shipping a new
  build through App Review.
