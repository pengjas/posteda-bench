"""Reflexion-style DRC error-fixing agent (vLLM-served open model).

Wraps the React primitives from agents/drc/react/gemma_react.py with a
verbal-reinforcement trial loop (Shinn et al., arXiv:2303.11366):

  trial_1 = ReAct(iters_per_attempt)              # actor only
  if not all_drc_cleared:
      reflection_1 = SelfReflect(trajectory_1)    # tool-less LLM call
      trial_2 = ReAct(iters_per_attempt, system_prompt += reflection_1)

The reflection LLM call does NOT consume any iterations, only token cost.
Sandbox state (modified.gds, report.lyrdb, etc.) is snapshotted before
trial 1 and restored before each subsequent trial so reflection-guided
retries start from the same seed as the first trial — the only thing
carried across trials is the reflection text itself.

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
    query_with_pts,
    change_shape,
    add_shape,
    move_cell,
    query_drc_report,
)
# Shared throughput-normalized $/token tracker for self-hosted vLLM models.
# Same module the React vLLM agent imports.
from agents.ppa.react import vllm_cost_tracker as cost_tracker
from agents.drc.reflexion.reflexion_core import (
    run_reflexion,
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


if __name__ == "__main__":
    separator = "=" * 80
    print(f"\n\n\n{separator}")
    print("DRC Reflexion Agent Run - New Conversation Initialized")
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{separator}\n")

    parser = argparse.ArgumentParser(description="run the DRC reflexion agent")
    parser.add_argument("--drc_rpt_path", type=str, required=True,
                        help="Initial DRC error report path")
    parser.add_argument("--iterations", type=int, default=8,
                        help="Total iteration budget across ALL reflexion trials")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="LLM temperature setting")
    parser.add_argument("--num_attempts", type=int, default=2,
                        help="Number of reflexion trials. iters_per_attempt = "
                             "iterations // num_attempts. Reflection step itself "
                             "does not consume iterations.")
    args = parser.parse_args()

    with open(args.drc_rpt_path, "r") as f:
        drc_error = f.read()

    # Endpoint / model overridable via VLLM_BASE_URL / VLLM_MODEL — same convention
    # as gemma_react.py so the CLI surface is identical.
    actor_llm = ChatOpenAI(
        model=os.environ.get("VLLM_MODEL", "google/gemma-4-31B-it"),
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://localhost:9888/v1"),
        openai_api_key="vllm_fake_key",
        temperature=args.temperature,
        timeout=600.0,
        max_retries=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        model_kwargs={"tool_choice": "auto"},
    )

    # Tool-less LLM dedicated to the Self-Reflection step. vLLM rejects any
    # request that sets `tool_choice` without also providing a `tools` array
    # ("When using `tool_choice`, `tools` must be set."), so the actor's llm
    # — which bakes in tool_choice="auto" via model_kwargs — cannot be reused
    # for the tool-less reflection call. We construct a separate ChatOpenAI
    # against the same vLLM endpoint, identical in every other way, but
    # without the tool_choice model_kwarg.
    reflection_llm = ChatOpenAI(
        model=os.environ.get("VLLM_MODEL", "google/gemma-4-31B-it"),
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://localhost:9888/v1"),
        openai_api_key="vllm_fake_key",
        temperature=args.temperature,
        timeout=600.0,
        max_retries=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )

    tools = [
        query_with_pts,
        change_shape,
        add_shape,
        move_cell,
        query_drc_report,
    ]
    llm_with_tools = actor_llm.bind_tools(tools)

    tool_call_counter["count"] = 0
    cost_tracker.reset()

    status = "COMPLETE"
    total_tool_calls = 0
    try:
        status, total_tool_calls = run_reflexion(
            llm=reflection_llm,
            llm_with_tools=llm_with_tools,
            tools=tools,
            system_prompt_content=SYSTEM_PROMPT,
            task_prompt=drc_error,
            iterations=args.iterations,
            num_attempts=args.num_attempts,
            cost_recorder=cost_tracker.record_langchain_usage,
        )
    except Exception as e:
        print(f"\n{separator}")
        print("AGENT RUN FAILED DUE TO ERROR:")
        print(str(e))
        print(f"{separator}\n", flush=True)
        total_tool_calls = tool_call_counter["count"]

    finally:
        model_name = getattr(actor_llm, "model_name", None) or getattr(actor_llm, "model", "unknown")
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
