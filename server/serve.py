"""Run the Scenic routing API.

Uses waitress — a production WSGI server that runs the same on Windows, macOS,
Linux, and in Docker (unlike gunicorn, which is Unix-only). One worker process
with a few threads: the ~1 GB graph is loaded once, and threads let requests
overlap (scipy releases the GIL during the heavy routing).

    python server/serve.py             # serve on 0.0.0.0:5057
    PORT=8080 python server/serve.py   # custom port

Importing `app` below loads the graph immediately, so the server is warm before
it accepts the first request.
"""

import os

from waitress import serve

from app import app  # noqa: E402 — importing builds the graph (warm startup)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    print(f"Scenic API serving on http://0.0.0.0:{port}")
    serve(app, host="0.0.0.0", port=port, threads=4)
