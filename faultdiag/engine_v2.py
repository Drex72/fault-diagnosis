"""Experimental Bayesian engine V2.

This keeps the V1 hypothesis/likelihood model intact and changes three things
that can be evaluated independently of new fault physics:

1. Tests are ranked by expected reduction in Bayes decision error, not only
   entropy reduction. This asks whether a test is likely to change the final
   diagnosis, rather than rewarding uncertainty reduction that cannot affect
   the decision.
2. Before stopping at very high confidence, one counterfactual challenge test
   may be taken. The challenge temporarily caps the leading hypothesis at 50 %
   and asks which legal test would best separate it from plausible
   alternatives. This is a guard against premature certainty.
3. A scalar current reading that is incompatible with every physical particle
   by a very large margin is treated as a suspect current sensor/tool result,
   not as decisive evidence about the circuit. Voltage readings are not gated
   this way because an unseen physical wiring fault can legitimately move them.

The original BayesEngine remains unchanged and is the paired baseline.
"""
from __future__ import annotations

import numpy as np

from .engine import ACTIONS, _ACTION_INDEX, BayesEngine, _entropy
from .measure import OBS_KEYS, obs_key, selector


class BayesEngineV2(BayesEngine):
    def __init__(
        self,
        *args,
        min_risk_reduction: float = 0.004,
        min_challenge_score: float = 0.012,
        sensor_gate_z: float = 10.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.min_risk_reduction = min_risk_reduction
        self.min_challenge_score = min_challenge_score
        self.sensor_gate_z = sensor_gate_z

    def reset(self, seed: int = 0):
        super().reset(seed)
        self.suspect_current: list[dict] = []
        self.challenge_done = False

    # ----- robust observation handling ---------------------------------
    def update(self, tool: str, args: dict, data: dict):
        ai = _ACTION_INDEX.get((tool, selector(tool, args)))
        if ai is None:
            return

        y = np.array([data[k] for k in OBS_KEYS[obs_key(tool, args)]], dtype=float)
        mix, zbest = self._loglik(ai, y)

        # A single current reading can be garbage (TF2) or a failed sensor
        # (e.g. H4). If no particle in any known circuit hypothesis can explain
        # it, allowing one Gaussian likelihood to dominate the whole history is
        # brittle. Quarantine only scalar current reads; do NOT apply this to
        # voltages or load-test vectors because unseen circuit faults can make
        # those legitimately inconsistent with the known model.
        if tool == "read_current" and y.size == 1:
            best_z = float(np.min(zbest))
            if best_z > self.sensor_gate_z:
                self.failed.add(ai)
                self.suspect_current.append(
                    {
                        "channel": args["channel"],
                        "measured": round(float(y[0]), 4),
                        "best_known_z": round(best_z, 2),
                    }
                )
                return

        self.LL += mix
        self.MAXZ = np.maximum(self.MAXZ, zbest)
        self.obs.append((ai, y))

    # ----- decision-focused active testing -----------------------------
    def _future_posteriors(self, ai: int, W: np.ndarray) -> np.ndarray:
        """Monte-Carlo posteriors after taking action ai under joint weights W."""
        P = self.pred[ai]
        flat = W.ravel()
        flat = flat / flat.sum()
        idx = self.rng.choice(self.H * self.K, size=self.n_eig, p=flat)
        hi, ki = np.divmod(idx, self.K)
        states = (self.rng.random(self.n_eig) < self.p_open[hi, ki]).astype(int)
        mean = P[hi, ki, states]
        y = mean + self.rng.normal(size=mean.shape) * self._sigma(ai, mean)

        mix, _ = self._loglik(ai, y)
        lw = np.log(np.clip(W, 1e-300, None))[None] + mix
        lw -= lw.max(axis=(1, 2), keepdims=True)
        w = np.exp(lw)
        return w.sum(2) / w.sum(axis=(1, 2))[:, None]

    def _metrics(self, ai: int, W: np.ndarray | None = None) -> tuple[float, float]:
        """Return (expected decision-risk reduction, entropy EIG in bits)."""
        W = self._joint() if W is None else W
        post = W.sum(1)
        post_new = self._future_posteriors(ai, W)

        risk_now = 1.0 - float(post.max())
        risk_after = 1.0 - post_new.max(axis=1)
        risk_gain = max(0.0, risk_now - float(risk_after.mean()))

        eig = float(_entropy(post) - _entropy(post_new).mean())
        return risk_gain, eig

    def eig(self, ai: int) -> float:
        # Keep the public method for agent/report compatibility.
        return self._metrics(ai)[1]

    def suggest(self, guard, n: int = 3) -> list[dict]:
        ranked = []
        for ai, (tool, args) in enumerate(ACTIONS):
            if ai in self.failed or guard.check(tool, args):
                continue
            risk_gain, eig = self._metrics(ai)
            cost = guard.cost_of(tool, args)
            ranked.append(
                {
                    "tool": tool,
                    "args": args,
                    "eig_bits": round(eig, 3),
                    "risk_reduction": round(risk_gain, 4),
                    "cost": cost,
                    "score": risk_gain / cost,
                }
            )
        return sorted(ranked, key=lambda r: (-r["score"], -r["eig_bits"]))[:n]

    def _challenge_weights(self) -> np.ndarray:
        """A 50/50 counterfactual between the MAP hypothesis and alternatives.

        Particle proportions inside the MAP hypothesis follow the current
        posterior. The other half uses the original prior over all non-MAP
        hypotheses, preventing an already-overconfident posterior from making
        every possible challenge look worthless.
        """
        current = self._joint()
        post = current.sum(1)
        top = int(np.argmax(post))

        W = np.zeros_like(current)
        top_mass = current[top]
        W[top] = 0.5 * top_mass / top_mass.sum()

        prior_h = np.exp(self.logprior - np.max(self.logprior))
        prior_h[top] = 0.0
        prior_h /= prior_h.sum()
        W += (0.5 * prior_h[:, None]) / self.K
        return W

    def challenge(self, guard) -> dict | None:
        W = self._challenge_weights()
        ranked = []
        for ai, (tool, args) in enumerate(ACTIONS):
            if ai in self.failed or guard.check(tool, args):
                continue
            risk_gain, eig = self._metrics(ai, W)
            cost = guard.cost_of(tool, args)
            ranked.append(
                {
                    "tool": tool,
                    "args": args,
                    "challenge_gain": risk_gain,
                    "eig_bits": eig,
                    "cost": cost,
                    "score": risk_gain / cost,
                }
            )
        if not ranked:
            return None
        best = max(ranked, key=lambda r: (r["score"], r["eig_bits"]))
        return best if best["score"] >= self.min_challenge_score else None

    def diagnosis(self) -> dict:
        d = super().diagnosis()
        d["suspect_current_sensors"] = list(self.suspect_current)
        d["challenge_performed"] = self.challenge_done
        return d

    def run(self, guard, seed: int = 0, max_steps: int = 30) -> dict:
        self.reset(seed)
        for _ in range(max_steps):
            if self.misfit():
                break

            post = self.posterior()
            if post.max() >= self.stop_p:
                if not self.challenge_done:
                    option = self.challenge(guard)
                    if option is not None:
                        self.challenge_done = True
                        tool, args = option["tool"], option["args"]
                        res = guard.call(tool, args)
                        if res["status"] == "ok":
                            self.update(tool, args, res["data"])
                        else:
                            self.mark_failed(tool, args)
                        continue
                break

            options = self.suggest(guard, n=1)
            if not options or options[0]["risk_reduction"] < self.min_risk_reduction:
                break

            tool, args = options[0]["tool"], options[0]["args"]
            res = guard.call(tool, args)
            if res["status"] == "ok":
                self.update(tool, args, res["data"])
            else:
                self.mark_failed(tool, args)

        d = self.diagnosis()
        return {"method": "bayes_v2", **d}
