"""Build the diagnostic methods A to E by name, for the experiment scripts and the GUI.

    fixed = A (fixed sequence)   tree = B (decision tree)   bayes = C (Bayesian engine)
    llm   = D (LLM alone)        hybrid = E (LLM on top of the engine)
"""
from __future__ import annotations

from .agent import Agent, OpenAICompatLLM
from .baselines import run_fixed, run_tree
from .engine import BayesEngine
from .logsetup import get_logger
from .runmeta import ollama_model_version

log = get_logger("methods")

METHODS = ("fixed", "tree", "bayes", "llm", "hybrid")
DEFAULT_URL = {"ollama": "http://localhost:11434/v1", "llamacpp": "http://localhost:8080/v1"}


def llm_extra(server: str, think: bool) -> dict:
    """Request fields that switch a thinking model's reasoning off (docs/DECISIONS.md)."""
    if think:
        return {}
    return {"reasoning_effort": "none"} if server == "ollama" else {"chat_template_kwargs": {"enable_thinking": False}}


def build_methods(names, model: str | None = None, server: str = "ollama", base_url: str | None = None,
                  think: bool = False, timeout_s: float = 180, model_version: str | None = None):
    """Returns (methods, method_meta): name -> fn(guard, seed), name -> record extras."""
    base_url = base_url or DEFAULT_URL[server]
    engine = BayesEngine()
    methods, meta = {}, {}
    for m in names:
        if m == "fixed":
            methods[m] = run_fixed
        elif m == "tree":
            methods[m] = run_tree
        elif m == "bayes":
            methods[m] = engine.run
        elif m in ("llm", "hybrid"):
            if not model:
                raise ValueError(f"a model name is needed for method {m}")
            extra = llm_extra(server, think)
            log.info("method %s: model %s on %s, request extras %s", m, model, base_url, extra)
            agent = Agent(OpenAICompatLLM(base_url, model, timeout_s=timeout_s, extra=extra),
                          engine if m == "hybrid" else None)
            methods[m] = agent.run
            meta[m] = {"model": model, "think": think,
                       "model_version": model_version or (ollama_model_version(base_url, model)
                                                          if server == "ollama" else None)}
        else:
            raise ValueError(f"unknown method {m}; choose from {', '.join(METHODS)}")
    return methods, meta
