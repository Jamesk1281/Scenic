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

**One-time setup on the serving laptop:**

```sh
git clone <repo> && cd Scenic
python3 -m venv .venv
.venv/bin/pip install -r server/requirements-serve.txt
# copy the two graph parquets from your dev machine into data/processed/, e.g.:
#   scp data/processed/graph_*.parquet you@laptop:~/Scenic/data/processed/
```

**Run the API:**

```sh
server/serve.sh            # gunicorn on 0.0.0.0:5057, warm and ready
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

**Keep it running across reboots/crashes (macOS launchd).** Save this as
`~/Library/LaunchAgents/app.scenic.api.plist`, edit the paths, then
`launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key>            <string>app.scenic.api</string>
  <key>ProgramArguments</key> <array><string>/Users/YOU/Scenic/server/serve.sh</string></array>
  <key>KeepAlive</key>        <true/>   <!-- restart if it dies -->
  <key>RunAtLoad</key>        <true/>   <!-- start on login -->
</dict></plist>
```

(Do the same for `cloudflared tunnel run scenic` so the tunnel comes up too.)
Also set the laptop to never sleep on power: System Settings → Battery → Options.

## Option B — Docker

```sh
docker build -f server/Dockerfile -t scenic-api .   # build context = repo root
docker run -p 5057:5057 --restart unless-stopped scenic-api
```

## Option C — Bare VPS

Same as the laptop, minus the tunnel. Run `server/serve.sh` (or the gunicorn
line inside it) under systemd, and put **Caddy** or **Cloudflare** in front for
HTTPS.

## Notes

- **One worker.** Each gunicorn worker loads the full graph (~1–1.5 GB
  resident), so `serve.sh` uses `--workers 1 --threads 4`: threads let requests
  overlap (scipy releases the GIL during routing) without a second copy of the
  graph. Add workers only if you have RAM to spare. ~2 GB RAM is plenty.
- **Responses are gzipped** (`flask-compress`), ~3–4× smaller. Route GeoJSON is
  large and repetitive, and on a home connection your *upload* bandwidth is the
  real ceiling — compression multiplies how many routes the laptop can serve.
- **Warm at startup.** The graph and its lookups are built when the server boots,
  so the first request is already fast (no cold penalty after a restart).
- **Data location** comes from the `SCENIC_DATA` env var (default
  `data/processed`); it's not a CLI arg, so it works the same under gunicorn.

## Pointing the clients at it

- **iOS app:** it reads `SCENIC_API` (defaults to `http://127.0.0.1:5057`). Set
  it to the tunnel's **https** origin for a real device — App Transport Security
  requires TLS, which the Cloudflare tunnel provides.
- **Web demo:** a static page (e.g. on `jameskouvlis.com`) can call the same
  `https://api.jameskouvlis.com/api/route` endpoint — a live, clickable resume
  artifact backed by the laptop. (The old MapLibre demo lives in git history at
  commit `82044e2` and is cheap to revive on this API.)
```
