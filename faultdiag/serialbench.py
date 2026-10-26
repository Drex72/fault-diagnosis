"""The real bench over USB serial: same execute() interface as SimBench.

Protocol (firmware/bench): newline-delimited JSON at 115200 baud.
    request   {"id": n, "tool": "...", "args": {...}}
    response  {"id": n, "status": "ok|error|timeout|safety_abort", "data": {...}, "error": "..."}
    events    {"event": "hello" | "heartbeat_lost" | "overcurrent" | "test_load_forced_off", ...}

A reader thread routes responses to waiting requests by id and handles events.
A heartbeat thread keeps the firmware's host-heartbeat alive while a model
thinks; if this process dies, the firmware switches every load off by itself.

A hello event after connecting means the board reset mid-session: the session
is marked interrupted and every later call raises BenchInterrupted. It never
silently continues.
"""
from __future__ import annotations

import json
import re
import select
import sys
import threading
import time
from collections import deque

from .guard import load_policy
from .logsetup import get_logger

log = get_logger("serialbench")

BENCH_TOOLS = ("read_voltage", "read_current", "load_test")
MAX_COOLDOWN_WAIT_S = 30.0  # wait out the firmware's thermal cool-down up to this long


class BenchError(RuntimeError):
    """The bench cannot be used (no hello, looser limits than policy, port gone)."""


class BenchInterrupted(BenchError):
    """The board reset or the link was lost mid-session. The session is void."""


class SerialLink:
    """Line I/O over a serial port (pyserial)."""

    def __init__(self, port: str, baud: int = 115200):
        import serial  # imported here so simulation-only installs do not need pyserial

        self._serial_mod = serial
        self.port, self.baud = port, baud
        self.ser = serial.Serial(port, baud, timeout=0.1)
        self._buf = b""

    def write_line(self, line: str) -> None:
        self.ser.write(line.encode() + b"\n")
        self.ser.flush()

    def read_line(self, timeout: float = 0.1) -> str | None:
        """One complete line, or None if none arrived within timeout."""
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self._buf += chunk
            elif time.monotonic() >= deadline:
                return None
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", errors="replace").strip("\r")

    def reopen(self) -> None:
        try:
            self.ser.close()
        except Exception:  # noqa: BLE001 - the port may already be gone
            pass
        self._buf = b""
        self.ser = self._serial_mod.Serial(self.port, self.baud, timeout=0.1)

    def close(self) -> None:
        self.ser.close()


def check_limits(fw: dict, policy: dict) -> list[str]:
    """Firmware limits must be at least as strict as the policy. Returns problems."""
    lt, problems = policy["load_test"], []
    if fw.get("pulse_max_ms", 1e9) > lt["width_ms_max"]:
        problems.append(f"firmware pulse cap {fw.get('pulse_max_ms')} ms > policy {lt['width_ms_max']} ms")
    if fw.get("pulse_min_ms", 0) < lt["width_ms_min"]:
        problems.append(f"firmware pulse minimum {fw.get('pulse_min_ms')} ms < policy {lt['width_ms_min']} ms")
    limit = policy["safety"]["max_source_current_a"]
    if fw.get("src_limit_a", 1e9) > limit:
        problems.append(f"firmware source current limit {fw.get('src_limit_a')} A > policy {limit} A")
    return problems


def cli_prober(point_a: str, point_b: str, instruction: str | None, timeout_s: float) -> float | None:
    """Ask the technician at the terminal for a handheld-meter reading (volts)."""
    msg = instruction or f"Measure the DC voltage from {point_a} (red lead) to {point_b} (black lead)."
    print(f"\n*** HUMAN PROBE ({timeout_s:.0f} s) ***\n{msg}\nType the reading in volts and press Enter: ",
          end="", file=sys.stderr, flush=True)
    deadline = time.monotonic() + timeout_s
    while (left := deadline - time.monotonic()) > 0:
        ready, _, _ = select.select([sys.stdin], [], [], left)
        if not ready:
            break
        text = sys.stdin.readline().strip().lower().removesuffix("v").strip()
        try:
            return float(text)
        except ValueError:
            print(f"'{text}' is not a number. Volts, for example 12.31: ", end="", file=sys.stderr, flush=True)
    print("\nno reading: human probe timed out", file=sys.stderr)
    return None


class SerialBench:
    def __init__(self, link, policy: dict | None = None, prober=None, heartbeat_s: float = 1.0,
                 hello_timeout_s: float = 4.0, reconnect_s: float = 5.0, on_event=None, sleep=time.sleep):
        self.link = link
        self.sleep = sleep
        self.policy = policy or load_policy()
        self.timeout_s = self.policy["timeouts_s"]["default"]
        self.probe_timeout_s = self.policy["timeouts_s"]["human_probe"]
        self.prober = prober
        self.heartbeat_s = heartbeat_s
        self.reconnect_s = reconnect_s
        self.on_event = on_event
        self.hello: dict | None = None
        self.events: list[dict] = []
        self.rejected_lines: list[str] = []  # errors the firmware sent with id null (corrupt or over-long lines)
        self.interrupted: str | None = None
        self.connected = True
        self.inhibited = False
        self._pending: dict[int, list] = {}  # id -> [threading.Event, response]
        self._quiet: set[int] = set()       # heartbeat ids: response consumed silently
        self._answered: deque = deque(maxlen=512)
        self._timed_out: deque = deque(maxlen=512)
        self._next_id = 1
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._last_sent = 0.0
        self._hb_paused_until = 0.0
        self._stop = threading.Event()
        self._hello_seen = threading.Event()

        self._reader = threading.Thread(target=self._read_loop, name="serialbench-reader", daemon=True)
        self._reader.start()
        if not self._hello_seen.wait(hello_timeout_s):  # board already running: no reset on open
            info = self.request("info", timeout=self.timeout_s)
            if info["status"] != "ok":
                self.close()
                raise BenchError(f"no hello and no answer to info from the bench: {info.get('error')}")
            self.hello = {"event": "info", **info["data"]}
            self._hello_seen.set()
        log.info("bench connected: fw=%s calibration=%s reset_reason=%s", self.firmware,
                 self.hello.get("calibration"), self.hello.get("reset_reason"))
        problems = check_limits(self.hello.get("limits", {}), self.policy)
        if problems:
            self.close()
            raise BenchError("firmware limits looser than config/policy.json: " + "; ".join(problems))
        self._hb = threading.Thread(target=self._heartbeat_loop, name="serialbench-heartbeat", daemon=True)
        self._hb.start()

    # ------------------------------------------------------------ properties
    @property
    def firmware(self) -> str | None:
        return (self.hello or {}).get("fw")

    @property
    def calibration_id(self) -> str | None:
        return (self.hello or {}).get("calibration")

    # ------------------------------------------------------------ threads
    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                line = self.link.read_line(0.1)
            except Exception as exc:  # noqa: BLE001 - USB unplugged, port gone
                if self._stop.is_set():
                    return
                self._lost_link(exc)
                continue
            if line is None or not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.warning("corrupt line from bench ignored: %r", line[:120])
                continue
            if not isinstance(msg, dict):
                log.warning("non-object line from bench ignored: %r", line[:120])
            elif "event" in msg:
                self._event(msg)
            else:
                self._response(msg)

    def _lost_link(self, exc) -> None:
        self.connected = False
        log.error("bench link lost (%s); reconnecting for up to %.0f s", exc, self.reconnect_s)
        self._event({"event": "link_lost", "error": str(exc)})
        self._fail_pending("bench link lost")
        deadline = time.monotonic() + self.reconnect_s
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                self.link.reopen()
                self.connected = True
                log.warning("bench link reopened; a board reset on reconnect will interrupt the session")
                self._event({"event": "reconnected"})
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        self._interrupt(f"bench link lost and not recovered within {self.reconnect_s:.0f} s ({exc})")
        self._stop.wait(1.0)

    def _event(self, msg: dict) -> None:
        name = msg.get("event")
        msg = {**msg, "t": time.time()}
        self.events.append(msg)
        if name == "hello":
            if not self._hello_seen.is_set():
                self.hello = msg
                self._hello_seen.set()
            else:
                self._interrupt(f"bench reset mid-session (reset_reason={msg.get('reset_reason')}, "
                                f"fw={msg.get('fw')})")
                self.hello = msg  # the new boot: a fresh session must re-run the self-test
        elif name == "heartbeat_lost":
            log.warning("bench reports heartbeat lost: loads switched off by firmware")
        elif name == "overcurrent":
            self.inhibited = True
            log.error("bench over-current %.2f A: firmware switched off and latched safe state", msg.get("amps", -1))
        elif name == "test_load_forced_off":
            log.error("bench forced the test load off outside a load test")
        elif name not in ("reconnected", "link_lost"):  # those two are raised here, not by the firmware
            log.warning("unknown bench event: %s", msg)
        if self.on_event:
            try:
                self.on_event(msg)
            except Exception:  # noqa: BLE001 - a GUI callback must not kill the reader
                log.exception("on_event callback failed")

    def _response(self, msg: dict) -> None:
        rid = msg.get("id")
        if rid is None:
            self.rejected_lines.append(msg.get("error") or "")
            log.error("bench rejected a line: %s", msg.get("error"))
            return
        with self._lock:
            slot = self._pending.pop(rid, None)
            quiet = rid in self._quiet
            self._quiet.discard(rid)
            if slot is None:
                kind = ("late (request had timed out)" if rid in self._timed_out else
                        "duplicate" if rid in self._answered else "stale or unknown")
                if not quiet:
                    log.warning("%s response id %s ignored", kind, rid)
                return
            self._answered.append(rid)
        slot[1] = msg
        slot[0].set()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_s / 2):
            now = time.monotonic()
            if self.interrupted or not self.connected or now < self._hb_paused_until:
                continue
            if now - self._last_sent >= self.heartbeat_s:
                try:
                    self._send("heartbeat", {}, quiet=True)
                except Exception as exc:  # noqa: BLE001 - the reader handles a lost link
                    log.debug("heartbeat send failed: %s", exc)

    def _interrupt(self, why: str) -> None:
        if not self.interrupted:
            self.interrupted = why
            log.error("SESSION INTERRUPTED: %s", why)
        self._fail_pending(why)

    def _fail_pending(self, why: str) -> None:
        with self._lock:
            slots = list(self._pending.items())
            self._pending.clear()
        for rid, slot in slots:
            slot[1] = {"id": rid, "status": "error", "error": why}
            slot[0].set()

    # ------------------------------------------------------------ requests
    def _send(self, tool: str, args: dict, quiet: bool = False) -> tuple[int, list]:
        slot = [threading.Event(), None]
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            self._pending[rid] = slot
            if quiet:
                self._quiet.add(rid)
        line = json.dumps({"id": rid, "tool": tool, "args": args}, separators=(",", ":"))
        with self._wlock:
            self.link.write_line(line)
            self._last_sent = time.monotonic()
        return rid, slot

    def request(self, tool: str, args: dict | None = None, timeout: float | None = None) -> dict:
        """Low-level call: any firmware tool. Returns the response dict."""
        if not self.connected:
            return {"status": "error", "error": "bench disconnected"}
        timeout = self.timeout_s if timeout is None else timeout
        try:
            rid, slot = self._send(tool, args or {})
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"write failed: {exc}"}
        if not slot[0].wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
                self._timed_out.append(rid)
            log.warning("bench timeout: %s %s (no response in %.1f s)", tool, args, timeout)
            return {"status": "timeout", "error": f"no response from bench in {timeout:.1f} s"}
        return slot[1]

    def _load_test_timeout(self, args: dict) -> float:
        lim = (self.hello or {}).get("limits", {})
        on_ms = lim.get("steady_ms", 2000) if args.get("mode") == "steady" else args.get("width_ms", 100)
        return self.timeout_s + on_ms / 1000.0

    def execute(self, tool: str, args: dict) -> dict:
        if self.interrupted:
            raise BenchInterrupted(self.interrupted)
        if tool == "human_probe":
            return self._human_probe(args)
        if tool not in BENCH_TOOLS:
            return {"status": "error", "error": f"tool {tool} is not available on the bench"}
        timeout = self._load_test_timeout(args) if tool == "load_test" else self.timeout_s
        resp = self.request(tool, args, timeout)
        cooldown = re.match(r"cooldown: (\d+) ms", resp.get("error") or "")
        if cooldown and int(cooldown.group(1)) / 1000 <= MAX_COOLDOWN_WAIT_S:
            wait = int(cooldown.group(1)) / 1000 + 0.05
            log.info("load test: waiting %.1f s for the test resistor to cool (firmware limit)", wait)
            self.sleep(wait)
            resp = self.request(tool, args, timeout)
        if self.interrupted:
            raise BenchInterrupted(self.interrupted)
        out = {"status": resp.get("status", "error")}
        if out["status"] == "ok":
            out["data"] = resp.get("data") or {}
        else:
            out["error"] = resp.get("error") or "no error text"
        return out

    def _human_probe(self, args: dict) -> dict:
        if self.prober is None:
            return {"status": "error", "error": "no technician prompt available for human_probe"}
        volts = self.prober(args["point_a"], args["point_b"], args.get("instruction"), self.probe_timeout_s)
        if self.interrupted:
            raise BenchInterrupted(self.interrupted)
        if volts is None:
            return {"status": "timeout", "error": "technician did not enter a reading in time"}
        return {"status": "ok", "data": {"volts": round(float(volts), 4)}}

    # ------------------------------------------------------------ bench control
    def self_test(self, watchdog: bool = False) -> dict:
        resp = self.request("self_test", {"watchdog": watchdog})
        if resp.get("status") != "ok":
            raise BenchError(f"self_test failed to run: {resp.get('error')}")
        return resp["data"]

    def safe_state(self) -> dict:
        resp = self.request("safe_state")
        if resp.get("status") == "ok":  # the firmware latches it until resume()
            self.inhibited = True
            log.warning("safe state latched by the host: loads off, load tests refused until resume")
        else:
            log.error("safe_state not confirmed by the bench: %s", resp)
        return resp

    def resume(self) -> dict:
        self.inhibited = False
        return self.request("resume")

    def info(self) -> dict:
        return self.request("info")

    def pause_heartbeat(self, seconds: float) -> None:
        """Self-test only: stop sending heartbeats so the firmware cutoff can be observed."""
        self._hb_paused_until = time.monotonic() + seconds

    def close(self) -> None:
        self._stop.set()
        try:
            self.link.close()
        except Exception:  # noqa: BLE001
            pass


class ToolFailures:
    """Host-side tool-failure injection for the tool-failure scenarios (TF1, TF2)
    on hardware: the bench itself cannot be made to fail on demand, so the
    failure is applied between the guard and the bench. Recorded in the truth file."""

    def __init__(self, bench, failures: dict, rng):
        self.bench, self.failures, self.rng = bench, failures, rng

    def execute(self, tool: str, args: dict) -> dict:
        sel = args.get("point") or args.get("channel") or args.get("mode") or ""
        failure = self.failures.get(f"{tool}:{sel}")
        if failure == "timeout":
            return {"status": "timeout", "error": "no response from instrument"}
        res = self.bench.execute(tool, args)
        if failure == "garbage" and res.get("status") == "ok":
            res = {**res, "data": {k: round(float(self.rng.uniform(0, 16)), 4) for k in res["data"]}}
        return res

    def __getattr__(self, name):
        return getattr(self.bench, name)
