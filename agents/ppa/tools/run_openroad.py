"""Tool for running OpenROAD flow via docker shell with proper isolation."""

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Tuple
from langchain_core.tools import tool

from .utils import get_sandbox_root


# OpenROAD flow scripts location.
# Set the OPENROAD_FLOW_DIR env var to point to your local
# OpenROAD-flow-scripts/flow checkout. Falls back to ~/OpenROAD-flow-scripts/flow.
OPENROAD_FLOW_DIR = os.environ.get(
    "OPENROAD_FLOW_DIR",
    os.path.expanduser("~/OpenROAD-flow-scripts/flow"),
)

# Get execution timeout from environment variable (default 1800 seconds = 30 minutes)
def _get_tool_timeout() -> int:
    """Get tool timeout from TOOL_TIMEOUT env var, default 1800s."""
    return int(os.environ.get("TOOL_TIMEOUT", 1800))


def _parse_flow_variant(config_mk_path: str) -> str:
    """
    Parse FLOW_VARIANT from config.mk file.
    Returns "base" if not found (default).
    """
    if not os.path.isfile(config_mk_path):
        return "base"

    try:
        with open(config_mk_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Look for: export FLOW_VARIANT = <value>
        match = re.search(r'export\s+FLOW_VARIANT\s*=\s*(\S+)', content)
        if match:
            return match.group(1).strip()
    except Exception:
        pass

    return "base"


def _parse_export_var(config_mk_path: str, var_name: str) -> Optional[str]:
    """Parse `export <var_name> [?:+]= <value>` from a .mk file.

    Accepts every Make assignment operator (=, ?=, :=, ::=, +=) so a config
    like `export DESIGN_NICKNAME ?= riscv32i` is recognized. Missing ?= here
    silently returned None and caused the sandbox to be laid out under
    DESIGN_NAME instead of DESIGN_NICKNAME, breaking every downstream path.
    """
    if not os.path.isfile(config_mk_path):
        return None

    try:
        with open(config_mk_path, 'r', encoding='utf-8') as f:
            content = f.read()

        match = re.search(
            rf'export\s+{re.escape(var_name)}\s*(?:\?|:|::|\+)?=\s*(\S+)',
            content,
        )
        if match:
            return match.group(1).strip()
    except Exception:
        pass

    return None


def _parse_design_name(config_mk_path: str) -> Optional[str]:
    """Parse DESIGN_NAME from config.mk file."""
    return _parse_export_var(config_mk_path, "DESIGN_NAME")


def _setup_temp_workspace(sandbox_root: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Setup a temporary workspace for OpenROAD flow execution.
    Creates a completely separate directory in _temp_runs/ to avoid polluting the main OpenROAD directory.

    Returns: (temp_dir, temp_dir_relative, design_config_relative_path, design_name, design_dir, flow_variant)
    On failure: (None, None, None, None, None, error_msg)
    """
    try:
        # Find the config.mk file in sandbox
        config_dir = os.path.join(sandbox_root, "config")
        if not os.path.isdir(config_dir):
            return None, None, None, None, None, "Config directory not found in sandbox"

        mk_files = [f for f in os.listdir(config_dir) if f.endswith('.mk')]
        if not mk_files:
            return None, None, None, None, None, "No .mk file found in config directory"

        mk_file = mk_files[0]
        config_mk_path = os.path.join(config_dir, mk_file)

        # Parse design name, nickname, and flow variant
        design_name = _parse_design_name(config_mk_path)
        if not design_name:
            design_name = mk_file.replace('.mk', '')

        # ORFS uses DESIGN_NICKNAME for the per-design directory under
        # designs/<platform>/ and designs/src/ when it is set; fall back to
        # DESIGN_NAME otherwise. Lay out the temp workspace with the same
        # name so $(DESIGN_NICKNAME) / $(DESIGN_NAME) substitutions and
        # ORFS's own results/logs/reports paths all resolve consistently.
        design_nickname = _parse_export_var(config_mk_path, "DESIGN_NICKNAME")
        design_dir = design_nickname or design_name

        flow_variant = _parse_flow_variant(config_mk_path)

        # Create temp directory with unique name in _temp_runs/
        temp_base = os.path.join(OPENROAD_FLOW_DIR, "_temp_runs")
        os.makedirs(temp_base, exist_ok=True)
        unique_suffix = uuid.uuid4().hex[:8]
        temp_dir_name = f"{design_name}_{unique_suffix}"
        temp_dir = os.path.join(temp_base, temp_dir_name)
        os.makedirs(temp_dir, exist_ok=True)

        # Create directory structure in temp:
        # _temp_runs/{name}_{uuid}/designs/asap7/{design_dir}/ - for config files
        # _temp_runs/{name}_{uuid}/designs/src/{design_dir}/  - for source files
        temp_design_dir = os.path.join(temp_dir, "designs", "asap7", design_dir)
        temp_src_dir = os.path.join(temp_dir, "designs", "src", design_dir)
        os.makedirs(temp_design_dir, exist_ok=True)
        os.makedirs(temp_src_dir, exist_ok=True)

        # Copy config files (.mk, .sdc) to temp design dir
        for f in os.listdir(config_dir):
            src = os.path.join(config_dir, f)
            dst = os.path.join(temp_design_dir, f)
            if os.path.isfile(src):
                shutil.copy2(src, dst)

        # Copy source files (.v, .sv) to temp src dir (including subdirectories)
        src_dir = os.path.join(sandbox_root, "src")
        if os.path.isdir(src_dir):
            shutil.rmtree(temp_src_dir)
            shutil.copytree(src_dir, temp_src_dir)

        # DESIGN_CONFIG and WORK_HOME as relative paths from OPENROAD_FLOW_DIR
        design_config_relative = f"_temp_runs/{temp_dir_name}/designs/asap7/{design_dir}/{mk_file}"
        temp_dir_relative = f"_temp_runs/{temp_dir_name}"

        # Update config.mk to use relative paths for VERILOG_FILES and SDC_FILE
        temp_mk_path = os.path.join(temp_design_dir, mk_file)
        _update_config_paths(temp_mk_path, temp_dir_relative, design_dir)

        return temp_dir, temp_dir_relative, design_config_relative, design_name, design_dir, flow_variant

    except Exception as e:
        return None, None, None, None, None, f"Failed to setup temp workspace: {e}"


def _update_config_paths(config_mk_path: str, temp_dir_relative: str, design_dir: str):
    """
    Update the config.mk to use relative paths for VERILOG_FILES and SDC_FILE.
    This ensures paths point to the copied files using relative paths from OPENROAD_FLOW_DIR.

    Handles both `$(DESIGN_NAME)` / `$(DESIGN_NICKNAME)` variable references
    AND a literal `design_dir` name hard-coded in the path (e.g. the riscv_top
    config writes `$(DESIGN_HOME)/src/riscv32i/*.v` and
    `$(DESIGN_HOME)/$(PLATFORM)/riscv32i/...` with `riscv32i` as a literal
    instead of `$(DESIGN_NICKNAME)`). Without this fallback the path stays
    unchanged, still expands to the real ORFS tree, and the flow fails with
    "No rule to make target .../riscv32i.sdc".
    """
    try:
        with open(config_mk_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Get the temp design home as relative path (e.g., _temp_runs/{uuid}/designs)
        temp_design_home = f"{temp_dir_relative}/designs"
        design_dir_re = re.escape(design_dir)
        # \b after the literal alternative keeps `ibex` from matching as a prefix
        # of `ibex_sv` (the ibex_core config uses `ibex_sv` as the source subdir
        # but `ibex` as DESIGN_NICKNAME / design_dir).
        suffix_alt = rf'(?:\$\(DESIGN_(?:NAME|NICKNAME)\)|{design_dir_re}\b)'

        # VERILOG_FILES: $(DESIGN_HOME)/src/{$(DESIGN_{NAME,NICKNAME})|<design_dir>}
        #   -> temp src dir
        content = re.sub(
            rf'\$\(DESIGN_HOME\)/src/{suffix_alt}',
            f'{temp_design_home}/src/{design_dir}',
            content
        )

        # SDC_FILE / IO_CONSTRAINTS:
        #   $(DESIGN_HOME)/$(PLATFORM)/{$(DESIGN_{NAME,NICKNAME})|<design_dir>}/...
        #   -> temp design dir
        content = re.sub(
            rf'\$\(DESIGN_HOME\)/\$\(PLATFORM\)/{suffix_alt}',
            f'{temp_design_home}/asap7/{design_dir}',
            content
        )

        with open(config_mk_path, 'w', encoding='utf-8') as f:
            f.write(content)

    except Exception:
        pass  # If update fails, the original paths may still work


def _collect_results(temp_dir: str, design_dir: str, flow_variant: str, sandbox_root: str) -> Tuple[bool, str]:
    """Collect results from temp OpenROAD workspace back to sandbox.

    `design_dir` is the directory name ORFS uses under results/logs/reports —
    DESIGN_NICKNAME when set in the .mk, else DESIGN_NAME.
    """
    try:
        # Results are in temp_dir/{results,logs,reports}/asap7/{design_dir}/{flow_variant}/
        flow_results = os.path.join(temp_dir, "results", "asap7", design_dir, flow_variant)
        flow_logs = os.path.join(temp_dir, "logs", "asap7", design_dir, flow_variant)
        flow_reports = os.path.join(temp_dir, "reports", "asap7", design_dir, flow_variant)

        sandbox_results = os.path.join(sandbox_root, "results")
        sandbox_logs = os.path.join(sandbox_root, "logs")
        sandbox_reports = os.path.join(sandbox_root, "reports")

        # Clean up old results before copying new ones
        for d in [sandbox_results, sandbox_logs, sandbox_reports]:
            if os.path.isdir(d):
                shutil.rmtree(d)

        files_copied = 0

        # Copy results
        if os.path.isdir(flow_results):
            os.makedirs(sandbox_results, exist_ok=True)
            for f in os.listdir(flow_results):
                src = os.path.join(flow_results, f)
                dst = os.path.join(sandbox_results, f)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    files_copied += 1

        # Copy logs
        if os.path.isdir(flow_logs):
            os.makedirs(sandbox_logs, exist_ok=True)
            for f in os.listdir(flow_logs):
                src = os.path.join(flow_logs, f)
                dst = os.path.join(sandbox_logs, f)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    files_copied += 1

        # Copy reports
        if os.path.isdir(flow_reports):
            os.makedirs(sandbox_reports, exist_ok=True)
            for f in os.listdir(flow_reports):
                src = os.path.join(flow_reports, f)
                dst = os.path.join(sandbox_reports, f)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    files_copied += 1

        return True, f"Copied {files_copied} result files to sandbox"

    except Exception as e:
        return False, f"Failed to collect results: {e}"


def _cleanup_temp_dir(temp_dir: str):
    """Clean up the temporary workspace directory."""
    if temp_dir and os.path.isdir(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


@tool
def run_openroad_flow(
    target: str = "finish",
    extra_args: Optional[str] = None,
) -> str:
    """
    **PURPOSE:** Run OpenROAD flow via docker shell to execute the EDA process.

    **WHEN TO USE:** Use this tool after modifying config files (.mk, .sdc) or
    source files (.v) to re-run the synthesis and place-and-route flow and
    evaluate the impact on PPA (Power, Performance, Area) metrics.

    **PARAMETERS:**
    - `target` (str): Make target to execute. Default: "finish" (full flow).
        Common targets:
        - "finish": Run complete flow (synth -> route -> gds)
        - "synth": Run synthesis only
        - "floorplan": Run up to floorplan
        - "place": Run up to placement
        - "cts": Run up to clock tree synthesis
        - "route": Run up to routing
    - `extra_args` (str, optional): Additional make arguments (e.g., "VERBOSE=1").

    **WORKFLOW:**
    1. Creates a temporary workspace (does NOT pollute the main OpenROAD directory)
    2. Copies config (.mk, .sdc) and source (.v) files from sandbox
    3. Executes the flow via docker shell
    4. Copies results (reports, logs, outputs) back to sandbox
    5. Cleans up temporary workspace

    **AFTER RUNNING:**
    Use `report_ppa()` to check all PPA metrics

    **EXAMPLES:**
    1. Run full flow:
       `run_openroad_flow(target="finish")`

    2. Run with verbose output:
       `run_openroad_flow(target="finish", extra_args="VERBOSE=1")`

    **OUTPUT:** Returns execution summary including exit status and key output.
    """
    # Get timeout from environment variable
    timeout = _get_tool_timeout()
    sandbox_root = get_sandbox_root()

    if not sandbox_root:
        return "ERROR: Sandbox root not configured (PPA_SANDBOX_ROOT not set)"

    # Sanitize extra_args
    if extra_args:
        safe_pattern = r'^[A-Za-z0-9_=\s\-\.]+$'
        if not re.match(safe_pattern, extra_args):
            return "ERROR: Invalid characters in extra_args"

    # Setup temp workspace in _temp_runs/
    temp_dir, temp_dir_relative, design_config_relative, design_name, design_dir, flow_variant = _setup_temp_workspace(sandbox_root)

    if temp_dir is None:
        return f"ERROR: {flow_variant}"  # flow_variant contains error message in this case

    # Build the make command with relative paths for both DESIGN_CONFIG and WORK_HOME
    make_cmd = f"make DESIGN_CONFIG={design_config_relative} WORK_HOME={temp_dir_relative} {target}"
    if extra_args:
        make_cmd += f" {extra_args}"

    # Wrap with docker shell
    docker_cmd = f"util/docker_shell {make_cmd}"

    # Log the actual command being executed
    print(f"\n{'='*80}", flush=True)
    print(f"EXECUTING OPENROAD COMMAND:", flush=True)
    print(f"  Working Directory: {OPENROAD_FLOW_DIR}", flush=True)
    print(f"  Command: {docker_cmd}", flush=True)
    print(f"  Temp Dir: {temp_dir}", flush=True)
    print(f"{'='*80}\n", flush=True)

    try:
        result = subprocess.run(
            docker_cmd,
            shell=True,
            cwd=OPENROAD_FLOW_DIR,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        returncode = result.returncode
        stderr = result.stderr

        if returncode == 0:
            # Collect results back to sandbox
            success, collect_msg = _collect_results(temp_dir, design_dir, flow_variant, sandbox_root)

            # Clean up temp directory
            _cleanup_temp_dir(temp_dir)

            return f"SUCCESS: Flow completed for {design_name}. {collect_msg}. Use report_ppa() to check metrics."
        else:
            # Extract key error message
            error_lines = stderr.strip().split('\n')
            key_errors = [l for l in error_lines[-10:] if 'error' in l.lower() or 'Error' in l or 'Stop' in l]
            error_summary = key_errors[-1] if key_errors else error_lines[-1] if error_lines else "Unknown error"

            # Collect partial results (logs/reports) even on failure for debugging
            _collect_results(temp_dir, design_dir, flow_variant, sandbox_root)

            # Clean up temp directory after collecting
            _cleanup_temp_dir(temp_dir)

            return f"FAILED: Flow failed for {design_name}. Error: {error_summary}"

    except subprocess.TimeoutExpired:
        # Collect partial results even on timeout for debugging
        if temp_dir:
            _collect_results(temp_dir, design_dir, flow_variant, sandbox_root)
            _cleanup_temp_dir(temp_dir)
        return f"FAILED: Flow timed out after {timeout} seconds"

    except Exception as e:
        # Collect partial results even on exception for debugging
        if temp_dir:
            try:
                _collect_results(temp_dir, design_dir, flow_variant, sandbox_root)
            except:
                pass  # Ignore collection errors
            _cleanup_temp_dir(temp_dir)
        return f"FAILED: {e}"


