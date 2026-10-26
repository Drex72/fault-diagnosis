"""Final held-out faults H3, H4: bench physics only.

These tests check what the simulated bench reports. They deliberately do not
run any diagnostic method on H3 or H4 (working rule 6: no looking at method
behaviour on the frozen set before the final run).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.baselines import run_fixed  # noqa: E402
from faultdiag.circuit import solve  # noqa: E402
from faultdiag.harness import SCENARIOS, dev_scenarios, make_bench, run_all  # noqa: E402


def test_h3_drops_voltage_between_battery_and_bus():
    b = make_bench("H3", 7)
    src = b.execute("read_voltage", {"point": "src"})["data"]["volts"]
    bus = b.execute("read_voltage", {"point": "bus"})["data"]["volts"]
    assert src - bus > 0.3
    assert 0.3 <= b.state.params.r_main <= 0.6


def test_h4_src_current_sensor_reads_low_everywhere_it_is_used():
    b = make_bench("H4", 7)
    true_i = solve(b.state.params).i_src
    gain = b.state.current_gain["src"]
    amps = b.execute("read_current", {"channel": "src"})["data"]["amps"]
    assert amps == pytest.approx(gain * true_i, abs=0.03)
    pulse = b.execute("load_test", {"mode": "pulse", "width_ms": 100})["data"]
    assert pulse["i_src_before"] == pytest.approx(gain * true_i, abs=0.03)
    motor = b.execute("read_current", {"channel": "motor"})["data"]["amps"]
    assert motor == pytest.approx(solve(b.state.params).i_motor, abs=0.03)  # other channel unaffected


def test_final_heldout_is_fenced_off_from_development_runs():
    assert {"H3", "H4"}.isdisjoint(dev_scenarios())
    assert {"H1", "H2"} <= set(dev_scenarios())
    assert list(SCENARIOS)[-2:] == ["H3", "H4"]  # appended, so earlier seeds are unchanged
    with pytest.raises(ValueError, match="frozen"):
        run_all({"fixed": run_fixed}, ["H3"], 1)


def test_new_field_defaults_leave_in_model_benches_unchanged():
    b = make_bench("F2", 3)
    assert b.state.current_gain == {}
    r = np.array([b.execute("read_current", {"channel": "src"})["data"]["amps"] for _ in range(5)])
    assert abs(r.mean() - solve(b.state.params).i_src) < 0.01
