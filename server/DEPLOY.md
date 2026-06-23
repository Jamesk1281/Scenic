# Deploying the Scenic API

The whole backend is self-hosted and free per request (no Google/Apple/Mapbox
metering). Costs are flat, not per-user: the same box serves 3 or 3,000 people.
It runs happily on **a spare laptop** (the cheapest option — recommended while
usership is low) or a **$5–10/mo VPS** (always-on, when you outgrow the laptop).

## What ships

Only a few files are needed to *serve* routes — never the OSM/elevation data or
the pipeline scripts:

- **Data** (build locally, copy over): `data/processed/graph_edges.parquet` +
  `graph_nodes.parquet` (~75 MB total).
- **Code**: `server/app.py`, and `pipeline/router.py` + `common.py` + `score.py`
  (router imports the latter two for shared constants and the scoring weights).

Regenerate the graph locally with the pipeline (see the top-level README) when
the scoring changes, then copy the two parquet files over.

## Option A — Spare laptop + Cloudflare tunnel (recommended for now)

A laptop that stays on can be the backend, with a tunnel exposing it publicly
without touching your router or opening any ports (the laptop dials *out* to
Cloudflare, which relays traffic back — and provides HTTPS, which iOS requires).

**One-time setup on the serving laptop** (works on Windows, macOS, and Linux —
the server uses waitress, which is cross-platform):

```sh
# get the project + the two graph parquets into data/processed/ (any method:
# git clone, AirDrop, copy/paste — the parquets are gitignored so copy them too)
cd Scenic

# macOS / Linux:
python3 -m venv .venv && .venv/bin/pip install -r server/requirements-serve.txt

# Windows (PowerShell):
#   python -m venv .venv ; .venv\Scripts\pip install -r server\requirements-serve.txt
```

**Run the API** (warm and ready on `0.0.0.0:5057`):

```sh
.venv/bin/python server/serve.py          # macOS / Linux
# .venv\Scripts\python server\serve.py    # Windows
```

**Expose it** (second terminal). Quick, throwaway URL for testing:

```sh
brew install cloudflared
cloudflared tunnel --url http://localhost:5057      # → https://<random>.trycloudflare.com
```

Stable URL on your own domain (e.g. `api.jameskouvlis.com`) — do this once you
want it permanent: add the domain to a free Cloudflare account, then
`cloudflared tunnel login`, `cloudflared tunnel create scenic`, map a hostname
to `http://localhost:5057`, and run `cloudflared tunnel run scenic`. Cloudflare's
docs walk it step by step.

**Keep it running across reboots/crashes, and stop the laptop sleeping:**

- **Windows:** the simplest is **Task Scheduler** — create a task that runs
  `…\.venv\Scripts\python …\server\serve.py` "at log on" with "restart on
  failure", and a second one for the `cloudflared` command. Then Settings →
  System → Power → set "When plugged in, turn off screen / sleep" to **Never**.
  (For a hands-off service that runs even when logged out, [NSSM](https://nssm.cc)
  wraps either command as a Windows service.)
- **macOS:** a `launchd` agent in `~/Library/LaunchAgents/` with `RunAtLoad` +
  `KeepAlive` running `…/server/serve.py`, plus System Settings → Battery →
  Options → never sleep on power.

## Option B — Docker

```sh
docker build -f server/Dockerfile -t scenic-api .   # build context = repo root
docker run -p 5057:5057 --restart unless-stopped scenic-api
```

## Option C — Bare VPS

Same as the laptop, minus the tunnel. Run `server/serve.py` under systemd, and
put **Caddy** or **Cloudflare** in front for HTTPS.

## Notes

- **One process, a few threads.** waitress loads the ~1–1.5 GB graph once and
  serves with 4 threads; scipy releases the GIL during routing, so requests
  overlap without a second copy of the graph. ~2 GB RAM is plenty.
- **Responses are gzipped** (`flask-compress`), ~3–4× smaller. Route GeoJSON is
  large and repetitive, and on a home connection your *upload* bandwidth is the
  real ceiling — compression multiplies how many routes the laptop can serve.
- **Warm at startup.** The graph and its lookups are built when the server boots,
  so the first request is already fast (no cold penalty after a restart).
- **Data location** comes from the `SCENIC_DATA` env var (default
  `data/processed`); set it only if your parquets live elsewhere.

## Pointing the clients at it

- **iOS app:** it reads `SCENIC_API` (defaults to `http://127.0.0.1:5057`). Set
  it to the tunnel's **https** origin for a real device — App Transport Security
  requires TLS, which the Cloudflare tunnel provides.
- **Web demo:** a static page (e.g. on `jameskouvlis.com`) can call the same
  `https://api.jameskouvlis.com/api/route` endpoint — a live, clickable resume
  artifact backed by the laptop. (The old MapLibre demo lives in git history at
  commit `82044e2` and is cheap to revive on this API.)
