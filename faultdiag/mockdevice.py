"""Mock serial devices for testing without a board.

MockBenchDevice speaks the firmware/bench protocol and enforces the same
limits (self-test gate, pulse cap, cool-down, battery cutoff, over-current,
heartbeat, safe state), with readings from a SimBench. MockInjectorDevice
speaks the firmware/injector protocol and swaps the mock bench's fault state.

Anything run through these is a simulation: label it source "sim".
"""
from __future__ import annotations

import json
import queue
import time

import numpy as np

from .faults import FaultState, sample_healthy
from .logsetup import get_logger
from .simbench import SimBench

log = get_logger("mockdevice")

TAPS = ("src", "bus", "motor", "lamp", "load")
CURRENTS = ("src", "motor")
FIRMWARE_LIMITS = {  # firmware/bench/lib/benchcore/src/benchcore.h, struct Limits
    "pulse_min_ms": 10, "pulse_max_ms": 500, "pulse_default_ms": 100, "steady_ms": 2000,
    "cooldown_min_ms": 2000, "duty_max": 0.10, "src_limit_a": 8.0, "battery_cutoff_v": 11.5,
    "heartbeat_timeout_ms": 3000, "idle_check_ms": 100,
}
HELLO_LIMIT_KEYS = ("pulse_min_ms", "pulse_max_ms", "steady_ms", "cooldown_min_ms", "duty_max", "src_limit_a",
                    "battery_cutoff_v", "heartbeat_timeout_ms")


class _Device:
    """Output queue, scheduled lines and the test hooks shared by both mocks."""

    def __init__(self, clock):
        self.clock = clock
        self.out: queue.Queue = queue.Queue()
        self._scheduled: list[tuple[float, str]] = []
        self.drop_next = 0       # swallow responses (the host times out)
        self.duplicate_next = 0  # send a response twice
        self.corrupt_next = 0    # send a broken line before the response
        self.stale_next = 0      # send a response with an unknown id before the real one
        self.delay_next = 0.0    # deliver the next response this many seconds late

    def emit(self, obj: dict) -> None:
        self.out.put(json.dumps(obj, separators=(",", ":")))

    def _respond(self, resp: dict) -> None:
        if self.drop_next:
            self.drop_next -= 1
            return
        if self.corrupt_next:
            self.corrupt_next -= 1
            self.out.put('{"id": ' + str(resp.get("id")) + ', "sta')
        if self.stale_next and isinstance(resp.get("id"), int):
            self.stale_next -= 1
            self.emit({**resp, "id": resp["id"] + 100000})
        line = json.dumps(resp, separators=(",", ":"))
        if self.delay_next:
            self._scheduled.append((self.clock() + self.delay_next, line))
            self.delay_next = 0.0
        else:
            self.out.put(line)
        if self.duplicate_next:
            self.duplicate_next -= 1
            self.out.put(line)

    def flush_scheduled(self) -> None:
        now = self.clock()
        due = [x for x in self._scheduled if x[0] <= now]
        self._scheduled = [x for x in self._scheduled if x[0] > now]
        for _, line in due:
            self.out.put(line)

    def tick(self) -> None:
        self.flush_scheduled()


class MockBenchDevice(_Device):
    FW = "bench-mock-0.1.0"

    def __init__(self, state: FaultState | None = None, seed: int = 0, clock=time.monotonic,
                 limits: dict | None = None, raw_error: dict | None = None, cooldown_scale: float = 1.0,
                 self_test_fail: set | None = None):
        super().__init__(clock)
        self.rng = np.random.default_rng(seed)
        self.lim = {**FIRMWARE_LIMITS, **(limits or {})}
        self.raw_error = raw_error or {}  # "tap:src" -> (gain, offset) of the uncalibrated reading
        self.cooldown_scale = cooldown_scale  # mock only: shortens the thermal wait for quick demos
        self.self_test_fail = set(self_test_fail or ())
        self.cal = self._identity_cal()
        self.set_state(state or FaultState(sample_healthy(self.rng)))
        self.boot("poweron")

    @staticmethod
    def _identity_cal() -> dict:
        return {"id": "uncalibrated", "taps": {t: {"gain": 1.0, "offset": 0.0} for t in TAPS},
                "currents": {c: {"gain": 1.0, "offset": 0.0} for c in CURRENTS}}

    def set_state(self, state: FaultState) -> None:
        self.sim = SimBench(state, self.rng, max_source_current=self.lim["src_limit_a"])

    # ------------------------------------------------------------ lifecycle
    def boot(self, reason: str) -> None:
        now = self.clock()
        self.reset_reason = reason
        self.boot_safe = True
        self.test_load_on = False
        self.host_lost = True
        self.inhibited = False
        self.self_test_passed = False
        self.had_load_test = False
        self.last_host = self.last_idle = self.last_load_end = now
        self.last_load_on_ms = 0
        self.wdt_reset_at = None
        self.booted_at = now
        self._scheduled = []
        self.emit({"event": "hello", "fw": self.FW, "calibration": self.cal["id"], "reset_reason": reason,
                   "boot_safe": self.boot_safe, "limits": {k: self.lim[k] for k in HELLO_LIMIT_KEYS}})

    def reset(self, reason: str = "software") -> None:
        """Simulate a board reset (brown-out, watchdog, button)."""
        log.debug("mock bench reset: %s", reason)
        self.boot(reason)

    def tick(self) -> None:
        super().tick()
        now = self.clock()
        if self.wdt_reset_at is not None and now >= self.wdt_reset_at:
            self.boot("task_wdt")
            return
        if not self.host_lost and (now - self.last_host) * 1000 > self.lim["heartbeat_timeout_ms"]:
            self.host_lost = True
            self.test_load_on = False
            self.emit({"event": "heartbeat_lost", "uptime_ms": int((now - self.booted_at) * 1000),
                       "test_load_on": False})
        if (now - self.last_idle) * 1000 >= self.lim["idle_check_ms"]:
            self.last_idle = now
            amps = self._reported_current("src")
            if amps > self.lim["src_limit_a"]:
                self.inhibited = True
                self.emit({"event": "overcurrent", "amps": round(amps, 4), "inhibited": True})

    # ------------------------------------------------------------ readings
    def _raw(self, kind: str, name: str, true_value: float) -> float:
        g, o = self.raw_error.get(f"{kind}:{name}", (1.0, 0.0))
        return true_value * g + o

    def _cal(self, kind: str, name: str, raw: float) -> float:
        c = self.cal["taps" if kind == "tap" else "currents"][name]
        return raw * c["gain"] + c["offset"]

    def _volts(self, point: str) -> float | None:
        res = self.sim.execute("read_voltage", {"point": point})
        return res["data"]["volts"] if res["status"] == "ok" else None

    def _current(self, channel: str) -> float | None:
        res = self.sim.execute("read_current", {"channel": channel})
        return res["data"]["amps"] if res["status"] == "ok" else None

    def _reported_current(self, channel: str) -> float:
        """What the firmware sees: the raw INA226 reading with calibration applied."""
        return self._cal("current", channel, self._raw("current", channel, self._current(channel)))

    def cooldown_remaining_ms(self) -> int:
        if not self.had_load_test:
            return 0
        needed = max(self.lim["cooldown_min_ms"], self.last_load_on_ms * (1 / self.lim["duty_max"] - 1))
        needed *= self.cooldown_scale
        since = (self.clock() - self.last_load_end) * 1000
        return 0 if since >= needed else int(needed - since)

    # ------------------------------------------------------------ protocol
    MAX_LINE = 512  # firmware LineReader

    def handle_line(self, line: str) -> None:
        if len(line) > self.MAX_LINE:
            self._respond({"id": None, "status": "error", "data": {},
                           "error": f"line too long (max {self.MAX_LINE} bytes); request dropped"})
            return
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("request must be a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            self._respond({"id": None, "status": "error", "error": f"bad json: {exc}", "data": {}})
            return
        self.last_host = self.clock()
        self.host_lost = False
        status, data, error = self._tool(req.get("tool"), req.get("args") or {})
        resp = {"id": req.get("id"), "status": status, "data": data or {}}
        if error:
            resp["error"] = error
        self._respond(resp)

    def _tool(self, tool, args: dict):
        if tool is None:
            return "error", None, "missing tool"
        if tool == "read_voltage":
            if args.get("point") not in TAPS:
                return "error", None, "unknown point"
            v = self._volts(args["point"])
            return ("ok", {"volts": round(self._cal("tap", args["point"], self._raw("tap", args["point"], v)), 4)},
                    None)
        if tool == "read_current":
            if args.get("channel") not in CURRENTS:
                return "error", None, "unknown channel"
            return "ok", {"amps": round(self._reported_current(args["channel"]), 4)}, None
        if tool == "load_test":
            return self._load_test(args)
        if tool == "heartbeat":
            return "ok", {"uptime_ms": int((self.clock() - self.booted_at) * 1000)}, None
        if tool == "safe_state":
            self.test_load_on = False
            self.inhibited = True
            return "ok", {"inhibited": True, "test_load_on": False}, None
        if tool == "resume":
            self.inhibited = False
            return "ok", {"inhibited": False}, None
        if tool == "self_test":
            return self._self_test(bool(args.get("watchdog", False)))
        if tool == "info":
            return "ok", {"fw": self.FW, "calibration": self.cal["id"],
                          "uptime_ms": int((self.clock() - self.booted_at) * 1000),
                          "reset_reason": self.reset_reason, "host_lost": self.host_lost,
                          "inhibited": self.inhibited, "self_test_passed": self.self_test_passed,
                          "cooldown_remaining_ms": self.cooldown_remaining_ms(), "test_load_on": self.test_load_on,
                          "limits": {k: self.lim[k] for k in HELLO_LIMIT_KEYS}}, None
        if tool == "calibration":
            return self._calibration(args)
        if tool == "read_raw":
            kind, name = args.get("kind"), args.get("name")
            if kind == "tap" and name == "gnd":
                return "ok", {"raw": round(float(self.rng.normal(0, 0.002)), 4)}, None
            if kind == "tap" and name in TAPS:
                return "ok", {"raw": round(self._raw("tap", name, self._volts(name)), 4)}, None
            if kind == "current" and name in CURRENTS:
                return "ok", {"raw": round(self._raw("current", name, self._current(name)), 4)}, None
            return "error", None, "unknown tap or channel"
        return "error", None, f"unknown tool: {tool}"

    def _load_test(self, args: dict):
        mode = args.get("mode")
        if mode not in ("pulse", "steady"):
            return "error", None, "mode must be pulse or steady"
        w = args.get("width_ms", self.lim["pulse_default_ms"])
        if not isinstance(w, int) or isinstance(w, bool):
            return "error", None, "width_ms must be an integer"
        if not self.lim["pulse_min_ms"] <= w <= self.lim["pulse_max_ms"]:
            return "error", None, f"width_ms outside {self.lim['pulse_min_ms']}..{self.lim['pulse_max_ms']}"
        if not self.self_test_passed:
            return "error", None, "refused: the safety self-test has not passed since boot; send self_test first"
        if self.inhibited:
            return "error", None, "inhibited: safe state latched; send resume to allow load tests"
        wait = self.cooldown_remaining_ms()
        if wait:
            return "error", None, f"cooldown: {wait} ms left (test resistor thermal limit)"
        vb = self._volts("src")
        if vb < self.lim["battery_cutoff_v"]:
            return "error", None, (f"battery_low: {vb:.2f} V is below the {self.lim['battery_cutoff_v']:.2f} V "
                                   "cutoff; load test refused")
        res = self.sim.execute("load_test", {"mode": mode, "width_ms": w} if mode == "pulse" else {"mode": mode})
        self.had_load_test = True
        self.last_load_on_ms = w if mode == "pulse" else self.lim["steady_ms"]
        self.last_load_end = self.clock()
        self.test_load_on = False
        if res["status"] != "ok":
            return "safety_abort", None, "source current over the limit; test load switched off"
        return "ok", res["data"], None

    def _self_test(self, watchdog: bool):
        lim = self.lim
        crit = {
            "boot_safe_state": self.boot_safe, "test_load_off": not self.test_load_on, "watchdog_enabled": True,
            "heartbeat_cutoff_logic": True,
            "limits_within_policy": (lim["pulse_max_ms"] <= 500 and lim["src_limit_a"] <= 8.0
                                     and lim["battery_cutoff_v"] >= 11.5 and lim["heartbeat_timeout_ms"] <= 5000
                                     and lim["duty_max"] <= 0.10),
            "adc_present": True, "ina226_src_present": True,
        }
        for k in self.self_test_fail:
            crit[k] = False
        passed = all(crit.values())
        self.self_test_passed = passed
        if watchdog:
            self.wdt_reset_at = self.clock() + 0.3
        return "ok", {"critical": crit, "info": {"ina226_motor_present": True, "adc_ground_reference": True},
                      "passed": passed, "watchdog_reset_pending": watchdog}, None

    def _calibration(self, args: dict):
        action = args.get("action", "get")
        if action == "clear":
            self.cal = self._identity_cal()
        elif action == "set":
            cid = args.get("id")
            if not isinstance(cid, str) or not cid or len(cid) >= 48:
                return "error", None, "calibration id missing or too long"
            new = self._identity_cal()
            new["id"] = cid
            for group, names in (("taps", TAPS), ("currents", CURRENTS)):
                for name in names:
                    ch = (args.get(group) or {}).get(name)
                    if ch is None:
                        continue
                    g, o = float(ch.get("gain", 1.0)), float(ch.get("offset", 0.0))
                    if not (0.5 <= g <= 2.0 and -2.0 <= o <= 2.0):
                        return "error", None, f"calibration for {name} out of range (gain 0.5..2, offset -2..2)"
                    new[group][name] = {"gain": g, "offset": o}
            self.cal = new
        elif action != "get":
            return "error", None, "action must be get, set or clear"
        return "ok", json.loads(json.dumps(self.cal)), None


class MockInjectorDevice(_Device):
    """firmware/injector protocol. on_change(faults) is called when the active set changes."""

    FW = "injector-mock-0.1.0"
    FAULTS = ("F1", "F2", "F3", "F4", "F5@motor", "F5@lamp", "F5@load", "H1", "H2", "H3", "H4")

    def __init__(self, on_change=None, clock=time.monotonic, host_timeout_s: float = 10.0):
        super().__init__(clock)
        self.on_change = on_change
        self.host_timeout_s = host_timeout_s
        self.active: list[str] = []
        self.host_seen = False
        self.last_host = clock()
        self.boot("poweron")

    def boot(self, reason: str) -> None:
        self._set([])
        self.host_seen = False
        self.emit({"event": "hello", "fw": self.FW, "reset_reason": reason, "faults": list(self.FAULTS)})

    def reset(self, reason: str = "software") -> None:
        self.boot(reason)

    def _set(self, faults: list[str]) -> None:
        self.active = sorted(faults)
        if self.on_change:
            self.on_change(list(self.active))

    def tick(self) -> None:
        super().tick()
        if self.host_seen and self.clock() - self.last_host > self.host_timeout_s:
            self.host_seen = False
            cleared = bool(self.active)
            self._set([])
            self.emit({"event": "host_lost", "cleared": cleared})

    def handle_line(self, line: str) -> None:
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            self._respond({"id": None, "status": "error", "error": "bad json"})
            return
        self.host_seen, self.last_host = True, self.clock()
        cmd, error = req.get("cmd"), None
        if cmd == "set":
            faults = req.get("faults") or []
            unknown = [f for f in faults if f not in self.FAULTS]
            if unknown:
                error = f"unknown fault: {unknown[0]}"
            elif len(faults) > 3:
                error = "at most 3 faults at once"
            else:
                self._set(faults)
        elif cmd == "clear":
            self._set([])
        elif cmd not in ("status", "heartbeat"):
            error = f"unknown cmd: {cmd}" if cmd else "missing cmd"
        resp = {"id": req.get("id"), "status": "error" if error else "ok", "data": {"active": list(self.active)}}
        if error:
            resp["error"] = error
        self._respond(resp)


class MockLink:
    """Same interface as serialbench.SerialLink, wired to a mock device.

    unplug() makes reads fail like a pulled USB cable; replug() lets reopen()
    succeed, and the board resets on reopen as an ESP32 DevKit does (DTR)."""

    def __init__(self, device, reset_on_open: bool = True):
        self.device = device
        self.reset_on_open = reset_on_open
        self.unplugged = False
        self.closed = False
        self.sent: list[str] = []

    def write_line(self, line: str) -> None:
        if self.unplugged or self.closed:
            raise OSError("device not configured")
        self.sent.append(line)
        self.device.tick()  # real firmware ticks continuously; time passed before this byte arrived
        self.device.handle_line(line)

    def read_line(self, timeout: float = 0.1) -> str | None:
        if self.unplugged:
            time.sleep(min(timeout, 0.05))
            raise OSError("device not configured")
        deadline = time.monotonic() + timeout
        while True:
            self.device.tick()
            try:
                return self.device.out.get(timeout=0.01)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    return None

    def unplug(self) -> None:
        self.unplugged = True

    def replug(self) -> None:
        self.unplugged = False

    def reopen(self) -> None:
        if self.unplugged:
            raise OSError("no such port")
        self.closed = False
        if self.reset_on_open:
            self.device.reset("poweron")

    def close(self) -> None:
        self.closed = True
