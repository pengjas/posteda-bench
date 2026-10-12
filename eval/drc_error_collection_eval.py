
import pya
import klayout.db as db
import json
import os

report_db = pya.ReportDatabase()
report_db.load("./report.lyrdb")

drc_errors = {}
drc_errors["description"] = report_db.description
drc_errors["top-cell"] = report_db.top_cell_name
drc_errors["drc errors"] = []   # 存放所有错误（完整）

# 按 (category, cell_name) 分组，保存每组的完整列表
grouped = {}  # key: (cat_name, cell_name) -> list of drc_error dicts

# 同步统计每组总数（也可直接用 len(grouped[key])）
category_cell_count = {}  # key: (cat_name, cell_name) -> int

for item in report_db.each_item():
    category = report_db.category_by_id(item.category_id())
    cat_name = category.name()
    if cat_name == "V5.M6.AUX.2":
        continue
    cat_desc = category.description

    cell = report_db.cell_by_id(item.cell_id())
    cell_name = cell.name()

    # 构造 drc_error dict
    drc_error = {
        "category": cat_name,
        "description": cat_desc,
        "values": [],
        "cell_name": cell_name,
        "parent": None,
        "trans": None
    }

    # 收集 values（注意在循环外初始化列表）
    values_list = []
    for value in item.each_value():
        values_list.append(value.to_s())
    drc_error["values"] = values_list

    # 尝试获取 parent / trans（如果有多个 reference，记录最后一个；按需可改为记录列表）
    for ref in cell.each_reference():
        parent_id = ref.parent_cell_id
        parent_cell = report_db.cell_by_id(parent_id)
        drc_error["parent"] = parent_cell.name()
        drc_error["trans"] = ref.trans.to_s()

    # 将错误加入总列表
    drc_errors["drc errors"].append(drc_error)

    # 按组保存完整记录
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
                # 这里可以自定义显示格式，当前直接打印字典
                print(f"[{i}] {err}")
            else:
                # 超过 10 条，停止显示具体项，打印剩余说明
                remaining = total - 10
                print(f"... Only first 10 errors shown; {remaining} more not displayed.")
                break
        print("=======================================")

final_errors_num = len(drc_errors["drc errors"])
info_path = "./info.json"

# 如果 info.json 已存在，先读入再更新；否则创建新字典
if os.path.exists(info_path):
    with open(info_path, "r", encoding="utf-8") as f:
        try:
            info_data = json.load(f)
        except json.JSONDecodeError:
            info_data = {}
else:
    info_data = {}

info_data["final_errors_num"] = final_errors_num

with open(info_path, "w", encoding="utf-8") as f:
    json.dump(info_data, f, indent=4, ensure_ascii=False)
