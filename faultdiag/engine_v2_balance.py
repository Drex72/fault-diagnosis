"""Two-current model-consistency experiment on top of BayesEngineV2.

The source-current sensor measures every branch.  The motor-current sensor
measures one branch directly.  Their difference is therefore the aggregate
non-motor current.  Rather than assuming a particular hidden fault, compare
that derived quantity against every known physical particle.  A large residual
means the known-fault model cannot account for where the current is going.

This is a cheaper alternative to the four-reading KCL probe.
"""
from __future__ import annotations

import numpy as np

from .engine import _ACTION_INDEX
from .engine_v2 import BayesEngineV2
from .measure import selector


_ACTIONS = (
    ("read_current", {"channel": "src"}),
    ("read_current", {"channel": "motor"}),
)


class BayesEngineV2Balance(BayesEngineV2):
    def __init__(self, *args, balance_z: float = 6.0, **kwargs):
        kwargs.setdefault("min_confirm_score", 1.0)
        super().__init__(*args, **kwargs)
        self.balance_z_threshold = balance_z
        self.balance_done = False
        self.balance_report: dict | None = None
        self.balance_misfit = False

    def reset(self, seed: int = 0):
        super().reset(seed)
        self.balance_done = False
        self.balance_report = None
        self.balance_misfit = False

    def _latest_scalar(self, ai: int) -> float | None:
        for obs_ai, y in reversed(self.obs):
            if obs_ai == ai and y.size == 1:
                return float(y[0])
        return None

    def _collect(self, guard) -> bool:
        for tool, args in _ACTIONS:
            ai = _ACTION_INDEX[(tool, selector(tool, args))]
            if self._latest_scalar(ai) is not None:
                continue
            if ai in self.failed or guard.check(tool, args):
                return False
            res = guard.call(tool, args)
            if res["status"] != "ok":
                self.mark_failed(tool, args)
                return False
            self.update(tool, args, res["data"])
            if self._latest_scalar(ai) is None:
                return False
        return True

    def _balance_z(self) -> tuple[float, float, float]:
        ai_src, ai_motor = [_ACTION_INDEX[(t, selector(t, a))] for t, a in _ACTIONS]
        i_src = self._latest_scalar(ai_src)
        i_motor = self._latest_scalar(ai_motor)
        if i_src is None or i_motor is None:
            raise RuntimeError("current balance probe incomplete")
        q_obs = i_src - i_motor

        p_src = self.pred[ai_src][..., 0]    # (H,K,2)
        p_motor = self.pred[ai_motor][..., 0]
        q_pred = p_src - p_motor

        s_src = self._sigma(ai_src, self.pred[ai_src])[..., 0]
        s_motor = self._sigma(ai_motor, self.pred[ai_motor])[..., 0]
        sig = np.sqrt(s_src ** 2 + s_motor ** 2)
        z = np.abs(q_obs - q_pred) / np.maximum(sig, 1e-9)

        impossible = self.p_open == 0
        z[:, :, 1] = np.where(impossible, np.inf, z[:, :, 1])
        flat_i = int(np.argmin(z))
        hi, ki, si = np.unravel_index(flat_i, z.shape)
        return float(z[hi, ki, si]), float(q_obs), float(q_pred[hi, ki, si])

    def misfit(self) -> bool:
        return self.balance_misfit or super().misfit()

    def diagnosis(self) -> dict:
        d = super().diagnosis()
        d["current_balance_probe"] = self.balance_report
        return d

    def run(self, guard, seed: int = 0, max_steps: int = 30) -> dict:
        self.reset(seed)
        for _ in range(max_steps):
            if self.misfit():
                break

            post = self.posterior()
            options = self.suggest(guard, n=1)
            if not options:
                break
            best = options[0]

            if post.max() >= self.stop_p:
                top_i = int(np.argmax(post))
                if len(self.hyps[top_i]) >= 2 and not self.balance_done:
                    self.balance_done = True
                    if self._collect(guard):
                        z, observed, nearest = self._balance_z()
                        self.balance_report = {
                            "z": round(z, 2),
                            "observed_nonmotor_a": round(observed, 4),
                            "nearest_known_a": round(nearest, 4),
                            "threshold": self.balance_z_threshold,
                        }
                        if z > self.balance_z_threshold:
                            self.balance_misfit = True
                            break
                    continue
                break

            if best["risk_reduction"] < self.min_risk_reduction:
                break

            tool, args = best["tool"], best["args"]
            res = guard.call(tool, args)
            if res["status"] == "ok":
                self.update(tool, args, res["data"])
            else:
                self.mark_failed(tool, args)

        d = self.diagnosis()
        return {"method": "bayes_v2_balance", **d}
