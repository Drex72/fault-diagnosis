import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.engine_robust import BayesEngineRobust  # noqa: E402
from faultdiag.guard import PolicyGuard  # noqa: E402
from faultdiag.harness import make_bench  # noqa: E402
from faultdiag.methods import METHODS, build_methods  # noqa: E402


def guard_for(name, seed=3):
    return PolicyGuard(make_bench(name, seed))


def test_robust_defaults_are_the_validated_configuration():
    engine = BayesEngineRobust(n_samples=12, n_eig_samples=16)
    assert engine.min_confirm_score == 1.0
    assert engine.challenge_complex_hypothesis is True
    assert engine.aggregate_misfit_z == 2.0
    assert engine.return_ratio_threshold == 10.0
    assert engine.hidden_sigma_threshold == 15.0
    assert engine.audit_compound is True
    assert engine.max_human_probes == 1
    assert engine.sensor_gate_z == 10.0


def test_robust_stays_hidden_from_production_method_list():
    assert METHODS == ("fixed", "tree", "bayes", "llm", "hybrid")
    methods, _ = build_methods(["bayes_robust"])
    assert list(methods) == ["bayes_robust"]


def test_failed_voltage_channel_exposes_independent_probe_candidate():
    engine = BayesEngineRobust(n_samples=12, n_eig_samples=16)
    engine.reset(1)
    engine.mark_failed("read_voltage", {"point": "motor"})
    candidates = engine._probe_candidates(guard_for("TF1", seed=1))
    assert candidates
    assert all(row["tool"] == "human_probe" for row in candidates)
    assert any(
        "motor" in (row["args"]["point_a"], row["args"]["point_b"])
        for row in candidates
    )


def test_robust_accepts_at_most_one_probe_observation():
    engine = BayesEngineRobust(n_samples=12, n_eig_samples=16)
    engine.reset(1)
    guard = guard_for("TF1", seed=1)
    engine.mark_failed("read_voltage", {"point": "motor"})

    first = engine._probe_candidates(guard)
    assert first
    choice = first[0]
    result = guard.call(choice["tool"], choice["args"])
    assert result["status"] == "ok"
    engine.update(choice["tool"], choice["args"], result["data"])

    assert len(engine.probe_obs) == 1
    assert engine._probe_candidates(guard) == []
