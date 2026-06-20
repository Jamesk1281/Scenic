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

COPY pipeline/router.py pipeline/
COPY server/app.py server/
COPY web/ web/
COPY data/processed/graph_edges.parquet data/processed/graph_nodes.parquet data/processed/

EXPOSE 5057
# gunicorn with a long timeout; the graph loads once per worker at boot.
CMD ["gunicorn", "--chdir", "server", "--workers", "1", "--threads", "4", \
     "--timeout", "120", "--bind", "0.0.0.0:5057", "app:app"]
