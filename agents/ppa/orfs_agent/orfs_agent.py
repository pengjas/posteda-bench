"""ORFS-Agent adapted for posteda_bench PPA tasks.

Runs `OUTER_ITERATIONS` outer iterations × `PARALLEL_RUNS` candidates per
iteration. Iteration 1 = LHS init around defaults; iterations 2..N = GP-guided
top-k with diversity. Search space (tunable params + ranges + defaults) is
discovered ONCE by an LLM call that reads only prompt.txt + config/* +
src/ list + FlowVariables.md.

CLI matches `gpt_react.py` so it slots into eval_ppa_sr.sh:
    --prompt_path <path>    (we cd'd into sandbox, so this is ./prompt.txt)
    --temperature <T>
    --iterations <N>        (we treat this as TOTAL ORFS budget; outer iters
                             are derived as ceil(N / PARALLEL_RUNS))

Outputs that the eval harness expects:
  - sandbox/{config,src,reports,logs,results,info.json} updated to the BEST
    candidate's run before exit.
  - stdout line: "AGENT RUN COMPLETE. TOTAL TOOL CALLS: <n>"
  - $AGENT_RESULT_DIR/cost.json
  - $AGENT_RESULT_DIR/optim_history.json (per-iteration trajectory; new)
  - $AGENT_RESULT_DIR/orfs_summary.json  (final best + SR/VRR; new)
"""

import argparse
import datetime
import math
import os
import sys
import time
import traceback
from typing import Any, Dict, List

# Make `agents.*` imports work when this file is invoked directly.
# Resolve the repo root from this file's location: <repo>/agents/ppa/orfs_agent/orfs_agent.py
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI

from agents.ppa.react import cost_tracker
from agents.ppa.orfs_agent.gp_optimizer import (gp_propose,
                                                 initial_candidates)
from agents.ppa.orfs_agent.metrics import (compute_sr_vrr, extract_metrics,
                                            load_targets, objective_value,
                                            write_json)
from agents.ppa.orfs_agent.parallel_executor import run_candidates_parallel
from agents.ppa.orfs_agent.param_apply import copy_outputs_back
from agents.ppa.orfs_agent.search_space_discovery import discover_search_space


# =============================================================================
# Hardcoded run config — edit these values directly to change behavior.
# =============================================================================

# Outer-loop / parallelism / paths
DEFAULT_OUTER_ITERATIONS = 6   # number of GP iterations (was --iterations / parallel)
DEFAULT_PARALLEL_RUNS    = 3   # candidates run concurrently per iteration
# FlowVariables.md is under the repo's eval/ directory; can be overridden via env.
FLOW_VARS_PATH = os.environ.get(
    "FLOW_VARS_PATH",
    os.path.join(_REPO_ROOT, "eval", "FlowVariables.md"),
)

# LLM backend selection: "openai" | "vllm"
LLM_BACKEND = "vllm"

# --- OpenAI cloud (used when LLM_BACKEND == "openai") ---
OPENAI_MODEL   = "gpt-5"
# Set OPENAI_API_KEY in your environment; never commit it to source.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# --- vLLM local server (used when LLM_BACKEND == "vllm") ---
VLLM_MODEL           = "qwen3.5-9b"
VLLM_BASE_URL        = "http://localhost:9888/v1"
VLLM_API_KEY         = "vllm_fake_key"
VLLM_ENABLE_THINKING = True
VLLM_TIMEOUT         = 600.0

# =============================================================================


def _print_section(title: str):
    print("\n" + "=" * 80, flush=True)
    print(title, flush=True)
    print("=" * 80, flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="ORFS-Agent (LLM-setup + GP-loop) for posteda_bench PPA")
    parser.add_argument("--prompt_path", required=True,
                        help="path to prompt.txt (typically ./prompt.txt)")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--iterations", type=int, default=18,
                        help="total ORFS-run budget; outer iters = "
                             "ceil(iterations / parallel)")
    args = parser.parse_args()

    # --- run config (hardcoded above; CLI --iterations is informational) ----
    parallel = DEFAULT_PARALLEL_RUNS
    outer_iters = DEFAULT_OUTER_ITERATIONS

    # TOOL_TIMEOUT is set by eval_ppa_sr.sh (1800s) and consumed inside
    # run_openroad_flow; we read it here only to compute our process-join
    # deadline. Default 1800s if the harness didn't set it.
    tool_timeout = int(os.environ.get("TOOL_TIMEOUT", 1800))

    sandbox = os.environ.get("PPA_SANDBOX_ROOT") or os.getcwd()
    result_dir = os.environ.get("AGENT_RESULT_DIR") or sandbox
    info_path = os.path.join(sandbox, "info.json")

    _print_section("ORFS-Agent run starting")
    print(f"Time:        {datetime.datetime.now()}", flush=True)
    print(f"Sandbox:     {sandbox}", flush=True)
    print(f"ResultDir:   {result_dir}", flush=True)
    print(f"OuterIters:  {outer_iters}", flush=True)
    print(f"Parallel:    {parallel}", flush=True)
    print(f"ToolTimeout: {tool_timeout}s", flush=True)
    print(f"FlowVars:    {FLOW_VARS_PATH}", flush=True)

    # --- LLM bootstrap (all values hardcoded at top of this file) -----------
    # To switch model: edit LLM_BACKEND / OPENAI_MODEL / VLLM_MODEL above.
    # The OpenAI/gpt-5 path below is preserved verbatim — set LLM_BACKEND =
    # "openai" and OPENAI_MODEL = "gpt-5" to use it.
    if LLM_BACKEND == "vllm":
        model_name = VLLM_MODEL
        print(f"Backend:     vLLM ({model_name} @ {VLLM_BASE_URL})", flush=True)
        llm = ChatOpenAI(
            model=VLLM_MODEL,
            openai_api_base=VLLM_BASE_URL,
            openai_api_key=VLLM_API_KEY,
            temperature=args.temperature,
            timeout=VLLM_TIMEOUT,
            max_retries=0,
            extra_body=({"chat_template_kwargs": {"enable_thinking": True}}
                        if VLLM_ENABLE_THINKING else {}),
            model_kwargs={"tool_choice": "auto"},
        )
    else:
        # ---- OpenAI cloud (gpt-5): unchanged from the original path ----
        os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
        model_name = OPENAI_MODEL
        print(f"Backend:     OpenAI ({model_name})", flush=True)
        llm = init_chat_model(
            model=model_name, temperature=args.temperature,
            model_provider="openai", timeout=400.0,
        )

    cost_tracker.reset()
    tool_call_count = 0
    recursion_limit_reached = False  # kept for log-line compatibility

    targets = load_targets(info_path)
    print(f"\nTargets (Python-side, from info.json): {targets}", flush=True)

    history: List[Dict[str, Any]] = []
    best: Dict[str, Any] = {
        "iter": None, "cand": None, "vector": None, "metrics": {},
        "objective": float("inf"), "success": 0, "vrr": 0.0, "work_dir": None,
    }

    try:
        # --- Phase 0: LLM setup ---------------------------------------------
        _print_section("Phase 0: LLM search-space discovery")
        params = discover_search_space(llm, sandbox, FLOW_VARS_PATH, result_dir)
        if not params:
            print("ERROR: empty search space; aborting.", flush=True)
            return _finalize(model_name, tool_call_count, recursion_limit_reached,
                             best, result_dir, history, params=[])

        # --- Phase 1..N: optimization loop ----------------------------------
        for outer in range(1, outer_iters + 1):
            _print_section(f"Iteration {outer}/{outer_iters}")
            seed = 1000 + outer

            # build candidate vectors
            X_hist = [h["vector"] for h in history if h.get("vector")]
            y_hist = [h["objective"] for h in history if h.get("vector")]
            if outer == 1 or len(X_hist) < 2:
                vectors = initial_candidates(params, parallel, seed=seed)
            else:
                vectors = gp_propose(params, X_hist, y_hist, parallel,
                                     seed=seed, n_lhs=60)

            print(f"  proposed {len(vectors)} candidate(s) for iter {outer}",
                  flush=True)
            for i, v in enumerate(vectors):
                print(f"    cand {i+1}: {dict(zip([p['name'] for p in params], v))}",
                      flush=True)

            iter_dir = os.path.join(result_dir, f"orfs_iter_{outer}")
            results = run_candidates_parallel(
                main_sandbox=sandbox, iter_dir=iter_dir,
                params=params, vectors=vectors,
                tool_timeout=tool_timeout,
            )
            tool_call_count += len(results)

            # score each candidate
            for r in results:
                obj = objective_value(r["metrics"], targets)
                succ, vrr = compute_sr_vrr(r["metrics"], targets)
                r["objective"] = obj
                r["success"] = succ
                r["vrr"] = vrr
                r["iter"] = outer

                # record to flat history (one entry per candidate)
                history.append({
                    "iter": outer, "cand": r["idx"], "vector": r["vector"],
                    "metrics": r["metrics"], "objective": obj,
                    "success": succ, "vrr": vrr, "status": r["status"],
                    "elapsed": r["elapsed"], "work_dir": r["work_dir"],
                })

                better = (succ > best["success"]) or (
                    succ == best["success"] and obj < best["objective"])
                if better:
                    best = {
                        "iter": outer, "cand": r["idx"],
                        "vector": r["vector"], "metrics": r["metrics"],
                        "objective": obj, "success": succ, "vrr": vrr,
                        "work_dir": r["work_dir"],
                    }

            # iteration summary
            iter_succ = max((r["success"] for r in results), default=0)
            iter_vrr = max((r["vrr"] for r in results), default=0.0)
            print(f"\n  iter {outer} summary: SR={iter_succ}, "
                  f"max_VRR={iter_vrr:.4f}, "
                  f"best_obj_so_far={best['objective']:.4f}, "
                  f"best_success={best['success']}",
                  flush=True)

            # persist incremental history each iteration
            write_json(result_dir, "optim_history.json", history)

            # early stop if we hit success
            if best["success"] == 1:
                print("\n  early stop: targets met by best candidate", flush=True)
                break

        # --- copy best candidate's outputs into the main sandbox ------------
        if best["work_dir"] and os.path.isdir(best["work_dir"]):
            _print_section("Copying best candidate's outputs into sandbox")
            print(f"  best: iter={best['iter']} cand={best['cand']} "
                  f"obj={best['objective']:.4f} succ={best['success']} "
                  f"vrr={best['vrr']:.4f}", flush=True)
            copy_outputs_back(best["work_dir"], sandbox)
        else:
            print("WARNING: no successful work_dir to copy back.", flush=True)

        return _finalize(model_name, tool_call_count, recursion_limit_reached,
                         best, result_dir, history, params=params)

    except KeyboardInterrupt:
        print("\nInterrupted by user.", flush=True)
        return _finalize(model_name, tool_call_count, recursion_limit_reached,
                         best, result_dir, history, params=[])

    except Exception as e:
        print("\nAGENT RUN FAILED DUE TO ERROR:", flush=True)
        print(str(e), flush=True)
        traceback.print_exc()
        return _finalize(model_name, tool_call_count, recursion_limit_reached,
                         best, result_dir, history, params=[])


def _finalize(model_name: str, tool_call_count: int,
              recursion_limit_reached: bool,
              best: Dict[str, Any], result_dir: str,
              history: List[Dict[str, Any]],
              params: List[Dict[str, Any]]):
    write_json(result_dir, "optim_history.json", history)
    write_json(result_dir, "orfs_summary.json", {
        "best": {k: v for k, v in best.items() if k != "work_dir"},
        "n_iters_run": len({h["iter"] for h in history}) if history else 0,
        "n_candidates_run": len(history),
        "successes_total": sum(1 for h in history if h.get("success") == 1),
        "tunable_params": params,
    })
    cost_data = cost_tracker.save_cost_json(model_name)

    print("\n" + "=" * 80, flush=True)
    status = "RECURSION LIMIT REACHED" if recursion_limit_reached else "COMPLETE"
    print(f"AGENT RUN {status}. TOTAL TOOL CALLS: {tool_call_count}", flush=True)
    print(
        f"TOKEN USAGE: input={cost_data['input_tokens']}, "
        f"output={cost_data['output_tokens']}, total={cost_data['total_tokens']}",
        flush=True,
    )
    print(
        f"ESTIMATED COST: ${cost_data['total_cost_usd']:.6f} "
        f"(input=${cost_data['input_cost_usd']:.6f}, "
        f"output=${cost_data['output_cost_usd']:.6f})",
        flush=True,
    )
    print("=" * 80 + "\n", flush=True)


if __name__ == "__main__":
    main()
