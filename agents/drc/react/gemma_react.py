"""
DRC (Design Rule Checking) Error Fixing Agent using LangGraph and
google/gemma-4-31B-it served locally by vLLM (OpenAI-compatible /v1 endpoint).

This agent iteratively:
1. Analyzes DRC error reports
2. Queries the layout to understand geometric context
3. Applies targeted fixes (move_cell, change_shape, add_shape)
4. Verifies fixes by running DRC again
5. Repeats until errors are resolved or iterations exhausted

CLI surface, system prompt, tool timeouts, AgentState, and error-handling
logic are kept identical to google_react.py so eval_drc_sr.sh can swap agents
without modification. The only real difference is the model instantiation
(vLLM via langchain_openai.ChatOpenAI) and the cost tracker (throughput-
normalized $/token via vllm_cost_tracker).
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
from langgraph.errors import GraphRecursionError
import multiprocessing
from typing_extensions import NotRequired

# Import tools from the tools package
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


def call_llm(state: AgentState):
    print("\n--- CALL LLM: call_llm ---", flush=True)
    print(state["messages"][-1], flush=True)

    # Retry forever: the vLLM server occasionally accepts a request and never
    # responds. Each attempt has a 10s SDK-level timeout (set on ChatOpenAI),
    # so a wedged call abandons the socket and tries again instead of hanging.
    attempt = 0
    while True:
        attempt += 1
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] --- attempt #{attempt}: WAITING for LLM response (per-attempt timeout=10s, msgs={len(state['messages'])}) ... ---", flush=True)
        _llm_t0 = time.time()
        try:
            response = llm_with_tools.invoke(state["messages"])

            _llm_dt = time.time() - _llm_t0
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] LLM call successful! (took {_llm_dt:.1f}s, attempt #{attempt})", flush=True)
            cost_tracker.record_langchain_usage(response)
            print("=================================RESPONSE==================================", flush=True)
            print(response, flush=True)
            print("=================================RESPONSE==================================", flush=True)

            return {"messages": [response]}

        except Exception as e:
            _llm_dt = time.time() - _llm_t0
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] attempt #{attempt} failed after {_llm_dt:.1f}s: {type(e).__name__}: {e}", flush=True)
            print(f"--- retrying ---", flush=True)
            time.sleep(1)
            continue


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
                continue

            # Start isolated process to execute tool.invoke
            p = multiprocessing.Process(
                target=self._run_tool,
                args=(tool, tool_args, return_dict),
            )

            p.start()
            print(f"--- Waiting for '{tool_name}' (max 1200s)... ---", flush=True)
            p.join(timeout=1200)

            if p.is_alive():
                # Timeout - kill the process
                print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!", flush=True)
                print(f"Tool '{tool_name}' exceeded 1200 seconds and will be terminated.", flush=True)
                p.terminate()
                p.join()
                tool_result_content = (
                    f"Error: Tool '{tool_name}' timed out after 1200 seconds and was terminated."
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

            # Keep original query_change_args check logic
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

        # Build return agent_state
        agent_state["messages"] = outputs
        if query_change_args:
            agent_state["query_change_args"] = query_change_args

        current_count = inputs.get("tool_call_count", 0)

        # Add the number of tools called in this step
        num_called_this_step = len(message.tool_calls)

        # Update the count in the state to be returned
        agent_state["tool_call_count"] = current_count + num_called_this_step

        # Also update the global counter (persists even when exceptions occur)
        tool_call_counter["count"] = current_count + num_called_this_step

        return agent_state


def route_after_tools(state: AgentState):
    print("\n--- ROUTER: route_after_tool ---")

    # Find the latest tool call request (stored in call_agent's AIMessage)
    tool_requests = next(
        (m.tool_calls for m in reversed(state["messages"]) if isinstance(m, AIMessage) and m.tool_calls),
        []
    )
    for tool_call in tool_requests:
        if tool_call["name"] == "change_path_width":
            print("--- ROUTING: Found change_path_width, flow END ---")
            return "end_flow"
    print("--- ROUTING: Found find_target_shape or other tool, returning to call_agent ---")
    return "continue"


def should_continue(state: AgentState) -> str:
    """
    Router function: Check if the latest message (usually AIMessage) in AgentState contains tool calls.
    """
    last_message = state["messages"][-1]

    # Check if LLM output contains tool calls
    if last_message.tool_calls:
        # If there are tool calls, return "continue" to go to call_tools
        return "continue"
    else:
        # If no tool calls, return "end_flow" to end the process
        return "end_flow"


def run_agent(prompt: str):
    initial_state: AgentState = {
        "messages": [
            system_message,
            HumanMessage(content=prompt)
        ],
        "drc_errors": [prompt],
        "tool_call_count": 0
    }
    # One iteration = call_agent -> call_tools = 2 node transitions
    # So recursion_limit needs to be iterations * 2
    result = graph.invoke(initial_state, {"recursion_limit": args.iterations * 2 + 1})
    return result


if __name__ == "__main__":
    separator = "=" * 80
    print(f"\n\n\n{separator}")
    print("LLM Agent Run - New Conversation Initialized")
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{separator}\n")

    parser = argparse.ArgumentParser(description="run the DRC fixing agent")
    parser.add_argument("--drc_rpt_path", type=str, help="Initial DRC error report path", required=True)
    parser.add_argument("--iterations", type=int, default=8, help="Maximum number of iterations for the agent")
    parser.add_argument("--temperature", type=float, default=0.7, help="LLM temperature setting")
    args = parser.parse_args()

    with open(args.drc_rpt_path, "r") as f:
        drc_error = f.read()

    # ============================================================================
    # LLM: google/gemma-4-31B-it served by a local vLLM instance.
    # The server is started with --enable-auto-tool-choice --tool-call-parser gemma4,
    # so structured tool_calls come back in the OpenAI format.
    # Endpoint / model are overridable via env vars (VLLM_BASE_URL, VLLM_MODEL)
    # so the CLI surface stays identical to google_react.py.
    # ============================================================================
    llm = ChatOpenAI(
        model=os.environ.get("VLLM_MODEL", "google/gemma-4-31B-it"),
        # vLLM (Gemma) is served on cs-u-ding6000pro:8888. Use the LAN IP so
        # this works from any client host, not just from the server itself.
        # Override with VLLM_BASE_URL if the endpoint moves.
        openai_api_base=os.environ.get("VLLM_BASE_URL", "http://134.84.150.139:8888/v1"),
        openai_api_key="vllm_fake_key",
        temperature=args.temperature,
        # chat_template_kwargs is a top-level vLLM request field; OpenAI's SDK
        # merges extra_body into the request body, so this lands in the right
        # place. The gemma4 reasoning parser then emits a reasoning block,
        # which shows up as extra output tokens in cost.json.
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        model_kwargs={"tool_choice": "auto"},
    )
    # llm = ChatOpenAI(
    #     model="Qwen/Qwen3.5-122B-A10B",
    #     openai_api_base="http://134.84.150.135:8001/v1",
    #     openai_api_key="vllm_fake_key",
    #     temperature=args.temperature,
    #     # 10s timeout per attempt (vLLM occasionally accepts a request and never
    #     # responds; abandoning + retrying recovers in seconds).
    #     # max_retries=0 disables the SDK's silent internal retries so the
    #     # while-loop in call_llm() is the single source of retry truth.
    #     timeout=10.0,
    #     max_retries=0,
    #     extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    #     # extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    #     model_kwargs={"tool_choice": "auto"},
    # )
    llm = ChatOpenAI(
        model="google/gemma-4-31B-it",
        # model="qwen3.5-9b",
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

    tools = [query_with_pts, change_shape, add_shape, move_cell, query_drc_report]
    llm_with_tools = llm.bind_tools(tools)

    system_prompt_content = """
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
        final_state = run_agent(drc_error)
        # On success, get count from state (should match global counter)
        final_tool_count = final_state.get("tool_call_count", tool_call_counter["count"])
    except GraphRecursionError as e:
        recursion_limit_reached = True
        print(f"\n{separator}")
        print("AGENT RUN HIT RECURSION LIMIT:")
        print(str(e))
        print(f"{separator}\n", flush=True)
        # Use the global counter which persists even when exception occurs
        final_tool_count = tool_call_counter["count"]
    except Exception as e:
        print(f"\n{separator}")
        print("AGENT RUN FAILED DUE TO ERROR:")
        print(str(e))
        print(f"{separator}\n", flush=True)
        # Use the global counter for any other exception as well
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
