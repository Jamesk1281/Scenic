# Scenic routing API. Build context is the repo root:
#   docker build -f server/Dockerfile -t scenic-api .
#   docker run -p 5057:5057 scenic-api
#
# Expects the prebuilt graph (data/processed/graph_*.parquet) to be present in
# the build context — generate it locally with the pipeline first, or mount it
# at runtime with -v $(pwd)/data:/app/data and drop the COPY data line.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev gdal-bin && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Runtime-only deps (no osmium/matplotlib needed to *serve* routes)
COPY server/requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

# router.py imports common.py (shared constants) and score.py (WEIGHTS/STRETCH
# it re-blends scores from), so all three must ship.
COPY pipeline/router.py pipeline/common.py pipeline/score.py pipeline/
COPY server/app.py server/serve.py server/
COPY data/processed/graph_edges.parquet data/processed/graph_nodes.parquet data/processed/

EXPOSE 5057
# waitress (cross-platform); the graph loads once at startup.
CMD ["python", "server/serve.py"]
