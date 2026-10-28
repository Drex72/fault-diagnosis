"""Build the diagnostic methods A to E by name, for the experiment scripts and the GUI.

    fixed = A (fixed sequence)   tree = B (decision tree)   bayes = C (Bayesian engine)
    llm   = D (LLM alone)        hybrid = E (LLM on top of the engine)
"""
from __future__ import annotations

from . import gemini, jev, openrouter
from .agent import Agent, OpenAICompatLLM
from .baselines import run_fixed, run_tree
from .engine import BayesEngine
from .engine_v2 import BayesEngineV2
from .logsetup import get_logger
from .runmeta import ollama_model_version

log = get_logger("methods")

METHODS = ("fixed", "tree", "bayes", "llm", "hybrid")
DEFAULT_URL = {"ollama": "http://localhost:11434/v1", "llamacpp": "http://localhost:8080/v1", "jev": jev.DEFAULT_URL,
               "openrouter": openrouter.DEFAULT_URL, "gemini": gemini.DEFAULT_URL}
# jev: TypeSafe's cloud decision model (faultdiag/jev.py); openrouter and gemini: cloud chat models
# with an API key (faultdiag/openrouter.py, faultdiag/gemini.py)
SERVERS = tuple(DEFAULT_URL)
CLOUD_CHAT = {"openrouter": openrouter, "gemini": gemini}


def llm_extra(server: str, think: bool) -> dict:
    """Request fields that switch a thinking model's reasoning off (docs/DECISIONS.md)."""
    if think:
        return {}
    return {"reasoning_effort": "none"} if server == "ollama" else {"chat_template_kwargs": {"enable_thinking": False}}


class _Observed:
    """Passes each model reply to a callback (the GUI shows the reasoning live)."""

    def __init__(self, llm, on_reply):
        self.llm, self.on_reply = llm, on_reply

    def __getattr__(self, name):  # the wrapped client's counters (usage, last_wait_s)
        if name == "llm":
            raise AttributeError(name)
        return getattr(self.llm, name)

    def __call__(self, messages, tools):
        msg = self.llm(messages, tools)
        try:
            self.on_reply(msg)
        except Exception:  # noqa: BLE001 - a display callback must not break a session
            log.exception("on_reply callback failed")
        return msg


def build_methods(names, model: str | None = None, server: str = "ollama", base_url: str | None = None,
                  think: bool = False, timeout_s: float = 180, model_version: str | None = None, on_reply=None,
                  rpm: float | None = None):
    """Returns (methods, method_meta): name -> fn(guard, seed), name -> record extras.
    on_reply(message): optional, called with every model reply. rpm: at most this many model
    requests per minute, for a cloud chat server's rate limit (Gemini's free tier by default)."""
    base_url = base_url or DEFAULT_URL[server]
    engine = BayesEngine()
    engine_v2 = BayesEngineV2(min_confirm_score=0.005)
    engine_v2_loose = BayesEngineV2(min_confirm_score=0.002)
    engine_v2_strict = BayesEngineV2(min_confirm_score=0.010)
    engine_v2_xstrict = BayesEngineV2(min_confirm_score=0.020)
    engine_v2_hard = BayesEngineV2(min_confirm_score=1.0)
    engine_v2_verify = BayesEngineV2(min_confirm_score=1.0, verify_signatures=True)
    methods, meta = {}, {}
    jev_llm = cloud = None
    for m in names:
        if m == "fixed":
            methods[m] = run_fixed
        elif m == "tree":
            methods[m] = run_tree
        elif m == "bayes":
            methods[m] = engine.run
        elif m == "bayes_v2":
            methods[m] = engine_v2.run
        elif m == "bayes_v2_loose":
            methods[m] = engine_v2_loose.run
        elif m == "bayes_v2_strict":
            methods[m] = engine_v2_strict.run
        elif m == "bayes_v2_xstrict":
            methods[m] = engine_v2_xstrict.run
        elif m == "bayes_v2_hard":
            methods[m] = engine_v2_hard.run
        elif m == "bayes_v2_verify":
            methods[m] = engine_v2_verify.run
        elif m in ("llm", "hybrid"):
            if not model:
                raise ValueError(f"a model name is needed for method {m}")
            if server == "jev":  # one client for D and E, checked once before the run
                if jev_llm is None:
                    jev_llm = jev.JevLLM(base_url, model, jev.api_key(), timeout_s=timeout_s)
                    jev_llm.check()
                log.info("method %s: Jev model %s on %s", m, model, base_url)
                agent = Agent(_Observed(jev_llm, on_reply) if on_reply else jev_llm, engine if m == "hybrid" else None)
                methods[m] = agent.run
                meta[m] = {"model": model, "think": False, "model_version": model_version or model, "server": "jev"}
                continue
            if server in CLOUD_CHAT:  # key, model and settings checked once before the run
                if cloud is None:
                    key = CLOUD_CHAT[server].api_key()
                    cloud = {**CLOUD_CHAT[server].check(base_url, model, key, think), "key": key}
                pace = rpm or (gemini.FREE_TIER_RPM if server == "gemini" else None)
                log.info("method %s: model %s on %s, request extras %s, temperature %s, at most %s requests a minute",
                         m, model, base_url, cloud["extra"], cloud["temperature"], pace or "any")
                llm = OpenAICompatLLM(base_url, model, timeout_s=timeout_s, temperature=cloud["temperature"],
                                      extra=cloud["extra"], api_key=cloud["key"],
                                      min_interval_s=60 / pace if pace else 0.0)
                agent = Agent(_Observed(llm, on_reply) if on_reply else llm, engine if m == "hybrid" else None)
                methods[m] = agent.run
                meta[m] = {"model": model, "think": think, "model_version": model_version or cloud["version"],
                           "server": server, "temperature": cloud["temperature"], **({"rpm": pace} if pace else {})}
                continue
            extra = llm_extra(server, think)
            log.info("method %s: model %s on %s, request extras %s", m, model, base_url, extra)
            llm = OpenAICompatLLM(base_url, model, timeout_s=timeout_s, extra=extra)
            agent = Agent(_Observed(llm, on_reply) if on_reply else llm, engine if m == "hybrid" else None)
            methods[m] = agent.run
            meta[m] = {"model": model, "think": think,
                       "model_version": model_version or (ollama_model_version(base_url, model)
                                                          if server == "ollama" else None)}
        else:
            raise ValueError(f"unknown method {m}; choose from {', '.join(METHODS)}")
    return methods, meta
