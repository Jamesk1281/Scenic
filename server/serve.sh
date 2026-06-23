#!/usr/bin/env bash
#
# Run the Scenic routing API for a local / spare-laptop deployment.
#
# One-time setup on the serving machine:
#   git clone <repo> && cd Scenic
#   python3 -m venv .venv
#   .venv/bin/pip install -r server/requirements-serve.txt
#   # copy the prebuilt graph from your dev machine into data/processed/:
#   #   graph_edges.parquet  +  graph_nodes.parquet   (~75 MB, that's all the
#   #   server needs — no OSM/elevation data at runtime)
#
# Then to serve:   server/serve.sh        (override port with PORT=8080 server/serve.sh)
#
# Expose it to the internet with a Cloudflare tunnel in a second terminal:
#   cloudflared tunnel --url http://localhost:5057      # instant free https URL
# (see server/DEPLOY.md for a stable named tunnel on your own domain).

set -euo pipefail
cd "$(dirname "$0")/.."          # repo root, wherever this script lives

source .venv/bin/activate

# One worker keeps the ~1 GB graph in memory a single time; threads let requests
# overlap (scipy releases the GIL during the heavy routing). Add --workers only
# if you have RAM to spare — each worker loads another full copy of the graph.
exec gunicorn --chdir server \
     --workers 1 --threads 4 --timeout 120 \
     --bind "0.0.0.0:${PORT:-5057}" \
     app:app
