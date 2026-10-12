"""Shared token-usage and cost tracking for PPA agents.

Each agent imports this module, calls `record_langchain_usage(response)` (for
LangChain LLMs), `record_openai_usage(resp)` (for raw OpenAI-compatible
clients) or `record_anthropic_usage(resp)` (for the Anthropic SDK) after every
LLM call, and calls `save_cost_json(model_name)` once in its `finally` block.
The resulting cost.json lands in $AGENT_RESULT_DIR (provided by eval_ppa_sr.sh)
and is picked up by the eval script's aggregation stage.
"""

import json
import os


# Cumulative token counts for the current agent run. Reset with reset().
# `input_tokens` is the *total* prompt size: uncached input plus any tokens
# billed at the cache-write / cache-read rate. The two cache buckets are also
# tracked separately so cost can be priced correctly; they stay 0 for agents
# that do not use prompt caching.
# Cache writes are split by TTL because they bill differently (5m = 1.25x input,
# 1h = 2x input); cache reads are 0.1x regardless.
token_usage = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_creation_5m_input_tokens": 0,
    "cache_creation_1h_input_tokens": 0,
    "cache_read_input_tokens": 0,
}


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
    # Cache writes bill at 1.25x input, cache reads at 0.1x input; those rates
    # are derived automatically when not spelled out here.
    "claude-opus-5":                         {"input": 5.00  / 1e6, "output": 25.00 / 1e6},
    "claude-opus-4-8":                       {"input": 5.00  / 1e6, "output": 25.00 / 1e6},
    "claude-sonnet-5":                       {"input": 3.00  / 1e6, "output": 15.00 / 1e6},
    "claude-haiku-4-5":                      {"input": 1.00  / 1e6, "output": 5.00  / 1e6},
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


def _add(input_tokens, output_tokens, total_tokens=None,
         cache_creation=0, cache_read=0):
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    tot = int(total_tokens) if total_tokens is not None else (inp + out)
    token_usage["input_tokens"] += inp
    token_usage["output_tokens"] += out
    token_usage["total_tokens"] += tot
    token_usage["cache_creation_input_tokens"] += int(cache_creation or 0)
    token_usage["cache_read_input_tokens"] += int(cache_read or 0)
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


def record_anthropic_usage(usage):
    """Pull token counts from an Anthropic Messages API `usage` block.

    Accepts either the SDK's `Usage` object or its dict form. Prompt tokens are
    reported by the API in three buckets (uncached input, cache writes, cache
    reads); they are summed into input_tokens and also kept separately so
    save_cost_json() can bill each at its own rate.
    """
    if usage is None:
        return
    if isinstance(usage, dict):
        def get(key):
            return usage.get(key, 0) or 0
    else:
        def get(key):
            return getattr(usage, key, 0) or 0

    uncached_in = int(get("input_tokens"))
    cache_write = int(get("cache_creation_input_tokens"))
    cache_read = int(get("cache_read_input_tokens"))
    out_raw = int(get("output_tokens"))
    inp_raw = uncached_in + cache_write + cache_read

    # Per-TTL cache-write breakdown, when the API reports it. Anything not
    # broken out is billed at the 5m rate.
    breakdown = get("cache_creation")
    if breakdown:
        bget = (breakdown.get if isinstance(breakdown, dict)
                else lambda k: getattr(breakdown, k, 0))
        write_1h = int(bget("ephemeral_1h_input_tokens") or 0)
        write_5m = int(bget("ephemeral_5m_input_tokens") or 0)
    else:
        write_1h, write_5m = 0, cache_write
    token_usage["cache_creation_1h_input_tokens"] += write_1h
    token_usage["cache_creation_5m_input_tokens"] += write_5m

    inp, out, _ = _add(inp_raw, out_raw,
                       cache_creation=cache_write, cache_read=cache_read)
    _log_cumulative(inp, out)


def save_cost_json(model_name, result_dir=None):
    """Write cost.json into $AGENT_RESULT_DIR (or `result_dir` if given, or cwd).

    Called once in the agent's `finally` block. Never raises.
    """
    result_dir = result_dir or os.environ.get("AGENT_RESULT_DIR", ".")
    rates = TOKEN_COST.get(model_name, {"input": 0.0, "output": 0.0})
    in_rate = rates.get("input", 0.0)
    # Anthropic bills cache writes at 1.25x (5m TTL) / 2x (1h TTL) and cache
    # reads at 0.1x the input rate; all cache buckets are 0 unless the agent
    # enabled prompt caching.
    cache_write_5m_rate = rates.get("cache_write_5m", in_rate * 1.25)
    cache_write_1h_rate = rates.get("cache_write_1h", in_rate * 2.0)
    cache_read_rate = rates.get("cache_read", in_rate * 0.1)
    cache_write_tokens = token_usage["cache_creation_input_tokens"]
    cache_write_1h = token_usage["cache_creation_1h_input_tokens"]
    # Anything not reported per-TTL falls back to the 5m rate.
    cache_write_5m = max(0, cache_write_tokens - cache_write_1h)
    cache_read_tokens = token_usage["cache_read_input_tokens"]
    uncached_input = max(
        0, token_usage["input_tokens"] - cache_write_tokens - cache_read_tokens
    )

    input_cost = (
        uncached_input * in_rate
        + cache_write_5m * cache_write_5m_rate
        + cache_write_1h * cache_write_1h_rate
        + cache_read_tokens * cache_read_rate
    )
    output_cost = token_usage["output_tokens"] * rates["output"]
    total_cost = input_cost + output_cost
    data = {
        "model": model_name,
        "input_tokens": token_usage["input_tokens"],
        "output_tokens": token_usage["output_tokens"],
        "total_tokens": token_usage["total_tokens"],
        "cache_creation_input_tokens": cache_write_tokens,
        "cache_creation_5m_input_tokens": cache_write_5m,
        "cache_creation_1h_input_tokens": cache_write_1h,
        "cache_read_input_tokens": cache_read_tokens,
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
    for key in token_usage:
        token_usage[key] = 0
