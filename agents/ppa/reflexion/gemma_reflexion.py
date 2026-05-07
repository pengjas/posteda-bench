"""Reflexion-style PPA optimization agent (vLLM-served open model).

Wraps the React primitives from agents/ppa/react/gemma_react.py with a
verbal-reinforcement trial loop (Shinn et al., arXiv:2303.11366):

  trial_1 = ReAct(iters_per_attempt)              # actor only
  if not all_targets_met:
      reflection_1 = SelfReflect(trajectory_1)    # tool-less LLM call
      trial_2 = ReAct(iters_per_attempt, system_prompt += reflection_1)

The reflection LLM call does NOT consume any iterations, only token cost.
Sandbox state (config/, src/, info.json, flow outputs) is snapshotted before
trial 1 and restored before each subsequent trial so reflection-guided
retries start from the same seed as the first trial.

CLI surface and end-of-run log line ('AGENT RUN COMPLETE. TOTAL TOOL CALLS: N')
are kept identical to gemma_react.py so eval_ppa_sr.sh can swap agents without
modification, and so analyze_trajectory.py / summarize_trajectory_stats.py
emit Layer-1 metrics with no changes.
"""

import os
import argparse
import datetime

from langchain_openai import ChatOpenAI

from agents.ppa.tools import (
    read_file,
    list_files,
    edit_file,
    write_file,
    run_openroad_flow,
    report_ppa,
)
from agents.ppa.react import vllm_cost_tracker as cost_tracker
from agents.ppa.reflexion.reflexion_core import (
    run_reflexion,
    tool_call_counter,
)


SYSTEM_PROMPT = """
You are an expert digital design engineer diagnosing and fixing PPA (Power, Performance, Area) violations in OpenROAD flow. Your goal is to find ROOT CAUSES and apply targeted fixes, not randomly tune parameters.

**Target Metrics (from task prompt):**
- `target_effective_period`: Clock period (ps)
- `target_power`: Total power (W)
- `target_area`: Die area (µm²)

**Diagnostic Workflow:**

**1. ROOT CAUSE ANALYSIS (Start Here)**
   - Use `report_ppa()` to identify which metrics violate targets
   - Examine logs and reports to understand WHY:
     - `read_file(command="cat logs/6_report.json")` - design statistics
     - `read_file(command="tail -100 logs/synth/6_1_report.log")` - synthesis details
     - `read_file(command="grep -i 'error\\|warning\\|critical' logs/...")` - issues
   - Analyze intermediate results to find bottlenecks
   - Form hypothesis about the root cause before making changes

**2. IDENTIFY SOLUTION**
   Multiple fix strategies available:
   - **Config parameters** (FlowVariables.md): Flow settings, density, buffers, etc.
   - **Timing constraints** (config/*.sdc): Clock period, I/O delays, false paths
   - **Design files** (src/*.v): RTL optimizations if design issues found
   - **Other configs**: Any file affecting the design flow

**3. VERIFY SOLUTION VIABILITY**
   - If changing config parameters: `read_file(command="grep -A 10 'PARAM_NAME' FlowVariables.md")`
   - Understand: What does it affect? Valid range? Side effects?
   - Confirm it addresses your diagnosed root cause

**4. APPLY FIX**
   - `edit_file(file_path="...", old_content="...", new_content="...")`
   - Make targeted changes based on root cause analysis

**5. VERIFY FIX**
   - `run_openroad_flow(target="finish")`
   - `report_ppa()` to check if violation is resolved
   - If unsuccessful, analyze why and try different approach

**CRITICAL RULES:**
- DO NOT blindly tune parameters hoping something works
- DO NOT skip root cause analysis
- DO investigate logs/reports before making changes
- DO verify parameter meanings if modifying FlowVariables
- DO consider all fix options (not just config parameters)

**Success:** actual_period <= target_effective_period AND actual_power <= target_power AND actual_area <= target_area

Think like a debug engineer: diagnose, hypothesize, fix, verify. Call tools one at a time.
"""


if __name__ == "__main__":
    separator = "=" * 80
    print(f"\n\n\n{separator}")
    print("PPA Reflexion Agent Run - New Conversation Initialized")
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{separator}\n")

    parser = argparse.ArgumentParser(description="run the PPA reflexion agent")
    parser.add_argument("--prompt_path", type=str, required=True,
                        help="Initial prompt/task description path")
    parser.add_argument("--iterations", type=int, default=16,
                        help="Total iteration budget across ALL reflexion trials")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="LLM temperature setting")
    parser.add_argument("--num_attempts", type=int, default=2,
                        help="Number of reflexion trials. iters_per_attempt = "
                             "iterations // num_attempts. Reflection step itself "
                             "does not consume iterations.")
    args = parser.parse_args()

    with open(args.prompt_path, "r") as f:
        task_prompt = f.read()

    # vLLM endpoint / model are overridable via env vars (VLLM_BASE_URL,
    # VLLM_MODEL) so the CLI surface stays identical to gemma_react.py.
    actor_llm = ChatOpenAI(
        model=os.environ.get("VLLM_MODEL", "Qwen/Qwen3.5-27B"),
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://134.84.150.139:8027/v1"),
        openai_api_key="vllm_fake_key",
        temperature=args.temperature,
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
        model=os.environ.get("VLLM_MODEL", "Qwen/Qwen3.5-27B"),
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://134.84.150.139:8027/v1"),
        openai_api_key="vllm_fake_key",
        temperature=args.temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )

    tools = [
        read_file,
        list_files,
        edit_file,
        write_file,
        run_openroad_flow,
        report_ppa,
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
            task_prompt=task_prompt,
            iterations=args.iterations,
            num_attempts=args.num_attempts,
            cost_tracker_module=cost_tracker,
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
