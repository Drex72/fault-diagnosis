"""Host serial transport against the mock firmware (no board needed)."""
import os
import threading
import time
import tty

import numpy as np
import pytest

from faultdiag.guard import load_policy
from faultdiag.harness import grade, run_blind_session, state_for_faults
from faultdiag.injector import InjectorClient, InjectorError
from faultdiag.mockdevice import MockBenchDevice, MockInjectorDevice, MockLink
from faultdiag.serialbench import (BenchError, BenchInterrupted, SerialBench, SerialLink,
                                   ToolFailures)

FAST = {"hello_timeout_s": 0.5, "heartbeat_s": 0.2, "reconnect_s": 1.0}


def connect(device=None, **kw):
    device = device or MockBenchDevice(seed=1)
    link = MockLink(device)
    bench = SerialBench(link, **{**FAST, **kw})
    return bench, device, link


def test_hello_readings_and_self_test_gate():
    bench, dev, _ = connect()
    try:
        assert bench.firmware == "bench-mock-0.1.0" and bench.calibration_id == "uncalibrated"
        v = bench.execute("read_voltage", {"point": "src"})
        assert v["status"] == "ok" and 11.5 < v["data"]["volts"] < 13.5
        refused = bench.execute("load_test", {"mode": "pulse", "width_ms": 100})
        assert refused["status"] == "error" and "self-test" in refused["error"]
        assert bench.self_test()["passed"] is True
        ok = bench.execute("load_test", {"mode": "pulse", "width_ms": 100})
        assert ok["status"] == "ok" and set(ok["data"]) >= {"v_src_before", "v_src_immediate", "i_src_during"}
    finally:
        bench.close()


def test_failed_self_test_keeps_load_off():
    bench, _, _ = connect(MockBenchDevice(seed=1, self_test_fail={"adc_present"}))
    try:
        st = bench.self_test()
        assert st["passed"] is False and st["critical"]["adc_present"] is False
        assert "self-test" in bench.execute("load_test", {"mode": "pulse"})["error"]
    finally:
        bench.close()


def test_looser_firmware_limits_are_refused():
    with pytest.raises(BenchError, match="looser"):
        connect(MockBenchDevice(seed=1, limits={"pulse_max_ms": 1000}))


def test_human_probe_paths():
    calls = []

    def prober(a, b, instruction, timeout_s):
        calls.append((a, b, timeout_s))
        return 0.42

    bench, _, _ = connect(prober=prober)
    try:
        r = bench.execute("human_probe", {"point_a": "ret", "point_b": "neg"})
        assert r == {"status": "ok", "data": {"volts": 0.42}}
        assert calls == [("ret", "neg", load_policy()["timeouts_s"]["human_probe"])]
        bench.prober = lambda *a: None
        assert bench.execute("human_probe", {"point_a": "ret", "point_b": "neg"})["status"] == "timeout"
        bench.prober = None
        assert bench.execute("human_probe", {"point_a": "ret", "point_b": "neg"})["status"] == "error"
    finally:
        bench.close()


def test_corrupt_duplicate_and_stale_lines_are_ignored(caplog):
    bench, dev, _ = connect()
    try:
        dev.corrupt_next = dev.duplicate_next = dev.stale_next = 1
        r = bench.execute("read_current", {"channel": "motor"})
        assert r["status"] == "ok"
        time.sleep(0.2)
        text = caplog.text
        assert "corrupt line" in text and "duplicate response" in text and "stale or unknown" in text
        assert bench.execute("read_current", {"channel": "src"})["status"] == "ok"
    finally:
        bench.close()


def test_timeout_then_late_response():
    bench, dev, _ = connect()
    bench.timeout_s = 0.3
    try:
        dev.drop_next = 1
        assert bench.execute("read_voltage", {"point": "bus"})["status"] == "timeout"
        dev.delay_next = 0.6
        assert bench.execute("read_voltage", {"point": "bus"})["status"] == "timeout"
        time.sleep(0.5)  # the late answer arrives and must not be taken for the next request
        r = bench.execute("read_voltage", {"point": "lamp"})
        assert r["status"] == "ok"
    finally:
        bench.close()


def test_reset_mid_session_interrupts():
    bench, dev, _ = connect()
    try:
        assert bench.execute("read_voltage", {"point": "src"})["status"] == "ok"
        dev.reset("brownout")
        time.sleep(0.3)
        assert bench.interrupted and "brownout" in bench.interrupted
        with pytest.raises(BenchInterrupted):
            bench.execute("read_voltage", {"point": "src"})
    finally:
        bench.close()


def test_blind_session_marks_interrupt_and_grading_joins_truth():
    bench, dev, _ = connect()
    policy = load_policy()

    def method(guard, seed=0):
        guard.call("read_voltage", {"point": "src"})
        dev.reset("brownout")
        time.sleep(0.3)
        guard.call("read_voltage", {"point": "bus"})
        return {"status": "diagnosed", "elements": []}

    try:
        rec = run_blind_session("x", method, 0, policy, {"source": "sim"}, bench, "s001")
        assert rec["status"] == "interrupted" and "scenario" not in rec and "truth" not in rec
        ok = {**rec, "session_id": "s002", "status": "diagnosed", "elements": ["motor"]}
        graded = grade([rec, ok], [{"session_id": "s001", "scenario": "F3", "truth": ["motor"]},
                                   {"session_id": "s002", "scenario": "F3", "truth": ["motor"]}])
        assert [g["correct"] for g in graded] == [False, True] and graded[1]["set"] == "single"
        with pytest.raises(ValueError):
            grade([rec], [])
    finally:
        bench.close()


def test_heartbeat_keeps_host_alive_and_its_loss_is_reported():
    dev = MockBenchDevice(seed=1, limits={"heartbeat_timeout_ms": 500})
    bench, _, _ = connect(dev)
    try:
        bench.execute("read_voltage", {"point": "src"})
        time.sleep(1.2)
        assert not [e for e in bench.events if e["event"] == "heartbeat_lost"]
        bench.pause_heartbeat(1.0)
        time.sleep(1.0)
        assert [e for e in bench.events if e["event"] == "heartbeat_lost"]
    finally:
        bench.close()


def test_cooldown_is_waited_out():
    slept = []

    def sleep(s):
        slept.append(s)
        time.sleep(s)

    bench, _, _ = connect(MockBenchDevice(seed=1, cooldown_scale=0.2), sleep=sleep)
    try:
        bench.self_test()
        assert bench.execute("load_test", {"mode": "pulse", "width_ms": 100})["status"] == "ok"
        assert bench.execute("load_test", {"mode": "pulse", "width_ms": 100})["status"] == "ok"
        assert len(slept) == 1 and 0.1 < slept[0] < 1.0
    finally:
        bench.close()


def test_overcurrent_event_latches_inhibit():
    dev = MockBenchDevice(seed=1, raw_error={"current:src": (5.0, 0.0)})
    bench, _, _ = connect(dev)
    try:
        bench.self_test()
        time.sleep(0.4)
        assert bench.inhibited and [e for e in bench.events if e["event"] == "overcurrent"]
        assert "inhibited" in bench.execute("load_test", {"mode": "pulse"})["error"]
    finally:
        bench.close()


def test_safe_state_and_resume():
    bench, dev, _ = connect()
    try:
        bench.self_test()
        assert bench.safe_state()["data"]["inhibited"] is True
        assert "inhibited" in bench.execute("load_test", {"mode": "pulse"})["error"]
        bench.resume()
        assert bench.execute("load_test", {"mode": "pulse"})["status"] == "ok"
    finally:
        bench.close()


def test_unplug_replug_resets_and_interrupts():
    bench, dev, link = connect()
    try:
        link.unplug()
        time.sleep(0.3)
        assert bench.execute("read_voltage", {"point": "src"})["status"] == "error"
        link.replug()
        time.sleep(0.8)
        assert bench.interrupted  # the ESP32 resets when the port reopens
    finally:
        bench.close()


def test_unplug_without_recovery_interrupts():
    bench, dev, link = connect()
    try:
        link.unplug()
        time.sleep(1.8)
        assert bench.interrupted and "not recovered" in bench.interrupted
    finally:
        bench.close()


def test_tool_failure_wrapper():
    bench, _, _ = connect()
    try:
        tf = ToolFailures(bench, {"read_voltage:motor": "timeout", "read_current:motor": "garbage"},
                          np.random.default_rng(0))
        assert tf.execute("read_voltage", {"point": "motor"})["status"] == "timeout"
        g = tf.execute("read_current", {"channel": "motor"})
        assert g["status"] == "ok" and 0 <= g["data"]["amps"] <= 16
        assert tf.firmware == bench.firmware
    finally:
        bench.close()


def test_injector_drives_mock_bench_state():
    rng = np.random.default_rng(3)
    bdev = MockBenchDevice(seed=3)
    seen = []

    def on_change(faults):
        seen.append(faults)
        bdev.set_state(state_for_faults(faults, rng))

    inj = InjectorClient(MockLink(MockInjectorDevice(on_change)), hello_timeout_s=0.5)
    bench, _, _ = connect(bdev)
    try:
        healthy = bench.execute("read_current", {"channel": "motor"})["data"]["amps"]
        assert inj.set(["F3"]) == ["F3"]
        stalled = bench.execute("read_current", {"channel": "motor"})["data"]["amps"]
        assert stalled > healthy * 1.5  # locked rotor draws more
        with pytest.raises(InjectorError, match="unknown fault"):
            inj.set(["F9"])
        with pytest.raises(InjectorError, match="at most 3"):
            inj.set(["F1", "F2", "F3", "H1"])
        assert inj.status() == ["F3"]
        inj.clear()
        assert inj.status() == [] and seen[-1] == []
    finally:
        inj.close()
        bench.close()


def test_injector_clears_itself_when_host_goes_quiet():
    dev = MockInjectorDevice(host_timeout_s=0.3)
    inj = InjectorClient(MockLink(dev), hello_timeout_s=0.5, heartbeat_s=60)
    try:
        inj.set(["H2"])
        time.sleep(0.5)
        assert inj.status() == [] and [e for e in inj.events if e["event"] == "host_lost"]
    finally:
        inj.close()


def test_state_for_faults():
    rng = np.random.default_rng(0)
    s = state_for_faults(["H4"], rng)
    assert 0.45 <= s.current_gain["src"] <= 0.65
    s = state_for_faults(["F1", "F3"], rng)
    assert s.params.motor_stalled and 0.20 <= s.params.r_int <= 0.30


def test_end_to_end_over_a_real_pty():
    """pyserial SerialLink on a pseudo-terminal, mock firmware on the other end."""
    master, slave = os.openpty()
    tty.setraw(slave)  # no echo or newline translation before pyserial opens it
    dev = MockBenchDevice(seed=2)
    stop = threading.Event()

    def firmware():
        buf = b""
        os.set_blocking(master, False)
        while not stop.is_set():
            dev.tick()
            while not dev.out.empty():
                os.write(master, dev.out.get().encode() + b"\n")
            try:
                buf += os.read(master, 4096)
            except (BlockingIOError, OSError):
                time.sleep(0.01)
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                dev.handle_line(line.decode())

    t = threading.Thread(target=firmware, daemon=True)
    t.start()
    link = SerialLink(os.ttyname(slave))
    bench = SerialBench(link, **FAST)
    try:
        assert bench.firmware == "bench-mock-0.1.0"
        assert bench.self_test()["passed"]
        r = bench.execute("load_test", {"mode": "pulse", "width_ms": 50})
        assert r["status"] == "ok", r
        assert bench.execute("read_current", {"channel": "src"})["status"] == "ok"
    finally:
        bench.close()
        stop.set()
        t.join(1)
        os.close(master)
        os.close(slave)
