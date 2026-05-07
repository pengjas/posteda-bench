"""Apply a candidate parameter vector to a sandbox's config files.

The LLM's setup phase emits a list of tunable params, each with:
  - file:       relative path inside the sandbox (e.g. "config/foo.mk")
  - var_kind:   "make_export" | "sdc_clk_period" | "sdc_create_clock_period"
  - name:       variable name (for make_export) or label (for SDC kinds)
  - type:       "int" | "float" | "categorical"
  - range:      [lo, hi] for numeric, [v1, v2, ...] for categorical
  - default:    current value parsed from the design

This module knows how to *write* a chosen value into the right file. It does
not parse FlowVariables.md — that's the LLM's job during setup.
"""

import os
import re
import shutil
from typing import Any, Dict, List, Tuple


def format_value(value: Any, ptype: str) -> str:
    if ptype == "int":
        return str(int(round(float(value))))
    if ptype == "float":
        return f"{float(value):g}"
    return str(value)


def apply_param(sandbox: str, param: Dict[str, Any], value: Any) -> Tuple[bool, str]:
    """Apply one parameter's chosen value into its file. Returns (ok, msg)."""
    rel_file = param.get("file", "")
    full = os.path.join(sandbox, rel_file)
    if not os.path.isfile(full):
        return False, f"file not found: {rel_file}"

    var_kind = param.get("var_kind", "make_export")
    name = param.get("name", "")
    val_str = format_value(value, param.get("type", "float"))

    try:
        with open(full, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return False, f"read failed: {e}"

    new_text = text
    changed = False

    if var_kind == "make_export":
        # match: optional leading whitespace, "export NAME [?:+]= ..."
        pat = re.compile(
            rf"^([ \t]*export\s+{re.escape(name)}\s*(?:\?|:|::|\+)?=\s*)\S.*$",
            re.MULTILINE,
        )
        if pat.search(text):
            new_text = pat.sub(lambda m: m.group(1) + val_str, text, count=1)
            changed = new_text != text
        else:
            # append a new export line at end of file
            sep = "" if text.endswith("\n") else "\n"
            new_text = text + f"{sep}export {name} = {val_str}\n"
            changed = True

    elif var_kind == "sdc_clk_period":
        pat = re.compile(r"(set\s+clk_period\s+)([\d.eE+-]+)")
        if pat.search(text):
            new_text = pat.sub(rf"\g<1>{val_str}", text, count=1)
            changed = new_text != text
        else:
            return False, "no `set clk_period` line found in sdc"

    elif var_kind == "sdc_create_clock_period":
        # create_clock -name X -period <val> ...
        pat = re.compile(r"(create_clock[^\n]*-period\s+)([\d.eE+-]+)")
        if pat.search(text):
            new_text = pat.sub(rf"\g<1>{val_str}", text, count=1)
            changed = new_text != text
        else:
            return False, "no `create_clock -period` line found in sdc"

    else:
        return False, f"unknown var_kind: {var_kind}"

    if not changed:
        return True, "no change needed"

    try:
        with open(full, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception as e:
        return False, f"write failed: {e}"

    return True, f"applied {name}={val_str}"


def apply_vector(sandbox: str, params: List[Dict[str, Any]],
                 vector: List[Any]) -> List[str]:
    """Apply a full candidate vector (one value per param) and return per-param msgs."""
    msgs = []
    for p, v in zip(params, vector):
        ok, msg = apply_param(sandbox, p, v)
        msgs.append(f"{p.get('name', '?')}: {'OK' if ok else 'FAIL'} ({msg})")
    return msgs


def snapshot_sandbox(src: str, dst: str) -> None:
    """Copy a sandbox tree (config/src/info.json/prompt.txt/...) into dst.

    Skips reports/logs/results since each candidate run will overwrite them.
    """
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)
    for entry in os.listdir(src):
        s = os.path.join(src, entry)
        d = os.path.join(dst, entry)
        if entry in ("reports", "logs", "results", "objects", "_orig_src"):
            continue
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            try:
                shutil.copy2(s, d)
            except Exception:
                pass


def copy_outputs_back(src_work: str, dst_sandbox: str) -> None:
    """After the best candidate is picked, copy its config + outputs to the
    main sandbox so the eval harness verifier sees them."""
    for sub in ("config", "src", "reports", "logs", "results"):
        s = os.path.join(src_work, sub)
        d = os.path.join(dst_sandbox, sub)
        if not os.path.isdir(s):
            continue
        if os.path.isdir(d):
            shutil.rmtree(d)
        try:
            shutil.copytree(s, d)
        except Exception as e:
            print(f"Warning: could not copy {sub} back: {e}", flush=True)
