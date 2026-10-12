"""Cost tracker for agents served by a local vLLM endpoint.

Interface-compatible with `cost_tracker.py` (same `reset()`,
`record_langchain_usage(response)`, `save_cost_json(model_name)` signatures and
the same cost.json schema) so `eval_ppa_sr.sh` aggregates it without changes.

Why a separate tracker: self-hosted inference has no $/token invoice, but we
still want a cost number that (a) ranks agents fairly against cloud-API agents,
and (b) does not depend on who else happened to be hitting the vLLM endpoint
during this run. The standard fix is throughput-normalized token cost:

    $/output_token = gpu_hourly_rate / (3600 * decode_tokens_per_sec_per_gpu)
    $/input_token  = gpu_hourly_rate / (3600 * prefill_tokens_per_sec_per_gpu)

This is invariant to server load (unlike wall-clock latency), attributes cost
to the tokens *this agent* generated (unlike "2 GPUs x my latency"), and maps
cleanly onto cloud vendors' public $/1M-token pricing.

Default rates below are derived for gemma-4-31B-it on 2x RTX PRO 6000 Blackwell
Server @ $1.50/GPU-hr:
    decode  ~1500 tok/s aggregate (H100 ref: 1260 tok/s @ concurrency 128)
    prefill ~25000 tok/s aggregate (~20-30x decode)
    server  2 * $1.50 = $3.00/hr
    -> output: $3.00 / (1500 * 3600 / 1e6) = $0.556 / 1M out
    -> input:  $3.00 / (25000 * 3600 / 1e6) = $0.033 / 1M in

Override per-deployment via env vars:
    VLLM_INPUT_USD_PER_1M   (default 0.033)
    VLLM_OUTPUT_USD_PER_1M  (default 0.556)
"""

import json
import os


token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# Per-token USD rates for self-hosted models. Keyed identically to
# cost_tracker.TOKEN_COST so downstream aggregation is uniform. Rates here are
# throughput-normalized (see module docstring). Env vars override at
# save_cost_json time.
TOKEN_COST = {
    "google/gemma-4-31B-it": {"input": 0.033 / 1e6, "output": 0.556 / 1e6},
    # Qwen/Qwen3.5-122B-A10B on 2x H200 (TP=2).
    # Derivation (throughput-normalized, same methodology as Gemma above):
    #   server  = 2 * $3.50/GPU-hr = $7.00/hr        (H200 specialist-cloud range)
    #   decode  ~8,000 tok/s aggregate              (scaled from MLPerf Qwen3-VL-235B-A22B
    #                                                on 8xH200: 28,680 tok/s -> ~3,600 tok/s
    #                                                per GPU per 22B active; 10B active ->
    #                                                ~1.6x faster; conservative on 2 GPUs)
    #   prefill ~150,000 tok/s aggregate            (MoE prefill w/ prefix caching, ~20x decode)
    #   -> output: $7.00 / (8000 * 3600 / 1e6)  = $0.243 / 1M out
    #   -> input:  $7.00 / (150000 * 3600 / 1e6) = $0.013 / 1M in
    # Cross-check: OpenRouter Qwen3.5 Plus (same MoE family) lists $0.26 in / $1.56 out
    # per 1M; our output is same order, our input is lower (expected for self-host vs API).
    "Qwen/Qwen3.5-122B-A10B": {"input": 0.013 / 1e6, "output": 0.243 / 1e6},
    # Qwen/Qwen3.5-9B (dense) on 2x RTX PRO 6000 Blackwell Server (TP=2).
    # Key matches the vLLM --served-model-name ("qwen3.5-9b"), which is what
    # ChatOpenAI(model=...) returns and what save_cost_json receives.
    # Derivation (throughput-normalized, same methodology as Gemma above):
    #   server  = 2 * $1.50/GPU-hr = $3.00/hr
    #   decode  ~3,000 tok/s aggregate              (dense 9B, ~2x gemma-4-31B
    #                                                decode rate per GPU)
    #   prefill ~50,000 tok/s aggregate             (~17x decode, dense)
    #   -> output: $3.00 / (3000 * 3600 / 1e6)  = $0.278 / 1M out
    #   -> input:  $3.00 / (50000 * 3600 / 1e6) = $0.017 / 1M in
    "qwen3.5-9b": {"input": 0.017 / 1e6, "output": 0.278 / 1e6},
}


def _rates_for(model_name):
    """Return {'input': ..., 'output': ...} $/token rates. Env vars win over
    the hard-coded table, table wins over zero."""
    env_in = os.environ.get("VLLM_INPUT_USD_PER_1M")
    env_out = os.environ.get("VLLM_OUTPUT_USD_PER_1M")
    base = TOKEN_COST.get(model_name, {"input": 0.0, "output": 0.0})
    inp = (float(env_in) / 1e6) if env_in is not None else base["input"]
    out = (float(env_out) / 1e6) if env_out is not None else base["output"]
    return {"input": inp, "output": out}


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

    ChatOpenAI against a vLLM /v1 endpoint populates usage_metadata the same
    way the OpenAI provider does, so this works unchanged.
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
    """Pull token counts from a raw OpenAI-style response."""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    inp_raw = getattr(usage, "prompt_tokens", 0) or 0
    out_raw = getattr(usage, "completion_tokens", 0) or 0
    tot_raw = getattr(usage, "total_tokens", None)
    inp, out, _ = _add(inp_raw, out_raw, tot_raw)
    _log_cumulative(inp, out)


def save_cost_json(model_name, result_dir=None):
    """Write cost.json into $AGENT_RESULT_DIR. Schema matches cost_tracker."""
    result_dir = result_dir or os.environ.get("AGENT_RESULT_DIR", ".")
    rates = _rates_for(model_name)
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
