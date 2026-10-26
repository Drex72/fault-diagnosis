import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.agent import Agent  # noqa: E402
from faultdiag.baselines import run_fixed, run_tree  # noqa: E402
from faultdiag.circuit import CircuitParams, pulse_response, solve  # noqa: E402
from faultdiag.engine import BayesEngine  # noqa: E402
from faultdiag.faults import FaultState  # noqa: E402
from faultdiag.guard import PolicyGuard  # noqa: E402
from faultdiag.harness import make_bench  # noqa: E402
from faultdiag.simbench import SimBench  # noqa: E402
from faultdiag.tools import validate_call  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    return BayesEngine()


def guard_for(name, seed=3):
    return PolicyGuard(make_bench(name, seed))


# ---- circuit physics ------------------------------------------------------
def test_kirchhoff_current_balance():
    for p in (CircuitParams(), replace(CircuitParams(), motor_stalled=True), replace(CircuitParams(), r_parasitic=6.0)):
        s = solve(p)
        assert s.i_src == pytest.approx(s.i_motor + s.i_lamp + s.i_load + s.i_parasitic, abs=1e-9)


def test_pulse_recovers_ohmic_resistance():
    p = replace(CircuitParams(), r_int=0.25, r_pol=0.15)
    before, imm, _ = pulse_response(p, 0.1)
    est = (before.v["src"] - imm.v["src"]) / (imm.i_src - before.i_src)
    assert est == pytest.approx(0.25, rel=1e-6)


def test_fault_signatures():
    h = solve(CircuitParams())
    f2 = solve(replace(CircuitParams(), r_conn_motor=1.5))
    stall = solve(replace(CircuitParams(), motor_stalled=True))
    lamp_open = solve(replace(CircuitParams(), lamp_open=True))
    assert h.v["bus"] - h.v["motor"] < 0.05 and f2.v["bus"] - f2.v["motor"] > 0.4
    assert stall.i_motor > 2.5 and stall.motor_speed == 0
    assert lamp_open.v["lamp"] == pytest.approx(lamp_open.v["ret"], abs=1e-9)


# ---- tool contract and guard ------------------------------------------------
def test_validator():
    assert validate_call("read_voltage", {"point": "bus"}) == []
    assert validate_call("read_voltage", {"point": "kitchen"})
    assert validate_call("load_test", {"mode": "pulse", "width_ms": 5000})
    assert validate_call("load_test", {"mode": "pulse", "extra": 1})
    assert validate_call("format_disk", {})


def test_guard_budget_and_limits():
    g = guard_for("healthy")
    assert g.call("read_voltage", {"point": "nowhere"})["status"] == "rejected"
    assert g.spent == 0
    for _ in range(3):
        assert g.call("load_test", {"mode": "pulse"})["status"] == "ok"
    assert g.call("load_test", {"mode": "pulse"})["status"] == "rejected"  # limit 3
    assert g.spent == 9
    g.policy = {**g.policy, "budget": 10}
    assert g.call("load_test", {"mode": "steady"})["status"] == "rejected"  # over budget


def test_tool_failures_are_reported():
    g = guard_for("TF1")
    r = g.call("read_voltage", {"point": "motor"})
    assert r["status"] == "timeout" and g.spent == 1


# ---- engine -------------------------------------------------------------
def test_engine_never_imports_heldout():
    code = "import sys; sys.path.insert(0, %r); import faultdiag.engine, faultdiag.agent; print('faultdiag.heldout' in sys.modules)" % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


@pytest.mark.parametrize("name,truth", [("healthy", []), ("F1", ["battery"]), ("F2", ["motor_connection"]),
                                        ("F3", ["motor"]), ("C1", ["battery", "motor"])])
def test_engine_diagnoses_in_model(engine, name, truth):
    d = engine.run(guard_for(name), seed=1)
    assert d["status"] == "diagnosed" and d["elements"] == truth


def test_engine_flags_misfit_for_ground_fault(engine):
    assert engine.run(guard_for("H1"), seed=1)["status"] == "out_of_model"


def test_baselines_run():
    assert run_fixed(guard_for("F2"))["elements"] == ["motor_connection"]
    assert run_tree(guard_for("F3"))["elements"] == ["motor"]


# ---- agent loop with a scripted model --------------------------------------
class ScriptedLLM:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, messages, tools):
        self.seen.append(messages[-1])
        return self.replies.pop(0)


def call(name, args, i=0):
    return {"content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_agent_recovers_from_invalid_call(engine):
    llm = ScriptedLLM([
        call("read_voltage", {"point": "kitchen"}, 0),                       # invalid
        call("read_voltage", {"point": "bus"}, 1),
        {"content": '{"name": "human_probe", "arguments": {"point_a": "ret", "point_b": "neg"}}'},  # text form
        call("submit_diagnosis", {"faults": [{"element": "ground_return"}], "confidence": 0.8}, 2),
    ])
    d = Agent(llm, engine).run(guard_for("H1"), seed=1)
    s = d["agent_stats"]
    assert d["elements"] == ["ground_return"]
    assert s["invalid"] == 1 and s["content_parsed"] == 1 and s["fallbacks"] == 0


def test_agent_falls_back_to_engine_after_repeated_invalid(engine):
    bad = call("nonexistent_tool", {})
    llm = ScriptedLLM([bad, bad, bad, call("submit_diagnosis", {"faults": [], "confidence": 0.5})])
    g = guard_for("healthy")
    d = Agent(llm, engine, max_retries=2).run(g, seed=1)
    assert d["agent_stats"]["fallbacks"] == 1 and len(g.history) == 1


def test_state_message_carries_engine_report(engine):
    llm = ScriptedLLM([call("submit_diagnosis", {"faults": [], "confidence": 0.5})])
    Agent(llm, engine).run(guard_for("healthy"), seed=1)
    state = json.loads(llm.seen[0]["content"])
    assert {"top_hypotheses", "suggested_tests", "model_misfit"} <= set(state["engine"])
