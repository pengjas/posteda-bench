
import pya
import klayout.db as db
import glob
import json

# Find 6_drc_count.rpt in current directory
rpt_files = glob.glob("6_drc_count.rpt")
if not rpt_files:
    # Try searching in current path recursively
    rpt_files = glob.glob("**/6_drc_count.rpt", recursive=True)

if not rpt_files:
    raise FileNotFoundError("6_drc_count.rpt not found in current path")

rpt_path = rpt_files[0]

report_db = pya.ReportDatabase()
report_db.load(rpt_path)

drc_errors = {}
drc_errors["description"] = report_db.description
drc_errors["top-cell"] = report_db.top_cell_name
drc_errors["drc errors"] = []

grouped = {}  # key: (cat_name, cell_name) -> list of drc_error dicts

category_cell_count = {}  # key: (cat_name, cell_name) -> int

for item in report_db.each_item():
    category = report_db.category_by_id(item.category_id())
    cat_name = category.name()
    cat_desc = category.description

    cell = report_db.cell_by_id(item.cell_id())
    cell_name = cell.name()

    drc_error = {
        "category": cat_name,
        "description": cat_desc,
        "values": [],
        "cell_name": cell_name,
        "parent": None,
        "trans": None
    }

    values_list = []
    for value in item.each_value():
        values_list.append(value.to_s())
    drc_error["values"] = values_list

    for ref in cell.each_reference():
        parent_id = ref.parent_cell_id
        parent_cell = report_db.cell_by_id(parent_id)
        drc_error["parent"] = parent_cell.name()
        drc_error["trans"] = ref.trans.to_s()

    drc_errors["drc errors"].append(drc_error)

    key = (cat_name, cell_name)
    grouped.setdefault(key, []).append(drc_error)
    category_cell_count[key] = category_cell_count.get(key, 0) + 1

# Initialize cell name and errors number
init_cell_name = drc_errors["top-cell"]
init_errors_num = len(drc_errors["drc errors"])

# Update info.json with the correct values
try:
    with open("info.json", "r") as f:
        info_data = json.load(f)

    info_data["init_cell_name"] = init_cell_name
    info_data["init_errors_num"] = init_errors_num

    with open("info.json", "w") as f:
        json.dump(info_data, f, indent=4)
except FileNotFoundError:
    # If info.json doesn't exist, create it
    info_data = {
        "init_cell_name": init_cell_name,
        "init_errors_num": init_errors_num
    }
    with open("info.json", "w") as f:
        json.dump(info_data, f, indent=4)

# Write output to prompt.txt
with open("prompt.txt", "w") as f:
    f.write("the input gds file is ./top_cell.gds\n")

    if len(drc_errors["drc errors"]) == 0:
        f.write("Congrats! No DRC errors found. So make a brief summary and end.\n")
    else:
        f.write(f"Description: {drc_errors['description']}\n")
        f.write("=======================================\n")
        f.write(f"Top Cell: {drc_errors['top-cell']}\n")
        f.write("=======================================\n")
        # for i in range(len(drc_errors["drc errors"])):
        #     f.write(str(drc_errors["drc errors"][i]) + "\n")
        #     f.write("=======================================\n")
        for key, errors in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
            cat, cell = key
            total = len(errors)
            f.write(f"--- {cat}  |  cell: {cell} ---\n")
            for i, err in enumerate(errors):
                if i < 10:
                    f.write(f"[{i}] {err}\n")
                else:
                    remaining = total - 10
                    f.write(f"... Only first 10 errors shown; {remaining} more not displayed.\n")
                    break
            f.write("=======================================\n")
