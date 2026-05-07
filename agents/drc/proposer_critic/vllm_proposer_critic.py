"""
DRC (Design Rule Checking) Error Fixing Agent using a Planner-Checker workflow,
served by a local vLLM instance (OpenAI-compatible /v1 endpoint).

Workflow:
  planner -> checker -> call_tools -> planner (loop)

- Planner: has tools bound, proposes an initial action (tool + args) as a structured proposal
- Checker: reviews the proposal, corrects if needed, then executes the actual tool call
- Tool results feed back to the planner for the next iteration

The planner's proposed tool_calls are NOT executed directly — they are formatted into a
text proposal passed to the checker as a HumanMessage, avoiding unresolved tool_call_id issues.

CLI surface and log format are kept aligned with gemma_react.py (the React-style DRC
agent) so benchmark/utils/eval_drc_sr.sh can swap agents without modification:
  - Accepts --drc_rpt_path, --temperature, --iterations
  - Final stdout line matches: "AGENT RUN (COMPLETE|RECURSION LIMIT REACHED). TOTAL TOOL CALLS: <N>"
  - Tool-call invocation lines match: "--- TOOL CALL: Invoking tool '<name>' with args: ..."
  - Tool error markers match: "ERROR: TIMEOUT", "ERROR: TOOL FAILED"
  - Writes cost.json into $AGENT_RESULT_DIR via vllm_cost_tracker
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

# Import tools from the DRC tools package
from agents.drc.tools import (
    query_drc_report,
    query_with_pts,
    move_cell,
    change_shape,
    add_shape,
)

from agents.ppa.react import vllm_cost_tracker as cost_tracker

# Global counter to track tool calls (persists even when exceptions occur)
tool_call_counter = {"count": 0}


# --- State Definition ---
class AgentState(TypedDict):
    """Shared state in LangGraph workflow"""
    messages: Annotated[list, add_messages]
    drc_errors: Annotated[list, add_messages]
    output_gds: str
    query_change_args: dict
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

    vLLM's reasoning parsers extract the model's <think>...</think> block into a
    separate `reasoning_content` field. langchain-openai surfaces that as either
    `additional_kwargs.reasoning_content` or `response_metadata.reasoning_content`
    (depending on version), but it does NOT merge it back into AIMessage.content
    — so the agent log shows content='' and the next turn's context loses the
    reasoning entirely. This helper copies whichever sidecar field is populated
    into `content` (joined with the original content if any), so logs are
    readable and the reasoning is carried forward as conversation context.
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

    Backoff schedule: 10s, 30s, 60s, 90s, 120s (~5min total) — covers a full
    vLLM re-init on a big model. Only APIConnectionError is retried; other
    errors propagate immediately.
    """
    backoff_schedule = [10, 30, 60, 90, 120]
    max_conn_retries = len(backoff_schedule) + 1
    for conn_attempt in range(max_conn_retries):
        try:
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


def call_planner(state: AgentState):
    """Planner step. Logging mirrors gemma_react.py's call_llm: if the previous
    step was a tool call, echo each ToolMessage in the `[name]: content` shape
    that downstream log-parsers expect; otherwise just print the latest message.
    """
    print("\n--- CALL LLM: call_planner ---")
    messages = state["messages"]

    # Find the most recent AIMessage to count how many tool calls were made
    recent_tool_count = 0
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            recent_tool_count = len(msg.tool_calls)
            break

    if recent_tool_count > 0:
        recent_tool_msgs = []
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
                recent_tool_msgs.insert(0, msg)
                if len(recent_tool_msgs) >= recent_tool_count:
                    break
            elif isinstance(msg, AIMessage):
                break

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
        query_change_args = None
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

            p = multiprocessing.Process(
                target=self._run_tool,
                args=(tool, tool_args, return_dict),
            )
            p.start()
            print(f"--- Waiting for '{tool_name}' (max 1200s)... ---", flush=True)
            p.join(timeout=1200)

            if p.is_alive():
                print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!", flush=True)
                print(f"Tool '{tool_name}' exceeded 1200 seconds and will be terminated.", flush=True)
                p.terminate()
                p.join()
                tool_result_content = (
                    f"Error: Tool '{tool_name}' timed out after 1200 seconds and was terminated."
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

            # Same query/change alignment check as the React-style DRC agent
            if tool_name == "change_size":
                query_change_args = inputs.get("query_change_args", {})
                for key, value in query_change_args.items():
                    if value != tool_args.get(key):
                        return {
                            "messages": ToolMessage(
                                content=(
                                    "The change you want to make is not aligned with the last query. "
                                    "Please make sure the changed entity is the one that was queried in the last step."
                                ),
                                name="error",
                            )
                        }
            elif tool_name == "query_with_pts":
                query_change_args = tool_args

        agent_state["messages"] = outputs
        if query_change_args:
            agent_state["query_change_args"] = query_change_args

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
        "drc_errors": [prompt],
        "tool_call_count": 0,
        "planner_proposal": "",
    }
    # Each iteration = planner + checker + tools = 3 node transitions
    result = graph.invoke(initial_state, {"recursion_limit": args.iterations * 3 + 1})
    return result


if __name__ == "__main__":
    separator = "=" * 80
    print(f"\n\n\n{separator}")
    print("DRC Planner-Checker Agent Run (vLLM) - New Conversation Initialized")
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{separator}\n")

    parser = argparse.ArgumentParser(description="run the DRC fixing planner-checker agent with vLLM")
    parser.add_argument("--drc_rpt_path", type=str, help="Initial DRC error report path", required=True)
    parser.add_argument("--iterations", type=int, default=8, help="Maximum number of iterations for the agent")
    parser.add_argument("--temperature", type=float, default=0.7, help="LLM temperature setting")
    args = parser.parse_args()

    with open(args.drc_rpt_path, "r") as f:
        drc_error = f.read()

    # ============================================================================
    # LLM: served by a local vLLM instance.
    # Endpoint / model overridable via env vars (VLLM_BASE_URL, VLLM_MODEL) so the
    # CLI surface stays identical to the React-style gemma_react.py.
    # ============================================================================
    llm = ChatOpenAI(
        model="qwen3.5-9b",
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://localhost:9888/v1"),
        openai_api_key="vllm_fake_key",
        temperature=args.temperature,
        timeout=600.0,
        max_retries=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        model_kwargs={"tool_choice": "auto"},
    )

    tools = [query_with_pts, change_shape, add_shape, move_cell, query_drc_report]
    # Both planner and checker have tools bound; only checker's tool_calls are executed
    llm_planner_with_tools = llm.bind_tools(tools)
    llm_checker_with_tools = llm.bind_tools(tools)

    planner_system_prompt = """You are the PLANNER agent for fixing Design Rule Checking (DRC) errors in GDS layout files. Your job is to propose EXACTLY ONE tool call per turn. The Checker reviews and executes it; tool results return on the next turn.

**OUTPUT FORMAT (REQUIRED — do not skip the reasoning):**

Every turn you MUST emit BOTH:

1. A visible reasoning block (plain text in the message content) that walks through:
   - **Observation:** what the latest tool result / DRC report tells you (cite specific coordinates, layers, cell names from the history — do not paraphrase vaguely).
   - **Diagnosis:** your current hypothesis about the root cause of the violation.
   - **Plan:** the next 1-3 steps you intend to take, and why this specific tool call is the right next step.
   - **Argument justification:** for `move_cell` / `change_shape`, name the exact prior `query_with_pts` result your arguments come from; for `query_with_pts`, say why these levels and points; for `query_drc_report`, state which fix you are verifying.

2. The single proposed tool call itself.

A tool call with no reasoning is INVALID. The Checker uses your reasoning to decide whether to approve or correct the call, so terse "calling X" output is not enough — show the chain of thought that leads to the call. If you produce only a tool call without the reasoning text, treat that as a bug in your own output.

**Your Core Workflow (The Fix-and-Verify Loop):**

1. **Analyze the Goal:** The user provides a DRC error report. Read the error type, message, layers, and exact coordinates.

2. **Observe the Environment:**
    * Propose `query_with_pts` to understand the layout around the current error.
    * Choose the observation levels (`"first"`, `"second"`, `"third"`) based on the error's complexity. For simple errors, `level=["first"]` may suffice. A good general start is `level=["first", "second"]`. If the area is dense or the fix may have wide-ranging effects, add `"third"`.

3. **Reason and Plan:** Based on the `query_with_pts` output, analyze spatial relationships and formulate a precise hypothesis for a fix. Some fixes need a single action; others need a sequence of modifications — plan all the necessary steps. This reasoning MUST appear in your message content, not be kept implicit.

4. **Act (Execute the Fix):**
    * Propose the chosen modification tool (`move_cell`, `change_shape`, `add_shape`).
    * **CRITICAL RULE:** When using `move_cell` or `change_shape`, the `cell_name`, `type`, `direct_layers`, `context_layers`, and `query_pts` MUST come exactly from the most recent `query_with_pts` call you are basing the action on. This is non-negotiable. If the GDS may have changed since that query, propose a fresh `query_with_pts` first to refresh the context.

5. **Verify the Result:**
    * Once the fix (single action or sequence) is complete, propose `query_drc_report` against the modified GDS to check whether the original error is gone and no new critical errors were introduced.

6. **Re-evaluate and Repeat:**
    * If the original error is resolved and no new critical errors appeared, output the reasoning summary plus text only (no tool call): "TASK COMPLETE: [summary]".
    * Otherwise, restart from step 2 using the new DRC report to inform the next attempt.

**Tool Usage:**
* `query_with_pts`: Your "eyes" — gather geometric context at the start of each iteration.
* `move_cell` / `change_shape` / `add_shape`: Your "hands" — execute one precise change.
* `query_drc_report`: Your "verifier" — validate the result after a complete fix.

ONE tool call per turn, preceded by the reasoning block above — the Checker executes the call.
"""
    planner_system_message = SystemMessage(content=planner_system_prompt)

    checker_system_prompt = """You are the CHECKER agent. The Planner has just proposed ONE tool call (in the final HumanMessage of the conversation). You see the same task and history. Your job is to either approve the proposal by calling the tool exactly as proposed, or correct it by calling the tool with fixed arguments. You MUST emit a tool call unless the task is genuinely complete.

**Verification before executing:**

1. **Right tool, right step** — does the proposed action fit the current point in the Observe -> Act -> Verify loop? If no `query_with_pts` has been done for the current GDS state, replace the proposal with a `query_with_pts`. After a fix, the next call should be `query_drc_report`.
2. **Right context (CRITICAL)** — for `move_cell` or `change_shape`, the `cell_name`, `type`, `direct_layers`, `context_layers`, and `query_pts` MUST come exactly from the most recent `query_with_pts` result in the conversation history. If any value is hallucinated, or if the GDS has been modified since that query, replace the proposal with a fresh `query_with_pts`.
3. **Right arguments** — coordinates, cell names, and layer names must come from real query results, not be invented.
4. **No repetition** — if the exact (tool, args) was already tried in history without progress, reject and pick a different lever.

**Actions:**
- APPROVE: call the tool with the Planner's exact arguments.
- CORRECT: state the issue in one short sentence, then call the tool with fixed arguments.
- COMPLETE (no tool call): only when the Planner said "TASK COMPLETE" AND the latest `query_drc_report` in history confirms the original error is resolved with no new critical errors. Otherwise you MUST emit a tool call.

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
        final_state = run_agent(drc_error)
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
