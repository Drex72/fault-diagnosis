"""Start the dashboard: python -m gui [--port 8765] [--open]

Serves on 127.0.0.1 only. Everything (pages, scripts, data) comes from this
laptop, so it works with no internet.
"""
from __future__ import annotations

import argparse
import os
import webbrowser
from pathlib import Path

from faultdiag.logsetup import get_logger

from .server import make_server

ROOT = Path(__file__).resolve().parent.parent
log = get_logger("gui")


def main():
    ap = argparse.ArgumentParser(description="fault-diagnosis dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", type=Path, default=ROOT / "gui" / "data" / "gui.sqlite")
    ap.add_argument("--results", type=Path, default=Path(os.environ.get("FAULTDIAG_RESULTS_DIR", ROOT / "results")))
    ap.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    args = ap.parse_args()
    srv, app = make_server(args.host, args.port, args.results, args.db)
    url = f"http://{args.host}:{srv.server_address[1]}/"
    print(f"dashboard on {url}  (Ctrl+C to stop; data in {args.db})", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stopping: any running session is stopped; the bench firmware switches its loads off "
              "when the heartbeat stops", flush=True)
    finally:
        app.live.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
