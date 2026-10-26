"""HTTP server for the dashboard: JSON API, server-sent events, static files.

Standard library only (http.server, sqlite3). Every asset is served from
gui/static; nothing is fetched from the internet.
"""
from __future__ import annotations

import json
import mimetypes
import queue
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from faultdiag import calib
from faultdiag.harness import dev_scenarios
from faultdiag.hwrun import hardware_scenarios
from faultdiag.logsetup import get_logger
from faultdiag.methods import METHODS
from faultdiag.serialbench import BenchError

from . import experiments
from .live import Hub, LiveManager
from .store import TRACKER, Store

log = get_logger("gui.server")
STATIC = Path(__file__).resolve().parent / "static"
REPLAY_LIMIT = 2000


class App:
    def __init__(self, results_dir: Path, db_path: Path, cal_dir: Path | None = None):
        self.results = results_dir
        self.cal_dir = cal_dir or calib.CAL_DIR
        self.store = Store(db_path)
        self.interrupted_on_start = self.store.recover_after_restart()
        if self.interrupted_on_start:
            log.warning("sessions interrupted by the last server stop: %s", self.interrupted_on_start)
        self.hub = Hub(self.store)
        self.live = LiveManager(self.store, self.hub, results_dir, cal_dir=self.cal_dir)


class HttpError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"device": p.device, "description": p.description} for p in list_ports.comports()]


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by make_server
    server_version = "faultdiag-gui"

    def log_message(self, fmt, *args):  # quiet access log; errors are logged where they happen
        log.debug("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------ helpers
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, default=str).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            data = json.loads(self.rfile.read(n))
        except json.JSONDecodeError as exc:
            raise HttpError(400, f"bad JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise HttpError(400, "body must be a JSON object")
        return data

    def _dispatch(self, method: str):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if method == "GET" and url.path == "/api/events":
                return self._sse(q)
            out = self.route(method, url.path, q)
            if isinstance(out, tuple):  # (bytes, content type, extra headers)
                return self._send(200, *out)
            return self._json(out)
        except HttpError as exc:
            return self._json({"error": str(exc)}, exc.code)
        except (LookupError,) as exc:
            return self._json({"error": str(exc)}, 404)
        except (ValueError, TypeError) as exc:
            return self._json({"error": str(exc)}, 400)
        except (RuntimeError, BenchError) as exc:
            return self._json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001 - reported to the browser and the terminal
            log.exception("%s %s failed", method, self.path)
            return self._json({"error": f"server error: {exc!r}"}, 500)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # ------------------------------------------------------------ routes
    def route(self, method: str, path: str, q: dict):
        app = self.app
        if method == "GET" and (path == "/" or path.startswith("/static/")):
            return self._static("index.html" if path == "/" else path[len("/static/"):])
        if method == "GET":
            if path == "/api/status":
                return {**app.live.status(), "interrupted_on_start": app.interrupted_on_start,
                        "last_event_id": app.store.last_event_id()}
            if path == "/api/options":
                return {"methods": list(METHODS), "sim_scenarios": dev_scenarios(),
                        "bench_scenarios": hardware_scenarios(None, final=False), "ports": _ports()}
            if path == "/api/sessions":
                return app.store.sessions(int(q.get("limit", 50)))
            m = re.fullmatch(r"/api/sessions/([\w-]+)/events", path)
            if m:
                if not app.store.session(m.group(1)):
                    raise LookupError(f"no session {m.group(1)}")
                return app.store.events(int(q.get("after", 0)), m.group(1), int(q.get("limit", 5000)))
            if path == "/api/runs":
                return experiments.list_runs(app.results)
            if path == "/api/runs/summary":
                return experiments.summary(app.results, q["run"])
            if path == "/api/runs/sessions":
                return experiments.session_index(app.results, q["run"])
            if path == "/api/runs/session":
                return experiments.session(app.results, q["run"], int(q["index"]))
            if path == "/api/runs/csv":
                name = q["run"].replace("/", "_") + ".csv"
                return (experiments.to_csv(app.results, q["run"]).encode(), "text/csv",
                        {"Content-Disposition": f'attachment; filename="{name}"'})
            if path == "/api/models":
                return experiments.models(app.results)
            if path == "/api/calibration":
                return experiments.calibration(app.cal_dir, app.results / "mock-serial" / "calibration")
            if path == "/api/tracker/export.md":
                return (app.store.tracker_markdown().encode(), "text/markdown; charset=utf-8",
                        {"Content-Disposition": 'attachment; filename="tracker.md"'})
            m = re.fullmatch(r"/api/tracker/(\w+)", path)
            if m:
                return app.store.tracker(m.group(1))
        if method == "POST":
            body = self._body()
            if path == "/api/bench/connect":
                return app.live.connect(body.get("mode", "mock"), body.get("port"), body.get("injector_port"))
            if path == "/api/bench/disconnect":
                app.live.disconnect()
                return {"ok": True}
            if path == "/api/session/start":
                return app.live.start(body.get("mode", "sim"), body.get("method", "bayes"), body.get("model") or None,
                                      body.get("scenario") or None, body.get("seed"), body.get("server", "ollama"),
                                      bool(body.get("think")), bool(body.get("allow_stale_calibration")))
            if path == "/api/session/stop":
                return {"stopping": app.live.stop()}
            if path == "/api/safe_state":
                return app.live.safe_state()
            if path == "/api/resume":
                return app.live.resume()
            if path == "/api/probe":
                try:
                    volts = float(body["volts"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise HttpError(400, "volts must be a number") from exc
                if not app.live.probe(volts):
                    raise HttpError(409, "no human probe is waiting")
                return {"accepted": volts}
            m = re.fullmatch(r"/api/tracker/(\w+)", path)
            if m:
                return app.store.tracker_create(m.group(1), body)
        m = re.fullmatch(r"/api/tracker/(\w+)/(\d+)", path)
        if m and method == "PUT":
            return app.store.tracker_update(m.group(1), int(m.group(2)), self._body())
        if m and method == "DELETE":
            app.store.tracker_delete(m.group(1), int(m.group(2)))
            return {"deleted": int(m.group(2))}
        raise HttpError(404, f"no route {method} {path}")

    def _static(self, name: str):
        p = (STATIC / name).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            raise HttpError(404, f"no file {name}")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        return p.read_bytes(), ctype, {}

    def _sse(self, q: dict):
        """Replays stored events after Last-Event-ID (or ?after=), then streams new ones."""
        after = int(self.headers.get("Last-Event-ID") or q.get("after") or 0)
        sub = self.app.hub.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            if after == 0:  # a new tab: start from the most recent session
                recent = self.app.store.sessions(1)
                after = 0
                if recent:
                    first = self.app.store.events(0, recent[0]["id"], 1)
                    after = first[0]["id"] - 1 if first else self.app.store.last_event_id()
                else:
                    after = max(0, self.app.store.last_event_id() - 50)
            last = after
            for ev in self.app.store.events(after, None, REPLAY_LIMIT):
                self._write_event(ev)
                last = ev["id"]
            self.wfile.flush()
            while True:
                try:
                    ev = sub.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if ev["id"] <= last:
                    continue
                self._write_event(ev)
                self.wfile.flush()
                last = ev["id"]
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.app.hub.unsubscribe(sub)

    def _write_event(self, ev: dict):
        self.wfile.write(f"id: {ev['id']}\ndata: {json.dumps(ev, default=str)}\n\n".encode())


def make_server(host: str, port: int, results_dir: Path, db_path: Path, cal_dir: Path | None = None):
    app = App(results_dir, db_path, cal_dir)
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv, app


_ = TRACKER  # tables are validated in the store
