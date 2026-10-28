"""Experimental V3: physics-residual investigation after Bayesian model rejection.

V2 decides whether the known fault catalogue adequately explains the evidence.
When it does not, V3 does not immediately invent another named Bayesian class.
It gathers a minimal conservation-law evidence set and solves two generic
physical slack quantities:

- the shared return resistance required for KCL to close if there is no hidden
  load;
- the unaccounted branch current required for KCL to close if the return path
  stays near its healthy value.

This is a small parity/residual layer, not a replacement for the Bayesian
engine. H1/H2 remain absent from the V1/V2 hypothesis list.
"""
from __future__ import annotations

import math

import numpy as np

from .engine import ACTIONS
from .engine_v2 import BayesEngineV2
from .faults import apply_atoms, sample_healthy


class BayesEngineV3Residual(BayesEngineV2):
    def __init__(
        self,
        *args,
        return_ratio_threshold: float = 4.0,
        hidden_sigma_threshold: float = 6.0,
        **kwargs,
    ):
        particle_seed = kwargs.get("seed", args[1] if len(args) > 1 else 0)
        super().__init__(*args, **kwargs)
        self.return_ratio_threshold = return_ratio_threshold
        self.hidden_sigma_threshold = hidden_sigma_threshold

        # Replay the engine's exact particle-generation RNG so we can recover
        # the component values belonging to every (hypothesis, particle).
        rng = np.random.default_rng(particle_seed)
        self._states = []
        for h in self.hyps:
            row = []
            for _ in range(self.K):
                row.append(apply_atoms(sample_healthy(rng), h, rng))
            self._states.append(row)

    def reset(self, seed: int = 0):
        super().reset(seed)
        self.physics_investigation: dict | None = None

    @staticmethod
    def _history_value(guard, tool: str, selector_key: str, selector_value: str, field: str):
        for entry in reversed(guard.history):
            if (
                entry.get("status") == "ok"
                and entry.get("tool") == tool
                and entry.get("args", {}).get(selector_key) == selector_value
            ):
                data = entry.get("data") or {}
                if field in data:
                    return float(data[field])
        return None

    def _ensure_scalar(self, guard, tool: str, args: dict, field: str):
        key = "point" if tool == "read_voltage" else "channel"
        value = args[key]
        existing = self._history_value(guard, tool, key, value, field)
        if existing is not None:
            return existing

        res = guard.call(tool, args)
        if res["status"] != "ok":
            self.mark_failed(tool, args)
            return None
        self.update(tool, args, res["data"])
        return float(res["data"][field])

    def _best_state(self):
        lw = self.logprior[:, None] + self.LL
        hi, k = np.unravel_index(np.argmax(lw), lw.shape)
        return hi, k, self._states[hi][k]

    def investigate_physics(self, guard) -> dict:
        """Acquire enough evidence to evaluate current-balance residuals."""
        i_src = self._ensure_scalar(guard, "read_current", {"channel": "src"}, "amps")
        i_motor = self._ensure_scalar(guard, "read_current", {"channel": "motor"}, "amps")
        v_lamp = self._ensure_scalar(guard, "read_voltage", {"point": "lamp"}, "volts")
        v_load = self._ensure_scalar(guard, "read_voltage", {"point": "load"}, "volts")

        if None in (i_src, i_motor, v_lamp, v_load) or abs(i_src) < 1e-6:
            return {
                "status": "insufficient_evidence",
                "elements": [],
                "reason": "required KCL evidence unavailable",
            }

        _, _, state = self._best_state()
        p = state.params
        g_lamp = 1.0 / p.r_lamp
        g_load = 1.0 / p.r_load
        denom = g_lamp + g_load

        # If no extra load exists, this is the return-node voltage required
        # for measured source current to equal the known branch currents.
        v_ret_zero_hidden = (
            v_lamp * g_lamp + v_load * g_load + i_motor - i_src
        ) / denom
        r_return_est = v_ret_zero_hidden / i_src
        return_ratio = r_return_est / max(p.r_gnd, 1e-9)

        # If instead the return path is healthy, this is current not accounted
        # for by the motor, lamp and resistive-load branches.
        v_ret_expected = i_src * p.r_gnd
        i_hidden = (
            i_src
            - i_motor
            - (v_lamp - v_ret_expected) / p.r_lamp
            - (v_load - v_ret_expected) / p.r_load
        )

        # First-order measurement uncertainty. Parameter tolerance is already
        # represented by the selected physical particle; this sigma represents
        # instrument noise for the four newly used observations.
        sigma_i = self.sigma0["i"]
        sigma_v = self.sigma0["v"]
        sigma_hidden = math.sqrt(
            2.0 * sigma_i**2
            + (sigma_v / p.r_lamp) ** 2
            + (sigma_v / p.r_load) ** 2
        )
        hidden_z = i_hidden / max(sigma_hidden, 1e-9)

        # These are generic physical interpretations, not discrete H1/H2
        # likelihoods. A very large positive required return resistance means
        # the common return path is abnormal. A strongly positive unaccounted
        # current means there is an unmodelled branch/load (or a source-current
        # measurement problem; future sensor-health fusion can separate them).
        if return_ratio >= self.return_ratio_threshold:
            elements = ["ground_return"]
            status = "diagnosed"
            explanation = "KCL closes only with an abnormally large shared-return resistance"
        elif hidden_z >= self.hidden_sigma_threshold and r_return_est < 0:
            elements = ["unmonitored_load"]
            status = "diagnosed"
            explanation = "KCL requires positive unaccounted branch current under a healthy return path"
        else:
            elements = []
            status = "out_of_model"
            explanation = "known model rejected, but conservation residual does not localise one physical slack"

        return {
            "status": status,
            "elements": elements,
            "reason": explanation,
            "r_return_est_ohm": round(float(r_return_est), 4),
            "return_ratio": round(float(return_ratio), 2),
            "unaccounted_current_a": round(float(i_hidden), 4),
            "unaccounted_current_z": round(float(hidden_z), 2),
        }

    def diagnosis(self) -> dict:
        d = super().diagnosis()
        d["physics_investigation"] = self.physics_investigation
        return d

    def run(self, guard, seed: int = 0, max_steps: int = 30) -> dict:
        d = super().run(guard, seed=seed, max_steps=max_steps)

        if d["status"] == "out_of_model":
            self.physics_investigation = self.investigate_physics(guard)
            if self.physics_investigation["status"] == "diagnosed":
                # Keep the Bayesian report/residuals, but replace the terminal
                # answer with the physical residual localisation.
                d = self.diagnosis()
                d["status"] = "diagnosed"
                d["elements"] = list(self.physics_investigation["elements"])
            else:
                d = self.diagnosis()

        return {"method": "bayes_v3_residual", **d}
