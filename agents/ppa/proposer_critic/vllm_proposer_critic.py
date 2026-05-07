"""
PPA Optimization Agent using Planner-Checker workflow with google/gemma-4-31B-it
served locally by vLLM (OpenAI-compatible /v1 endpoint).

Workflow:
  planner -> checker -> call_tools -> planner (loop)

- Planner: has tools bound, proposes an initial action (tool + args) as a structured proposal
- Checker: reviews the proposal, corrects if needed, then executes the actual tool call
- Tool results feed back to the planner for the next iteration

The planner's proposed tool_calls are NOT executed directly — they are formatted into a
text proposal passed to the checker as a HumanMessage, avoiding unresolved tool_call_id issues.

CLI surface and log format are kept aligned with google_planner_checker.py so
benchmark/utils/eval_ppa_sr.sh can swap agents without modification:
  - Accepts --prompt_path, --temperature, --iterations
  - Final stdout line matches: "AGENT RUN (COMPLETE|RECURSION LIMIT REACHED). TOTAL TOOL CALLS: <N>"
  - Writes cost.json into $AGENT_RESULT_DIR via vllm_cost_tracker
  - Honors TOOL_TIMEOUT env var for the OpenROAD flow tool
"""

import json
import os
import traceback
from typing import Annotated, TypedDict
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
import argparse
import datetime
import time
import openai
from langgraph.errors import GraphRecursionError
import multiprocessing
from typing_extensions import NotRequired

# Import tools from the PPA tools package
from agents.ppa.tools import (
    read_file,
    list_files,
    edit_file,
    write_file,
    run_openroad_flow,
    report_ppa,
)

from agents.ppa.react import vllm_cost_tracker as cost_tracker

# Global counter to track tool calls (persists even when exceptions occur)
tool_call_counter = {"count": 0}


# --- State Definition ---
class AgentState(TypedDict):
    """Shared state in LangGraph workflow"""
    messages: Annotated[list, add_messages]
    ppa_violations: Annotated[list, add_messages]
    tool_call_count: NotRequired[int]
    planner_proposal: str  # Planner's latest proposal text (not stored in messages)


def format_planner_proposal(response: AIMessage) -> str:
    """Convert planner's AIMessage (with or without tool_calls) into a readable text proposal."""
    parts = []
    if response.content:
        parts.append(str(response.content))
    if response.tool_calls:
        parts.append("\nPROPOSED ACTION:")
        for tc in response.tool_calls:
            parts.append(f"  Tool: `{tc['name']}`")
            parts.append(f"  Arguments:\n{json.dumps(tc['args'], indent=4)}")
    return "\n".join(parts)


def _absorb_reasoning(response):
    """Fold vLLM `reasoning_content` back into AIMessage.content.

    vLLM with `--reasoning-parser gemma4` extracts the model's <think>...</think>
    block into a separate `reasoning_content` field. langchain-openai surfaces
    that as either `additional_kwargs.reasoning_content` or
    `response_metadata.reasoning_content` (depending on version), but it does
    NOT merge it back into AIMessage.content — so the agent log shows
    content='' and the next turn's context loses the reasoning entirely.
    This helper copies whichever sidecar field is populated into `content`
    (joined with the original content if any), so logs are readable and the
    reasoning is carried forward as conversation context.
    """
    try:
        if getattr(response, "content", None):
            return response
        ak = getattr(response, "additional_kwargs", {}) or {}
        rm = getattr(response, "response_metadata", {}) or {}
        reasoning = (
            ak.get("reasoning_content")
            or ak.get("reasoning")
            or rm.get("reasoning_content")
            or rm.get("reasoning")
            or ""
        )
        if reasoning:
            response.content = reasoning
    except Exception:
        # Never let a logging-only enhancement break the run
        pass
    return response


def _invoke_with_conn_retry(llm, msgs, label):
    """Invoke an LLM with retry on transient vLLM connectivity failures.

    Same backoff schedule as gemma_react.py: 10s, 30s, 60s, 90s, 120s
    (~5min total) — covers a full vLLM re-init on a big model. Only
    APIConnectionError is retried; other errors propagate immediately.
    """
    backoff_schedule = [10, 30, 60, 90, 120]
    max_conn_retries = len(backoff_schedule) + 1
    for conn_attempt in range(max_conn_retries):
        try:
            # response = llm.invoke(msgs, config={"request_timeout": 400.0})
            # return response
            response = llm.invoke(msgs, config={"request_timeout": 400.0})
            return _absorb_reasoning(response)
        except openai.APIConnectionError as conn_err:
            if conn_attempt == max_conn_retries - 1:
                raise
            wait_s = backoff_schedule[conn_attempt]
            print(
                f"!!! vLLM unreachable during {label} "
                f"(attempt {conn_attempt + 1}/{max_conn_retries}), "
                f"retrying in {wait_s}s: {conn_err}",
                flush=True,
            )
            time.sleep(wait_s)


# --- Previous (helper-based) call_planner / call_checker, kept for reference ---
# def _echo_recent_tool_results(messages):
#     """Print the latest ToolMessages in `[name]: content` format."""
#     recent_tool_count = 0
#     for msg in reversed(messages):
#         if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
#             recent_tool_count = len(msg.tool_calls)
#             break
#     if recent_tool_count == 0:
#         return
#     recent_tool_msgs = []
#     for msg in reversed(messages):
#         if isinstance(msg, ToolMessage):
#             recent_tool_msgs.insert(0, msg)
#             if len(recent_tool_msgs) >= recent_tool_count:
#                 break
#         elif isinstance(msg, AIMessage):
#             break
#     if recent_tool_msgs:
#         print(f"--- Received {len(recent_tool_msgs)} tool result(s) ---")
#         for tm in recent_tool_msgs:
#             print(f"  [{tm.name}]: {tm.content}")
#
# def call_planner(state: AgentState):
#     print("\n--- CALL LLM: call_planner ---")
#     msgs = [planner_system_message] + state["messages"]
#     _echo_recent_tool_results(state["messages"])
#     print(msgs[-1])
#     ...
#
# def call_checker(state: AgentState):
#     print("\n--- CALL LLM: call_checker ---")
#     ...
#     msgs = [checker_system_message] + state["messages"] + [proposal_msg]
#     print(proposal_msg)
#     ...
# ----------------------------------------------------------------------


def call_planner(state: AgentState):
    """Planner step. Logging mirrors gemma_react.py's call_llm exactly:
    if the previous step was a tool call, echo each ToolMessage in the
    `  [name]: content` shape that analyze_trajectory.py parses; otherwise
    just print the latest message.
    """
    print("\n--- CALL LLM: call_planner ---")
    messages = state["messages"]

    # Find the most recent AIMessage to count how many tool calls were made
    recent_tool_count = 0
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            recent_tool_count = len(msg.tool_calls)
            break

    # Get only the ToolMessages from the current turn (last N where N = tool_calls count)
    if recent_tool_count > 0:
        recent_tool_msgs = []
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
                recent_tool_msgs.insert(0, msg)
                if len(recent_tool_msgs) >= recent_tool_count:
                    break
            elif isinstance(msg, AIMessage):
                break  # Stop if we hit the AIMessage

        if recent_tool_msgs:
            print(f"--- Received {len(recent_tool_msgs)} tool result(s) ---")
            for tm in recent_tool_msgs:
                print(f"  [{tm.name}]: {tm.content}")
    else:
        print(messages[-1])

    msgs = [planner_system_message] + messages
    try:
        response = _invoke_with_conn_retry(llm_planner_with_tools, msgs, "planner")
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Planner call successful!", flush=True)
        cost_tracker.record_langchain_usage(response)
        print("=================================PLANNER==================================", flush=True)
        print(response, flush=True)
        print("=================================PLANNER==================================", flush=True)
        proposal_text = format_planner_proposal(response)
        return {"planner_proposal": proposal_text}
    except Exception as e:
        print(f"\n\n!!!!!!!!!!!!!!!!!!!!  ERROR: PLANNER FAILED  !!!!!!!!!!!!!!!!!!!!")
        print(f"Error Type: {type(e)}")
        print(f"Error Details: {e}")
        traceback.print_exc()
        raise e


def call_checker(state: AgentState):
    """Checker step. Same retry + cost-tracking pattern as call_planner.
    The checker always sees a synthesized proposal HumanMessage as its last
    input; tool results were already echoed at the start of call_planner
    on the same iteration, so we don't re-echo here.
    """
    print("\n--- CALL LLM: call_checker ---")
    proposal_msg = HumanMessage(content=(
        f"=== PLANNER PROPOSAL ===\n{state['planner_proposal']}\n\n"
        "Review the above proposal. If correct, execute it exactly. "
        "If incorrect or suboptimal, briefly explain the issue and execute the corrected version."
    ))
    msgs = [checker_system_message] + state["messages"] + [proposal_msg]
    print(proposal_msg)
    try:
        response = _invoke_with_conn_retry(llm_checker_with_tools, msgs, "checker")
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Checker call successful!", flush=True)
        cost_tracker.record_langchain_usage(response)
        print("=================================CHECKER==================================", flush=True)
        print(response, flush=True)
        print("=================================CHECKER==================================", flush=True)
        return {"messages": [response]}
    except Exception as e:
        print(f"\n\n!!!!!!!!!!!!!!!!!!!!  ERROR: CHECKER FAILED  !!!!!!!!!!!!!!!!!!!!")
        print(f"Error Type: {type(e)}")
        print(f"Error Details: {e}")
        traceback.print_exc()
        raise e


class BasicToolNode:
    """A node that runs the tools requested in the last AIMessage (with timeout-safe process isolation)."""

    def __init__(self, tools: list) -> None:
        self.tools_by_name = {tool.name: tool for tool in tools}

    def _run_tool(self, tool, args, return_dict):
        try:
            result = tool.invoke(args)
            return_dict["result"] = json.dumps(result)
        except Exception:
            return_dict["error"] = traceback.format_exc()

    def __call__(self, inputs: dict):
        if messages := inputs.get("messages", []):
            message = messages[-1]
        else:
            raise ValueError("No message found in input")

        outputs = []
        agent_state = {}

        for tool_call in message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            print(f"\n--- TOOL CALL: Invoking tool '{tool_name}' with args: {tool_args} ---", flush=True)

            tool_result_content = None
            manager = multiprocessing.Manager()
            return_dict = manager.dict()

            tool = self.tools_by_name.get(tool_name)
            if tool is None:
                print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: UNKNOWN TOOL  !!!!!!!!!!!!!!!!!!!!", flush=True)
                tool_result_content = f"Error: Unknown tool '{tool_name}'."
                outputs.append(
                    ToolMessage(
                        content=tool_result_content,
                        name=tool_name,
                        tool_call_id=tool_call["id"],
                    )
                )
                continue

            tool_timeout = int(os.environ.get("TOOL_TIMEOUT", 1800))
            timeout = (tool_timeout + 60) if tool_name == "run_openroad_flow" else 300

            p = multiprocessing.Process(
                target=self._run_tool,
                args=(tool, tool_args, return_dict),
            )
            p.start()
            print(f"--- Waiting for '{tool_name}' (max {timeout}s)... ---", flush=True)
            p.join(timeout=timeout)

            if p.is_alive():
                print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!", flush=True)
                print(f"Tool '{tool_name}' exceeded {timeout} seconds and will be terminated.", flush=True)
                p.terminate()
                p.join()
                tool_result_content = (
                    f"Error: Tool '{tool_name}' timed out after {timeout} seconds and was terminated."
                )
            else:
                if "error" in return_dict:
                    print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TOOL FAILED  !!!!!!!!!!!!!!!!!!!!", flush=True)
                    print(f"Tool '{tool_name}' failed with error:\n{return_dict['error']}", flush=True)
                    tool_result_content = (
                        f"Error: Tool '{tool_name}' failed. Exception Traceback:\n{return_dict['error']}"
                    )
                else:
                    print(f"--- Tool '{tool_name}' finished successfully. ---", flush=True)
                    tool_result_content = return_dict.get("result", "{}")

            outputs.append(
                ToolMessage(
                    content=tool_result_content,
                    name=tool_name,
                    tool_call_id=tool_call["id"],
                )
            )

        agent_state["messages"] = outputs

        current_count = inputs.get("tool_call_count", 0)
        num_called_this_step = len(message.tool_calls)
        agent_state["tool_call_count"] = current_count + num_called_this_step
        tool_call_counter["count"] = current_count + num_called_this_step

        return agent_state


def should_continue(state: AgentState) -> str:
    """Route after checker: if tool calls present go to tools, else end."""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "continue"
    else:
        return "end_flow"


def run_agent(prompt: str):
    initial_state: AgentState = {
        "messages": [HumanMessage(content=prompt)],
        "ppa_violations": [prompt],
        "tool_call_count": 0,
        "planner_proposal": "",
    }
    # Each iteration = planner + checker + tools = 3 node transitions
    result = graph.invoke(initial_state, {"recursion_limit": args.iterations * 3 + 1})
    return result


if __name__ == "__main__":
    separator = "=" * 80
    print(f"\n\n\n{separator}")
    print("PPA Planner-Checker Agent Run (vLLM google/gemma-4-31B-it) - New Conversation Initialized")
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{separator}\n")

    parser = argparse.ArgumentParser(description="run the PPA optimization planner-checker agent with vLLM google/gemma-4-31B-it")
    parser.add_argument("--prompt_path", type=str, help="Initial prompt/task description path", required=True)
    parser.add_argument("--iterations", type=int, default=16, help="Maximum number of iterations for the agent")
    parser.add_argument("--temperature", type=float, default=0.7, help="LLM temperature setting")
    args = parser.parse_args()

    with open(args.prompt_path, "r") as f:
        task_prompt = f.read()

    # ============================================================================
    # LLM: google/gemma-4-31B-it served by a local vLLM instance on :8006.
    # Endpoint / model are overridable via env vars (VLLM_BASE_URL, VLLM_MODEL)
    # so the CLI surface stays identical to other planner-checker agents.
    # ============================================================================
    # llm = ChatOpenAI(
    #     model=os.environ.get("VLLM_MODEL", "google/gemma-4-31B-it"),
    #     openai_api_base=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8006/v1"),
    #     openai_api_key="vllm_fake_key",
    #     temperature=args.temperature,
    #     timeout=400.0,
    #     extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    #     model_kwargs={"tool_choice": "auto"},
    # )
    llm = ChatOpenAI(
        model="qwen3.5-9b",
        # vLLM is served on a different host (cs-u-ding6000pro). Use its LAN IP
        # so this works from any client host. Override with VLLM_BASE_URL if
        # the endpoint moves.
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://localhost:9888/v1"),
        openai_api_key="vllm_fake_key",
        temperature=args.temperature,
        timeout=600.0,
        max_retries=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        model_kwargs={"tool_choice": "auto"},
    )

    tools = [
        read_file,
        list_files,
        edit_file,
        write_file,
        run_openroad_flow,
        report_ppa,
    ]
    # Both planner and checker have tools bound; only checker's tool_calls are executed
    llm_planner_with_tools = llm.bind_tools(tools)
    llm_checker_with_tools = llm.bind_tools(tools)

    planner_system_prompt = """You are the PLANNER agent for diagnosing and fixing PPA (Power, Performance, Area) violations in OpenROAD flow. Your goal is to find ROOT CAUSES and propose targeted fixes, not randomly tune parameters. Each turn you propose EXACTLY ONE tool call. The Checker reviews and executes it; tool results return on the next turn.

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
   - Form a hypothesis about the root cause before proposing changes

**2. IDENTIFY SOLUTION**
   Multiple fix strategies available:
   - **Config parameters** (FlowVariables.md): Flow settings, density, buffers, etc.
   - **Timing constraints** (config/*.sdc): Clock period, I/O delays, false paths
   - **Design files** (src/*.v): RTL optimizations if design issues found
   - **Other configs**: Any file affecting the design flow

**3. VERIFY SOLUTION VIABILITY**
   - If changing config parameters: propose `read_file(command="grep -A 10 'PARAM_NAME' FlowVariables.md")`
   - Understand: What does it affect? Valid range? Side effects?
   - Confirm it addresses your diagnosed root cause

**4. APPLY FIX**
   - Propose `edit_file(file_path="...", old_content="...", new_content="...")`
   - Make targeted changes based on root cause analysis

**5. VERIFY FIX**
   - Propose `run_openroad_flow(target="finish")`
   - Then `report_ppa()` to check if the violation is resolved
   - If unsuccessful, diagnose again and try a different approach

**CRITICAL RULES:**
- DO NOT blindly tune parameters hoping something works
- DO NOT skip root cause analysis
- DO investigate logs/reports before proposing changes
- DO verify parameter meanings if modifying FlowVariables
- DO consider all fix options (not just config parameters)
- ONE tool call per turn — the Checker executes it
- If all targets are met, output text only (no tool call): "TASK COMPLETE: [summary of changes]"

**Success:** actual_period <= target_effective_period AND actual_power <= target_power AND actual_area <= target_area

Think like a debug engineer: diagnose, hypothesize, fix, verify. Propose tools one at a time.
"""
    planner_system_message = SystemMessage(content=planner_system_prompt)

    checker_system_prompt = """You are the CHECKER agent. The Planner has just proposed ONE tool call (in the final HumanMessage of the conversation). You see the same task and history. Your job is to either approve the proposal by calling the tool exactly as proposed, or correct it by calling the tool with fixed arguments. You MUST emit a tool call unless the task is genuinely complete.

**Target Metrics (from task prompt):**
- `target_effective_period`: Clock period (ps)
- `target_power`: Total power (W)
- `target_area`: Die area (µm²)

**Verification before executing:**

1. **Right tool, right reason** — does the proposed action address the diagnosed PPA violation, or is it speculative tuning? If no root cause was investigated yet (no relevant read_file in history), replace the proposal with a diagnostic read_file.
2. **Right file** — paths exist (config/*.mk, config/*.sdc, src/*.v). For edit_file, old_content must match what was actually shown by a prior read_file in this conversation. If the file's current content isn't already in history, replace the proposal with a read_file first.
3. **Right variable** — parameter names and values are valid. If unsure, replace the proposal with `read_file(command="grep -A 10 'PARAM_NAME' FlowVariables.md")` to confirm.
4. **Right sequence** — after edit_file/write_file the next call should be run_openroad_flow(target="finish"); after a successful run_openroad_flow the next should be report_ppa().
5. **No repetition** — if the exact (tool, args) was already tried in history without improvement, reject and pick a different lever.

**CRITICAL RULES:**
- DO NOT execute blind parameter tuning — replace it with a diagnostic read_file
- DO NOT skip root cause investigation if the history shows none was done
- DO verify parameter meanings against FlowVariables.md before editing
- DO read a file before editing it if its current content isn't already in history

**Actions:**
- APPROVE: call the tool with the Planner's exact arguments.
- CORRECT: state the issue in one short sentence, then call the tool with fixed arguments.
- COMPLETE (no tool call): only when the Planner said "TASK COMPLETE" AND the latest report_ppa() in history confirms actual_period <= target_effective_period AND actual_power <= target_power AND actual_area <= target_area. Otherwise you MUST emit a tool call.

Be terse. One sentence of reasoning at most, then the tool call.
"""
    checker_system_message = SystemMessage(content=checker_system_prompt)

    tool_node = BasicToolNode(tools)
    workflow = StateGraph(AgentState)
    workflow.add_node("call_planner", call_planner)
    workflow.add_node("call_checker", call_checker)
    workflow.add_node("call_tools", tool_node)
    workflow.add_edge("call_planner", "call_checker")
    workflow.add_edge("call_tools", "call_planner")
    workflow.add_conditional_edges(
        "call_checker",
        should_continue,
        {
            "continue": "call_tools",
            "end_flow": END,
        },
    )
    workflow.set_entry_point("call_planner")
    graph = workflow.compile()

    tool_call_counter["count"] = 0
    cost_tracker.reset()
    recursion_limit_reached = False

    try:
        final_state = run_agent(task_prompt)
        final_tool_count = final_state.get("tool_call_count", tool_call_counter["count"])
    except GraphRecursionError as e:
        recursion_limit_reached = True
        print(f"\n{separator}")
        print("AGENT RUN HIT RECURSION LIMIT:")
        print(str(e))
        print(f"{separator}\n", flush=True)
        final_tool_count = tool_call_counter["count"]
    except Exception as e:
        print(f"\n{separator}")
        print("AGENT RUN FAILED DUE TO ERROR:")
        print(str(e))
        print(f"{separator}\n", flush=True)
        final_tool_count = tool_call_counter["count"]
    finally:
        model_name = getattr(llm, "model_name", None) or getattr(llm, "model", "unknown")
        cost_data = cost_tracker.save_cost_json(model_name)
        print(f"\n{separator}")
        status = "RECURSION LIMIT REACHED" if recursion_limit_reached else "COMPLETE"
        print(f"AGENT RUN {status}. TOTAL TOOL CALLS: {final_tool_count}")
        print(
            f"TOKEN USAGE: input={cost_data['input_tokens']}, "
            f"output={cost_data['output_tokens']}, total={cost_data['total_tokens']}"
        )
        print(
            f"ESTIMATED COST: ${cost_data['total_cost_usd']:.6f} "
            f"(input=${cost_data['input_cost_usd']:.6f}, output=${cost_data['output_cost_usd']:.6f})"
        )
        print(f"{separator}\n", flush=True)
