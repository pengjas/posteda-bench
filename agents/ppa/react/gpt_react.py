"""
PPA (Power, Performance, Area) Optimization Agent using LangGraph and Google Gemini.

This agent iteratively:
1. Reads target metrics from info.json (target_effective_period, target_power, target_area)
2. Analyzes current PPA metrics from reports
3. Modifies config files (.mk, .sdc) to optimize for targets
4. Re-runs OpenROAD flow to verify improvements
5. Repeats until targets are met or iterations exhausted
"""

import json
import traceback
import os
from typing import Annotated, TypedDict
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
import vertexai
from langchain.chat_models import init_chat_model
import argparse
import datetime
import time
import google.api_core.exceptions as google_exceptions
from langgraph.errors import GraphRecursionError
import multiprocessing
from google.oauth2 import service_account
from typing_extensions import NotRequired

# Import tools from the PPA tools package
from agents.ppa.tools import (
    # Reading tools
    read_file,
    list_files,
    # Editing tools
    edit_file,
    write_file,
    # Execution tools
    run_openroad_flow,
    # Metrics reporting
    report_ppa,
)

from agents.ppa.react import cost_tracker

# Global counter to track tool calls (persists even when exceptions occur)
tool_call_counter = {"count": 0}


# --- State Definition ---
class AgentState(TypedDict):
    """Shared state in LangGraph workflow"""
    messages: Annotated[list, add_messages]
    ppa_violations: Annotated[list, add_messages]
    tool_call_count: NotRequired[int]


def call_llm(state: AgentState):
    print("\n--- CALL LLM: call_llm ---")
    messages = state["messages"]

    # Find the most recent AIMessage to count how many tool calls were made
    recent_tool_count = 0
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
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
    try:
        response = llm_with_tools.invoke(
            state["messages"],
            config={"request_timeout": 400.0}
        )

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] LLM call successful!", flush=True)
        cost_tracker.record_langchain_usage(response)
        print("=================================RESPONSE==================================", flush=True)
        print(response, flush=True)
        print("=================================RESPONSE==================================", flush=True)

        return {"messages": [response]}

    except google_exceptions.DeadlineExceeded as e:
        print(f"\n\n!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!")
        print(f"LLM call timed out after 400 seconds!")
        print(f"Error Type: {type(e)}")
        print(f"Error Details: {e}")
        traceback.print_exc()
        print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n\n")
        raise e

    except Exception as e:
        print(f"\n\n!!!!!!!!!!!!!!!!!!!!  ERROR: UNKNOWN  !!!!!!!!!!!!!!!!!!!!")
        print(f"Unexpected error during LLM call:")
        print(f"Error Type: {type(e)}")
        print(f"Error Details: {e}")
        traceback.print_exc()
        print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n\n")
        raise e


class BasicToolNode:
    """A node that runs the tools requested in the last AIMessage (with timeout-safe process isolation)."""

    def __init__(self, tools: list) -> None:
        self.tools_by_name = {tool.name: tool for tool in tools}

    def _run_tool(self, tool, args, return_dict):
        """Run tool.invoke in an isolated process and return result or error"""
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

            # For OpenROAD flow execution, use TOOL_TIMEOUT env var (default 1800s)
            # Add 60s buffer so the tool's internal timeout fires first, allowing cleanup
            # before the agent kills the process
            # Other tools use 300s default
            tool_timeout = int(os.environ.get("TOOL_TIMEOUT", 1800))
            timeout = (tool_timeout + 60) if tool_name == "run_openroad_flow" else 300

            # Start isolated process to execute tool.invoke
            p = multiprocessing.Process(
                target=self._run_tool,
                args=(tool, tool_args, return_dict),
            )

            p.start()
            print(f"--- Waiting for '{tool_name}' (max {timeout}s)... ---", flush=True)
            p.join(timeout=timeout)

            if p.is_alive():
                # Timeout - kill the process
                print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!", flush=True)
                print(f"Tool '{tool_name}' exceeded {timeout} seconds and will be terminated.", flush=True)
                p.terminate()
                p.join()
                tool_result_content = (
                    f"Error: Tool '{tool_name}' timed out after {timeout} seconds and was terminated."
                )
            else:
                # Process finished: check result
                if "error" in return_dict:
                    print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TOOL FAILED  !!!!!!!!!!!!!!!!!!!!", flush=True)
                    print(f"Tool '{tool_name}' failed with error:\n{return_dict['error']}", flush=True)
                    tool_result_content = (
                        f"Error: Tool '{tool_name}' failed. Exception Traceback:\n{return_dict['error']}"
                    )
                else:
                    print(f"--- Tool '{tool_name}' finished successfully. ---", flush=True)
                    tool_result_content = return_dict.get("result", "{}")

            # Add result (success or failure) to outputs
            outputs.append(
                ToolMessage(
                    content=tool_result_content,
                    name=tool_name,
                    tool_call_id=tool_call["id"],
                )
            )

        # Build return agent_state
        agent_state["messages"] = outputs

        current_count = inputs.get("tool_call_count", 0)

        # Add the number of tools called in this step
        num_called_this_step = len(message.tool_calls)

        # Update the count in the state to be returned
        agent_state["tool_call_count"] = current_count + num_called_this_step

        # Also update the global counter (persists even when exceptions occur)
        tool_call_counter["count"] = current_count + num_called_this_step

        return agent_state


def should_continue(state: AgentState) -> str:
    """
    Router function: Check if the latest message (usually AIMessage) in AgentState contains tool calls.
    """
    last_message = state["messages"][-1]

    # Check if LLM output contains tool calls
    if last_message.tool_calls:
        return "continue"
    else:
        return "end_flow"


def run_agent(prompt: str):
    initial_state: AgentState = {
        "messages": [
            system_message,
            HumanMessage(content=prompt)
        ],
        "ppa_violations": [prompt],
        "tool_call_count": 0
    }
    # One iteration = call_agent -> call_tools = 2 node transitions
    result = graph.invoke(initial_state, {"recursion_limit": args.iterations * 2 + 1})
    return result


if __name__ == "__main__":
    separator = "=" * 80
    print(f"\n\n\n{separator}")
    print("PPA Optimization Agent Run - New Conversation Initialized")
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{separator}\n")

    parser = argparse.ArgumentParser(description="run the PPA optimization agent")
    parser.add_argument("--prompt_path", type=str, help="Initial prompt/task description path", required=True)
    parser.add_argument("--iterations", type=int, default=16, help="Maximum number of iterations for the agent")
    parser.add_argument("--temperature", type=float, default=0.7, help="LLM temperature setting")
    args = parser.parse_args()

    with open(args.prompt_path, "r") as f:
        task_prompt = f.read()

    # ============================================================================
    # OPTION 1: OpenAI (gpt-5 / gpt-5-mini)
    # ============================================================================
    # Set OPENAI_API_KEY in your environment before running.
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY env var is not set")
    llm = init_chat_model(model="gpt-5", temperature=args.temperature, model_provider="openai", timeout=400.0)
    # llm = init_chat_model(model="gpt-5-mini", temperature=args.temperature, model_provider="openai", timeout=400.0)

    # ============================================================================
    # OPTION 2: Google GenAI with API Key (Gemini models only)
    # ============================================================================
    # api_key = os.environ.get("GOOGLE_API_KEY")
    # llm = init_chat_model(
    #     model="gemini-1.5-flash",  # Only gemini models available
    #     temperature=args.temperature,
    #     model_provider="google_genai",
    #     api_key=api_key,
    #     timeout=400.0
    # )

    # PPA optimization tools
    tools = [
        # Reading tools
        read_file,
        list_files,
        # Editing tools
        edit_file,
        write_file,
        # Execution tools
        run_openroad_flow,
        # Metrics reporting
        report_ppa,
    ]
    llm_with_tools = llm.bind_tools(tools)

    system_prompt_content = """
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
     - `read_file(command="grep -i 'error\|warning\|critical' logs/...")` - issues
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

**Success:** actual_period ≤ target_effective_period AND actual_power ≤ target_power AND actual_area ≤ target_area

Think like a debug engineer: diagnose, hypothesize, fix, verify. Call tools one at a time.
"""
    system_message = SystemMessage(content=system_prompt_content)

    tool_node = BasicToolNode(tools)
    workflow = StateGraph(AgentState)
    workflow.add_node("call_agent", call_llm)
    workflow.add_node("call_tools", tool_node)
    workflow.add_edge("call_tools", "call_agent")
    workflow.add_conditional_edges(
        "call_agent",
        should_continue,
        {
            "continue": "call_tools",
            "end_flow": END
        }
    )
    workflow.set_entry_point("call_agent")
    workflow.set_finish_point("call_agent")
    graph = workflow.compile()

    # Reset the global tool call counter before each run
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
