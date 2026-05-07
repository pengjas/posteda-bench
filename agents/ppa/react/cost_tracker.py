"""Shared token-usage and cost tracking for PPA agents.

Each agent imports this module, calls `record_langchain_usage(response)` (for
LangChain LLMs) or `record_openai_usage(resp)` (for raw OpenAI-compatible
clients) after every LLM call, and calls `save_cost_json(model_name)` once in
its `finally` block. The resulting cost.json lands in $AGENT_RESULT_DIR
(provided by eval_ppa_sr.sh) and is picked up by the eval script's aggregation
stage.
"""

import json
import os


# Cumulative token counts for the current agent run. Reset with reset().
token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# Per-token USD rates. Update as vendor pricing changes.
# Expressed as dollars-per-1M-tokens divided by 1e6 to keep the table
# human-readable at a glance.
TOKEN_COST = {
    # --- Google Vertex AI / GenAI ---
    "gemini-3-flash-preview":                {"input": 0.50  / 1e6, "output": 3.00  / 1e6},
    "gemini-3-flash":                        {"input": 0.50  / 1e6, "output": 3.00  / 1e6},
    "gemini-2.0-flash-001":                  {"input": 0.10  / 1e6, "output": 0.40  / 1e6},
    "gemini-1.5-pro-001":                    {"input": 1.25  / 1e6, "output": 5.00  / 1e6},
    "gemini-1.5-flash":                      {"input": 0.075 / 1e6, "output": 0.30  / 1e6},

    # --- OpenAI ---
    "gpt-5":                                 {"input": 1.25  / 1e6, "output": 10.00 / 1e6},
    "gpt-5.1":                               {"input": 1.25  / 1e6, "output": 10.00 / 1e6},
    "gpt-5-mini":                            {"input": 0.25  / 1e6, "output": 2.00  / 1e6},
    "gpt-4o":                                {"input": 2.50  / 1e6, "output": 10.00 / 1e6},
    "gpt-4o-mini":                           {"input": 0.15  / 1e6, "output": 0.60  / 1e6},

    # --- Anthropic ---
    "claude-haiku-4-5-20251001":             {"input": 1.00  / 1e6, "output": 5.00  / 1e6},
    "claude-sonnet-4-5-20250929":            {"input": 3.00  / 1e6, "output": 15.00 / 1e6},
    "claude-sonnet-4-20250514":              {"input": 3.00  / 1e6, "output": 15.00 / 1e6},
    "claude-opus-4-20250514":                {"input": 15.00 / 1e6, "output": 75.00 / 1e6},

    # --- Vertex AI MaaS (third-party) ---
    "deepseek-ai/deepseek-v3.1-maas":        {"input": 0.27  / 1e6, "output": 1.10  / 1e6},
    "qwen/qwen3-next-80b-a3b-instruct-maas": {"input": 0.15  / 1e6, "output": 0.60  / 1e6},
    # Model Garden self-deployed pricing is node-hours rather than per-token;
    # leave as 0 so the field exists but does not inflate token-derived cost.
    "qwen/qwen3-coder@qwen3-coder-480b-a35b-instruct": {"input": 0.0, "output": 0.0},
}


def _add(input_tokens, output_tokens, total_tokens=None):
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    tot = int(total_tokens) if total_tokens is not None else (inp + out)
    token_usage["input_tokens"] += inp
    token_usage["output_tokens"] += out
    token_usage["total_tokens"] += tot
    return inp, out, tot


def _log_cumulative(inp, out):
    print(
        f"  Tokens this call: in={inp}, out={out}, "
        f"cumulative: in={token_usage['input_tokens']}, "
        f"out={token_usage['output_tokens']}, "
        f"total={token_usage['total_tokens']}",
        flush=True,
    )


def record_langchain_usage(response):
    """Pull token counts from a LangChain response's usage_metadata.

    Works for google_vertexai, openai, anthropic providers — they all populate
    the LangChain-standard usage_metadata dict with input_tokens / output_tokens
    / total_tokens keys.
    """
    um = getattr(response, "usage_metadata", None)
    if not um:
        return
    if isinstance(um, dict):
        inp_raw = um.get("input_tokens", 0)
        out_raw = um.get("output_tokens", 0)
        tot_raw = um.get("total_tokens")
    else:
        inp_raw = getattr(um, "input_tokens", 0)
        out_raw = getattr(um, "output_tokens", 0)
        tot_raw = getattr(um, "total_tokens", None)
    inp, out, _ = _add(inp_raw, out_raw, tot_raw)
    _log_cumulative(inp, out)


def record_openai_usage(resp):
    """Pull token counts from an OpenAI-style response (resp.usage)."""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    inp_raw = getattr(usage, "prompt_tokens", 0) or 0
    out_raw = getattr(usage, "completion_tokens", 0) or 0
    tot_raw = getattr(usage, "total_tokens", None)
    inp, out, _ = _add(inp_raw, out_raw, tot_raw)
    _log_cumulative(inp, out)


def save_cost_json(model_name, result_dir=None):
    """Write cost.json into $AGENT_RESULT_DIR (or `result_dir` if given, or cwd).

    Called once in the agent's `finally` block. Never raises.
    """
    result_dir = result_dir or os.environ.get("AGENT_RESULT_DIR", ".")
    rates = TOKEN_COST.get(model_name, {"input": 0.0, "output": 0.0})
    input_cost = token_usage["input_tokens"] * rates["input"]
    output_cost = token_usage["output_tokens"] * rates["output"]
    total_cost = input_cost + output_cost
    data = {
        "model": model_name,
        "input_tokens": token_usage["input_tokens"],
        "output_tokens": token_usage["output_tokens"],
        "total_tokens": token_usage["total_tokens"],
        "input_cost_usd": round(input_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd": round(total_cost, 6),
    }
    path = os.path.join(result_dir, "cost.json")
    try:
        os.makedirs(result_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Cost saved to {path}", flush=True)
    except Exception as e:
        print(f"Warning: could not save cost.json at {path}: {e}", flush=True)
    return data


def reset():
    token_usage["input_tokens"] = 0
    token_usage["output_tokens"] = 0
    token_usage["total_tokens"] = 0
