"""Host driver for the fault-injection controller (firmware/injector).

Protocol: newline-delimited JSON at 115200 baud.
    {"id": n, "cmd": "set", "faults": ["F2", "F5@motor"]}  -> {"id": n, "status": "ok", "data": {"active": [...]}}
    {"id": n, "cmd": "clear" | "status" | "heartbeat"}
    events: {"event": "hello", ...}, {"event": "host_lost", "cleared": bool}

The injector clears every fault by itself if the host goes quiet for 10 s, so
a heartbeat thread runs while a scenario is active. The diagnostic methods
never talk to this module; only scripts/run_hardware.py does.
"""
from __future__ import annotations

import json
import threading
import time

from .logsetup import get_logger

log = get_logger("injector")

# Scenario name -> injector relays. Hardware counterpart of harness.SCENARIOS
# (F1b is not run on hardware: one series resistor gives one severity).
HW_FAULTS = {
    "healthy": [], "F1": ["F1"], "F2": ["F2"], "F3": ["F3"], "F4": ["F4"],
    "F5@motor": ["F5@motor"], "F5@lamp": ["F5@lamp"], "F5@load": ["F5@load"],
    "C1": ["F1", "F3"], "C2": ["F2", "F5@motor"],
    "H1": ["H1"], "H2": ["H2"],
    "TF1": ["F2"], "TF2": ["F3"],  # plus the host-side tool failure from harness.SCENARIOS
    "H3": ["H3"], "H4": ["H4"],
}


class InjectorError(RuntimeError):
    pass


class InjectorClient:
    def __init__(self, link, timeout_s: float = 2.0, heartbeat_s: float = 3.0, hello_timeout_s: float = 4.0):
        self.link = link
        self.timeout_s = timeout_s
        self.heartbeat_s = heartbeat_s
        self.hello: dict | None = None
        self.reset_seen = False
        self.events: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._stop = threading.Event()
        deadline = time.monotonic() + hello_timeout_s
        while self.hello is None and time.monotonic() < deadline:
            self._read_one(0.1)
        if self.hello is None:  # already running: status answers instead
            st = self._call({"cmd": "status"})
            self.hello = {"event": "status", "fw": None, **st}
        log.info("injector connected: fw=%s", self.hello.get("fw"))
        self._hb = threading.Thread(target=self._heartbeat, name="injector-heartbeat", daemon=True)
        self._hb.start()

    def _read_one(self, timeout: float) -> dict | None:
        line = self.link.read_line(timeout)
        if not line:
            return None
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log.warning("corrupt line from injector ignored: %r", line[:120])
            return None
        if isinstance(msg, dict) and "event" in msg:
            self.events.append(msg)
            if msg["event"] == "hello":
                if self.hello is not None:
                    self.reset_seen = True
                    log.error("injector reset: every fault relay was released by the reset")
                self.hello = msg
            elif msg["event"] == "host_lost":
                log.error("injector lost the host and cleared all faults (cleared=%s)", msg.get("cleared"))
            return None
        return msg if isinstance(msg, dict) else None

    def _call(self, req: dict) -> dict:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            self.link.write_line(json.dumps({"id": rid, **req}, separators=(",", ":")))
            deadline = time.monotonic() + self.timeout_s
            while time.monotonic() < deadline:
                msg = self._read_one(0.05)
                if msg is None:
                    continue
                if msg.get("id") == rid:
                    if msg.get("status") != "ok":
                        raise InjectorError(f"injector refused {req}: {msg.get('error')}")
                    return msg.get("data") or {}
                log.warning("injector: stale response id %s ignored", msg.get("id"))
        raise InjectorError(f"injector did not answer {req} within {self.timeout_s:.1f} s")

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_s):
            try:
                self._call({"cmd": "heartbeat"})
            except Exception as exc:  # noqa: BLE001 - reported, the run checks status before each session
                log.error("injector heartbeat failed: %s", exc)

    def set(self, faults: list[str]) -> list[str]:
        active = self._call({"cmd": "set", "faults": list(faults)})["active"]
        if sorted(active) != sorted(faults):
            raise InjectorError(f"injector reports {active}, wanted {faults}")
        return active

    def clear(self) -> None:
        self._call({"cmd": "clear"})

    def status(self) -> list[str]:
        return self._call({"cmd": "status"})["active"]

    def close(self) -> None:
        self._stop.set()
        try:
            self.clear()
        except Exception as exc:  # noqa: BLE001
            log.error("could not clear the injector on close: %s (it clears itself after 10 s)", exc)
        try:
            self.link.close()
        except Exception:  # noqa: BLE001
            pass
