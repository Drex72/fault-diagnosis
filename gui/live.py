"""Live sessions for the GUI: one at a time, watched from any number of tabs.

Every step, engine update, model reply, warning and bench event is stored in
SQLite and pushed to the browsers over server-sent events. A shadow Bayesian
engine follows the readings of whichever method runs, so the hypothesis
chart works for every method.

Modes: "sim" (SimBench), "mock" (mock serial devices, labelled sim) and
"hardware" (the real bench and injector). On hardware the scenario is chosen
by the server and hidden until the session ends.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from faultdiag import calib
from faultdiag.engine import BayesEngine
from faultdiag.guard import PolicyGuard, load_policy
from faultdiag.harness import SCENARIOS, dev_scenarios, make_bench, state_for_faults, truth_elements
from faultdiag.hwrun import hardware_scenarios
from faultdiag.injector import HW_FAULTS, InjectorClient
from faultdiag.logsetup import get_logger
from faultdiag.methods import build_methods
from faultdiag.mockdevice import MockBenchDevice, MockInjectorDevice, MockLink
from faultdiag.runmeta import LAGOS, lagos_now, run_meta
from faultdiag.serialbench import BenchError, BenchInterrupted, SerialBench, SerialLink, ToolFailures

log = get_logger("gui")


class Hub:
    """Stores each event, then fans it out to every connected browser."""

    def __init__(self, store):
        self.store = store
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()

    def publish(self, session_id, kind: str, payload: dict) -> dict:
        ev = self.store.add_event(session_id, kind, payload)
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(ev)
            except queue.Full:  # a stalled tab: drop it, it reconnects and replays from the store
                self.unsubscribe(q)
        return ev

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q) -> None:
        with self.lock:
            self.clients.discard(q)


class SessionStopped(Exception):
    pass


class ObservedGuard(PolicyGuard):
    def __init__(self, bench, policy, on_step, stop: threading.Event):
        super().__init__(bench, policy)
        self.on_step, self.stop = on_step, stop

    def call(self, tool, args=None):
        if self.stop.is_set():
            raise SessionStopped("stopped from the GUI")
        entry = super().call(tool, args)
        self.on_step(entry, self)
        return entry


class GuiProber:
    """human_probe on mock or hardware: the technician types the reading in the GUI."""

    def __init__(self, hub, hint=None):
        self.hub, self.hint = hub, hint
        self.session_id = None
        self._event = threading.Event()
        self._value = None
        self.waiting = False

    def __call__(self, a, b, instruction, timeout_s):
        self._event.clear()
        self._value = None
        self.waiting = True
        hint = self.hint(a, b) if self.hint else None
        self.hub.publish(self.session_id, "probe_request",
                         {"point_a": a, "point_b": b, "timeout_s": timeout_s, "hint": hint,
                          "instruction": instruction or f"Measure the DC voltage from {a} (red) to {b} (black)."})
        got = self._event.wait(timeout_s)
        self.waiting = False
        self.hub.publish(self.session_id, "probe_done", {"volts": self._value if got else None})
        return self._value if got else None

    def submit(self, volts: float) -> bool:
        if not self.waiting:
            return False
        self._value = float(volts)
        self._event.set()
        return True

    def cancel(self) -> None:
        self._event.set()


class _LogRelay(logging.Handler):
    """Warnings raised in the session thread (invalid calls, fallbacks, bench errors) go to the GUI."""

    def __init__(self, hub, session_id, thread_id):
        super().__init__(logging.WARNING)
        self.hub, self.session_id, self.thread_id = hub, session_id, thread_id

    def emit(self, record):
        if record.thread == self.thread_id:
            self.hub.publish(self.session_id, "log", {"level": record.levelname, "logger": record.name,
                                                      "msg": record.getMessage()})


class LiveManager:
    def __init__(self, store, hub, results_dir: Path, policy: dict | None = None, cal_dir: Path | None = None):
        self.store, self.hub, self.results_dir = store, hub, results_dir
        self.policy = policy or load_policy()
        self.cal_dir = cal_dir or calib.CAL_DIR
        self.lock = threading.Lock()
        self.current: dict | None = None
        self.link: dict | None = None  # connected bench: {"mode", "bench", "injector", "devices"}

    # ------------------------------------------------------------ bench connection
    def _on_bench_event(self, msg):
        self.hub.publish(self.current["id"] if self.current else None, "bench", msg)

    def connect(self, mode: str, port: str | None = None, injector_port: str | None = None) -> dict:
        with self.lock:
            if self.current:
                raise RuntimeError("a session is running")
            self._disconnect()
            prober = GuiProber(self.hub)
            if mode == "mock":
                rng = np.random.default_rng()
                bdev = MockBenchDevice(seed=int(rng.integers(1 << 30)), cooldown_scale=0.05)
                idev = MockInjectorDevice(on_change=lambda f: bdev.set_state(state_for_faults(f, rng)))
                prober.hint = lambda a, b: bdev.sim.execute("human_probe", {"point_a": a, "point_b": b})["data"]["volts"]
                bench = SerialBench(MockLink(bdev), self.policy, prober=prober, on_event=self._on_bench_event)
                injector = InjectorClient(MockLink(idev))
            elif mode == "hardware":
                if not port or not injector_port:
                    raise ValueError("hardware needs the bench port and the injector port")
                bench = SerialBench(SerialLink(port), self.policy, prober=prober, on_event=self._on_bench_event)
                try:
                    injector = InjectorClient(SerialLink(injector_port))
                except Exception:
                    bench.close()
                    raise
            else:
                raise ValueError("mode must be mock or hardware")
            self.link = {"mode": mode, "bench": bench, "injector": injector, "prober": prober,
                         "port": port, "injector_port": injector_port}
        self.hub.publish(None, "bench", {"event": "connected", **self.bench_status()})
        return self.bench_status()

    def _disconnect(self):
        if self.link:
            for part in ("injector", "bench"):
                try:
                    self.link[part].close()
                except Exception as exc:  # noqa: BLE001
                    log.warning("closing %s: %s", part, exc)
            self.link = None

    def disconnect(self) -> None:
        with self.lock:
            if self.current:
                raise RuntimeError("a session is running")
            self._disconnect()
        self.hub.publish(None, "bench", {"event": "disconnected"})

    def bench_status(self) -> dict:
        if not self.link:
            return {"connected": False, "mode": None}
        b = self.link["bench"]
        return {"connected": b.connected and not b._stop.is_set(), "mode": self.link["mode"],
                "firmware": b.firmware, "calibration": b.calibration_id, "interrupted": b.interrupted,
                "inhibited": b.inhibited, "port": self.link["port"],
                "injector_fw": (self.link["injector"].hello or {}).get("fw"),
                "calibration_problem": calib.check(b.calibration_id, self.cal_dir)
                if self.link["mode"] == "hardware" else None}

    # ------------------------------------------------------------ sessions
    def status(self) -> dict:
        cur = self.current
        return {"bench": self.bench_status(), "session": None if not cur else
                {k: cur[k] for k in ("id", "mode", "source", "method", "model", "started")} |
                {"scenario": None if cur["hidden"] else cur["scenario"],
                 "probe_waiting": bool(cur.get("prober") and cur["prober"].waiting)}}

    def start(self, mode: str, method: str, model: str | None = None, scenario: str | None = None,
              seed: int | None = None, server: str = "ollama", think: bool = False,
              allow_stale_calibration: bool = False) -> dict:
        with self.lock:
            if self.current:
                raise RuntimeError("a session is already running; stop it first")
            rng = np.random.default_rng()
            seed = int(rng.integers(0, 100_000)) if seed is None else int(seed)
            if mode == "sim":
                scenario = scenario or str(rng.choice(dev_scenarios()))
                if scenario not in dev_scenarios():
                    raise ValueError(f"{scenario} is not a development scenario (the frozen H3, H4 are never run here)")
                bench, source, hidden = make_bench(scenario, seed), "sim", False
            elif mode in ("mock", "hardware"):
                if not self.link or self.link["mode"] != mode:
                    raise ValueError(f"connect the {mode} bench first")
                allowed = hardware_scenarios(None, final=False)
                if mode == "hardware" or not scenario:
                    scenario = str(rng.choice(allowed))
                if scenario not in allowed:
                    raise ValueError(f"{scenario} cannot be run on the bench")
                b = self.link["bench"]
                if b.interrupted:  # the board reset: reconnect before anything else
                    raise BenchError("the bench reset since it was connected; reconnect it")
                if b.inhibited:  # cleared only by a person, never automatically
                    raise BenchError("the safe state is latched; check the bench, then press Clear safe state")
                if mode == "hardware":
                    problem = calib.check(b.calibration_id, self.cal_dir)
                    if problem and not allow_stale_calibration:
                        raise BenchError(f"refusing to run: {problem}")
                st = b.self_test()
                if not st.get("passed"):
                    failing = [k for k, v in st.get("critical", {}).items() if not v]
                    raise BenchError(f"firmware self-test failed ({', '.join(failing)}); no load will be energised")
                self.link["injector"].set(HW_FAULTS[scenario])
                spec = SCENARIOS[scenario]
                bench = (ToolFailures(b, spec["failures"], np.random.default_rng(seed + 10_000))
                         if spec.get("failures") else b)
                source, hidden = ("sim" if mode == "mock" else "hardware"), mode == "hardware"
            else:
                raise ValueError("mode must be sim, mock or hardware")
            sid = datetime.now(LAGOS).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
            methods, meta = build_methods([method], model, server, think=think,
                                          on_reply=lambda msg: self._on_reply(sid, msg))
            prober = self.link["prober"] if mode != "sim" and self.link else None
            if prober:
                prober.session_id = sid
            self.current = {"id": sid, "mode": mode, "source": source, "method": method, "model": model,
                            "scenario": scenario, "hidden": hidden, "seed": seed, "started": lagos_now(),
                            "stop": threading.Event(), "prober": prober, "meta": meta.get(method, {})}
            self.store.new_session(sid, mode=mode, source=source, method=method, model=model,
                                   scenario=None if hidden else scenario)
            self.hub.publish(sid, "session_start", {"id": sid, "mode": mode, "source": source, "method": method,
                                                    "model": model, "seed": seed, "budget": self.policy["budget"],
                                                    "scenario": None if hidden else scenario})
            t = threading.Thread(target=self._run, args=(self.current, methods[method], bench), daemon=True,
                                 name=f"session-{sid}")
            self.current["thread"] = t
            t.start()
            return {"id": sid}

    def _on_reply(self, sid, msg):
        calls = [{"name": c["function"]["name"], "args": c["function"].get("arguments")}
                 for c in (msg.get("tool_calls") or [])]
        self.hub.publish(sid, "agent", {"content": (msg.get("content") or "")[:4000], "tool_calls": calls})

    def _run(self, cur, fn, bench):
        sid = cur["id"]
        observer = BayesEngine()
        observer.reset(cur["seed"])
        relay = _LogRelay(self.hub, sid, threading.get_ident())
        pkg_log = logging.getLogger("faultdiag")
        pkg_log.addHandler(relay)

        def on_step(entry, guard):
            self.hub.publish(sid, "step", {**{k: entry.get(k) for k in ("step", "tool", "args", "status", "data",
                                                                         "error", "reason", "cost", "spent")},
                                           "remaining": guard.remaining})
            if "step" in entry:  # executed, not rejected
                if entry["status"] == "ok":
                    observer.update(entry["tool"], entry["args"], entry["data"])
                else:
                    observer.mark_failed(entry["tool"], entry["args"])
                self.hub.publish(sid, "engine", {"step": entry["step"], "top": observer.top(5),
                                                 "misfit": observer.misfit(), "residuals": observer.residuals(3)})

        guard = ObservedGuard(bench, self.policy, on_step, cur["stop"])
        error = None
        try:
            diag = fn(guard, seed=cur["seed"])
        except SessionStopped:
            diag = {"status": "stopped", "elements": []}
        except BenchInterrupted as exc:
            diag, error = {"status": "interrupted", "elements": []}, str(exc)
        except Exception as exc:  # noqa: BLE001 - shown in the GUI, the server keeps running
            log.exception("session %s crashed", sid)
            diag, error = {"status": "crashed", "elements": []}, repr(exc)
        finally:
            pkg_log.removeHandler(relay)
            if cur["mode"] != "sim" and self.link:
                try:
                    self.link["injector"].clear()
                except Exception as exc:  # noqa: BLE001 - it clears itself after 10 s of silence
                    log.error("could not clear the injector: %s", exc)
        truth = truth_elements(SCENARIOS[cur["scenario"]])
        rec = {**run_meta(cur["source"], self.policy), "model": None, "model_version": None, **cur["meta"],
               "timestamp": cur["started"], "session_id": sid, "via": f"gui-{cur['mode']}",
               "method": cur["method"], "scenario": cur["scenario"], "set": SCENARIOS[cur["scenario"]]["set"],
               "seed": cur["seed"], "truth": truth, "elements": diag.get("elements", []), "status": diag["status"],
               "correct": diag["status"] == "diagnosed" and sorted(diag.get("elements", [])) == truth,
               "cost": guard.spent, "actions": len(guard.history), "rejections": len(guard.rejections),
               "error": error, "agent_stats": diag.get("agent_stats"), "history": guard.history,
               "diagnosis": {k: v for k, v in diag.items() if k != "agent_stats"}}
        if cur["mode"] != "sim" and self.link:
            b = self.link["bench"]
            rec.update(firmware=b.firmware, calibration=b.calibration_id, transport=cur["mode"])
        if diag["status"] not in ("stopped", "interrupted"):
            self._save(rec)
        if cur["hidden"]:
            self.store.reveal_scenario(sid, cur["scenario"])
        end = {k: rec[k] for k in ("status", "elements", "truth", "correct", "cost", "scenario", "error",
                                   "agent_stats")}
        end["confidence"] = diag.get("confidence")
        self.store.end_session(sid, diag["status"], end)
        self.hub.publish(sid, "session_end", end)
        with self.lock:
            self.current = None

    def _save(self, rec: dict) -> None:
        """GUI sessions go to results/live/<source>-<date>/, one record per line, never mixed."""
        out = self.results_dir / "live" / f"{rec['source']}-{datetime.now(LAGOS):%Y%m%d}"
        out.mkdir(parents=True, exist_ok=True)
        meta = out / "meta.json"
        if not meta.exists():
            meta.write_text(json.dumps({"source": rec["source"], "kind": "live sessions from the GUI",
                                        "started": lagos_now()}, indent=2))
        with open(out / "sessions.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def stop(self) -> bool:
        cur = self.current
        if not cur:
            return False
        cur["stop"].set()
        if cur.get("prober"):
            cur["prober"].cancel()
        self.hub.publish(cur["id"], "log", {"level": "WARNING", "logger": "gui", "msg": "stop requested"})
        return True

    def safe_state(self) -> dict:
        """Emergency: latch the firmware safe state, then stop the session. The firmware's own limits
        apply whether or not this is pressed."""
        self.stop()
        if not self.link:
            return {"sent": False, "reason": "no bench connected (simulation needs no safe state)"}
        r = self.link["bench"].safe_state()
        self.hub.publish(self.current["id"] if self.current else None, "bench",
                         {"event": "safe_state", "response": r})
        return {"sent": True, "response": r}

    def resume(self) -> dict:
        """Clears the firmware's safe-state latch. Only between sessions, and only on a person's click."""
        with self.lock:
            if self.current:
                raise RuntimeError("a session is running")
            if not self.link:
                raise BenchError("no bench connected")
            r = self.link["bench"].resume()
        self.hub.publish(None, "bench", {"event": "resumed", "response": r})
        return r

    def probe(self, volts: float) -> bool:
        cur = self.current
        return bool(cur and cur.get("prober") and cur["prober"].submit(volts))

    def shutdown(self) -> None:
        self.stop()
        t = self.current and self.current.get("thread")
        if t:
            t.join(5)
        self._disconnect()
