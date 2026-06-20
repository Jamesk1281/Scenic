# Deploying the Scenic API

The whole backend is self-hosted and free per request (no Google/Apple/Mapbox
metering), so it runs comfortably on a $5–10/mo VPS. Costs are flat, not
per-user: the same box serves 3 or 3,000 people.

## What ships

Only two artifacts are needed to *serve* routes (not the whole pipeline):

- `data/processed/graph_edges.parquet` + `graph_nodes.parquet` (~tens of MB)
- `server/app.py` + `pipeline/router.py` + `web/`

Regenerate the graph locally with the pipeline (see top-level README), then ship
it; the server never touches OSM/elevation data at runtime.

## Docker (simplest)

```sh
docker build -f server/Dockerfile -t scenic-api .
docker run -p 5057:5057 scenic-api
```

## Bare VPS

```sh
pip install -r server/requirements-serve.txt
gunicorn --chdir server --workers 1 --threads 4 --timeout 120 \
         --bind 0.0.0.0:5057 app:app
```

Notes:
- Use **1 worker**: each worker loads the full graph (~hundreds of MB resident),
  so prefer threads over processes. 4GB RAM is plenty for Massachusetts.
- Put **Cloudflare** (free tier) in front for TLS + caching; a front-page-of-Reddit
  day is then survivable on the small box.
- First request after boot is slow (graph load on first route); hit `/api/health`
  on deploy to warm it.

## Hosting the web demo

`web/index.html` is static and can live on **GitHub Pages / Cloudflare Pages**
(free). Point `const API` in `index.html` at the VPS origin and enable CORS
(already on via flask-cors). The resume link is then just that Pages URL.
