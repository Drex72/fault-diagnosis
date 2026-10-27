"""OpenRouter (https://openrouter.ai) as the model server for D and E: one API key for cloud chat
models (Claude, GPT, Gemini), for comparison runs only. It needs the internet and an API key in
OPENROUTER_API_KEY (set it yourself; it is never written to a record or a log).

The chat requests go through the same client as the local models (agent.OpenAICompatLLM), with
the key in a header. This module adds the check before a run: the key works, the model exists
and calls tools, and which of our request fields the model accepts:

- temperature: some models ignore it (Claude 5 models on 27 Oct 2026); it is then not sent, and
  the record says so (temperature null), because their replies can differ between runs;
- reasoning: off unless --think, sent as {"reasoning": {"effort": "none"}}. Models whose reasoning
  is mandatory (Claude Opus 5.5 and Sonnet 5.5 on 27 Oct 2026) refuse that, so the run stops
  before it starts and asks for --think, which leaves the model's default effort.

API: GET {base_url}/key, GET {base_url}/models, POST {base_url}/chat/completions, documented at
https://openrouter.ai/docs (read on 27 Oct 2026).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .logsetup import get_logger

log = get_logger("openrouter")

DEFAULT_URL = "https://openrouter.ai/api/v1"
KEY_ENV = "OPENROUTER_API_KEY"
CHECK_TIMEOUT_S = 15


def api_key() -> str:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise ValueError(f"set {KEY_ENV} to your OpenRouter API key to use --server openrouter "
                         "(https://openrouter.ai/settings/keys)")
    return key


def _get(url: str, key: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        if exc.code in (401, 403):
            raise ValueError(f"OpenRouter refused the API key (HTTP {exc.code}); check {KEY_ENV}: {detail}") from exc
        raise ValueError(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"cannot reach OpenRouter at {url} within {CHECK_TIMEOUT_S} s (internet needed): {exc}") from exc


def check(base_url: str, model: str, key: str, think: bool) -> dict:
    """Before a run. Returns {"extra", "temperature", "version"} for the chat client and the record.
    Raises ValueError with what to do if the key, the model or the settings will not work."""
    base = base_url.rstrip("/")
    log.info("checking the API key and model %s at %s (up to %d s each)", model, base, CHECK_TIMEOUT_S)
    info = _get(base + "/key", key).get("data") or {}
    log.info("OpenRouter answered: key accepted (label %r, credit left %s)", info.get("label"),
             "no limit set" if info.get("limit_remaining") is None else f"${info['limit_remaining']:.2f}")
    entry = next((m for m in _get(base + "/models", key).get("data", []) if m.get("id") == model), None)
    if entry is None:
        raise ValueError(f"OpenRouter has no model {model!r}; use its id from https://openrouter.ai/models, "
                         "e.g. anthropic/claude-opus-5.5")
    params = set(entry.get("supported_parameters") or [])
    if "tools" not in params:
        raise ValueError(f"{model} does not support tool calls on OpenRouter; the agent needs them")
    reasoning = entry.get("reasoning") or {}
    if think:
        extra = {}
        log.info("%s: reasoning left at the model's default (effort %s)", model, reasoning.get("default_effort", "unknown"))
    elif reasoning.get("mandatory"):
        raise ValueError(f"{model} always reasons and refuses reasoning off; run with --think "
                         f"(its default effort is {reasoning.get('default_effort', 'unknown')})")
    else:
        extra = {"reasoning": {"effort": "none"}} if "reasoning" in params else {}
    temperature = 0.0 if "temperature" in params else None
    if temperature is None:
        log.warning("%s ignores temperature: it is not sent, and replies may differ between runs", model)
    pricing = entry.get("pricing") or {}
    log.info("%s: $%.2f per million input tokens, $%.2f per million output tokens (OpenRouter's list)",
             model, float(pricing.get("prompt") or 0) * 1e6, float(pricing.get("completion") or 0) * 1e6)
    return {"extra": extra, "temperature": temperature, "version": entry.get("canonical_slug") or model}
