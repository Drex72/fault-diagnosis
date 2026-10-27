"""Gemini's free tier as the model server, against a fake Gemini server (no internet or key needed).

The fake answers the documented OpenAI-compatible shapes (https://ai.google.dev/gemini-api/docs/openai,
27 Oct 2026): GET /models (ids like "models/gemini-3.8-flash") and POST /chat/completions, a
bad key as HTTP 400 "API key not valid", reasoning that cannot be turned off on Gemini 3 models,
and 429 RESOURCE_EXHAUSTED bodies naming a per-minute or a per-day quota with a retryDelay.
"""
import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag import agent, gemini  # noqa: E402
from faultdiag.agent import AGENT_TOOLS, LLMUnavailable, OpenAICompatLLM  # noqa: E402
from faultdiag.guard import load_policy  # noqa: E402
from faultdiag.harness import run_trial  # noqa: E402
from faultdiag.methods import build_methods  # noqa: E402
from faultdiag.tools import openai_tools  # noqa: E402

KEY = "test-key-not-real-789"
FLASH = "gemini-3.8-flash"          # always reasons (Gemini 3)
OLD = "gemini-2.5-flash"            # reasoning can be turned off


def _call(name, args, sig=None):
    call = {"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    if sig:
        call["extra_content"] = {"google": {"thought_signature": sig}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _quota(per, delay="41s"):
    return (429, [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota.",
                             "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                          "violations": [{"quotaId": f"GenerateRequestsPer{per}PerProjectPerModel-FreeTier"}]},
                                         {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]}}])


SESSION = [_call("read_voltage", {"point": "motor"}, sig="sig-1"), _call("read_current", {"channel": "motor"}, sig="sig-2"),
           _call("submit_diagnosis", {"faults": [{"element": "motor_connection"}], "confidence": 0.8, "evidence": "step 1"})]


class _Fake(BaseHTTPRequestHandler):
    requests: list = []
    script: list = []          # replies to tool requests in order; a (status, body) tuple sends that instead

    def log_message(self, *a):
        pass

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _bad_key(self):
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            self._send(400, [{"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                        "message": "API key not valid. Please pass a valid API key."}}])
            return True
        return False

    def do_GET(self):
        self.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if not self._bad_key():
            self._send(200, {"object": "list", "data": [{"id": f"models/{m}", "object": "model"}
                                                        for m in (FLASH, OLD, "gemini-3.5-flash-lite")]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self._bad_key():
            return
        if body.get("reasoning_effort") == "none" and body["model"].startswith("gemini-3"):
            return self._send(400, [{"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                               "message": "Reasoning cannot be turned off for this model."}}])
        if not body.get("tools"):  # the check's "Reply with OK."
            step = {"role": "assistant", "content": "OK"}
        else:
            step = self.script.pop(0)
        if isinstance(step, tuple):
            return self._send(*step)
        self._send(200, {"model": body["model"], "choices": [{"index": 0, "message": step, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 1500, "completion_tokens": 200, "total_tokens": 1700,
                                   "completion_tokens_details": {"reasoning_tokens": 150}}})


@pytest.fixture
def fake():
    _Fake.requests, _Fake.script = [], []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1beta/openai"
    srv.shutdown()


class _Clock:
    """Stands in for agent.time: sleeping moves the clock on at once, so waits are counted, not spent."""

    def __init__(self):
        self.t, self.slept = 1000.0, []

    def monotonic(self):
        return self.t

    perf_counter = monotonic

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(agent, "time", c)
    return c


def _posts():
    return [r for r in _Fake.requests if r["path"].endswith("/chat/completions") and r["body"].get("tools")]


def test_session_end_to_end_on_the_free_tier(fake, monkeypatch, caplog, clock):
    monkeypatch.setenv(gemini.KEY_ENV, KEY)
    _Fake.script = list(SESSION)
    with caplog.at_level(logging.DEBUG):
        methods, meta = build_methods(["hybrid"], FLASH, "gemini", fake, think=True)
        rec = run_trial("F2", "hybrid", methods["hybrid"], 3, load_policy(), meta["hybrid"])

    assert meta["hybrid"] == {"model": FLASH, "think": True, "model_version": FLASH, "server": "gemini",
                              "temperature": 0.0, "rpm": gemini.FREE_TIER_RPM}
    assert rec["status"] == "diagnosed" and rec["elements"] == ["motor_connection"] and rec["correct"]
    posts = _posts()
    assert len(posts) == 3 and all(r["auth"] == f"Bearer {KEY}" for r in _Fake.requests)
    assert posts[0]["body"]["temperature"] == 0.0 and "reasoning_effort" not in posts[0]["body"]
    # requests are spaced out for the free tier's per-minute limit (60 / 8 = 7.5 s apart) ...
    assert clock.slept == [7.5, 7.5]
    # ... and that wait is not counted as the model's reply time
    assert rec["agent_stats"]["latency_s"] == [0.0, 0.0, 0.0]
    # Gemini 3 needs each tool call's thought signature back with it
    sent = [m["tool_calls"][0] for m in posts[-1]["body"]["messages"] if m.get("tool_calls")]
    assert [c["extra_content"]["google"]["thought_signature"] for c in sent] == ["sig-1", "sig-2"]
    assert KEY not in json.dumps(rec, default=str) and KEY not in caplog.text


def test_check_settings(fake):
    assert gemini.check(fake, FLASH, KEY, think=True) == {"extra": {}, "temperature": 0.0, "version": FLASH}
    assert gemini.check(fake, OLD, KEY, think=False)["extra"] == {"reasoning_effort": "none"}


def test_reasoning_off_refused_asks_for_think(fake):
    with pytest.raises(ValueError, match="--think"):
        gemini.check(fake, FLASH, KEY, think=False)


def test_unknown_model_names_free_ones(fake):
    with pytest.raises(ValueError, match="no model 'gemini-4-argon'.*gemini-3.8-flash"):
        gemini.check(fake, "gemini-4-argon", KEY, think=True)


def test_bad_key_stops_before_the_run(fake, monkeypatch):
    monkeypatch.setenv(gemini.KEY_ENV, "wrong-key-not-real")
    with pytest.raises(ValueError, match="refused the API key") as exc:
        build_methods(["hybrid"], FLASH, "gemini", fake, think=True)
    assert "wrong-key-not-real" not in str(exc.value)


def test_missing_key_points_to_ai_studio(monkeypatch):
    monkeypatch.delenv(gemini.KEY_ENV, raising=False)
    with pytest.raises(ValueError, match="aistudio.google.com/apikey"):
        build_methods(["hybrid"], FLASH, "gemini", "http://127.0.0.1:9/v1beta/openai", think=True)


def test_per_minute_limit_waits_the_retry_delay(fake, clock):
    _Fake.script = [_quota("Minute"), SESSION[0]]
    llm = OpenAICompatLLM(fake, FLASH, api_key=KEY)
    msg = llm([{"role": "user", "content": "{}"}], openai_tools(AGENT_TOOLS))
    assert msg["tool_calls"][0]["function"]["name"] == "read_voltage"
    assert clock.slept == [41.0] and llm.last_wait_s == 41.0 and llm.usage["requests"] == 1


def test_network_drop_is_retried_only_for_cloud(clock):
    """A DNS hiccup or dropped connection (seen on the real run, 27 Oct 2026) is waited out
    with a key; a local server with no key still stops at once."""
    closed = "http://127.0.0.1:9/v1beta/openai"   # nothing listens on port 9
    with pytest.raises(LLMUnavailable, match="cannot reach"):
        OpenAICompatLLM(closed, FLASH, api_key=KEY)([{"role": "user", "content": "{}"}], [])
    assert clock.slept == [5, 10, 20]
    with pytest.raises(LLMUnavailable, match="cannot reach"):
        OpenAICompatLLM(closed, FLASH)([{"role": "user", "content": "{}"}], [])
    assert clock.slept == [5, 10, 20]


@pytest.mark.parametrize("reply", [_quota("Day", "3600s"),   # Google; and OpenRouter's free models:
                                   (429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}})])
def test_daily_quota_stops_with_resume_hint(fake, clock, reply):
    _Fake.script = [reply]
    with pytest.raises(LLMUnavailable, match="daily request quota.*--resume"):
        OpenAICompatLLM(fake, FLASH, api_key=KEY)([{"role": "user", "content": "{}"}], openai_tools(AGENT_TOOLS))
    assert clock.slept == [] and len(_posts()) == 1   # no retries burn the next day's quota


def test_bench_stops_on_quota_and_resumes(fake, tmp_path):
    env = {**os.environ, gemini.KEY_ENV: KEY, "FAULTDIAG_RESULTS_DIR": str(tmp_path)}
    cmd = [sys.executable, str(ROOT / "scripts" / "bench_models.py"), "--server", "gemini", "--base-url", fake,
           "--models", FLASH, "--think", "--scenarios", "F2", "--trials", "2", "--rpm", "6000"]
    _Fake.script = [*SESSION, _quota("Day", "3600s")]
    first = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    assert first.returncode == 4, first.stderr
    run = next((tmp_path / "model-bench").iterdir())
    assert f"--resume {run}" in first.stderr
    partial = json.loads((run / "summary.json").read_text())
    assert [(r["sessions"], r["cloud_requests"], bool(r.get("stopped"))) for r in partial] == [(1, 3, True)]

    _Fake.script = list(SESSION)
    again = subprocess.run([*cmd, "--resume", str(run)], env=env, capture_output=True, text=True, timeout=120)
    assert again.returncode == 0, again.stderr
    recs = [json.loads(x) for x in (run / "sessions.jsonl").read_text().splitlines()]
    assert [r["seed"] for r in recs] == [5000, 5001] and all(r["correct"] for r in recs)
    (row,) = json.loads((run / "summary.json").read_text())
    assert row["sessions"] == 2 and row["resumed_sessions"] == 1 and "stopped" not in row
    assert row["cloud_requests"] == 6 and row["cloud_prompt_tokens"] == 6 * 1500 and row["rpm"] == 6000
    assert KEY not in (run / "sessions.jsonl").read_text() + first.stderr + again.stderr
