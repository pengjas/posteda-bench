"""Tool for collecting PPA metrics from the last OpenROAD run."""

import os
import re
import json
from typing import Optional, Dict, Any
from langchain_core.tools import tool

from .utils import get_sandbox_root


def _format_power(value: float) -> str:
    """Format power value for display (use scientific notation for small values)."""
    if value is None:
        return "N/A"
    if abs(value) < 0.001:
        return f"{value:.4e} W"
    else:
        return f"{value:.6f} W"


def _extract_effective_period(report_path: str) -> Optional[float]:
    """
    Extract effective clock period from timing report.

    Looks for pattern like: "core_clock period_min = 196.93"
    or similar patterns with clock name and period_min.
    """
    if not os.path.isfile(report_path):
        return None

    try:
        with open(report_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Pattern: <clock_name> period_min = <value>
        # e.g., "core_clock period_min = 196.93 fmax = 5078.01"
        # or "clk period_min = 196.93"
        match = re.search(r'\w+\s+period_min\s*=\s*([\d.]+)', content)
        if match:
            return float(match.group(1))

        return None
    except Exception:
        return None


def _extract_power(report_path: str) -> Optional[float]:
    """
    Extract total power from timing report.

    Looks for pattern like: "Total ... <power_value>" in power report section
    """
    if not os.path.isfile(report_path):
        return None

    try:
        with open(report_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Pattern for total power line in report
        # The power section typically has format:
        # Total  <internal>  <switching>  <leakage>  <total>
        # Look for "Total" followed by numbers
        match = re.search(
            r'Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)',
            content
        )
        if match:
            # The 4th value is total power
            return float(match.group(4))

        # Alternative: look for explicit "total power" or "Total Power"
        match = re.search(r'[Tt]otal\s+[Pp]ower[:\s]+([\d.eE+-]+)', content)
        if match:
            return float(match.group(1))

        return None
    except Exception:
        return None


def _extract_area(json_path: str) -> Optional[float]:
    """
    Extract die area from JSON report.

    Looks for: "finish__design__die__area" field
    """
    if not os.path.isfile(json_path):
        return None

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if 'finish__design__die__area' in data:
            return float(data['finish__design__die__area'])

        return None
    except Exception:
        return None


@tool
def report_ppa() -> str:
    """
    **PURPOSE:** Collect and report PPA (Power, Performance, Area) metrics from the last OpenROAD run.

    This tool automatically extracts key metrics from the result files:
    - `effective_period`: Clock period in picoseconds from timing report
    - `power`: Total power in Watts from timing report
    - `area`: Die area in square microns from JSON log

    **WHEN TO USE:** Use this tool after running `run_openroad_flow()` to quickly
    check all PPA metrics at once, instead of running multiple grep/jq commands.

    **METRIC SOURCES:**
    - `effective_period`: Extracted from `period_min` in `reports/6_finish.rpt`
    - `power`: Extracted from `Total` power line in `reports/6_finish.rpt`
    - `area`: Extracted from `finish__design__die__area` in `logs/6_report.json`

    **OUTPUT:** Returns a formatted report with all PPA metrics and comparison
    against targets (if info.json contains targets).

    **EXAMPLE OUTPUT:**
    ```
    === PPA Metrics Report ===

    Performance (Timing):
      effective_period: 196.93 ps
      target_effective_period: 180.00 ps
      status: NOT MET (need to reduce by 16.93 ps)

    Power:
      total_power: 0.00234 W
      target_power: 0.00300 W
      status: MET

    Area:
      die_area: 1234.56 um²
      target_area: 1500.00 um²
      status: MET

    Overall: 2/3 targets met
    ```
    """
    sandbox_root = get_sandbox_root()

    if not sandbox_root:
        return "ERROR: Sandbox root not configured (PPA_SANDBOX_ROOT not set)"

    # Paths for report files (use plural naming consistently)
    report_paths = [
        os.path.join(sandbox_root, "reports", "6_finish.rpt"),
    ]

    json_paths = [
        os.path.join(sandbox_root, "logs", "6_report.json"),
    ]

    info_path = os.path.join(sandbox_root, "info.json")

    # Find actual report file
    report_path = None
    for path in report_paths:
        if os.path.isfile(path):
            report_path = path
            break

    json_path = None
    for path in json_paths:
        if os.path.isfile(path):
            json_path = path
            break

    # Extract metrics
    effective_period = _extract_effective_period(report_path) if report_path else None
    power = _extract_power(report_path) if report_path else None
    area = _extract_area(json_path) if json_path else None

    # Read targets from info.json
    targets = {}
    if os.path.isfile(info_path):
        try:
            with open(info_path, 'r') as f:
                info = json.load(f)
            targets = {
                'target_effective_period': info.get('target_effective_period'),
                'target_power': info.get('target_power'),
                'target_area': info.get('target_area'),
            }
        except Exception:
            pass

    # Count total targets specified (regardless of whether metrics are available)
    targets_specified = sum(1 for k in ['target_effective_period', 'target_power', 'target_area']
                           if targets.get(k) is not None)

    # Build report
    lines = ["=" * 50]
    lines.append("PPA METRICS REPORT")
    lines.append("=" * 50)
    lines.append("")

    targets_met = 0
    targets_checked = 0

    # Performance (Timing)
    lines.append("PERFORMANCE (Timing):")
    target_period = targets.get('target_effective_period')
    if target_period is not None:
        lines.append(f"  target: {float(target_period):.2f} ps")
    if effective_period is not None:
        lines.append(f"  current: {effective_period:.2f} ps")
        if target_period is not None:
            target = float(target_period)
            targets_checked += 1
            if effective_period <= target:
                lines.append(f"  status: MET")
                targets_met += 1
            else:
                gap = effective_period - target
                lines.append(f"  status: NOT MET (need -{gap:.2f} ps)")
    else:
        lines.append("  current: NOT AVAILABLE (run flow first)")

    lines.append("")

    # Power
    lines.append("POWER:")
    target_pwr = targets.get('target_power')
    if target_pwr is not None:
        lines.append(f"  target: {_format_power(float(target_pwr))}")
    if power is not None:
        lines.append(f"  current: {_format_power(power)}")
        if target_pwr is not None:
            target = float(target_pwr)
            targets_checked += 1
            if power <= target:
                lines.append(f"  status: MET")
                targets_met += 1
            else:
                gap = power - target
                lines.append(f"  status: NOT MET (need -{_format_power(gap)})")
    else:
        lines.append("  current: NOT AVAILABLE (run flow first)")

    lines.append("")

    # Area
    lines.append("AREA:")
    target_ar = targets.get('target_area')
    if target_ar is not None:
        lines.append(f"  target: {float(target_ar):.2f} um²")
    if area is not None:
        lines.append(f"  current: {area:.2f} um²")
        if target_ar is not None:
            target = float(target_ar)
            targets_checked += 1
            if area <= target:
                lines.append(f"  status: MET")
                targets_met += 1
            else:
                gap = area - target
                lines.append(f"  status: NOT MET (need -{gap:.2f} um²)")
    else:
        lines.append("  current: NOT AVAILABLE (run flow first)")

    lines.append("")
    lines.append("=" * 50)

    # Summary
    if targets_specified > 0:
        if targets_checked > 0:
            lines.append(f"SUMMARY: {targets_met}/{targets_checked} targets met")
            if targets_met == targets_checked:
                lines.append("ALL TARGETS MET - SUCCESS!")
        else:
            lines.append(f"TARGETS SPECIFIED: {targets_specified} (run flow to check)")
    else:
        lines.append("No targets specified in info.json")

    lines.append("=" * 50)

    return "\n".join(lines)
