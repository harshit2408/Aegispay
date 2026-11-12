"""Reviewed LLM (provider, model, output cap) allowlist (R2/R14).

Stdlib only: the S1 preflight loads this file by path. Env values are never trusted; they
only *select* an entry, and the selection must be a member of REVIEWED. Each entry pins its
own endpoint, so an API key can only ever be sent to that provider's host.
"""

from collections.abc import Mapping

OUTPUT_CAP = 1024
# Free models: demo only, synthetic PDFs only; free providers may log prompts (docs/TOOLS.md).
REVIEWED = {
    ("openai", "gpt-6.1-sol", OUTPUT_CAP): "https://api.openai.com/v1/chat/completions",
    (
        "openrouter",
        "nvidia/nemotron-3.5-super-vl:free",
        OUTPUT_CAP,
    ): "https://openrouter.ai/api/v1/chat/completions",
}
DEFAULT_MODEL = {
    "openai": "gpt-6.1-sol",
    "openrouter": "nvidia/nemotron-3.5-super-vl:free",
}


def select(env: Mapping[str, str]) -> tuple[str, str]:
    """Return the reviewed (provider, model) named by LLM_* values; ValueError otherwise."""
    provider = (env.get("LLM_PROVIDER") or "").strip() or "openai"
    default = DEFAULT_MODEL.get(provider, "")
    extract = (env.get("LLM_MODEL_EXTRACT") or "").strip() or default
    reason = (env.get("LLM_MODEL_REASON") or "").strip() or default
    # One model per run: the TokenOps run, price and budget are keyed on a single (provider, model).
    if extract != reason or (provider, extract, OUTPUT_CAP) not in REVIEWED:
        raise ValueError("unreviewed LLM provider or model")
    return provider, extract


OPENROUTER_KEY_PREFIX = "sk-or-"


def key_matches(provider: str, api_key: str) -> bool:
    """Never send one provider's key to another (OpenRouter keys are `sk-or-...`)."""
    return api_key.startswith(OPENROUTER_KEY_PREFIX) == (provider == "openrouter")
