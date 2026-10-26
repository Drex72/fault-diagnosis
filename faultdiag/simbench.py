"""Simulated testbench. Same execute() interface the ESP32 transport will use.

It holds the ground truth; diagnostic methods only ever see tool results.
"""
from __future__ import annotations

from dataclasses import replace

from .circuit import solve
from .faults import FaultState
from .logsetup import get_logger
from .measure import OBS_KEYS, kind, obs_key, observe, selector

log = get_logger("simbench")


class SimBench:
    def __init__(self, state: FaultState, rng, noise_v: float = 0.01, noise_i: float = 0.005,
                 failures: dict | None = None, max_source_current: float = 8.0):
        self.state = state
        self.rng = rng
        self.noise = {"v": noise_v, "i": noise_i}
        self.failures = failures or {}  # "read_voltage:motor" -> "timeout" | "garbage"
        self.max_source_current = max_source_current
        log.debug("sim bench: tap offsets %s, current gains %s, p_lamp_open %.2f, tool failures %s",
                  state.sensor_offsets, state.current_gain, state.p_lamp_open, self.failures)

    def _params_now(self):
        p = self.state.params
        if self.state.p_lamp_open > 0:  # intermittent: state changes between readings
            p = replace(p, lamp_open=bool(self.rng.random() < self.state.p_lamp_open))
        return p

    def execute(self, tool: str, args: dict) -> dict:
        failure = self.failures.get(f"{tool}:{selector(tool, args)}")
        if failure == "timeout":
            return {"status": "timeout", "error": "no response from instrument"}
        p = self._params_now()

        if tool == "human_probe":
            v = solve(p).v
            reading = v[args["point_a"]] - v[args["point_b"]] + self.rng.normal(0, 0.005)
            return {"status": "ok", "data": {"volts": round(float(reading), 4)}}

        keys = OBS_KEYS[obs_key(tool, args)]
        values = observe(p, self.state.sensor_offsets, tool, args)
        for i, key in enumerate(keys):  # a current sensor with a gain error (source channel feeds the load test too)
            channel = args.get("channel") if key == "amps" else ("src" if key.startswith("i_src") else None)
            values[i] *= self.state.current_gain.get(channel, 1.0)
        if failure == "garbage":
            values = self.rng.uniform(0, 16, size=len(values))
        data = {k: round(float(x + self.rng.normal(0, self.noise[kind(k)])), 4) for k, x in zip(keys, values)}

        if tool == "load_test" and data["i_src_during"] > self.max_source_current:
            return {"status": "error", "error": "safety abort: source current over limit"}
        return {"status": "ok", "data": data}
