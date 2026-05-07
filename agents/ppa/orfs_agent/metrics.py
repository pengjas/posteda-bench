"""PPA metric extraction + per-candidate SR/VRR/objective computation.

Mirrors `benchmark/utils/ppa_score.py` so the agent's internal accounting of
success and Violation Reduction Rate matches the harness verdict at the end.

Note: only used by Python code, never surfaced into LLM context. LLM prompts
must not include info.json — targets are inferred by the LLM from prompt.txt.
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple


def extract_metrics(work_dir: str) -> Dict[str, Optional[float]]:
    """Pull period_min / total_power / die_area from a work_dir's flow outputs."""
    out = {"period": None, "power": None, "area": None}

    rpt = os.path.join(work_dir, "reports", "6_finish.rpt")
    if os.path.isfile(rpt):
        try:
            with open(rpt, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            m = re.search(r"period_min\s*=\s*([\d.]+)", text)
            if m:
                out["period"] = float(m.group(1))
            m = re.search(
                r"Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)",
                text,
            )
            if m:
                out["power"] = float(m.group(4))
        except Exception:
            pass

    js = os.path.join(work_dir, "logs", "6_report.json")
    if os.path.isfile(js):
        try:
            with open(js, "r", encoding="utf-8") as f:
                data = json.load(f)
            v = data.get("finish__design__die__area")
            if v is not None:
                out["area"] = float(v)
        except Exception:
            pass

    return out


def load_targets(info_path: str) -> Dict[str, Optional[float]]:
    """Read target_* and initial_* from info.json (for Python-side scoring only)."""
    out = {
        "target_period": None, "target_power": None, "target_area": None,
        "init_period": None,   "init_power": None,   "init_area": None,
    }
    if not os.path.isfile(info_path):
        return out
    try:
        with open(info_path) as f:
            info = json.load(f)
    except Exception:
        return out

    out["target_period"] = _f(info.get("target_effective_period"))
    out["target_power"] = _f(info.get("target_power"))
    out["target_area"] = _f(info.get("target_area"))
    out["init_period"] = _f(info.get("initial_effective_period")
                            or info.get("init_effective_period"))
    out["init_power"] = _f(info.get("initial_power") or info.get("init_power"))
    out["init_area"] = _f(info.get("initial_area") or info.get("init_area"))
    return out


def _f(x):
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def compute_sr_vrr(metrics: Dict[str, Optional[float]],
                   targets: Dict[str, Optional[float]]) -> Tuple[int, float]:
    """Return (success, vrr). Matches benchmark/utils/ppa_score.py rules."""
    pairs = []
    if targets["target_period"] is not None:
        pairs.append(("period", targets["target_period"],
                      targets["init_period"], metrics.get("period")))
    if targets["target_power"] is not None:
        pairs.append(("power", targets["target_power"],
                      targets["init_power"], metrics.get("power")))
    if targets["target_area"] is not None:
        pairs.append(("area", targets["target_area"],
                      targets["init_area"], metrics.get("area")))

    if not pairs:
        return 0, 0.0

    success = 1 if all(f is not None and f <= t for _, t, _, f in pairs) else 0

    if any(i is None or f is None for _, t, i, f in pairs):
        return success, 0.0

    if len(pairs) == 1:
        _, t, i, f = pairs[0]
        if t == i:
            return success, (1.0 if f <= t else 0.0)
        r = (f - i) / (t - i)
        return success, max(0.0, min(1.0, r))

    type_a = [(t, i, f) for _, t, i, f in pairs if i > t]
    if type_a and all(f >= i for t, i, f in type_a):
        return success, 0.0

    scores = []
    for _, t, i, f in pairs:
        if i > t:
            denom = t - i
            s = (f - i) / denom
            if s > 1.0:
                s = 1.0
        else:
            if f <= t:
                s = 1.0
            else:
                denom = t - i
                s = 0.0 if denom == 0 else -(f - t) / denom
        scores.append(s)
    return success, sum(scores) / len(scores)


def objective_value(metrics: Dict[str, Optional[float]],
                    targets: Dict[str, Optional[float]]) -> float:
    """Scalar the GP is trained on — lower is better.

    Negative VRR (so VRR=1 maps to obj=-1; VRR=0 maps to obj=0). Failed flows
    get a large penalty so the GP learns to avoid that region of parameter
    space.
    """
    targeted_dims = []
    for k, tk in (("period", "target_period"),
                  ("power", "target_power"),
                  ("area", "target_area")):
        if targets.get(tk) is not None:
            targeted_dims.append(k)

    if any(metrics.get(k) is None for k in targeted_dims):
        return 1e6
    _, vrr = compute_sr_vrr(metrics, targets)
    return -vrr


def write_json(result_dir: str, name: str, data) -> None:
    path = os.path.join(result_dir, name)
    try:
        os.makedirs(result_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: could not save {name}: {e}", flush=True)
