"""Cloud chat models through OpenRouter, against a fake OpenRouter server (no internet or key needed).

The fake answers the documented shapes (https://openrouter.ai/docs, 27 Oct 2026): GET /v1/key,
GET /v1/models (with each model's supported parameters and reasoning settings) and
POST /v1/chat/completions (OpenAI style, with usage, cost, provider and reasoning_details).
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

from faultdiag import agent, openrouter  # noqa: E402
from faultdiag.agent import AGENT_TOOLS, LLMUnavailable, OpenAICompatLLM  # noqa: E402
from faultdiag.guard import load_policy  # noqa: E402
from faultdiag.harness import run_trial  # noqa: E402
from faultdiag.methods import build_methods  # noqa: E402
from faultdiag.tools import openai_tools  # noqa: E402

KEY = "test-key-not-real-456"
OPUS = "anthropic/claude-opus-5.5"     # reasoning mandatory, ignores temperature (as listed on 27 Oct 2026)
PLAIN = "test/plain-model"             # reasoning optional, takes temperature
MODELS = [
    {"id": OPUS, "canonical_slug": "anthropic/claude-opus-5.5-20260921",
     "supported_parameters": ["tools", "reasoning", "max_tokens"],
     "reasoning": {"mandatory": True, "default_effort": "high"}, "pricing": {"prompt": "0.000004", "completion": "0.00002"}},
    {"id": PLAIN, "supported_parameters": ["tools", "reasoning", "temperature"], "reasoning": {"mandatory": False}},
    {"id": "test/no-tools", "supported_parameters": ["temperature"]},
]


def _call(name, args):
    return {"role": "assistant", "content": f"Next: {name}.", "reasoning_details": [{"type": "reasoning.text", "text": "x"}],
            "tool_calls": [{"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class _Fake(BaseHTTPRequestHandler):
    requests: list = []
    key_status = 200
    script: list = []          # replies in order; a (status, body) tuple sends that instead

    def log_message(self, *a):
        pass

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        self.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if self.path.endswith("/key"):
            if self.key_status != 200:
                return self._send(self.key_status, {"error": {"code": self.key_status, "message": "No auth credentials found"}})
            return self._send(200, {"data": {"label": "test", "limit_remaining": 5.0}})
        self._send(200, {"data": MODELS})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        step = self.script.pop(0)
        if isinstance(step, tuple):
            return self._send(*step)
        self._send(200, {"model": body["model"], "provider": "FakeProvider", "choices": [{"message": step}],
                         "usage": {"prompt_tokens": 2000, "completion_tokens": 300, "cost": 0.014,
                                   "completion_tokens_details": {"reasoning_tokens": 250}}})


@pytest.fixture
def fake():
    _Fake.requests, _Fake.key_status, _Fake.script = [], 200, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)


def _ask(llm):
    return llm([{"role": "system", "content": "bench"}, {"role": "user", "content": "{}"}], openai_tools(AGENT_TOOLS))


@pytest.mark.parametrize("method", ["hybrid", "llm"])
def test_session_end_to_end(fake, monkeypatch, caplog, method):
    monkeypatch.setenv(openrouter.KEY_ENV, KEY)
    _Fake.script = [_call("read_voltage", {"point": "motor"}), _call("read_current", {"channel": "motor"}),
                    _call("submit_diagnosis", {"faults": [{"element": "motor_connection"}], "confidence": 0.8,
                                               "evidence": "step 1"})]
    with caplog.at_level(logging.DEBUG):
        methods, meta = build_methods([method], OPUS, "openrouter", fake, think=True)
        rec = run_trial("F2", method, methods[method], 3, load_policy(), meta[method])

    assert meta[method] == {"model": OPUS, "think": True, "model_version": "anthropic/claude-opus-5.5-20260921",
                            "server": "openrouter", "temperature": None}
    assert rec["status"] == "diagnosed" and rec["elements"] == ["motor_connection"] and rec["correct"]
    assert [h["tool"] for h in rec["history"]] == ["read_voltage", "read_current"]
    assert rec["agent_stats"]["llm_calls"] == 3 and rec["agent_stats"]["invalid"] == 0
    posts = [r for r in _Fake.requests if r["path"] == "/v1/chat/completions"]
    assert len(posts) == 3 and all(r["auth"] == f"Bearer {KEY}" for r in _Fake.requests)
    first, last = posts[0]["body"], posts[-1]["body"]
    assert first["model"] == OPUS and "temperature" not in first and "reasoning" not in first
    # the reasoning of the latest call goes back with it; older ones are dropped
    with_reasoning = [m for m in last["messages"] if m.get("reasoning_details")]
    assert len(with_reasoning) == 1 and with_reasoning[0]["tool_calls"][0]["function"]["name"] == "read_current"
    # the key is sent only in the header: never in a record or a log line
    assert KEY not in json.dumps(rec, default=str) and KEY not in caplog.text


def test_settings_follow_what_the_model_accepts(fake):
    assert openrouter.check(fake, PLAIN, KEY, think=False) == {
        "extra": {"reasoning": {"effort": "none"}}, "temperature": 0.0, "version": PLAIN}
    assert openrouter.check(fake, OPUS, KEY, think=True)["extra"] == {}


def test_mandatory_reasoning_needs_think(fake):
    with pytest.raises(ValueError, match="--think"):
        openrouter.check(fake, OPUS, KEY, think=False)


@pytest.mark.parametrize("model, message", [("test/nope", "no model"), ("test/no-tools", "tool calls")])
def test_unusable_model_stops_before_the_run(fake, model, message):
    with pytest.raises(ValueError, match=message):
        openrouter.check(fake, model, KEY, think=True)


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv(openrouter.KEY_ENV, raising=False)
    with pytest.raises(ValueError, match=openrouter.KEY_ENV):
        build_methods(["hybrid"], OPUS, "openrouter", "http://127.0.0.1:9/v1", think=True)


def test_refused_key_stops_before_the_run(fake, monkeypatch):
    monkeypatch.setenv(openrouter.KEY_ENV, KEY)
    _Fake.key_status = 401
    with pytest.raises(ValueError, match="refused the API key") as exc:
        build_methods(["hybrid"], OPUS, "openrouter", fake, think=True)
    assert KEY not in str(exc.value)


def test_rate_limit_and_provider_error_are_waited_out(fake, caplog):
    _Fake.script = [(429, {"error": {"code": 429, "message": "slow down"}}),
                    (200, {"error": {"code": 502, "message": "provider down"}}), _call("read_voltage", {"point": "bus"})]
    llm = OpenAICompatLLM(fake, PLAIN, api_key=KEY)
    with caplog.at_level(logging.WARNING):
        msg = _ask(llm)
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"point": "bus"}
    assert "HTTP 429" in caplog.text and "provider down" in caplog.text
    assert llm.usage == {"requests": 1, "prompt_tokens": 2000, "completion_tokens": 300, "reasoning_tokens": 250,
                         "cost": 0.014}
    assert llm.providers == {"FakeProvider": 1} and llm.answered_by == PLAIN


def test_out_of_credits_stops_the_run(fake):
    _Fake.script = [(402, {"error": {"code": 402, "message": "Insufficient credits"}})]
    with pytest.raises(LLMUnavailable, match="out of credits"):
        _ask(OpenAICompatLLM(fake, PLAIN, api_key=KEY))


def test_local_server_is_not_retried(fake):
    """Without a key (Ollama, llama.cpp) an HTTP error stops at once, as before."""
    _Fake.script = [(503, {"error": "loading"}), _call("read_voltage", {"point": "bus"})]
    with pytest.raises(LLMUnavailable, match="HTTP 503"):
        _ask(OpenAICompatLLM(fake, PLAIN))
    assert len([r for r in _Fake.requests if r["path"] == "/v1/chat/completions"]) == 1
    assert "Authorization" not in json.dumps(_Fake.requests[0])
