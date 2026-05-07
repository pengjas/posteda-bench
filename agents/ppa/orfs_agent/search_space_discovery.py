"""LLM-driven setup phase: discover tunable params + ranges + defaults.

This is the *only* place the agent talks to an LLM. We give it the task
prompt, the design's config files, a list of src files, and FlowVariables.md.
It returns a structured search space via a single tool call.

Targets / objective are NOT picked by the LLM — they're parsed from info.json
by the orchestrator using `metrics.load_targets`. The LLM only chooses what
parameters to sweep and within what bounds.
"""

import json
import os
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agents.ppa.react import cost_tracker

from .prompts import (SETUP_SYSTEM_PROMPT, SUBMIT_SEARCH_SPACE_SCHEMA,
                      build_setup_user_message)


def _read_text(path: str, max_bytes: int = 200_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)
    except Exception:
        return ""


def _list_dir(path: str, exts: Optional[set] = None) -> List[str]:
    out = []
    if not os.path.isdir(path):
        return out
    for root, _, files in os.walk(path):
        for fn in files:
            if exts and os.path.splitext(fn)[1].lower() not in exts:
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, os.path.dirname(path))
            out.append(rel)
    return out


def gather_setup_context(sandbox: str,
                          flow_vars_path: str) -> Dict[str, Any]:
    """Collect the LLM-visible context. NEVER include info.json."""
    prompt_path = os.path.join(sandbox, "prompt.txt")
    prompt_text = _read_text(prompt_path) or ""

    config_dir = os.path.join(sandbox, "config")
    config_files: Dict[str, str] = {}
    if os.path.isdir(config_dir):
        for fn in sorted(os.listdir(config_dir)):
            full = os.path.join(config_dir, fn)
            if os.path.isfile(full):
                config_files[f"config/{fn}"] = _read_text(full)

    src_dir = os.path.join(sandbox, "src")
    src_list: List[str] = []
    if os.path.isdir(src_dir):
        for fn in sorted(os.listdir(src_dir)):
            src_list.append(f"src/{fn}")

    flow_vars = _read_text(flow_vars_path) if os.path.isfile(flow_vars_path) else ""

    return {
        "prompt_text": prompt_text,
        "config_files": config_files,
        "src_files_list": src_list,
        "flow_vars_excerpt": flow_vars,
    }


def discover_search_space(llm, sandbox: str, flow_vars_path: str,
                           result_dir: str) -> List[Dict[str, Any]]:
    """One LLM call. Returns list of tunable param dicts."""
    ctx = gather_setup_context(sandbox, flow_vars_path)
    user_msg = build_setup_user_message(
        ctx["prompt_text"], ctx["config_files"],
        ctx["src_files_list"], ctx["flow_vars_excerpt"]
    )

    llm_with_tools = llm.bind_tools([SUBMIT_SEARCH_SPACE_SCHEMA],
                                    tool_choice="submit_search_space")
    print("\n--- LLM SETUP CALL: discover_search_space ---", flush=True)

    response = llm_with_tools.invoke(
        [SystemMessage(content=SETUP_SYSTEM_PROMPT),
         HumanMessage(content=user_msg)],
        config={"request_timeout": 400.0},
    )
    cost_tracker.record_langchain_usage(response)

    # extract the tool_calls argument
    params: List[Dict[str, Any]] = []
    if getattr(response, "tool_calls", None):
        tc = response.tool_calls[0]
        args = tc.get("args", tc.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        params = args.get("tunable_params", [])

    if not params:
        print("WARNING: LLM did not return a search space; using empty list.",
              flush=True)

    # persist for diagnostics
    try:
        os.makedirs(result_dir, exist_ok=True)
        with open(os.path.join(result_dir, "search_space.json"), "w") as f:
            json.dump({"tunable_params": params}, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: could not save search_space.json: {e}", flush=True)

    print(f"--- discovered {len(params)} tunable params ---", flush=True)
    for p in params:
        print(f"   - {p.get('name')}: type={p.get('type')}, "
              f"range={p.get('range')}, default={p.get('default')}, "
              f"file={p.get('file')}, kind={p.get('var_kind')}",
              flush=True)
    return params
