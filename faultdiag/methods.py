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
from .engine_v2_consistency import BayesEngineV2Consistency
from .engine_v2_balance import BayesEngineV2Balance
from .engine_v2_human import BayesEngineV2Human
from .engine_v2_coupled import BayesEngineV2Coupled, BayesEngineV2HumanCoupled
from .logsetup import get_logger
from .runmeta import ollama_model_version

log = get_logger("methods")

METHODS = ("fixed", "tree", "bayes", "llm", "hybrid")
EXPERIMENTAL_METHODS = (
    "bayes_v2", "bayes_v2_loose", "bayes_v2_strict", "bayes_v2_xstrict",
    "bayes_v2_hard", "bayes_v2_verify", "bayes_v2_compound",
    "bayes_v2_kcl4", "bayes_v2_kcl5", "bayes_v2_kcl6",
    "bayes_v2_bal4", "bayes_v2_bal5", "bayes_v2_bal6",
    "bayes_v2_human3", "bayes_v2_human6", "bayes_v2_human10",
    "bayes_v2_coupled", "bayes_v2_human_coupled",
    "bayes_v2_coupled_topo", "bayes_v2_human_coupled_topo",
    "bayes_v2_human_coupled_sensor",
)
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
    v2_cache = {}
    kcl_cache = {}
    balance_cache = {}
    human_cache = {}
    coupled_cache = {}

    def v2(name):
        if name not in v2_cache:
            configs = {
                "bayes_v2": {"min_confirm_score": 0.005},
                "bayes_v2_loose": {"min_confirm_score": 0.002},
                "bayes_v2_strict": {"min_confirm_score": 0.010},
                "bayes_v2_xstrict": {"min_confirm_score": 0.020},
                "bayes_v2_hard": {"min_confirm_score": 1.0},
                "bayes_v2_verify": {"min_confirm_score": 1.0, "verify_signatures": True},
                "bayes_v2_compound": {"min_confirm_score": 1.0, "challenge_complex_hypothesis": True},
            }
            v2_cache[name] = BayesEngineV2(**configs[name])
        return v2_cache[name]

    def kcl(name):
        if name not in kcl_cache:
            threshold = {"bayes_v2_kcl4": 4.0, "bayes_v2_kcl5": 5.0, "bayes_v2_kcl6": 6.0}[name]
            kcl_cache[name] = BayesEngineV2Consistency(consistency_z=threshold)
        return kcl_cache[name]

    def balance(name):
        if name not in balance_cache:
            threshold = {"bayes_v2_bal4": 4.0, "bayes_v2_bal5": 5.0, "bayes_v2_bal6": 6.0}[name]
            balance_cache[name] = BayesEngineV2Balance(balance_z=threshold)
        return balance_cache[name]

    def human(name):
        if name not in human_cache:
            score = {"bayes_v2_human3": 0.003, "bayes_v2_human6": 0.006, "bayes_v2_human10": 0.010}[name]
            human_cache[name] = BayesEngineV2Human(balance_z=6.0, min_human_score=score)
        return human_cache[name]

    def coupled(name):
        if name not in coupled_cache:
            if name == "bayes_v2_coupled":
                coupled_cache[name] = BayesEngineV2Coupled(balance_z=6.0)
            elif name == "bayes_v2_coupled_topo":
                coupled_cache[name] = BayesEngineV2Coupled(balance_z=6.0, cross_branch_only=True)
            elif name == "bayes_v2_human_coupled_topo":
                coupled_cache[name] = BayesEngineV2HumanCoupled(
                    balance_z=6.0, min_human_score=0.006, cross_branch_only=True
                )
            elif name == "bayes_v2_human_coupled_sensor":
                coupled_cache[name] = BayesEngineV2HumanCoupled(
                    balance_z=6.0, min_human_score=0.006, cross_branch_only=True,
                    allow_sensor_ambiguity=True, sensor_fault_mass_threshold=0.50
                )
            else:
                coupled_cache[name] = BayesEngineV2HumanCoupled(
                    balance_z=6.0, min_human_score=0.006
                )
        return coupled_cache[name]

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
            methods[m] = v2("bayes_v2").run
        elif m == "bayes_v2_loose":
            methods[m] = v2("bayes_v2_loose").run
        elif m == "bayes_v2_strict":
            methods[m] = v2("bayes_v2_strict").run
        elif m == "bayes_v2_xstrict":
            methods[m] = v2("bayes_v2_xstrict").run
        elif m == "bayes_v2_hard":
            methods[m] = v2("bayes_v2_hard").run
        elif m == "bayes_v2_verify":
            methods[m] = v2("bayes_v2_verify").run
        elif m == "bayes_v2_compound":
            methods[m] = v2("bayes_v2_compound").run
        elif m in ("bayes_v2_kcl4", "bayes_v2_kcl5", "bayes_v2_kcl6"):
            methods[m] = kcl(m).run
        elif m in ("bayes_v2_bal4", "bayes_v2_bal5", "bayes_v2_bal6"):
            methods[m] = balance(m).run
        elif m in ("bayes_v2_human3", "bayes_v2_human6", "bayes_v2_human10"):
            methods[m] = human(m).run
        elif m in ("bayes_v2_coupled", "bayes_v2_human_coupled", "bayes_v2_coupled_topo", "bayes_v2_human_coupled_topo", "bayes_v2_human_coupled_sensor"):
            methods[m] = coupled(m).run
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
            raise ValueError(f"unknown method {m}; choose from {', '.join(METHODS + EXPERIMENTAL_METHODS)}")
    return methods, meta
