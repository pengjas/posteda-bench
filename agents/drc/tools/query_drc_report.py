"""Tool for querying DRC reports."""

import os
import sys
from langchain_core.tools import tool

from .utils import run_local_code


@tool
def query_drc_report(
    gds_file_path: str,
    rerun_drc_check: bool = True,
    filter_by_categories: list[str] | None = None,
    view_error_ranges: list[list[int]] | None = None
):
    """
    **PURPOSE:** Runs a DRC check to generate a new violation report OR queries a previously
    generated report. This is used to validate design changes and navigate detailed error lists.

    **WHEN TO USE (WORKFLOW):** This tool is designed for a multi-step inspection process:

    1.  **Initial Check (rerun_drc_check=True):**
        -   Use this mode immediately after modifying a GDS file. This executes a full DRC check.
        -   The output provides a detailed report of the **first 10 errors** for each violation
            category, along with the total error count for that category.
        -   Review this initial "page" of errors to assess the situation.
        -   **Example Call:** `query_drc_report(gds_file_path="/path/to/new.gds")`

    2.  **Navigating Further Errors (rerun_drc_check=False):**
        -   You only need this mode if a category contains **more than 10 errors** and you want
            to inspect violations beyond the initial set shown in the first step.
        -   This reads the **last generated report** without re-running the DRC.
        -   Use the filtering parameters to specify exactly which "page" of errors you need to see.
        -   **Example Scenario:** The initial check for category "M1.S.1" shows 10 errors but
            the total count is 50. To see the next set of errors, you would call:
            `query_drc_report(gds_file_path="/path/to/new.gds", rerun_drc_check=False, filter_by_categories=["M1.S.1"], view_error_ranges=[(10, 20)])`

    **PARAMETERS:**
    -   `gds_file_path` (str): The absolute path to the GDS file being analyzed.
    -   `rerun_drc_check` (bool): Controls the tool's behavior.
        -   `True` (Default): Executes a new DRC run. The output shows the **first 10 errors**
            per category. Filtering parameters are ignored.
        -   `False`: Skips the DRC run and reads the most recent report. Use this for
            navigating to different error indices.
    -   `filter_by_categories` (list[str] | None): A list of error category names you want to
        inspect further. **Only used when `rerun_drc_check=False`**.
    -   `view_error_ranges` (list[list[int]] | None): A list of (start, end) tuples.
        Specifies the precise index range of errors to view for the corresponding categories.
        **Only used when `rerun_drc_check=False`**.

    **OUTPUT:**
    -   If `rerun_drc_check=True`: Returns a report showing the **first 10 violations** for
        each error category, along with the total count for each category.
    -   If `rerun_drc_check=False`: Returns the **detailed, filtered view** of the requested
        DRC violations within the specified index ranges.
    """
    report_path = "./report.lyrdb"
    if rerun_drc_check:
        lydrc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "klayout", "asap7.lydrc")
        run_local_code(command=f"klayout -b -rd in_gds={gds_file_path} -rd report_file={report_path} -r {lydrc_path}", display_output=False)

    code = rf"""
import pya
import klayout.db as db

categories_to_show = {filter_by_categories}
index_ranges_to_show = {view_error_ranges}

try:
    report_db = pya.ReportDatabase()
    report_db.load("./report.lyrdb")
except Exception as e:
    print(f"Error loading report database: {{e}}")
    exit()

grouped = {{}}
categorized_errors = {{}}
total_errors_count = 0

for item in report_db.each_item():
    category = report_db.category_by_id(item.category_id())
    cat_name = category.name()
    if cat_name == "V5.M6.AUX.2":
        continue
    total_errors_count += 1
    cell = report_db.cell_by_id(item.cell_id())
    cell_name = cell.name()

    drc_error = {{
        "category": cat_name,
        "description": category.description,
        "values": [value.to_s() for value in item.each_value()],
        "cell_name": cell_name,
        "parent": None,
        "trans": None
    }}

    for ref in cell.each_reference():
        parent_cell = report_db.cell_by_id(ref.parent_cell_id)
        drc_error["parent"] = parent_cell.name()
        drc_error["trans"] = ref.trans.to_s()

    grouped.setdefault((cat_name, cell_name), []).append(drc_error)
    categorized_errors.setdefault(cat_name, []).append(drc_error)

if total_errors_count == 0:
    print("Congrats! No DRC errors found in the report.")

elif not categories_to_show:
    print("Displaying summary for all errors, grouped by (Category, Cell)...")
    print("=======================================")
    print("Description:", report_db.description)
    print("Top Cell:", report_db.top_cell_name)
    print("=======================================")

    for key, errors in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
        cat, cell = key
        print(f"--- {{cat}}  |  cell: {{cell}} ---")
        for i, err in enumerate(errors[:10]):
            print(f"[{{i}}] {{err}}")
        if len(errors) > 10:
            print(f"... Only first 10 errors shown; {{len(errors) - 10}} more not displayed.")
        print("=======================================")

else:
    print("Displaying a filtered view based on your specifications.")
    print("=================================================================")

    if len(categories_to_show) != len(index_ranges_to_show):
        print(f"Error: Mismatch in list lengths. 'categories_to_show' has {{len(categories_to_show)}} items, "
              f"but 'index_ranges_to_show' has {{len(index_ranges_to_show)}}.")
    else:
        for category_name, index_range in zip(categories_to_show, index_ranges_to_show):
            print(f"--- Querying Category: '{{category_name}}' | Requested Range: {{index_range}} ---")

            if category_name not in categorized_errors:
                print(f"Warning: Category '{{category_name}}' was not found in the DRC report.\\n")
                continue

            errors_in_cat = categorized_errors[category_name]
            total_in_cat = len(errors_in_cat)

            try:
                req_start, req_end = index_range
                if not all(isinstance(i, int) for i in [req_start, req_end]) or req_start < 0:
                    print(f"Error: Invalid index range {{index_range}}. Values must be non-negative integers. Skipping.\\n")
                    continue
                if req_start > req_end:
                    print(f"Error: Invalid index range {{index_range}}. Start index cannot be greater than end index. Skipping.\\n")
                    continue
            except (TypeError, ValueError):
                print(f"Error: Index range {{index_range}} is not a valid tuple (start, end). Skipping.\\n")
                continue

            if req_start >= total_in_cat:
                print(f"Warning: Start index {{req_start}} is out of bounds. This category only has {{total_in_cat}} errors (indices 0 to {{total_in_cat-1}}). Skipping.\\n")
                continue

            final_start = req_start
            final_end = req_end
            warnings = []

            if final_start == final_end:
                final_end = final_start + 1
                warnings.append(f"start and end are identical; showing single item at index {{final_start}}")

            if final_end - final_start > 10:
                final_end = final_start + 10
                warnings.append("exceeded 10-item limit")

            if final_end > total_in_cat:
                final_end = total_in_cat
                warnings.append("exceeded total available errors")

            if warnings:
                print(f"Info: Requested range ({{req_start}}, {{req_end}}) was adjusted because it {{', and '.join(warnings)}}.")

            errors_to_show = errors_in_cat[final_start:final_end]

            print(f"  Displaying errors with index {{final_start}} to {{final_end - 1}} (Found {{len(errors_to_show)}} of {{total_in_cat}} total).")

            for i, err in enumerate(errors_to_show):
                original_index = final_start + i
                print(f"  [{{original_index}}] {{err}}")
            print("-----------------------------------------------------------------\\n")
"""
    result_dir = os.environ.get("AGENT_RESULT_DIR")
    if not result_dir:
        error_msg = "CRITICAL ERROR: AGENT_RESULT_DIR environment variable not set. Cannot determine where to write tool script."
        print(error_msg, file=sys.stderr)
        return error_msg

    remote_path = os.path.join(result_dir, "drc_collection.py")
    return run_local_code(code, local_path=remote_path, command=f"klayout -b -r {remote_path}")
