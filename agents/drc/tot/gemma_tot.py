"""Tree-of-Thoughts DRC error-fixing agent (vLLM-served open model).

Wraps the same tool surface as gemma_react.py with a ToT search
(Yao et al., arXiv:2305.10601):

  expand(node) -> sample K candidate next actions in parallel
              -> LLM judge scores each candidate 1..10
              -> push (parent, candidate, score) into a global priority queue
  step()      -> pop highest-scored (parent, candidate)
              -> if parent != current sandbox state, restore parent's snapshot
              -> execute the candidate's tool calls (counts against the budget)
              -> snapshot the resulting state as a new tree node
              -> expand the new node

Backtracking falls out for free: when the current path stalls, the next pop
naturally goes to the highest-scored un-executed candidate of any ancestor.

Iteration semantics:
  --iterations is the TOTAL tool-call budget across the whole tree (matches
  ReAct's 5 runs * 16 iters = 80 total when invoked with --iterations 80).
  --max_depth caps the depth of any single path (default 16, matching ReAct's
  per-run iteration cap). The K candidate sampling + 1 scoring LLM call per
  expansion are billed in tokens but do NOT consume the iteration budget;
  only tool calls do.

CLI surface and end-of-run log line ('AGENT RUN COMPLETE. TOTAL TOOL CALLS: N')
are kept identical to gemma_react.py so eval_drc_sr.sh can swap agents without
modification, and so the eval-script's per-tool grep / token / cost / failure-
mode aggregations all still emit Layer-1 metrics with no changes.
"""

import os
import argparse
import datetime

from langchain_openai import ChatOpenAI

from agents.drc.tools import (
    query_drc_report,
    query_with_pts,
    move_cell,
    change_shape,
    add_shape,
)
# Shared throughput-normalized $/token tracker for self-hosted vLLM models.
# Same module the React vLLM agent imports.
from agents.ppa.react import vllm_cost_tracker as cost_tracker
from agents.drc.tot.tot_core import (
    run_tot,
    tool_call_counter,
)


SYSTEM_PROMPT = """
You are an expert AI assistant specializing in fixing Design Rule Checking (DRC) errors in GDS layout files. Your goal is to function as an autonomous agent that iteratively analyzes DRC reports, understands the geometric context of the layout, applies targeted fixes, and **verifies the outcome** until the error is resolved.

**Your Core Workflow (The Fix-and-Verify Loop):**

Your entire process should follow this mandatory iterative loop:

1.  **Analyze the Goal:** The user will provide you with an initial DRC error report. Carefully read the error type, the message, the layers involved, and the exact coordinates. This is your starting problem.

2.  **Observe the Environment:**
    * Your first action in each loop must be to use the `query_with_pts` tool to understand the layout around the current error.
    * **Choose the observation levels (`"first"`, `"second"`, `"third"`) based on the error's complexity.** For simple errors, `level=["first"]` might be sufficient. A good general starting point is `level=["first", "second"]`. If the surrounding area is dense or the fix might have wide-ranging effects, add `"third"` to get the full picture.

3.  **Reason and Plan:**
    * Based on the output from `query_with_pts`, analyze the spatial relationships and formulate a precise hypothesis for a fix.
    * Some fixes might require a single action, while others may need a sequence of modifications. Plan all the necessary steps. For example: "To fix this, I first need to move cell instance X, and then reshape a polygon in cell instance Y."

4.  **Act (Execute the Fix):**
    * Call the chosen modification tool(s) (`move_cell`, `change_shape`, `add_shape`) with the correct parameters to execute your plan.
    * **CRITICAL RULE:** When using `move_cell` or `change_shape`, you **MUST** use the exact same `cell_name`, `type`, `direct_layers`, `context_layers`, and `query_pts` from the `query_with_pts` call you are basing your action on. This is non-negotiable and ensures you modify the correct object in the correct context. So this means whenever you call `move_cell` or `change_shape`, you must guarantee that the gds file has not changed since the last `query_with_pts` call. Or else you must re-query with `query_with_pts` to get the latest context before making a change.

5.  **Verify the Result:**
    * **Once you have completed a single action or a sequence of actions that you believe constitutes a complete fix,** call the `query_drc_report` tool.
    * You must decide the appropriate moment for verification. There is no need to run it after every single modification if a fix requires multiple steps.
    * Pass the path of the newly modified GDS file to this tool to check if your fix worked and if you introduced any new errors.

6.  **Re-evaluate and Repeat:**
    * Analyze the new DRC report from the previous step.
    * **If the original error is gone and no new critical errors have appeared,** your job is done. Announce the successful fix.
    * **If the error persists or a new error has been introduced,** you must restart the loop from **Step 2: Observe the Environment**, using the information from the new DRC report to inform your next attempt.

**Tool Usage Guidelines:**

* **`query_with_pts`**: Your "eyes". Use it at the beginning of every iteration to gather fresh information. Be strategic about which levels you query.
* **`move_cell` / `change_shape` / `add_shape`**: Your "hands". Use these to execute a single, precise change or a sequence of changes.
* **`query_drc_report`**: Your "verifier". Use this tool after you have applied a complete set of changes to validate the result and guide your next loop.

Think step-by-step and be methodical. Your objective is to resolve the DRC error by intelligently iterating through the **Observe -> Act -> Verify** cycle.
"""


def _make_actor_llm(temperature):
    """Tool-bound Actor LLM. Same extra_body / tool_choice settings as
    gemma_react.py so behavior matches."""
    return ChatOpenAI(
        model=os.environ.get("VLLM_MODEL", "google/gemma-4-31B-it"),
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://localhost:9888/v1"),
        openai_api_key="vllm_fake_key",
        temperature=temperature,
        timeout=600.0,
        max_retries=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        model_kwargs={"tool_choice": "auto"},
    )


def _make_judge_llm(temperature):
    """Tool-less judge LLM for the candidate scorer. vLLM rejects any
    request that sets `tool_choice` without also providing a `tools` array
    ("When using `tool_choice`, `tools` must be set."), so the actor's
    handle — which bakes in tool_choice="auto" via model_kwargs — cannot
    be reused for the tool-less judge call. We construct a separate
    ChatOpenAI against the same vLLM endpoint, identical in every other
    way, but without the tool_choice model_kwarg."""
    return ChatOpenAI(
        model=os.environ.get("VLLM_MODEL", "google/gemma-4-31B-it"),
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://localhost:9888/v1"),
        openai_api_key="vllm_fake_key",
        temperature=temperature,
        timeout=600.0,
        max_retries=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )


if __name__ == "__main__":
    separator = "=" * 80
    print(f"\n\n\n{separator}")
    print("DRC Tree-of-Thoughts Agent Run - New Conversation Initialized")
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{separator}\n")

    parser = argparse.ArgumentParser(description="run the DRC Tree-of-Thoughts agent")
    parser.add_argument("--drc_rpt_path", type=str, required=True,
                        help="Initial DRC error report path")
    parser.add_argument("--iterations", type=int, default=80,
                        help="TOTAL tool-call budget across the whole tree "
                             "(default 80 = ReAct's 5 runs * 16 iters).")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="Temperature for the judge / non-sampling LLM calls.")
    parser.add_argument("--parallel_node", type=int, default=5,
                        help="K: candidates sampled at each tree expansion.")
    parser.add_argument("--max_depth", type=int, default=16,
                        help="Max depth for any single path through the tree.")
    parser.add_argument("--sample_temperature", type=float, default=0.9,
                        help="Temperature used ONLY when sampling the K candidates "
                             "(forced above 0 so candidates actually differ when "
                             "the eval harness passes --temperature 0).")
    args = parser.parse_args()

    with open(args.drc_rpt_path, "r") as f:
        drc_error = f.read()

    # judge_llm uses --temperature so the candidate scorer matches the
    # eval-harness's intended determinism. It is tool-less (see
    # _make_judge_llm docstring for why).
    judge_llm = _make_judge_llm(args.temperature)
    # sampler_llm forces a non-zero temperature so the K parallel samples in
    # each expansion actually differ even when the eval passes --temperature 0.
    sample_temp = max(args.sample_temperature, args.temperature, 0.7)
    sampler_llm = _make_actor_llm(sample_temp)

    tools = [query_with_pts, change_shape, add_shape, move_cell, query_drc_report]
    sampler_llm_with_tools = sampler_llm.bind_tools(tools)

    tool_call_counter["count"] = 0
    cost_tracker.reset()

    status = "COMPLETE"
    total_tool_calls = 0
    try:
        status, total_tool_calls = run_tot(
            judge_llm=judge_llm,
            sampler_llm_with_tools=sampler_llm_with_tools,
            tools=tools,
            system_prompt_content=SYSTEM_PROMPT,
            task_prompt=drc_error,
            iterations=args.iterations,
            parallel_node=args.parallel_node,
            max_depth=args.max_depth,
            cost_tracker_module=cost_tracker,
        )
    except Exception as e:
        print(f"\n{separator}")
        print("AGENT RUN FAILED DUE TO ERROR:")
        print(str(e))
        print(f"{separator}\n", flush=True)
        total_tool_calls = tool_call_counter["count"]

    finally:
        model_name = (
            getattr(judge_llm, "model_name", None)
            or getattr(judge_llm, "model", "unknown")
        )
        cost_data = cost_tracker.save_cost_json(model_name)
        print(f"\n{separator}")
        print(f"AGENT RUN {status}. TOTAL TOOL CALLS: {total_tool_calls}")
        print(
            f"TOKEN USAGE: input={cost_data['input_tokens']}, "
            f"output={cost_data['output_tokens']}, total={cost_data['total_tokens']}"
        )
        print(
            f"ESTIMATED COST: ${cost_data['total_cost_usd']:.6f} "
            f"(input=${cost_data['input_cost_usd']:.6f}, output=${cost_data['output_cost_usd']:.6f})"
        )
        print(f"{separator}\n", flush=True)
