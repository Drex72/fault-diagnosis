"""Bayesian human-probe fallback for instrumentation failures.

A technician with a DMM is treated as a high-cost sensor, not as an LLM-only
capability.  Candidate probes come from the circuit topology (element voltage
drops and node-to-return checks), and are considered only after an automated
measurement has failed.

This keeps the model auditable: the same physical particles predict both the
automated readings and the handheld readings.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .circuit import solve
from .engine_v2_balance import BayesEngineV2Balance
from .faults import apply_atoms, sample_healthy


# Topology-derived probe set: element drops and useful node references.
# These are not keyed to a particular fault label.
HUMAN_PROBES = (
    ("src", "bus"),
    ("bus", "motor"),
    ("bus", "lamp"),
    ("bus", "load"),
    ("motor", "ret"),
    ("lamp", "ret"),
    ("load", "ret"),
    ("bus", "ret"),
    ("ret", "neg"),
    ("src", "neg"),
)


class BayesEngineV2Human(BayesEngineV2Balance):
    def __init__(
        self,
        *args,
        seed: int = 0,
        min_human_score: float = 0.006,
        max_human_probes: int = 2,
        **kwargs,
    ):
        self._model_seed = seed
        super().__init__(*args, seed=seed, **kwargs)
        self.min_human_score = min_human_score
        self.max_human_probes = max_human_probes
        self.human_pred = self._build_human_predictions(seed)

    def reset(self, seed: int = 0):
        super().reset(seed)
        self.human_obs: list[dict] = []
        self.human_probes_used: set[tuple[str, str]] = set()

    def _build_human_predictions(self, seed: int) -> dict[tuple[str, str], np.ndarray]:
        """Recreate the same physical particles used by the base engine.

        BayesEngine samples one FaultState per (hypothesis, particle) from a
        deterministic RNG seed.  snapshot() consumes no randomness, so replaying
        that sampling sequence reconstructs the physical node voltages exactly,
        before multiplexed-sensor offsets are applied.
        """
        rng = np.random.default_rng(seed)
        out = {pair: np.zeros((self.H, self.K, 2)) for pair in HUMAN_PROBES}
        for hi, h in enumerate(self.hyps):
            for k in range(self.K):
                st = apply_atoms(sample_healthy(rng), h, rng)
                for si, lamp_open in enumerate((False, True)):
                    p = replace(st.params, lamp_open=lamp_open)
                    v = solve(p).v
                    for pair in HUMAN_PROBES:
                        out[pair][hi, k, si] = v[pair[0]] - v[pair[1]]
        return out

    def _human_sigma(self, P: np.ndarray) -> np.ndarray:
        # Keep the same conservative model-discrepancy floor used for voltage
        # taps. The simulated DMM itself is quieter (5 mV), but component/model
        # uncertainty is more important than pretending the meter is perfect.
        return np.sqrt(self.sigma0["v"] ** 2 + (self.rel * P) ** 2)

    def _human_loglik(self, pair: tuple[str, str], y):
        P = self.human_pred[pair]
        sig = self._human_sigma(P)
        y = np.asarray(y, dtype=float)[..., None, None, None]
        z = (y - P) / sig
        logn = -0.5 * z ** 2 - np.log(sig) - 0.5 * math.log(2 * math.pi)
        po = self.p_open
        with np.errstate(divide="ignore"):
            mix = np.logaddexp(np.log1p(-po) + logn[..., 0], np.log(po) + logn[..., 1])
        zabs = np.abs(z)
        zbest = np.where(po > 0, zabs.min(-1), zabs[..., 0])
        return mix, zbest

    def _human_metrics(self, pair: tuple[str, str]) -> tuple[float, float]:
        W = self._joint()
        post = W.sum(1)
        P = self.human_pred[pair]

        idx = self.rng.choice(self.H * self.K, size=self.n_eig, p=W.ravel())
        hi, ki = np.divmod(idx, self.K)
        states = (self.rng.random(self.n_eig) < self.p_open[hi, ki]).astype(int)
        mean = P[hi, ki, states]
        y = mean + self.rng.normal(size=mean.shape) * self._human_sigma(mean)

        mix, _ = self._human_loglik(pair, y)
        lw = np.log(np.clip(W, 1e-300, None))[None] + mix
        lw -= lw.max(axis=(1, 2), keepdims=True)
        w = np.exp(lw)
        post_new = w.sum(2) / w.sum(axis=(1, 2))[:, None]

        risk_now = 1.0 - float(post.max())
        risk_after = 1.0 - post_new.max(axis=1)
        risk_gain = max(0.0, risk_now - float(risk_after.mean()))

        from .engine import _entropy
        eig = float(_entropy(post) - _entropy(post_new).mean())
        return risk_gain, eig

    def human_suggest(self, guard) -> dict | None:
        if not self.failed or len(self.human_probes_used) >= self.max_human_probes:
            return None
        ranked = []
        for pair in HUMAN_PROBES:
            if pair in self.human_probes_used:
                continue
            args = {"point_a": pair[0], "point_b": pair[1]}
            if guard.check("human_probe", args):
                continue
            gain, eig = self._human_metrics(pair)
            cost = guard.cost_of("human_probe", args)
            ranked.append({
                "tool": "human_probe",
                "args": args,
                "pair": pair,
                "risk_reduction": gain,
                "eig_bits": eig,
                "cost": cost,
                "score": gain / cost,
            })
        if not ranked:
            return None
        best = max(ranked, key=lambda x: (x["score"], x["eig_bits"]))
        return best if best["score"] >= self.min_human_score else None

    def _human_update(self, pair: tuple[str, str], volts: float):
        mix, zbest = self._human_loglik(pair, float(volts))
        self.LL += mix
        self.MAXZ = np.maximum(self.MAXZ, zbest)
        self.human_obs.append({"pair": pair, "volts": float(volts)})
        self.human_probes_used.add(pair)

    def _take_human(self, guard, option: dict) -> bool:
        res = guard.call("human_probe", option["args"])
        if res["status"] != "ok":
            return False
        self._human_update(option["pair"], res["data"]["volts"])
        return True

    def diagnosis(self) -> dict:
        d = super().diagnosis()
        d["human_measurements"] = list(self.human_obs)
        return d

    def run(self, guard, seed: int = 0, max_steps: int = 30) -> dict:
        self.reset(seed)
        for _ in range(max_steps):
            if self.misfit():
                break

            post = self.posterior()
            auto_options = self.suggest(guard, n=1)
            auto = auto_options[0] if auto_options else None
            human = self.human_suggest(guard)

            if post.max() >= self.stop_p:
                # A failed high-value instrument can make a confident answer
                # brittle. Buy a human measurement only if its expected
                # decision-error reduction justifies the configured cost.
                if human is not None:
                    self._take_human(guard, human)
                    continue

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

            # When an instrument has failed, compare the best remaining
            # automated action with the best human action on the same
            # decision-risk-per-cost scale.
            candidates = []
            if auto is not None and auto["risk_reduction"] >= self.min_risk_reduction:
                candidates.append(auto)
            if human is not None:
                candidates.append(human)
            if not candidates:
                break
            best = max(candidates, key=lambda x: (x["score"], x["eig_bits"]))

            if best["tool"] == "human_probe":
                self._take_human(guard, best)
                continue

            res = guard.call(best["tool"], best["args"])
            if res["status"] == "ok":
                self.update(best["tool"], best["args"], res["data"])
            else:
                self.mark_failed(best["tool"], best["args"])

        d = self.diagnosis()
        return {"method": "bayes_v2_human", **d}
