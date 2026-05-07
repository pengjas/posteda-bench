#!/usr/bin/env python3
"""
PPA Metric Collection Script

This script extracts PPA (Power, Performance, Area) metrics from OpenROAD
output files and updates the info.json file with final results.

Metrics are extracted from:
- Timing (WNS, period_min): reports/6_finish.rpt
- Power (total_power): logs/6_report.json
- Area (die_area): logs/6_report.json

Targets are read from info.json:
- target_effective_period: Target clock period in ps
- target_power: Target total power in Watts
- target_area: Target die area in square microns

Success is determined by comparing actual metrics against all specified targets.

Used by eval_ppa.sh to verify agent results.
"""

import os
import re
import json
import sys
from pathlib import Path


def extract_timing_metrics(report_path: str) -> dict:
    """
    Extract timing metrics from the final timing report (report/6_finish.rpt).

    Extracts:
    - worst_slack: from "worst slack max <value>" line
    - period_min: from "clk period_min = <value>" line (effective clock period in ps)
    - wns, tns: from "wns max <value>" and "tns max <value>" lines
    """
    metrics = {
        "wns": None,
        "tns": None,
        "worst_slack": None,
        "period_min": None,
        "setup_violations": None,
        "hold_violations": None,
    }

    if not os.path.isfile(report_path):
        return metrics

    try:
        with open(report_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Extract worst slack max (e.g., "worst slack max -41.93")
        slack_match = re.search(r'worst slack max\s+(-?[\d.]+)', content)
        if slack_match:
            metrics["worst_slack"] = float(slack_match.group(1))

        # Extract period_min (e.g., "clk period_min = 196.93 fmax = 5078.01")
        period_match = re.search(r'period_min\s*=\s*([\d.]+)', content)
        if period_match:
            metrics["period_min"] = float(period_match.group(1))

        # Extract WNS (e.g., "wns max -41.93")
        wns_match = re.search(r'wns\s+max\s+(-?[\d.]+)', content)
        if wns_match:
            metrics["wns"] = float(wns_match.group(1))
        elif metrics["worst_slack"] is not None:
            # Use worst_slack as WNS if not found separately
            metrics["wns"] = metrics["worst_slack"]

        # Extract TNS (e.g., "tns max -1234.56")
        tns_match = re.search(r'tns\s+max\s+(-?[\d.]+)', content)
        if tns_match:
            metrics["tns"] = float(tns_match.group(1))

        # Extract setup violations
        setup_match = re.search(r'setup violation count\s+(\d+)', content)
        if setup_match:
            metrics["setup_violations"] = int(setup_match.group(1))

        # Extract hold violations
        hold_match = re.search(r'hold violation count\s+(\d+)', content)
        if hold_match:
            metrics["hold_violations"] = int(hold_match.group(1))

    except Exception as e:
        print(f"Error extracting timing metrics: {e}", file=sys.stderr)

    return metrics


def extract_power_from_report(report_path: str) -> float:
    """
    Extract total power from timing report (report/6_finish.rpt).

    Looks for pattern like: "Total  <internal>  <switching>  <leakage>  <total>"
    """
    if not os.path.isfile(report_path):
        return None

    try:
        with open(report_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Pattern for total power line in report
        # The power section typically has format:
        # Total  <internal>  <switching>  <leakage>  <total>
        match = re.search(
            r'Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)',
            content
        )
        if match:
            # The 4th value is total power
            return float(match.group(4))

        return None
    except Exception as e:
        print(f"Error extracting power from report: {e}", file=sys.stderr)
        return None


def extract_area_from_json(json_path: str) -> float:
    """
    Extract die area from log/6_report.json.

    Extracts: "finish__design__die__area" field (in square microns)
    """
    if not os.path.isfile(json_path):
        return None

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Extract die area
        if "finish__design__die__area" in data:
            return float(data["finish__design__die__area"])

        return None
    except Exception as e:
        print(f"Error extracting area from JSON: {e}", file=sys.stderr)
        return None


def check_targets(info: dict, timing_metrics: dict, power: float, area: float) -> dict:
    """
    Check if actual metrics meet the specified targets.

    Targets in info.json:
    - target_effective_period: actual period_min should be <= target
    - target_power: actual total_power should be <= target
    - target_area: actual die_area should be <= target

    Returns dict with success status for each metric and overall success.
    """
    results = {
        "timing_met": None,
        "power_met": None,
        "area_met": None,
        "all_targets_met": False,
        "targets_checked": [],
    }

    all_met = True
    any_target_specified = False

    # Check timing target (period_min <= target_effective_period)
    if "target_effective_period" in info and info["target_effective_period"] is not None:
        any_target_specified = True
        results["targets_checked"].append("timing")
        target_period = float(info["target_effective_period"])
        actual_period = timing_metrics.get("period_min")

        if actual_period is not None:
            results["timing_met"] = actual_period <= target_period
            if not results["timing_met"]:
                all_met = False
        else:
            results["timing_met"] = False
            all_met = False

    # Check power target (total_power <= target_power)
    if "target_power" in info and info["target_power"] is not None:
        any_target_specified = True
        results["targets_checked"].append("power")
        target_power_val = float(info["target_power"])

        if power is not None:
            results["power_met"] = power <= target_power_val
            if not results["power_met"]:
                all_met = False
        else:
            results["power_met"] = False
            all_met = False

    # Check area target (die_area <= target_area)
    if "target_area" in info and info["target_area"] is not None:
        any_target_specified = True
        results["targets_checked"].append("area")
        target_area_val = float(info["target_area"])

        if area is not None:
            results["area_met"] = area <= target_area_val
            if not results["area_met"]:
                all_met = False
        else:
            results["area_met"] = False
            all_met = False

    # Overall success: all specified targets must be met
    if any_target_specified:
        results["all_targets_met"] = all_met
    else:
        # No targets specified - check if WNS >= 0 as fallback
        wns = timing_metrics.get("wns")
        if wns is not None:
            results["all_targets_met"] = wns >= 0
            results["timing_met"] = wns >= 0
            results["targets_checked"].append("timing_wns")

    return results


def update_info_json(info_path: str, timing_metrics: dict, power: float, area: float, target_results: dict) -> bool:
    """Update info.json with final metrics and success status."""
    try:
        # Read existing info.json
        info = {}
        if os.path.isfile(info_path):
            with open(info_path, 'r') as f:
                info = json.load(f)

        # Update with final timing metrics
        if timing_metrics.get("wns") is not None:
            info["final_wns"] = timing_metrics["wns"]
        if timing_metrics.get("tns") is not None:
            info["final_tns"] = timing_metrics["tns"]
        if timing_metrics.get("worst_slack") is not None:
            info["final_worst_slack"] = timing_metrics["worst_slack"]
        if timing_metrics.get("period_min") is not None:
            info["final_period_min"] = timing_metrics["period_min"]
            info["final_effective_period"] = timing_metrics["period_min"]
        if timing_metrics.get("setup_violations") is not None:
            info["final_setup_violations"] = timing_metrics["setup_violations"]
        if timing_metrics.get("hold_violations") is not None:
            info["final_hold_violations"] = timing_metrics["hold_violations"]

        # Update with power (from report file)
        if power is not None:
            info["final_power"] = power
            info["final_total_power"] = power

        # Update with area (from JSON file)
        if area is not None:
            info["final_area"] = area
            info["final_die_area"] = area

        # Update with target check results
        info["timing_met"] = target_results.get("timing_met")
        info["power_met"] = target_results.get("power_met")
        info["area_met"] = target_results.get("area_met")
        info["all_targets_met"] = target_results.get("all_targets_met", False)
        info["targets_checked"] = target_results.get("targets_checked", [])

        # Write updated info.json
        with open(info_path, 'w') as f:
            json.dump(info, f, indent=2)

        return True

    except Exception as e:
        print(f"Error updating info.json: {e}", file=sys.stderr)
        return False


def main():
    """Main entry point."""
    # Find timing report (use plural naming consistently)
    report_paths = [
        "./reports/6_finish.rpt",
    ]

    report_path = None
    for path in report_paths:
        if os.path.isfile(path):
            report_path = path
            break

    # Find JSON metrics file (use plural naming consistently)
    json_paths = [
        "./logs/6_report.json",
    ]

    json_path = None
    for path in json_paths:
        if os.path.isfile(path):
            json_path = path
            break

    # Read info.json for targets
    info_path = "./info.json"
    info = {}
    if os.path.isfile(info_path):
        try:
            with open(info_path, 'r') as f:
                info = json.load(f)
        except Exception as e:
            print(f"Warning: Could not read info.json: {e}", file=sys.stderr)

    # Extract metrics
    timing_metrics = {}
    power = None
    area = None

    if report_path:
        print(f"Extracting timing metrics from: {report_path}")
        timing_metrics = extract_timing_metrics(report_path)
        print(f"Extracting power from: {report_path}")
        power = extract_power_from_report(report_path)
    else:
        print("Warning: No timing report found (6_finish.rpt)", file=sys.stderr)
        timing_metrics = {"wns": None, "tns": None, "worst_slack": None, "period_min": None}

    if json_path:
        print(f"Extracting area from: {json_path}")
        area = extract_area_from_json(json_path)
    else:
        print("Warning: No JSON report found (6_report.json)", file=sys.stderr)

    # Print extracted metrics
    print("\n=== Timing Metrics (from reports/6_finish.rpt) ===")
    print(f"  worst_slack: {timing_metrics.get('worst_slack')}")
    print(f"  period_min: {timing_metrics.get('period_min')}")
    print(f"  wns: {timing_metrics.get('wns')}")
    print(f"  tns: {timing_metrics.get('tns')}")

    print("\n=== Power (from reports/6_finish.rpt) ===")
    print(f"  total_power: {power}")

    print("\n=== Area (from logs/6_report.json) ===")
    print(f"  die_area: {area}")

    # Print targets
    print("\n=== Targets (from info.json) ===")
    if "target_effective_period" in info:
        print(f"  target_effective_period: {info['target_effective_period']}")
    if "target_power" in info:
        print(f"  target_power: {info['target_power']}")
    if "target_area" in info:
        print(f"  target_area: {info['target_area']}")

    # Check targets
    target_results = check_targets(info, timing_metrics, power, area)

    # Print target check results
    print("\n=== Target Check Results ===")
    print(f"  targets_checked: {target_results['targets_checked']}")
    if target_results["timing_met"] is not None:
        actual_period = timing_metrics.get('period_min')
        target_period = info.get('target_effective_period')
        status = "MET" if target_results["timing_met"] else "NOT MET"
        print(f"  timing: {status} (actual={actual_period}, target={target_period})")
    if target_results["power_met"] is not None:
        target_power_val = info.get('target_power')
        status = "MET" if target_results["power_met"] else "NOT MET"
        print(f"  power: {status} (actual={power}, target={target_power_val})")
    if target_results["area_met"] is not None:
        target_area_val = info.get('target_area')
        status = "MET" if target_results["area_met"] else "NOT MET"
        print(f"  area: {status} (actual={area}, target={target_area_val})")

    # Update info.json
    if update_info_json(info_path, timing_metrics, power, area, target_results):
        print(f"\nUpdated {info_path} with final metrics")
    else:
        print(f"\nFailed to update {info_path}", file=sys.stderr)

    # Print final summary
    if target_results["all_targets_met"]:
        print("\n[SUCCESS] All specified targets met!")
    else:
        failed = []
        if target_results["timing_met"] is False:
            failed.append("timing")
        if target_results["power_met"] is False:
            failed.append("power")
        if target_results["area_met"] is False:
            failed.append("area")
        if failed:
            print(f"\n[FAIL] Targets not met: {', '.join(failed)}")
        else:
            print("\n[FAIL] Could not verify targets (missing metrics)")


if __name__ == "__main__":
    main()
