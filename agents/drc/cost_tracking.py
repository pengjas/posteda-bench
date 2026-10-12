"""Shared cost-tracking helpers for DRC agents.

Usage (from any agent script):

    from agents.drc.cost_tracking import record_usage, write_cost_json, reset_tracker

    ...
    response = llm_with_tools.invoke(...)
    record_usage(response)                        # call after every successful LLM invoke
    ...

    finally:
        write_cost_json(llm)                      # call once at the end; writes cost.json

`write_cost_json` writes into the directory pointed to by the environment
variable `AGENT_RESULT_DIR` (same convention used by the reference
gpt_react.py / eval.sh pair), and also emits a short summary to stdout.

If `AGENT_RESULT_DIR` is unset, cost.json is written next to the process's
current working directory so the data is never lost.

The TOKEN_COST table covers the models we currently run in this benchmark.
Add or override entries as needed -- pricing is expressed in USD per token
(i.e. the per-1M-token list price divided by 1,000,000). For models that run
locally (paths beginning with `/mnt/...`) the cost is zero.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Pricing table (USD per token). Derived from public list prices; numbers here
# are approximate and should be refreshed if you care about exact cost.
# Unknown / local models fall through to the zero-cost default.
# ---------------------------------------------------------------------------
def _per_million(input_price: float, output_price: float) -> Dict[str, float]:
    return {"input": input_price / 1_000_000, "output": output_price / 1_000_000}


TOKEN_COST: Dict[str, Dict[str, float]] = {
    # --- OpenAI ---
    "gpt-5":          _per_million(1.25, 10.00),
    "gpt-5-mini":     _per_million(0.25, 2.00),
    "gpt-5-nano":     _per_million(0.05, 0.40),
    "gpt-4o":         _per_million(2.50, 10.00),
    "gpt-4o-mini":    _per_million(0.15, 0.60),
    "gpt-4.1":        _per_million(2.00, 8.00),
    "gpt-4.1-mini":   _per_million(0.40, 1.60),
    "gpt-4.1-nano":   _per_million(0.10, 0.40),

    # --- Google Gemini (AI Studio / Vertex list prices) ---
    "gemini-2.0-flash-001":     _per_million(0.10, 0.40),
    "gemini-2.5-flash":         _per_million(0.30, 2.50),
    "gemini-2.5-pro":           _per_million(1.25, 10.00),
    "gemini-3-flash-preview":   _per_million(0.30, 2.50),
    "gemini-3-pro-preview":     _per_million(1.25, 10.00),

    # --- DeepSeek (Vertex MaaS + direct API) ---
    "deepseek-chat":                    _per_million(0.27, 1.10),
    "deepseek-reasoner":                _per_million(0.55, 2.19),
    "deepseek-ai/deepseek-v3.1-maas":   _per_million(0.27, 1.10),

    # --- Qwen via Vertex MaaS (approximate) ---
    "qwen/qwen3-next-80b-a3b-instruct-maas": _per_million(0.15, 0.60),

    # --- Local / self-hosted (free on the compute side) ---
    # Any path beginning with "/" is treated as a local model.
}


# ---------------------------------------------------------------------------
# Module-level accumulators. Agent scripts run in a fresh process per attempt,
# so reset on import is the right default.
# ---------------------------------------------------------------------------
token_usage_tracker: Dict[str, int] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "reasoning_tokens": 0,
    "total_tokens": 0,
    "llm_calls": 0,
}


def reset_tracker() -> None:
    """Zero the accumulators. Call at the top of the agent's main()."""
    for k in token_usage_tracker:
        token_usage_tracker[k] = 0


def _extract_counts(um: Any) -> Dict[str, int]:
    """Pull token counts out of a LangChain UsageMetadata dict or similar.

    Handles the two most common shapes:
      * LangChain-normalized: input_tokens / output_tokens / total_tokens,
        optionally with output_token_details={"reasoning": N}.
      * Vendor-native (Gemini): prompt_token_count / candidates_token_count /
        thoughts_token_count / total_token_count.
    """
    if um is None:
        return {"input": 0, "output": 0, "reasoning": 0, "total": 0}

    get = (lambda k, d=0: um.get(k, d)) if isinstance(um, dict) else (lambda k, d=0: getattr(um, k, d))

    inp = get("input_tokens") or get("prompt_token_count") or 0
    out = get("output_tokens") or get("candidates_token_count") or 0
    rea = get("thoughts_token_count") or 0
    if not rea:
        details = get("output_token_details") or {}
        if isinstance(details, dict):
            rea = details.get("reasoning", 0) or 0
    tot = get("total_tokens") or get("total_token_count") or (inp + out + rea)

    return {"input": int(inp or 0), "output": int(out or 0), "reasoning": int(rea or 0), "total": int(tot or 0)}


def record_usage(response: Any) -> None:
    """Accumulate token counts from an LLM response.

    Call this immediately after a successful `llm.invoke(...)` /
    `llm_with_tools.invoke(...)`. Safe to call on responses without usage
    metadata -- those are silently ignored.
    """
    um = getattr(response, "usage_metadata", None)
    if um is None:
        return
    counts = _extract_counts(um)
    token_usage_tracker["input_tokens"] += counts["input"]
    token_usage_tracker["output_tokens"] += counts["output"]
    token_usage_tracker["reasoning_tokens"] += counts["reasoning"]
    token_usage_tracker["total_tokens"] += counts["total"]
    token_usage_tracker["llm_calls"] += 1


def _resolve_model_name(llm: Any) -> str:
    """Best-effort extraction of a human-readable model identifier."""
    for attr in ("model_name", "model", "deployment_name"):
        val = getattr(llm, attr, None)
        if isinstance(val, str) and val:
            return val
    return "unknown"


def _lookup_cost(model_name: str) -> Dict[str, float]:
    if model_name in TOKEN_COST:
        return TOKEN_COST[model_name]
    # Local path-based model: zero cost
    if model_name.startswith("/") or model_name == "unknown":
        return {"input": 0.0, "output": 0.0}
    # Fuzzy fallback: strip provider prefixes / versions to try a shorter key
    short = model_name.split("/")[-1]
    if short in TOKEN_COST:
        return TOKEN_COST[short]
    return {"input": 0.0, "output": 0.0}


def write_cost_json(llm: Any, result_dir: Optional[str] = None) -> Dict[str, Any]:
    """Serialize the accumulated tokens and computed cost to `cost.json`.

    Returns the same dict that was written, so the caller can also print or
    log it. Uses `AGENT_RESULT_DIR` if `result_dir` is not provided.
    """
    model_name = _resolve_model_name(llm)
    rates = _lookup_cost(model_name)
    input_cost = token_usage_tracker["input_tokens"] * rates["input"]
    output_cost = token_usage_tracker["output_tokens"] * rates["output"]
    total_cost = input_cost + output_cost

    cost_data: Dict[str, Any] = {
        "model": model_name,
        "llm_calls": token_usage_tracker["llm_calls"],
        "input_tokens": token_usage_tracker["input_tokens"],
        "output_tokens": token_usage_tracker["output_tokens"],
        "reasoning_tokens": token_usage_tracker["reasoning_tokens"],
        "total_tokens": token_usage_tracker["total_tokens"],
        "input_cost_usd": round(input_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd": round(total_cost, 6),
        "rate_input_per_token": rates["input"],
        "rate_output_per_token": rates["output"],
    }

    target_dir = result_dir or os.environ.get("AGENT_RESULT_DIR") or "."
    cost_path = os.path.join(target_dir, "cost.json")
    try:
        os.makedirs(target_dir, exist_ok=True)
        with open(cost_path, "w") as f:
            json.dump(cost_data, f, indent=2)
        print(f"Cost saved to {cost_path}", flush=True)
    except Exception as e:
        print(f"Warning: could not save cost.json: {e}", flush=True)

    print(
        f"TOKEN USAGE: input={cost_data['input_tokens']}, "
        f"output={cost_data['output_tokens']}, "
        f"reasoning={cost_data['reasoning_tokens']}, "
        f"total={cost_data['total_tokens']}",
        flush=True,
    )
    print(
        f"ESTIMATED COST: ${total_cost:.6f} "
        f"(input=${input_cost:.6f}, output=${output_cost:.6f}, model={model_name})",
        flush=True,
    )
    return cost_data
