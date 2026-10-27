"""Jev as the planner for D and E, against a fake Jev server (no internet or key needed).

The fake answers the documented shapes (https://docs.typesafe.ai/api, 27 Oct 2026):
GET /v1/models, and POST /v1/systemone with one `choice` question per request.
"""
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag import jev  # noqa: E402
from faultdiag.agent import AGENT_TOOLS, SUBMIT_SCHEMA, Agent, LLMBadOutput, LLMUnavailable  # noqa: E402
from faultdiag.engine import BayesEngine  # noqa: E402
from faultdiag.guard import load_policy  # noqa: E402
from faultdiag.harness import run_trial  # noqa: E402
from faultdiag.methods import build_methods  # noqa: E402
from faultdiag.tools import ELEMENTS, openai_tools, validate, validate_call  # noqa: E402

KEY = "test-key-not-real-123"
MODEL = "jev-1.14.0"


class _Fake(BaseHTTPRequestHandler):
    requests: list = []
    models_status = 200
    script: list = []          # labels to choose, in order; a (status, body) tuple sends that instead

    def log_message(self, *a):
        pass

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        self.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if self.models_status != 200:
            return self._send(self.models_status, {"detail": "invalid key"})
        self._send(200, {"data": [{"id": MODEL}, {"id": "jev-1.13.0"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        step = self.script.pop(0)
        if isinstance(step, tuple):
            return self._send(*step)
        (name, q), = body["questions"].items()
        probs = {k: (0.7 if k == step else 0.3 / max(1, len(q["criteria"]) - 1)) for k in q["criteria"]}
        self._send(200, {"model": MODEL, "usage": {"input_tokens": 1000, "output_tokens": 5},
                         "answers": {name: {"type": "choice", "choice": step, "confidence": 0.7, "probabilities": probs}}})


@pytest.fixture
def fake():
    _Fake.requests, _Fake.models_status, _Fake.script = [], 200, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(jev.time, "sleep", lambda s: None)


def test_options_cover_the_tool_contract():
    actions = jev.action_options({n: s for n, s in AGENT_TOOLS.items()})
    # 5 taps, 2 current channels, pulse and steady load tests, 21 probe pairs, submit
    assert len(actions) == 5 + 2 + 2 + 21 + 1
    for label, (tool, args, desc) in actions.items():
        if tool != jev.SUBMIT:
            assert validate_call(tool, args, AGENT_TOOLS) == [], label
    diag = jev.diagnosis_options()
    assert len(diag) == 1 + len(ELEMENTS) + len(ELEMENTS) * (len(ELEMENTS) - 1) // 2 <= jev.MAX_OPTIONS
    for elements in diag.values():
        assert validate(SUBMIT_SCHEMA["parameters"], {"faults": [{"element": e} for e in elements],
                                                      "confidence": 0.5}) == []


@pytest.mark.parametrize("method", ["hybrid", "llm"])
def test_jev_session_end_to_end(fake, monkeypatch, caplog, method):
    monkeypatch.setenv(jev.KEY_ENV, KEY)
    _Fake.script = ["read_voltage motor", "read_current motor", jev.SUBMIT, "motor_connection"]
    methods, meta = build_methods([method], MODEL, "jev", fake)
    assert meta[method]["model_version"] == MODEL and meta[method]["server"] == "jev"
    with caplog.at_level(logging.DEBUG):
        rec = run_trial("F2", method, methods[method], 3, load_policy(), meta[method])

    assert rec["status"] == "diagnosed" and rec["elements"] == ["motor_connection"] and rec["correct"]
    assert [h["tool"] for h in rec["history"]] == ["read_voltage", "read_current"]
    assert rec["diagnosis"]["confidence"] == 0.7
    assert rec["diagnosis"]["turns"][0]["content"].startswith("Jev chose read_voltage motor")
    assert rec["agent_stats"]["llm_calls"] == 3 and rec["agent_stats"]["invalid"] == 0

    get, *posts = _Fake.requests
    assert get["path"] == "/v1/models"
    assert all(r["auth"] == f"Bearer {KEY}" for r in _Fake.requests)
    assert [r["path"] for r in posts] == ["/v1/systemone"] * 4
    first, last = posts[0]["body"], posts[-1]["body"]
    assert first["model"] == MODEL and len(first["questions"]["next_action"]["criteria"]) == 31
    assert first["state"]["bench"].startswith("You are a diagnostic technician")
    assert ("engine" in first["state"]["session"][0]) == (method == "hybrid")
    assert len(last["questions"]["diagnosis"]["criteria"]) == len(jev.diagnosis_options())
    assert any(s.startswith("Action: read_current") for s in last["state"]["session"])
    # the key is sent only in the header: never in a record or a log line
    assert KEY not in json.dumps(rec, default=str) and KEY not in caplog.text


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv(jev.KEY_ENV, raising=False)
    with pytest.raises(ValueError, match=jev.KEY_ENV):
        build_methods(["hybrid"], MODEL, "jev", "http://127.0.0.1:9/v1")


def test_refused_key_stops_before_the_run(fake, monkeypatch):
    monkeypatch.setenv(jev.KEY_ENV, KEY)
    _Fake.models_status = 401
    with pytest.raises(ValueError, match="refused the API key") as exc:
        build_methods(["hybrid"], MODEL, "jev", fake)
    assert KEY not in str(exc.value)


def test_rate_limit_is_waited_out(fake, caplog):
    _Fake.script = [(429, {"detail": "slow down"}), "read_voltage bus"]
    llm = jev.JevLLM(fake, MODEL, KEY)
    with caplog.at_level(logging.WARNING):
        msg = llm([{"role": "system", "content": "bench"}, {"role": "user", "content": "{}"}],
                  openai_tools(AGENT_TOOLS))
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"point": "bus"}
    assert "HTTP 429" in caplog.text and llm.usage == {"requests": 1, "input_tokens": 1000}


def test_service_down_after_retries_stops_the_run(fake):
    _Fake.script = [(503, {"detail": "busy"})] * 4
    with pytest.raises(LLMUnavailable, match="HTTP 503"):
        jev.JevLLM(fake, MODEL, KEY)([{"role": "system", "content": "b"}], openai_tools(AGENT_TOOLS))


def test_answer_outside_the_options_counts_invalid_and_falls_back(fake):
    _Fake.script = ["make coffee"] * 40
    llm = jev.JevLLM(fake, MODEL, KEY)
    with pytest.raises(LLMBadOutput):
        llm([{"role": "system", "content": "b"}], openai_tools(AGENT_TOOLS))
    _Fake.script = ["make coffee"] * 40
    rec = run_trial("F2", "hybrid", Agent(llm, BayesEngine()).run, 3, load_policy())
    stats = rec["agent_stats"]
    assert stats["invalid"] > 0 and stats["server_parse_errors"] > 0 and stats["fallbacks"] > 0
    assert rec["error"] is None


def test_loop_turn_asks_only_for_the_diagnosis(fake):
    _Fake.script = ["battery"]
    msg = jev.JevLLM(fake, MODEL, KEY)([{"role": "system", "content": "b"}],
                                       openai_tools({"submit_diagnosis": SUBMIT_SCHEMA}))
    assert msg["tool_calls"][0]["function"]["name"] == jev.SUBMIT
    assert list(_Fake.requests[0]["body"]["questions"]) == ["diagnosis"]
