"""Run the Scenic routing API.

Uses waitress — a production WSGI server that runs the same on Windows, macOS,
Linux, and in Docker (unlike gunicorn, which is Unix-only). One worker process
with a few threads: the ~1 GB graph is loaded once, and the threads keep the
server responsive (accepting connections, serving /api/health) while a route is
being computed.

They do NOT make routing parallel. This used to claim scipy releases the GIL
during the Dijkstra, so requests overlapped; measured on the real graph, four
concurrent routes take 0.482 s against 0.519 s for four serial ones — a 1.08x
speedup, i.e. essentially serialized. Real throughput is therefore about one
route per ~95 ms of CPU (~5 req/s for the two-Dijkstra endpoint), not 4x that.
Going faster means more *processes*, and each one is another ~1 GB copy of the
graph — the trade to make deliberately, not to assume away.

    python server/serve.py                          # 0.0.0.0:5057
    PORT=8080 python server/serve.py                # custom port
    SCENIC_HOST=127.0.0.1 python server/serve.py    # localhost only

Importing `app` below loads the graph immediately, so the server is warm before
it accepts the first request.
"""

import os

from waitress import serve

from app import app  # noqa: E402 — importing builds the graph (warm startup)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    # 0.0.0.0 by default so a phone on the same Wi-Fi can reach a dev server.
    # Behind a tunnel, nothing off-box ever connects directly — cloudflared
    # reaches the app over loopback — so SCENIC_HOST=127.0.0.1 is worth setting
    # on a deployed box: it keeps the local network out and sidesteps the
    # Windows Firewall prompt entirely.
    host = os.environ.get("SCENIC_HOST", "0.0.0.0")
    print(f"Scenic API serving on http://{host}:{port}")
    serve(app, host=host, port=port, threads=4)
