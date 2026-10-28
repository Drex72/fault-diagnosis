"""Experimental Bayesian engine V2.

This keeps the V1 hypothesis/likelihood model intact and changes three things
that can be evaluated independently of new fault physics:

1. Tests are ranked by expected reduction in Bayes decision error, not only
   entropy reduction. This asks whether a test is likely to change the final
   diagnosis, rather than rewarding uncertainty reduction that cannot affect
   the decision.
2. The fixed 95 % stopping rule becomes value-of-information aware: after
   reaching high confidence, testing continues only when the best legal test
   still has enough expected decision-error reduction per unit cost.
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
        min_confirm_score: float = 0.005,
        sensor_gate_z: float = 10.0,
        verify_signatures: bool = False,
        challenge_complex_hypothesis: bool = False,
        min_signature_z: float = 4.0,
        max_verifications: int = 2,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.min_risk_reduction = min_risk_reduction
        self.min_challenge_score = min_challenge_score
        self.min_confirm_score = min_confirm_score
        self.sensor_gate_z = sensor_gate_z
        self.verify_signatures = verify_signatures
        self.challenge_complex_hypothesis = challenge_complex_hypothesis
        self.min_signature_z = min_signature_z
        self.max_verifications = max_verifications
        self._signature_z = self._build_signature_map()

    def reset(self, seed: int = 0):
        super().reset(seed)
        self.suspect_current: list[dict] = []
        self.challenge_done = False
        self.challenge_info: dict | None = None
        self.confirmations: list[dict] = []
        self.verifications: list[dict] = []

    # ----- automatically derived fault signatures -----------------------
    def _expected_prediction(self, ai: int, hi: int) -> np.ndarray:
        """Expected reading for one hypothesis, marginalising lamp intermittency."""
        P = self.pred[ai][hi]  # (K, 2, D)
        po = self.p_open[hi][:, None]
        return ((1.0 - po) * P[:, 0] + po * P[:, 1]).mean(axis=0)

    def _build_signature_map(self) -> dict:
        """Standardised action sensitivity for every single fault atom.

        No hand-written mapping says which instrument confirms which fault.
        The map is derived from the forward model by comparing each single
        fault with the healthy prediction in units of expected sensor noise.
        """
        from .faults import ATOMS

        healthy = 0
        out = {}
        for atom in ATOMS:
            hi = self.hyps.index(frozenset([atom]))
            scores = {}
            for ai in range(len(ACTIONS)):
                mu0 = self._expected_prediction(ai, healthy)
                mu1 = self._expected_prediction(ai, hi)
                sig = self._sigma(ai, mu0)
                z = float(np.sqrt(np.sum(((mu1 - mu0) / sig) ** 2)))
                scores[ai] = z
            out[atom] = scores
        return out

    def signature_map(self, n: int = 3) -> dict:
        out = {}
        for atom, scores in self._signature_z.items():
            order = sorted(scores, key=scores.get, reverse=True)[:n]
            out[atom] = [
                {
                    "tool": ACTIONS[ai][0],
                    "args": ACTIONS[ai][1],
                    "signature_z": round(scores[ai], 2),
                }
                for ai in order
            ]
        return out

    def _verification_option(self, guard) -> dict | None:
        """Cheapest strong, not-yet-observed witness for an unsupported MAP atom."""
        post = self.posterior()
        hi = int(np.argmax(post))
        atoms = list(self.hyps[hi])
        if not atoms:
            return None

        observed = {ai for ai, _ in self.obs}
        candidates = []
        for atom in atoms:
            scores = self._signature_z[atom]
            if any(scores[ai] >= self.min_signature_z for ai in observed):
                continue
            for ai, z in scores.items():
                tool, args = ACTIONS[ai]
                if z < self.min_signature_z or ai in observed or ai in self.failed or guard.check(tool, args):
                    continue
                cost = guard.cost_of(tool, args)
                candidates.append(
                    {
                        "atom": atom,
                        "tool": tool,
                        "args": args,
                        "signature_z": z,
                        "cost": cost,
                        "score": z / cost,
                    }
                )
        if not candidates:
            return None
        return max(candidates, key=lambda x: (x["score"], x["signature_z"]))

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
        d["challenge_info"] = self.challenge_info
        d["confirmations"] = list(self.confirmations)
        d["verifications"] = list(self.verifications)
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
                # A high-confidence two-fault explanation is expensive in the
                # model's prior and can also be how an unseen single physical
                # fault gets "explained away". Give such complex explanations
                # one counterfactual falsification probe before accepting them.
                top_i = int(np.argmax(post))
                if self.challenge_complex_hypothesis and len(self.hyps[top_i]) >= 2 and not self.challenge_done:
                    option = self.challenge(guard)
                    if option is not None:
                        self.challenge_done = True
                        self.challenge_info = {
                            k: (round(v, 4) if isinstance(v, float) else v)
                            for k, v in option.items()
                        }
                        res = guard.call(option["tool"], option["args"])
                        if res["status"] == "ok":
                            self.update(option["tool"], option["args"], res["data"])
                        else:
                            self.mark_failed(option["tool"], option["args"])
                        continue

                # Optional model-derived witness: only verify a fault when none
                # of the measurements already taken is strongly sensitive to
                # that fault according to the forward model.
                if self.verify_signatures and len(self.verifications) < self.max_verifications:
                    verify = self._verification_option(guard)
                    if verify is not None:
                        self.verifications.append(
                            {
                                "atom": verify["atom"],
                                "tool": verify["tool"],
                                "args": verify["args"],
                                "signature_z": round(float(verify["signature_z"]), 2),
                            }
                        )
                        res = guard.call(verify["tool"], verify["args"])
                        if res["status"] == "ok":
                            self.update(verify["tool"], verify["args"], res["data"])
                        else:
                            self.mark_failed(verify["tool"], verify["args"])
                        continue

                # Otherwise continue only when another test still has enough
                # decision value per unit cost. Setting min_confirm_score >= 1
                # reproduces a hard stop at stop_p.
                if best["score"] < self.min_confirm_score:
                    break
                self.confirmations.append(
                    {
                        "tool": best["tool"],
                        "args": best["args"],
                        "score": round(float(best["score"]), 4),
                        "risk_reduction": best["risk_reduction"],
                    }
                )
            elif best["risk_reduction"] < self.min_risk_reduction:
                break

            tool, args = best["tool"], best["args"]
            res = guard.call(tool, args)
            if res["status"] == "ok":
                self.update(tool, args, res["data"])
            else:
                self.mark_failed(tool, args)

        d = self.diagnosis()
        return {"method": "bayes_v2", **d}


class BayesEngineV2Probe(BayesEngineV2):
    """V2 plus Bayesian use of the technician's independent handheld meter.

    Probe candidates are exposed only when a voltage tap has failed or the
    posterior assigns material probability to that tap's sensor being faulty.
    The probe is expensive (policy cost 10), so it still competes against cheap
    automatic measurements on expected decision-risk reduction per unit cost.
    """

    def __init__(self, *args, sigma_probe: float = 0.015, probe_sensor_p: float = 0.20, max_human_probes: int = 2, marginal_fault_p: float | None = None, **kwargs):
        # Replay the exact particle RNG after the base initialisation so the
        # clean physical node voltages line up with the same (h, k) particles
        # used by V1/V2. snapshot() includes sensor offsets; a handheld meter
        # must not, which is why separate clean predictions are needed.
        particle_seed = kwargs.get("seed", args[1] if len(args) > 1 else 0)
        super().__init__(*args, **kwargs)
        self.sigma_probe = sigma_probe
        self.probe_sensor_p = probe_sensor_p
        self.max_human_probes = max_human_probes
        self.marginal_fault_p = marginal_fault_p

        from dataclasses import replace
        from .circuit import solve
        from .faults import apply_atoms, sample_healthy
        from .measure import PROBE_POINTS

        self.probe_points = PROBE_POINTS
        self._probe_index = {p: i for i, p in enumerate(PROBE_POINTS)}
        self.clean_v = np.zeros((self.H, self.K, 2, len(PROBE_POINTS)))
        rng = np.random.default_rng(particle_seed)
        for hi, h in enumerate(self.hyps):
            for k in range(self.K):
                st = apply_atoms(sample_healthy(rng), h, rng)
                for s, lamp_open in enumerate((False, True)):
                    p = replace(st.params, lamp_open=lamp_open)
                    sol = solve(p)
                    for pi, point in enumerate(PROBE_POINTS):
                        self.clean_v[hi, k, s, pi] = sol.v[point]

    def reset(self, seed: int = 0):
        super().reset(seed)
        self.probe_obs: list[dict] = []

    def _probe_means(self, point_a: str, point_b: str) -> np.ndarray:
        ia, ib = self._probe_index[point_a], self._probe_index[point_b]
        return self.clean_v[..., ia] - self.clean_v[..., ib]  # (H, K, 2)

    def _probe_sigma(self, mean: np.ndarray) -> np.ndarray:
        return np.sqrt(self.sigma_probe ** 2 + (0.002 * np.abs(mean)) ** 2)

    def _probe_loglik(self, point_a: str, point_b: str, y: np.ndarray):
        import math

        P = self._probe_means(point_a, point_b)
        sig = self._probe_sigma(P)
        yy = np.asarray(y, dtype=float)[..., None, None, None]
        z = (yy - P) / sig
        logn = -0.5 * z ** 2 - np.log(sig) - 0.5 * math.log(2 * math.pi)
        po = self.p_open
        with np.errstate(divide="ignore"):
            mix = np.logaddexp(np.log1p(-po) + logn[..., 0], np.log(po) + logn[..., 1])
        zbest = np.where(po > 0, np.abs(z).min(-1), np.abs(z[..., 0]))
        return mix, zbest

    def update(self, tool: str, args: dict, data: dict):
        if tool != "human_probe":
            return super().update(tool, args, data)

        y = np.array(float(data["volts"]))
        mix, zbest = self._probe_loglik(args["point_a"], args["point_b"], y)
        self.LL += mix
        self.MAXZ = np.maximum(self.MAXZ, zbest)
        self.probe_obs.append(
            {
                "point_a": args["point_a"],
                "point_b": args["point_b"],
                "volts": round(float(y), 4),
            }
        )

    def _future_probe_posteriors(self, point_a: str, point_b: str, W: np.ndarray) -> np.ndarray:
        P = self._probe_means(point_a, point_b)
        flat = W.ravel()
        flat = flat / flat.sum()
        idx = self.rng.choice(self.H * self.K, size=self.n_eig, p=flat)
        hi, ki = np.divmod(idx, self.K)
        states = (self.rng.random(self.n_eig) < self.p_open[hi, ki]).astype(int)
        mean = P[hi, ki, states]
        y = mean + self.rng.normal(size=mean.shape) * self._probe_sigma(mean)

        mix, _ = self._probe_loglik(point_a, point_b, y)
        lw = np.log(np.clip(W, 1e-300, None))[None] + mix
        lw -= lw.max(axis=(1, 2), keepdims=True)
        w = np.exp(lw)
        return w.sum(2) / w.sum(axis=(1, 2))[:, None]

    def _probe_metrics(self, point_a: str, point_b: str) -> tuple[float, float]:
        W = self._joint()
        post = W.sum(1)
        post_new = self._future_probe_posteriors(point_a, point_b, W)
        risk_now = 1.0 - float(post.max())
        risk_after = 1.0 - post_new.max(axis=1)
        risk_gain = max(0.0, risk_now - float(risk_after.mean()))
        eig = float(_entropy(post) - _entropy(post_new).mean())
        return risk_gain, eig

    def _probe_candidates(self, guard) -> list[dict]:
        if len(self.probe_obs) >= self.max_human_probes:
            return []
        points = set()

        # Direct substitution for an automatic voltage tap that failed.
        for ai in self.failed:
            tool, args = ACTIONS[ai]
            if tool == "read_voltage":
                points.add(args["point"])

        # An independent meter is also useful when the posterior itself says a
        # voltage sensor may be lying, even if the automatic read did not fail.
        post = self.posterior()
        for point in ("motor", "lamp", "load"):
            atom = f"F5@{point}"
            p_sensor = sum(float(post[hi]) for hi, h in enumerate(self.hyps) if atom in h)
            if p_sensor >= self.probe_sensor_p:
                points.add(point)

        candidates = []
        for point in sorted(points):
            pairs = [(point, "neg")]
            if point != "bus":
                pairs.append(("bus", point))
            for a, b in pairs:
                args = {"point_a": a, "point_b": b}
                if guard.check("human_probe", args):
                    continue
                risk_gain, eig = self._probe_metrics(a, b)
                cost = guard.cost_of("human_probe", args)
                candidates.append(
                    {
                        "tool": "human_probe",
                        "args": args,
                        "eig_bits": round(eig, 3),
                        "risk_reduction": round(risk_gain, 4),
                        "cost": cost,
                        "score": risk_gain / cost,
                    }
                )
        return candidates

    def suggest(self, guard, n: int = 3) -> list[dict]:
        ranked = super().suggest(guard, n=len(ACTIONS))
        ranked.extend(self._probe_candidates(guard))
        return sorted(ranked, key=lambda r: (-r["score"], -r["eig_bits"]))[:n]

    def atom_marginals(self) -> dict[str, float]:
        """Posterior probability that each fault atom is present.

        This is different from selecting the single MAP joint hypothesis.
        Marginals are useful when one component is unobservable: a compound
        hypothesis should not force an unsupported extra fault into the answer.
        """
        from .faults import ATOMS

        post = self.posterior()
        return {
            atom: float(sum(post[hi] for hi, h in enumerate(self.hyps) if atom in h))
            for atom in ATOMS
        }

    def diagnosis(self) -> dict:
        d = super().diagnosis()
        d["human_probes"] = list(self.probe_obs)
        d["fault_marginals"] = {k: round(v, 3) for k, v in self.atom_marginals().items()}

        if self.marginal_fault_p is not None and d["status"] == "diagnosed":
            from .faults import ATOM_ELEMENT

            selected = [
                ATOM_ELEMENT[atom]
                for atom, p in self.atom_marginals().items()
                if p >= self.marginal_fault_p
            ]
            d["elements"] = sorted(set(selected))
        return d
