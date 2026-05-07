"""Parallel candidate execution.

Each candidate gets its own work_dir copy of the sandbox. We spawn one process
per candidate; each process sets PPA_SANDBOX_ROOT to its work_dir and invokes
`run_openroad_flow` (the same tool gpt_react.py uses). After all candidates
finish, the parent reads metrics out of each work_dir.
"""

import multiprocessing as mp
import os
import time
import traceback
from typing import Any, Dict, List

from .metrics import extract_metrics
from .param_apply import apply_vector, snapshot_sandbox


def _candidate_worker(work_dir: str, return_dict: Dict[str, Any]):
    """Run inside a child process. Sets env then calls run_openroad_flow."""
    try:
        os.environ["PPA_SANDBOX_ROOT"] = work_dir
        from agents.ppa.tools import run_openroad_flow
        result = run_openroad_flow.invoke({})
        return_dict["status"] = "ok"
        return_dict["result"] = str(result)
    except Exception:
        return_dict["status"] = "error"
        return_dict["traceback"] = traceback.format_exc()


def run_candidates_parallel(main_sandbox: str,
                             iter_dir: str,
                             params: List[Dict[str, Any]],
                             vectors: List[List[Any]],
                             tool_timeout: int) -> List[Dict[str, Any]]:
    """Execute N candidates concurrently. Returns per-candidate dicts:
       {idx, work_dir, vector, apply_msgs, status, result, metrics, elapsed}
    """
    os.makedirs(iter_dir, exist_ok=True)

    candidates: List[Dict[str, Any]] = []
    procs = []
    managers = []

    # snapshot + apply + spawn — done sequentially because file I/O is fast
    for i, vec in enumerate(vectors):
        cand_dir = os.path.join(iter_dir, f"cand_{i+1}")
        snapshot_sandbox(main_sandbox, cand_dir)
        apply_msgs = apply_vector(cand_dir, params, vec)

        manager = mp.Manager()
        ret = manager.dict()
        managers.append(manager)
        p = mp.Process(target=_candidate_worker, args=(cand_dir, ret))
        candidates.append({
            "idx": i + 1,
            "work_dir": cand_dir,
            "vector": vec,
            "apply_msgs": apply_msgs,
            "status": "pending",
            "result": "",
            "metrics": {"period": None, "power": None, "area": None},
            "elapsed": 0.0,
            "_proc": p,
            "_ret": ret,
            "_t0": 0.0,
        })

    # start all in parallel
    for c in candidates:
        c["_t0"] = time.time()
        c["_proc"].start()
        print(f"  [iter] started candidate {c['idx']} -> {c['work_dir']}",
              flush=True)

    # join with per-candidate timeout (run_openroad_flow already enforces its
    # own timeout internally via TOOL_TIMEOUT; we add a small buffer)
    deadline = time.time() + tool_timeout + 120
    for c in candidates:
        remaining = max(1, int(deadline - time.time()))
        c["_proc"].join(timeout=remaining)
        if c["_proc"].is_alive():
            print(f"  [iter] candidate {c['idx']} timed out, terminating",
                  flush=True)
            c["_proc"].terminate()
            c["_proc"].join(timeout=10)
            c["status"] = "timeout"
            c["result"] = "candidate exceeded timeout"
        else:
            ret = c["_ret"]
            c["status"] = ret.get("status", "error")
            c["result"] = ret.get("result", ret.get("traceback", ""))

        c["elapsed"] = time.time() - c["_t0"]
        c["metrics"] = extract_metrics(c["work_dir"])

    # strip non-serializable fields
    out = []
    for c in candidates:
        out.append({
            "idx": c["idx"],
            "work_dir": c["work_dir"],
            "vector": c["vector"],
            "apply_msgs": c["apply_msgs"],
            "status": c["status"],
            "result": c["result"][:2000] if c["result"] else "",
            "metrics": c["metrics"],
            "elapsed": round(c["elapsed"], 1),
        })
    return out
