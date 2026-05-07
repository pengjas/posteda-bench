
import pya
import klayout.db as db

report_db = pya.ReportDatabase()
report_db.load("./6_drc_count.rpt")

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


if len(drc_errors["drc errors"]) == 0:
    print("Congrats! No DRC errors found. So make a brief summary and end.")

else:
    print("Description:", drc_errors["description"])
    print("=======================================")
    print("Top Cell:", drc_errors["top-cell"])
    print("=======================================")
    # for i in range(len(drc_errors["drc errors"])):
    #     print(drc_errors["drc errors"][i])
    #     print("=======================================")
    for key, errors in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
        cat, cell = key
        total = len(errors)
        print(f"--- {cat}  |  cell: {cell} ---")
        for i, err in enumerate(errors):
            if i < 10:
                print(f"[{i}] {err}")
            else:
                remaining = total - 10
                print(f"... Only first 10 errors shown; {remaining} more not displayed.")
                break
        print("=======================================")
