# Deploying the Scenic API

The whole backend is self-hosted and free per request (no Google/Apple/Mapbox
metering). Costs are flat, not per-user: the same box serves 3 or 3,000 people.
It runs happily on **a spare laptop** (the cheapest option — recommended while
usership is low) or a VPS (always-on, when you outgrow the laptop).

**Currently deployed** as Option A below: a spare Windows laptop running
`serve.py` behind a Cloudflare tunnel, serving `api.jameskouvlis.com` over HTTPS.

## What ships

Only a few files are needed to *serve* routes — never the OSM/elevation data or
the pipeline scripts:

- **Data** (build locally, copy over): `data/processed/graph_edges.parquet` +
  `graph_nodes.parquet` + `turn_restrictions.parquet` (~87 MB total; the third
  is well under a megabyte and the server refuses to start without it), plus
  `access_ways.parquet` + `access_entries.parquet` (~59 MB, and **optional** —
  see the 2026-08-23 note below).
- **Code**: `server/app.py`, and `pipeline/router.py` + `common.py` + `score.py`
  (router imports the latter two for shared constants and the scoring weights).

Regenerate the graph locally with the pipeline (see the top-level README) when
the scoring changes, then copy all three parquet files over.

> Deploying the length-weighted edge scoring needs a `graph.py` rerun and a
> fresh copy of both parquets. The old ones still *load* under the new code —
> the columns are unchanged — so nothing errors; the server would just keep
> serving the midpoint-sampled scores, which is the silent-disagreement case
> the warning below is about.

> The same applies to the strongly-connected component fix in `graph.py`.
> A graph built before it contains 645 nodes (199 km of road) that the router
> can never route out of, 167 of them with no outgoing edge at all — the API
> accepts a request snapping to one and then answers 404. The fix is in the
> *build*, so it takes a `graph.py` rerun and a fresh copy of both parquets;
> nothing about the serving code notices. Rebuilt, the graph is one strongly
> connected component and drops 712 edges (0.2%).

> **Turn-by-turn maneuvers need a rebuilt graph, and this one will not start
> without it.** The instructions carry exit numbers, ramp destinations and
> rotary exit counts, which come from OSM tags `graph.py` used to discard —
> `junction`, `destination`, `destination:ref` on edges, and the `ref` on
> `highway=motorway_junction` nodes. So `graph_edges.parquet` gains `junction`,
> `dest_ref` and `dest_name`, and `graph_nodes.parquet` gains `exit_ref`.
>
> Unlike every warning below this one, it is not a silent disagreement:
> `Router` checks for those columns at load and raises, because a graph that
> merely *routes* correctly while answering every rotary with a slight right
> and every exit with nothing is exactly the failure this file keeps warning
> about. Rerun `graph.py` and copy **both** parquets. Note the rebuild is now
> ~135 s rather than ~30 s: reading node tags means a Python callback per node.

> **Junction timing needs a rebuilt graph too, and this one will not start
> without it either.** Travel time is no longer free-flow: `router.py` prices
> each road at the speed its class is really driven and charges for the traffic
> signals and stop signs on it, in the direction those face. The counts come
> from OSM nodes `graph.py` used to discard, so `graph_edges.parquet` gains six
> columns — `n_signal_fwd`/`_rev`, `n_stop_fwd`/`_rev`, `n_giveway_fwd`/`_rev`.
>
> `Router` checks for them at load and raises, because a graph that routes
> perfectly well while charging nothing for 29,772 traffic controls answers
> every ETA 22% short with nothing anywhere saying so.
>
> The same build also writes a third file, `turn_restrictions.parquet`, without
> which the router cannot tell a legal turn from an illegal one — measured, 18%
> of long routes then contain a movement OSM forbids. It too is refused loudly
> at load. Rerun `graph.py` and copy **all three** parquets.
>
> The *seconds* each control is worth are not in the parquet — they live in
> `CONTROL_SECONDS` and `SPEED_FACTOR` in `router.py`, so re-fitting them from
> new drive traces is a code change and a restart rather than another rebuild
> and another 80 MB copy over the tunnel.

> **2026-08-23 — destinations now snap to the road you can get in from.**
> `highway=service` is extracted into `access_ways.parquet` and
> `access_entries.parquet`, and `Router.snap_destination` uses them to turn a pin
> inside a car park into that park's actual entrance. Measured on the drives of
> 2026-08-22: three of five destinations sat inside mapped car parks and two
> snapped to a road with no connection to the park at all — one to a cul-de-sac
> 102 m away whose real entrance was a secondary road 226 m in the other
> direction. Replaying that drive, the fix takes it from 13 off-route reroutes
> to 3.
>
> Unlike every other parquet here these two are **optional**: absent, the router
> logs nothing and behaves exactly as it did before, which is a worse answer but
> not a silent one. That is deliberate — a graph built before this date must
> still serve. They cost **+10 s startup, +0.5 GB RAM and 59 MB on disk**, and
> they are built by `extract.py`, not `graph.py`, so refreshing them means a
> pipeline run against the PBF rather than a graph rebuild.
>
> **The code and the parquets must come from the same commit.** `router.py`
> re-blends every edge's score live per request using `WEIGHTS` from `score.py`,
> so a server running different scoring constants than the ones that built the
> graph returns subtly wrong routes with no error anywhere.
> `test_neutral_weights_reproduce_the_precomputed_score` in `tests/test_routing.py`
> is the tripwire — run the suite on the serving box after copying data.

## Option A — Spare laptop + Cloudflare tunnel (recommended)

A laptop that stays on can be the backend, with a tunnel exposing it publicly
without touching your router or opening any ports. The laptop dials *out* to
Cloudflare, which relays traffic back down that connection — and terminates
HTTPS, which iOS App Transport Security requires.

Requires a domain whose nameservers point at Cloudflare (free plan is enough).

### 1. Python and the code

Install Python 3.12 from python.org (not the Microsoft Store build — its path
sandboxing complicates Task Scheduler later), ticking **Add python.exe to PATH**.
Clone to a path with **no spaces**: a venv's console scripts hard-code their
interpreter path, so they break if the folder is moved or contains a space.

```sh
git clone https://github.com/Jamesk1281/Scenic.git C:\Scenic
```

Copy `graph_edges.parquet`, `graph_nodes.parquet`,
`turn_restrictions.parquet` and — unless you are deliberately skipping the
access layer — `access_ways.parquet` and `access_entries.parquet` into
`C:\Scenic\data\processed\` (USB stick or a cloud folder; they are gitignored).

### 2. Virtualenv — serve dependencies only

```sh
# macOS / Linux:
python3 -m venv .venv
.venv/bin/python -m pip install -r server/requirements-serve.txt

# Windows (PowerShell) — run these one at a time, not chained with `;`, which
# continues past failures and leaves a working-looking venv with nothing in it:
#   python -m venv C:\Scenic\.venv
#   C:\Scenic\.venv\Scripts\python -m pip install -r C:\Scenic\server\requirements-serve.txt
```

Use `python -m pip`, never the `pip` script, for the shebang reason above.
`requirements-serve.lock.txt` holds exact known-good versions if you want them.

**Do not install `pipeline/requirements.txt` on the serving box.** It pulls
osmium, rasterio, matplotlib and folium — none of which are needed to serve, and
the first two are the usual Windows build headaches.

### 3. Verify before exposing anything

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests/
```

With all five parquets copied over, expect **230 passed, 3 skipped**. The three
skips are expected and permanent on a serving box: they need
`scored_chunks.parquet`, a pipeline artifact the server never reads and the file
list above deliberately does not copy. With the three required ones but not the
access layer, expect **226 passed, 7 skipped** — the four extra skips are the
car-park destination tests, standing aside for the same reason. With none of the
parquets — a fresh clone — expect **121 passed, 99 skipped**: everything that needs a built graph
steps aside cleanly rather than erroring, so a skip here means "the data isn't
here yet" and never "the data is wrong".

What matters is that nothing *fails*: a failure means the data and the code
disagree. (Count the skips, not the passes — the pass count moves whenever a
test is added, and a stale number in this file is its own false alarm.) If you
see *errors* rather than skips, the graph is present but partial — most likely
`turn_restrictions.parquet` never made it across.

### 4. Run the API

```sh
.venv/bin/python server/serve.py          # macOS / Linux
# C:\Scenic\.venv\Scripts\python C:\Scenic\server\serve.py    # Windows
```

Warm and listening on `0.0.0.0:5057`. Behind a tunnel nothing off-box needs to
connect directly, so `SCENIC_HOST=127.0.0.1` is worth setting — it keeps the
local network out and means you can safely decline the Windows Firewall prompt.

```sh
curl http://localhost:5057/api/health      # {"status":"ok","nodes":310807}
```

### 5. Tunnel it

```sh
brew install cloudflared                   # macOS
# winget install --id Cloudflare.cloudflared   # Windows
```

A throwaway URL is the fastest way to prove the chain works end to end:

```sh
cloudflared tunnel --url http://localhost:5057   # → https://<random>.trycloudflare.com
```

Note that `*.trycloudflare.com` is blocked by many DNS resolvers (1.1.1.1 for
Families, OpenDNS FamilyShield, NextDNS defaults) because it gets abused for
phishing — so a phone on filtered home DNS may fail to resolve it while the
serving box, already inside the tunnel, works fine. That is a reason to move to a
named tunnel rather than a reason to debug.

**Named tunnel on your own domain** (stable URL, and it can be rate-limited).
The CLI flow below needs only a normal free Cloudflare account — the Zero Trust
dashboard asks for a credit card even on its free tier, and is not required:

```sh
cloudflared tunnel login                                  # authorize the zone
cloudflared tunnel create scenic
cloudflared tunnel route dns scenic api.example.com       # creates the CNAME
```

Then a config file at `~/.cloudflared/config.yml` (Windows:
`C:\Users\<you>\.cloudflared\config.yml`) — a **new** file, not the
`<uuid>.json` credentials file `tunnel create` just wrote:

```yaml
tunnel: <tunnel-uuid>
credentials-file: /path/to/<tunnel-uuid>.json
ingress:
  - hostname: api.example.com
    service: http://localhost:5057
  - service: http_status:404
```

```sh
cloudflared tunnel run scenic
```

On Windows, write that file with `Set-Content -Encoding ascii`, not Notepad
(which silently appends `.txt`) and not PowerShell's `utf8` (which adds a
byte-order mark that the YAML parser rejects).

### 6. Rate limit

The API has no auth and permissive CORS, and each request is ~190 ms of CPU that
the 4 threads do **not** parallelize (see Notes) — a ceiling nearer **5 req/s**
than the 20 this file used to claim, so one loop in a script is a denial of
service, and comfortably sooner than assumed. At
`dash.cloudflare.com` → your domain → **Security → WAF → Rate
limiting rules** (main dashboard, no Zero Trust needed), match
`http.host eq "api.example.com"`, count by IP, and cap at **60 requests/minute**.

The rate belongs in the "When rate exceeds" fields, *not* in the match
expression. 60 rather than 30 because the app itself can legitimately burst:
seven sliders that each re-route on release, plus off-route reroutes every 8 s.

### 7. Keep it running, and stop the laptop sleeping

- **Windows:** two **Task Scheduler** tasks — `…\.venv\Scripts\python.exe` with
  argument `…\server\serve.py`, and `cloudflared.exe` with `tunnel run scenic` —
  both *At startup*, *Run whether user is logged on or not*, restart-on-failure.
  (`cloudflared service install` also works but runs as SYSTEM and expects its
  config under `C:\Windows\System32\config\systemprofile\.cloudflared\`.) Then,
  as Administrator: `powercfg /change standby-timeout-ac 0`,
  `powercfg /change hibernate-timeout-ac 0`, and to make the lid do nothing:
  `powercfg /setacvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 5ca83367-6e45-459f-a27b-476b1d01c936 0 ; powercfg /setactive SCHEME_CURRENT`.
  Set Windows Update **active hours** too — it will reboot the box eventually,
  which is exactly what the startup tasks are for.
- **macOS:** a `launchd` agent in `~/Library/LaunchAgents/` with `RunAtLoad` +
  `KeepAlive` running `…/server/serve.py`, plus System Settings → Battery →
  Options → never sleep on power.

Reboot and confirm `/api/health` answers without you touching anything. That is
the only real test of the setup.

## Option B — Docker

```sh
docker build -f server/Dockerfile -t scenic-api .   # build context = repo root
docker run -p 5057:5057 --restart unless-stopped scenic-api
```

## Option C — Bare VPS

Same as the laptop, minus the tunnel. Run `server/serve.py` under systemd, and
put **Caddy** or **Cloudflare** in front for HTTPS. Size it for ~2 GB of RAM,
which rules out the cheapest $5/mo tiers at most US providers (Hetzner is the
exception at roughly €4).

## Option D — No Cloudflare

- **Tailscale**: `tailscale serve --bg http://localhost:5057` publishes it over
  real HTTPS at `https://<machine>.<tailnet>.ts.net`, reachable only from your
  own devices. No domain, no ports, no public exposure, and rate limiting stops
  mattering. The trade is a `ts.net` hostname and a VPN profile on the phone.
- **Caddy + port forwarding**: point an A record at your home IP, forward 443,
  and let Caddy get a Let's Encrypt cert (TLS-ALPN needs only 443, not 80).
  Fully self-hosted, but it exposes your home IP, needs dynamic DNS, and is
  impossible behind CGNAT.

## Diagnosing it

| Response | Meaning | Fix |
| --- | --- | --- |
| `200` | working | — |
| `404 Not Found` | working, but on a path that does not exist | `GET /` describes the endpoints |
| `502` | tunnel connected, app not running | start `serve.py` |
| `530` (Error 1033) | DNS points at the tunnel, no `cloudflared` connected | start the tunnel |
| DNS failure | the name does not resolve | DNS, or a blocked `trycloudflare.com` |

## Notes

- **One process, a few threads — but routing does not run in parallel.** waitress
  loads the graph once and serves with 4 threads, which keeps the server
  responsive while a route computes. It does not multiply throughput: measured on
  the real graph, 4 concurrent routes take 0.482 s versus 0.519 s serial, a
  **1.08x** speedup. (This file used to say scipy releases the GIL during
  routing. It does not, for `scipy.sparse.csgraph.dijkstra`.) More throughput
  means more processes, at another ~1 GB of graph each.
  Measured **~0.8 GB physical footprint**; plan for
  2 GB free, so a 4 GB machine is fine and 8 GB comfortable. Note that plain RSS
  understates this badly on macOS, which compresses much of it out.
  (It was ~1.0 GB until the router's node-pair lookup stopped being a dict of
  three quarters of a million boxed tuples — that alone was 204 MB.)
- **~85 ms per route**, so a request for both options lands under 200 ms locally
  and ~500 ms through the tunnel. Nearly all of that is the Dijkstra itself,
  which solves to every node in the state; point-to-point search
  (A*/bidirectional), or scipy's `limit=` argument, is where a further speedup
  would come from if it is ever needed — and since the threads don't overlap
  routing, that is also the cheapest way to raise the concurrent ceiling.
- **Responses are gzipped** (`flask-compress`), ~3–4× smaller. Route GeoJSON is
  large and repetitive, and on a home connection your *upload* bandwidth is the
  real ceiling — compression multiplies how many routes the laptop can serve.
- **Warm at startup.** The graph and its lookups are built when the server boots,
  so the first request is already fast (no cold penalty after a restart).
- **Data location** comes from the `SCENIC_DATA` env var (default
  `data/processed`); set it only if your parquets live elsewhere.
- **Cloudflare stores nothing.** It is a doorway, not a copy: if the laptop
  sleeps or either process stops, the API is down within seconds.

## Pointing the clients at it

- **iOS app:** the deployed URL is baked into the bundle as `ScenicAPIBaseURL`
  in `ios/project.yml`. It has to be an Info.plist value rather than a scheme
  environment variable, because an env var only exists while Xcode owns the
  process — an app launched from the home screen, or relaunched by iOS after
  being jettisoned mid-drive, would otherwise fall back to localhost and fail
  every request. `SCENIC_API` still overrides it for local development.
  Note that on a free Apple developer account a sideloaded build expires after
  7 days and needs reinstalling.
- **Web demo:** a static page (e.g. on `jameskouvlis.com`) can call the same
  `https://api.jameskouvlis.com/api/route` endpoint — a live, clickable resume
  artifact backed by the laptop. (The old MapLibre demo lives in git history at
  commit `82044e2` and is cheap to revive on this API.)
