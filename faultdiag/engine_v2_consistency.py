"""Physics-consistency experiment on top of BayesEngineV2.

The check is deliberately not tied to a named held-out fault.  It evaluates
Kirchhoff current balance using four ordinary measurements and compares the
derived residual with the residual distribution produced by *all* known
Bayesian particles.  If no known physical particle can explain the aggregate
balance, the known-fault model is rejected.

This is an experimental model-adequacy layer.  V1 and the core V2 remain
unchanged.
"""
from __future__ import annotations

import numpy as np

from .engine import _ACTION_INDEX
from .engine_v2 import BayesEngineV2
from .measure import selector


_BALANCE_ACTIONS = (
    ("read_current", {"channel": "src"}),
    ("read_current", {"channel": "motor"}),
    ("read_voltage", {"point": "lamp"}),
    ("read_voltage", {"point": "load"}),
)
_R_LAMP = 14.4
_R_LOAD = 24.0


class BayesEngineV2Consistency(BayesEngineV2):
    def __init__(self, *args, consistency_z: float = 5.0, cross_branch_only: bool = False, **kwargs):
        # Keep the core V2 hard stop.  This class owns the single extra
        # model-adequacy probe.
        kwargs.setdefault("min_confirm_score", 1.0)
        super().__init__(*args, **kwargs)
        self.consistency_z_threshold = consistency_z
        self.cross_branch_only = cross_branch_only
        self.consistency_done = False
        self.consistency_report: dict | None = None
        self.consistency_misfit = False

    def reset(self, seed: int = 0):
        super().reset(seed)
        self.consistency_done = False
        self.consistency_report = None
        self.consistency_misfit = False

    def _latest_scalar(self, ai: int) -> float | None:
        for obs_ai, y in reversed(self.obs):
            if obs_ai == ai and y.size == 1:
                return float(y[0])
        return None

    def _collect_balance_probe(self, guard) -> bool:
        """Collect only missing measurements needed for the balance residual."""
        for tool, args in _BALANCE_ACTIONS:
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
            # A quarantined current reading is intentionally unavailable for
            # balance testing; do not turn sensor garbage into circuit evidence.
            if self._latest_scalar(ai) is None:
                return False
        return True

    def _balance_z(self) -> tuple[float, float, float]:
        """Return (best-known |z|, observed residual A, nearest predicted A).

        For each (hypothesis, particle, lamp-state), form the same aggregate
        current-balance residual from its predicted measurements.  The
        observation is compared against the *closest* known particle, making
        this a conservative model-rejection test rather than a classifier.
        """
        ais = [_ACTION_INDEX[(t, selector(t, a))] for t, a in _BALANCE_ACTIONS]
        vals = [self._latest_scalar(ai) for ai in ais]
        if any(v is None for v in vals):
            raise RuntimeError("balance probe incomplete")
        i_src, i_motor, v_lamp, v_load = (float(v) for v in vals)
        q_obs = i_src - i_motor - v_lamp / _R_LAMP - v_load / _R_LOAD

        ps = [self.pred[ai][..., 0] for ai in ais]  # each (H,K,2)
        q_pred = ps[0] - ps[1] - ps[2] / _R_LAMP - ps[3] / _R_LOAD

        # First-order propagation of the four independent instrument-noise
        # terms.  Shared calibration covariance will be fitted from hardware
        # later; this simulation experiment intentionally does not invent it.
        sigs = [self._sigma(ai, self.pred[ai])[..., 0] for ai in ais]
        sig_q = np.sqrt(
            sigs[0] ** 2
            + sigs[1] ** 2
            + (sigs[2] / _R_LAMP) ** 2
            + (sigs[3] / _R_LOAD) ** 2
        )
        z = np.abs(q_obs - q_pred) / np.maximum(sig_q, 1e-9)

        # Lamp-open state is impossible for particles with p_open == 0.
        impossible = self.p_open == 0
        z[:, :, 1] = np.where(impossible, np.inf, z[:, :, 1])
        flat_i = int(np.argmin(z))
        hi, ki, si = np.unravel_index(flat_i, z.shape)
        return float(z[hi, ki, si]), float(q_obs), float(q_pred[hi, ki, si])

    @staticmethod
    def _atom_zone(atom: str) -> str:
        # Structural location only; this does not encode any held-out fault.
        if atom == "F1":
            return "source"
        if atom in ("F2", "F3", "F5@motor"):
            return "motor"
        if atom in ("F4", "F5@lamp"):
            return "lamp"
        if atom == "F5@load":
            return "load"
        return "unknown"

    def _should_probe(self, hi: int) -> bool:
        atoms = self.hyps[hi]
        if len(atoms) < 2:
            return False
        if not self.cross_branch_only:
            return True
        # Pay for the conservation probe only when the current explanation
        # needs abnormalities on at least two different downstream branches.
        # An upstream source fault plus one local fault (e.g. C1) and two
        # faults co-located on one branch (e.g. C2) are not challenged.
        zones = {self._atom_zone(a) for a in atoms}
        downstream = zones - {"source", "unknown"}
        return len(downstream) >= 2

    def misfit(self) -> bool:
        return self.consistency_misfit or super().misfit()

    def diagnosis(self) -> dict:
        d = super().diagnosis()
        d["consistency_probe"] = self.consistency_report
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
                # Complex diagnoses are exactly where an unmodelled physical
                # cause can be "explained away" by two known faults.  Before
                # accepting one, run a topology-level conservation check once.
                if self._should_probe(top_i) and not self.consistency_done:
                    self.consistency_done = True
                    if self._collect_balance_probe(guard):
                        z, observed, nearest = self._balance_z()
                        self.consistency_report = {
                            "z": round(z, 2),
                            "observed_unaccounted_a": round(observed, 4),
                            "nearest_known_a": round(nearest, 4),
                            "threshold": self.consistency_z_threshold,
                        }
                        if z > self.consistency_z_threshold:
                            self.consistency_misfit = True
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
        return {"method": "bayes_v2_consistency", **d}
