"""Google's Gemini API as the model server for D and E, for comparison runs only. Some models
are free (Gemini 3.8 Flash on 27 Oct 2026), within per-minute and per-day request limits.
It needs the internet and a Google AI Studio API key in GEMINI_API_KEY (set it yourself; it is
never written to a record or a log).

Gemini has an OpenAI-compatible endpoint, so the chat requests go through the same client as
the local models (agent.OpenAICompatLLM), with the key in a header. This module adds the check
before a run: the key works, the model is listed, and one small test request with the run's
settings is accepted. Gemini 3 models always reason; turning reasoning off ("reasoning_effort":
"none") is refused, so the run stops before it starts and asks for --think, which leaves the
model's default level. Free-tier limits are met by spacing requests out (FREE_TIER_RPM); a used-up
daily quota stops the run with a clear message, to be continued with --resume.

API: https://ai.google.dev/gemini-api/docs/openai (read on 27 Oct 2026). Google may use
free-tier requests to improve its products; these hold only simulated bench readings.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .logsetup import get_logger

log = get_logger("gemini")

DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
KEY_ENV = "GEMINI_API_KEY"
CHECK_TIMEOUT_S = 60
FREE_TIER_RPM = 8   # requests per minute used by default; the free tier's own limit is in AI Studio


def api_key() -> str:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise ValueError(f"set {KEY_ENV} to your Google AI Studio API key to use --server gemini "
                         "(free: https://aistudio.google.com/apikey)")
    return key


def _request(url: str, key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        if exc.code in (401, 403) or "API key" in detail:  # Google answers 400 "API key not valid"
            raise ValueError(f"Gemini refused the API key (HTTP {exc.code}); check {KEY_ENV}: {detail}") from exc
        raise ValueError(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"cannot reach Gemini at {url} within {CHECK_TIMEOUT_S} s (internet needed): {exc}") from exc


def check(base_url: str, model: str, key: str, think: bool) -> dict:
    """Before a run. Returns {"extra", "temperature", "version"} for the chat client and the record.
    Raises ValueError with what to do if the key, the model or the settings will not work.
    Uses one request of the day's quota."""
    base = base_url.rstrip("/")
    log.info("checking the API key and model %s at %s (up to %d s)", model, base, CHECK_TIMEOUT_S)
    listed = {m.get("id", "").removeprefix("models/") for m in _request(base + "/models", key).get("data", [])}
    if model not in listed:
        free = sorted(m for m in listed if "flash" in m)[:8]
        raise ValueError(f"Gemini has no model {model!r} for this key; e.g. {', '.join(free) or 'gemini-3.8-flash'}")
    extra = {} if think else {"reasoning_effort": "none"}
    try:
        _request(base + "/chat/completions", key, {"model": model, "temperature": 0.0, **extra,
                                                    "messages": [{"role": "user", "content": "Reply with OK."}]})
    except ValueError as exc:
        if not think and "HTTP 400" in str(exc):
            raise ValueError(f"{model} refused reasoning off; it always reasons, so run with --think: {exc}") from exc
        raise
    log.info("Gemini answered: key accepted, %s takes these settings (reasoning %s, temperature 0)",
             model, "at the model's default level" if think else "off")
    return {"extra": extra, "temperature": 0.0, "version": model}
