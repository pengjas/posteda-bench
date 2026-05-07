"""Tool for changing shapes in a GDS layout."""

import os
import sys
from langchain_core.tools import tool

from .utils import run_local_code, get_ignore_case
from .layer_config import layer_relation


@tool
def change_shape(input_gds: str, target_shape_level: str, target_shape_index: int, cell_name: str, type: str, direct_layers: list, context_layers: list, query_pts: list, shape_type: str, solutions: list, change_in_cell: bool):
    """
    Modifies or deletes a specific shape identified by a prior 'query_with_pts' call. This allows for granular changes to polygons, paths, or boxes.

    IMPORTANT: To ensure you are modifying the correct shape, the parameters 'cell_name', 'type', 'direct_layers', 'context_layers', and 'query_pts' MUST EXACTLY MATCH the 'query_with_pts' call that identified the shape.

    Args:
        input_gds (str): The file path to the GDS layout to be modified.
        target_shape_level (str): The level ("first", "second", or "third") from the query output where the target shape is located.
        target_shape_index (int): The zero-based index of the target shape within the list for that level.
        cell_name (str): Must match the prior query.
        type (str): Must match the prior query.
        direct_layers (list): Must match the prior query.
        context_layers (list): Must match the prior query.
        query_pts (list): Must match the prior query.
        shape_type (str): The type of modification to perform. This determines the required format for the 'solutions' parameter.
                        Valid values:
                        - "polygon": Replaces the shape's points. `solutions` must be a list of DPoints, e.g., [(x1,y1), (x2,y2), ...].
                        - "delete": Deletes the shape. `solutions` can be an empty list [].
                        - "box_Center": Reshapes a box using its center, width, and height. `solutions` must be [(center_x, center_y), width, height].
                        - "box_Points": Reshapes a box using its lower-left and upper-right corner points. `solutions` must be [(llx, lly), (urx, ury)].
                        - "path_Points": Reshapes a path using a new width and spine points. `solutions` must be [width, [(x1,y1), (x2,y2), ...]].
        solutions (list): A list containing the geometric data for the modification, formatted according to the 'shape_type'. And the values for coordinates are relative to the shape's cell coordinate system, instead of the global coordinate system.
        change_in_cell (bool): Controls the scope of the shape modification.
                            - When modifying the top-level cell: This parameter should always be set to True.
                            - When modifying a sub-cell within the top cell: Set to True to apply the change globally. This alters the sub-cell's master blueprint, affecting all instances of it throughout the design. Set to False to apply the change locally. This creates a new, modified copy of the sub-cell for this specific instance only, leaving the original blueprint and all other instances untouched.

    Returns:
        str: A confirmation message indicating success or failure of the shape modification.
    """
    d_layers = []
    c_layers = []
    for ly in direct_layers:
        d_layers.extend(get_ignore_case(layer_relation, ly, []))
    for ly in context_layers:
        c_layers.extend(get_ignore_case(layer_relation, ly, []))

    output_gds = "./modified.gds"
    lyp_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "klayout", "asap7.lyp")
    code = rf'''
import pdb
import pya
import klayout.db as db
import math

import xml.etree.ElementTree as ET

def parse_layer_name(s):
    s = s.strip()

    if " - " in s:
        name_part, ld_part = s.split(" - ", 1)
        layer_id, datatype = ld_part.split("/")
        name_part = name_part.strip()
    else:
        parts = s.split("/")
        if len(parts) >= 2:
            layer_id = parts[0].strip()
            rest = parts[1].strip()
            datatype_parts = rest.split(None, 1)
            if len(datatype_parts) >= 2:
                datatype = datatype_parts[0].strip()
                name_part = datatype_parts[1].strip()
            else:
                datatype = datatype_parts[0].strip()
                name_part = ""
        else:
            return {{
                "name": s,
                "layer_id": 0,
                "datatype": 0
            }}

    return {{
        "name": name_part,
        "layer_id": int(layer_id),
        "datatype": int(datatype)
    }}

def parse_lyp(path):
    tree = ET.parse(path)
    root = tree.getroot()
    mapping = []
    for prop in root.findall("properties"):
        name = prop.findtext("name")
        if name:
            try:
                info = parse_layer_name(name)
                mapping.append(info)
            except Exception as e:
                print(f"Failed to parse name: '{{name}}', error: {{e}}")
                mapping.append({{
                    "name": name,
                    "layer_id": 0,
                    "datatype": 0
                }})
    return mapping


def point_on_edge_with_tol(edge: db.DEdge, pt: db.DPoint, tol: float = 1e-6) -> bool:
    p1, p2 = edge.p1, edge.p2
    vx, vy = p2.x - p1.x, p2.y - p1.y
    wx, wy = pt.x - p1.x, pt.y - p1.y

    cross = abs(vx * wy - vy * wx)
    norm = math.hypot(vx, vy)
    if norm == 0:
        return False
    dist = cross / norm
    if dist > tol:
        return False

    dot = vx * wx + vy * wy
    if dot < 0:
        return False
    if dot > vx * vx + vy * vy:
        return False
    return True

def edge_contains_with_tol(edge1: db.DEdge, edge2: db.DEdge, tol: float = 1e-6) -> bool:
    return (point_on_edge_with_tol(edge1, edge2.p1, tol) and
            point_on_edge_with_tol(edge1, edge2.p2, tol))


def check_edge_on_shape_boundary(shape_edge: db.DEdge, target_edge: db.DEdge, tolerance: float = 1e-3) -> bool:
    p1_x = round(shape_edge.p1.x, 5)
    p1_y = round(shape_edge.p1.y, 5)
    p2_x = round(shape_edge.p2.x, 5)
    p2_y = round(shape_edge.p2.y, 5)

    shape_edge = db.DEdge(db.DPoint(p1_x, p1_y), db.DPoint(p2_x, p2_y))
    if edge_contains_with_tol(shape_edge, target_edge, tolerance):
        return True
    return False



def check_edge_pair_in_polygon_boundary(dpolygon, edges):
    for target_edge in edges:
        on_boundary = any(check_edge_on_shape_boundary(dpolygon_edge, target_edge) for dpolygon_edge in dpolygon.each_edge())
        if on_boundary:
            return True
    return False

def traverse_layers(layers, target_cell, edges):
    shapes = []
    shapes_info = {{}}
    name = ""
    for lid in layers:
        id, dt = lid
        recursive_iterator = target_cell.begin_shapes_rec(ly.layer(id, dt))
        for shape_container in recursive_iterator:
            shape_in_cell = shape_container.shape()
            if shape_in_cell.is_box() or shape_in_cell.is_polygon() or shape_in_cell.is_path():
                dpolygon_in_cell = shape_in_cell.dpolygon
                absolute_dtrans = shape_container.dtrans()
                absolute_dpolygon = dpolygon_in_cell.transformed(absolute_dtrans)
                if shape_in_cell.cell.is_top():
                    cell_global_dbbox = absolute_dpolygon.bbox()
                else:
                    cell_dbbox = shape_in_cell.cell.dbbox()
                    cell_global_dbbox = cell_dbbox.transformed(absolute_dtrans)
                dpolygon = absolute_dpolygon
                if check_edge_pair_in_polygon_boundary(dpolygon, edges) and (shape_in_cell, (id, dt), absolute_dtrans) not in shapes:
                    shapes_info[(shape_in_cell, (id, dt), absolute_dtrans)] = {{"layer": (id, dt), "dtrans": absolute_dtrans, "cell_global_dbbox": cell_global_dbbox}}
                    shapes.append((shape_in_cell, (id, dt), absolute_dtrans))
    return shapes, shapes_info




def query_with_pts(cell, type, layers: list, query_pts: list):
    target_cell = ly.cell(cell)
    shapes = []

    edges = []
    if type == "edge-pair":
        if len(query_pts) != 2:
            print("For 'edge-pair' type, exactly two edges must be provided.")
            return
        edge1 = query_pts[0]
        edge2 = query_pts[1]
        p_1_1_x, p_1_1_y, p_1_2_x, p_1_2_y = edge1
        p_2_1_x, p_2_1_y, p_2_2_x, p_2_2_y = edge2
        target_edge1 = db.DEdge(db.DPoint(p_1_1_x, p_1_1_y), db.DPoint(p_1_2_x, p_1_2_y))
        target_edge2 = db.DEdge(db.DPoint(p_2_1_x, p_2_1_y), db.DPoint(p_2_2_x, p_2_2_y))
        edges.append(target_edge1)
        edges.append(target_edge2)

    elif type == "polygon":
        for i in range(len(query_pts)):
            point1 = db.DPoint(query_pts[i][0], query_pts[i][1])
            if i == len(query_pts) - 1:
                point2 = db.DPoint(query_pts[0][0], query_pts[0][1])
            else:
                point2 = db.DPoint(query_pts[i+1][0], query_pts[i+1][1])
            edge = db.DEdge(point1, point2)
            edges.append(edge)
    elif type == "edge":
        if len(query_pts) != 1:
            print("For 'edge' type, exactly one edge must be provided.")
            return
        edge = query_pts[0]
        p_1_x, p_1_y, p_2_x, p_2_y = edge
        target_edge = db.DEdge(db.DPoint(p_1_x, p_1_y), db.DPoint(p_2_x, p_2_y))
        edges.append(target_edge)

    else:
        print(f"Unsupported shape type: {{type}}")
        return None
    shapes, shapes_info = traverse_layers(layers, target_cell, edges)
    return shapes, shapes_info



def get_context_of_shapes(shapes, shapes_info, layers, target_cell):
    p1_x = None
    p1_y = None
    p2_x = None
    p2_y = None
    for shape in shapes:
        if shape[0].cell.name == target_cell.name:
            dbbox = shape[0].dbbox()
        else:
            dbbox = shapes_info[shape].get("cell_global_dbbox", None)
        if p1_x is None and p1_y is None and p2_x is None and p2_y is None:
            p1_x = dbbox.p1.x
            p1_y = dbbox.p1.y
            p2_x = dbbox.p2.x
            p2_y = dbbox.p2.y
        else:
            if dbbox.p1.x < p1_x:
                p1_x = dbbox.p1.x
            if dbbox.p1.y < p1_y:
                p1_y = dbbox.p1.y
            if dbbox.p2.x > p2_x:
                p2_x = dbbox.p2.x
            if dbbox.p2.y > p2_y:
                p2_y = dbbox.p2.y
    context = db.DBox(db.DPoint(p1_x, p1_y), db.DPoint(p2_x, p2_y))
    shapes_in_dbbox = []
    shapes_in_dbbox_info = {{}}
    for layer in layers:
        id, dt = layer
        lid = ly.layer(id, dt)
        it = target_cell.begin_shapes_rec_overlapping(lid, context)
        for shape_container in it:
            shape_in_cell = shape_container.shape()
            if shape_in_cell.is_box() or shape_in_cell.is_polygon() or shape_in_cell.is_path():
                absolute_dtrans = shape_container.dtrans()
                dpolygon_in_cell = shape_in_cell.dpolygon
                absolute_dpolygon = dpolygon_in_cell.transformed(absolute_dtrans)

                if (shape_in_cell, (id, dt), absolute_dtrans) in shapes and absolute_dtrans == shapes_info[(shape_in_cell, (id, dt), absolute_dtrans)]["dtrans"]:
                    continue
                shapes_in_dbbox.append((shape_in_cell, (id, dt), absolute_dtrans))
                shapes_in_dbbox_info[(shape_in_cell, (id, dt), absolute_dtrans)] = {{"layer": (id, dt), "dtrans": absolute_dtrans, "dpolygon": absolute_dpolygon}}



    new_p2_y = p2_y
    new_p1_y = p1_y
    new_p1_x = p1_x
    new_p2_x = p2_x
    temp_p2_y = p2_y
    temp_p1_y = p1_y
    temp_p1_x = p1_x
    temp_p2_x = p2_x
    increment = 0.05
    directions_to_extend = ["top", "bottom", "left", "right"]
    target_dbbox = target_cell.dbbox()
    target_p1_x = target_dbbox.p1.x
    target_p1_y = target_dbbox.p1.y
    target_p2_x = target_dbbox.p2.x
    target_p2_y = target_dbbox.p2.y
    shapes_outside = []
    shapes_outside_info = {{}}
    stop_all = False
    while "top" in directions_to_extend:
        temp_p2_y += increment
        if temp_p2_y >= target_p2_y:
            directions_to_extend.remove("top")
            new_p2_y = target_p2_y
            break
        for layer in layers:
            id, dt = layer
            lid = ly.layer(id, dt)
            it = target_cell.begin_shapes_rec_overlapping(lid, db.DBox(db.DPoint(p1_x, p1_y), db.DPoint(p2_x, temp_p2_y)))
            for shape_container in it:
                shape_in_cell = shape_container.shape()
                absolute_dtrans = shape_container.dtrans()
                if shape_in_cell.is_box() or shape_in_cell.is_polygon() or shape_in_cell.is_path():
                    if (shape_in_cell, (id, dt), absolute_dtrans) in shapes or (shape_in_cell, (id, dt), absolute_dtrans) in shapes_in_dbbox:
                        continue
                    stop_all = True
                    directions_to_extend.remove("top")
                    new_p2_y = temp_p2_y
                    break
            if stop_all:
                break
        if stop_all:
            break

    stop_all = False
    while "bottom" in directions_to_extend:
        temp_p1_y -= increment
        if temp_p1_y <= target_p1_y:
            directions_to_extend.remove("bottom")
            new_p1_y = target_p1_y
            break
        for layer in layers:
            id, dt = layer
            lid = ly.layer(id, dt)
            it = target_cell.begin_shapes_rec_overlapping(lid, db.DBox(db.DPoint(p1_x, temp_p1_y), db.DPoint(p2_x, p2_y)))
            for shape_container in it:
                shape_in_cell = shape_container.shape()
                absolute_dtrans = shape_container.dtrans()
                if shape_in_cell.is_box() or shape_in_cell.is_polygon() or shape_in_cell.is_path():
                    if (shape_in_cell, (id, dt), absolute_dtrans) in shapes or (shape_in_cell, (id, dt), absolute_dtrans) in shapes_in_dbbox:
                        continue
                    stop_all = True
                    directions_to_extend.remove("bottom")
                    new_p1_y = temp_p1_y
                    break
            if stop_all:
                break
        if stop_all:
            break

    stop_all = False
    while "left" in directions_to_extend:
        temp_p1_x -= increment
        if temp_p1_x <= target_p1_x:
            directions_to_extend.remove("left")
            new_p1_x = target_p1_x
            break
        for layer in layers:
            id, dt = layer
            lid = ly.layer(id, dt)
            it = target_cell.begin_shapes_rec_overlapping(lid, db.DBox(db.DPoint(temp_p1_x, p1_y), db.DPoint(p2_x, p2_y)))
            for shape_container in it:
                shape_in_cell = shape_container.shape()
                absolute_dtrans = shape_container.dtrans()
                if shape_in_cell.is_box() or shape_in_cell.is_polygon() or shape_in_cell.is_path():
                    if (shape_in_cell, (id, dt), absolute_dtrans) in shapes or (shape_in_cell, (id, dt), absolute_dtrans) in shapes_in_dbbox:
                        continue
                    stop_all = True
                    directions_to_extend.remove("left")
                    new_p1_x = temp_p1_x
                    break
            if stop_all:
                break
        if stop_all:
            break

    stop_all = False

    while "right" in directions_to_extend:
        temp_p2_x += increment
        if temp_p2_x >= target_p2_x:
            directions_to_extend.remove("right")
            new_p2_x = target_p2_x
            break
        for layer in layers:
            id, dt = layer
            lid = ly.layer(id, dt)
            it = target_cell.begin_shapes_rec_overlapping(lid, db.DBox(db.DPoint(p1_x, p1_y), db.DPoint(temp_p2_x, p2_y)))
            for shape_container in it:
                shape_in_cell = shape_container.shape()
                absolute_dtrans = shape_container.dtrans()
                if shape_in_cell.is_box() or shape_in_cell.is_polygon() or shape_in_cell.is_path():
                    if (shape_in_cell, (id, dt), absolute_dtrans) in shapes or (shape_in_cell, (id, dt), absolute_dtrans) in shapes_in_dbbox:
                        continue
                    stop_all = True
                    directions_to_extend.remove("right")
                    new_p2_x = temp_p2_x
                    break
            if stop_all:
                break
        if stop_all:
            break

    context_outside = db.DBox(db.DPoint(new_p1_x, new_p1_y), db.DPoint(new_p2_x, new_p2_y))
    for layer in layers:
        id, dt = layer
        lid = ly.layer(id, dt)
        it = target_cell.begin_shapes_rec_overlapping(lid, context_outside)
        for shape_container in it:
            shape_in_cell = shape_container.shape()
            if shape_in_cell.is_box() or shape_in_cell.is_polygon() or shape_in_cell.is_path():
                absolute_dtrans = shape_container.dtrans()
                dpolygon_in_cell = shape_in_cell.dpolygon
                absolute_dpolygon = dpolygon_in_cell.transformed(absolute_dtrans)
                if (shape_in_cell, (id, dt), absolute_dtrans) in shapes or (shape_in_cell, (id, dt), absolute_dtrans) in shapes_in_dbbox:
                    continue
                shapes_outside.append((shape_in_cell, (id, dt), absolute_dtrans))
                shapes_outside_info[(shape_in_cell, (id, dt), absolute_dtrans)] = {{"layer": (id, dt), "dtrans": absolute_dtrans, "dpolygon": absolute_dpolygon}}

    return shapes_in_dbbox, shapes_in_dbbox_info, shapes_outside, shapes_outside_info, context, context_outside


def output_definition(i, shape, shapes_info, cell_name, layer_id, dtrans, bbox=False):
    cell = shape.cell
    output_text = "==============================\\n"
    output_text += f"Shape {{i}}: "
    if cell.name == cell_name:
        if bbox:
            output_text += f"It belongs to the top cell: {{cell.name}}(boundary box: {{shapes_info[(shape, layer_id, dtrans)].get('cell_global_dbbox', None)}}, dtrans: {{dtrans}}). Layer: {{layer_to_name.get(layer_id, layer_id)}}{{layer_id}}. "
        else:
            output_text += f"It belongs to the top cell: {{cell.name}}(dtrans: {{dtrans}}). Layer: {{layer_to_name.get(layer_id, layer_id)}}{{layer_id}}. "
    else:
        if bbox:
            output_text += f"It belongs to the sub-cell: {{cell.name}}(boundary box: {{shapes_info[(shape, layer_id, dtrans)].get('cell_global_dbbox', None)}}, dtrans: {{dtrans}}). Layer: {{layer_to_name.get(layer_id, layer_id)}}{{layer_id}}. "
        else:
            output_text += f"It belongs to the sub-cell: {{cell.name}}(dtrans: {{dtrans}}). Layer: {{layer_to_name.get(layer_id, layer_id)}}{{layer_id}}. "
    if shape.is_box():
        output_text += "It is a box. "
        if shape.cell.name != cell_name:
            dbox_in_cell = shape.dbox
            dbox_in_global = dbox_in_cell.transformed(dtrans)
            output_text += f"Center in global: ({{round(dbox_in_global.center().x, 5)}}, {{round(dbox_in_global.center().y, 5)}}), Left-Bottom in global: ({{round(dbox_in_global.p1.x, 5)}}, {{round(dbox_in_global.p1.y, 5)}}), Right-Top in global: ({{round(dbox_in_global.p2.x, 5)}}, {{round(dbox_in_global.p2.y, 5)}}). "
        dcenter = shape.box_dcenter
        dwidth = round(shape.box_dwidth, 5)
        dheight = round(shape.box_dheight, 5)
        dleft_bottom = shape.box_dp1
        dright_top = shape.box_dp2
        output_text += f"Center in {{shape.cell.name}} cell: ({{round(dcenter.x, 5)}}, {{round(dcenter.y, 5)}}), Left-Bottom in {{shape.cell.name}} cell: ({{round(dleft_bottom.x, 5)}}, {{round(dleft_bottom.y, 5)}}), Right-Top in {{shape.cell.name}} cell: ({{round(dright_top.x, 5)}}, {{round(dright_top.y, 5)}}). Width: {{dwidth}}, Height: {{dheight}}. "
    elif shape.is_polygon():
        output_text += "It is a polygon. "
        if shape.cell.name != cell_name:
            dpolygon_in_cell = shape.dpolygon
            dpolygon_in_global = dpolygon_in_cell.transformed(dtrans)
            output_text += f"Outline Points in global: {{dpolygon_in_global.to_s()}}. "

        output_text += f"Outline Points in {{shape.cell.name}} cell: {{shape.dpolygon.to_s()}}. "
    elif shape.is_path():
        output_text += "It is a path. "
        if shape.cell.name != cell_name:
            dpath_in_cell = shape.dpath
            dpath_in_global = dpath_in_cell.transformed(dtrans)
            output_text += f" Spine Points in global: "
            for p in dpath_in_global.each_point():
                output_text += f" ({{round(p.x, 5)}}, {{round(p.y, 5)}})"

        output_text += f"Spine Points in {{shape.cell.name}} cell: "
        for p in shape.dpath.each_point():
            output_text += f" ({{round(p.x, 5)}}, {{round(p.y, 5)}})"
        dwidth = round(shape.dpath.width, 5)
        output_text += f". Width: {{dwidth}}."
    output_text += "\\n"
    return output_text


def get_query_bbox(type, query_pts):
    edges = []
    bbox = None
    if type == "edge-pair":
        if len(query_pts) != 2:
            print("For 'edge-pair' type, exactly two edges must be provided.")
            return
        edge1 = query_pts[0]
        edge2 = query_pts[1]
        p_1_1_x, p_1_1_y, p_1_2_x, p_1_2_y = edge1
        p_2_1_x, p_2_1_y, p_2_2_x, p_2_2_y = edge2
        target_edge1 = db.DEdge(db.DPoint(p_1_1_x, p_1_1_y), db.DPoint(p_1_2_x, p_1_2_y))
        target_edge2 = db.DEdge(db.DPoint(p_2_1_x, p_2_1_y), db.DPoint(p_2_2_x, p_2_2_y))
        edges.append(target_edge1)
        edges.append(target_edge2)
        p1_x = min(target_edge1.p1.x, target_edge1.p2.x, target_edge2.p1.x, target_edge2.p2.x)
        p1_y = min(target_edge1.p1.y, target_edge1.p2.y, target_edge2.p1.y, target_edge2.p2.y)
        p2_x = max(target_edge1.p1.x, target_edge1.p2.x, target_edge2.p1.x, target_edge2.p2.x)
        p2_y = max(target_edge1.p1.y, target_edge1.p2.y, target_edge2.p1.y, target_edge2.p2.y)
        bbox = db.DBox(db.DPoint(p1_x, p1_y), db.DPoint(p2_x, p2_y))

    elif type == "polygon":
        for i in range(len(query_pts)):
            point1 = db.DPoint(query_pts[i][0], query_pts[i][1])
            if i == len(query_pts) - 1:
                point2 = db.DPoint(query_pts[0][0], query_pts[0][1])
            else:
                point2 = db.DPoint(query_pts[i+1][0], query_pts[i+1][1])
            edge = db.DEdge(point1, point2)
            edges.append(edge)
        p1_x = min(pt[0] for pt in query_pts)
        p1_y = min(pt[1] for pt in query_pts)
        p2_x = max(pt[0] for pt in query_pts)
        p2_y = max(pt[1] for pt in query_pts)
        bbox = db.DBox(db.DPoint(p1_x, p1_y), db.DPoint(p2_x, p2_y))

    elif type == "edge":
        if len(query_pts) != 1:
            print("For 'edge' type, exactly one edge must be provided.")
            return
        edge = query_pts[0]
        p_1_x, p_1_y, p_2_x, p_2_y = edge
        target_edge = db.DEdge(db.DPoint(p_1_x, p_1_y), db.DPoint(p_2_x, p_2_y))
        edges.append(target_edge)
        p1_x = min(target_edge.p1.x, target_edge.p2.x)
        p1_y = min(target_edge.p1.y, target_edge.p2.y)
        p2_x = max(target_edge.p1.x, target_edge.p2.x)
        p2_y = max(target_edge.p1.y, target_edge.p2.y)
        bbox = db.DBox(db.DPoint(p1_x, p1_y), db.DPoint(p2_x, p2_y))
        if bbox.width() == 0:
            width = 0.1 * bbox.height() if bbox.height() != 0 else 0.1
            bbox.p2.x += width
        if bbox.height() == 0:
            height = 0.1 * bbox.width() if bbox.width() != 0 else 0.1
            bbox.p2.y += height

    return bbox

def calculate_distance(dbox, dpolygon) -> float:
    y_top = dbox.p2.y
    y_bottom = dbox.p1.y
    x_left = dbox.p1.x
    x_right = dbox.p2.x
    dbbox_dpolygon = dpolygon.bbox()
    y_top_poly = dbbox_dpolygon.p2.y
    y_bottom_poly = dbbox_dpolygon.p1.y
    x_left_poly = dbbox_dpolygon.p1.x
    x_right_poly = dbbox_dpolygon.p2.x
    distance = 0.0
    location = None
    if y_bottom_poly > y_top:
        distance = y_bottom_poly - y_top
        location = "above"
    if y_top_poly < y_bottom:
        if distance == 0.0 or (y_bottom - y_top_poly) > distance:
            distance = y_bottom - y_top_poly
            location = "below"
    if x_left_poly > x_right:
        if distance == 0.0 or (x_left_poly - x_right) > distance:
            distance = x_left_poly - x_right
            location = "right"
    if x_right_poly < x_left:
        if distance == 0.0 or (x_left - x_right_poly) > distance:
            distance = x_left - x_right_poly
            location = "left"
    return distance


def sort_shapes_by_proximity(shapes, shapes_info, shapes_in_dbbox, shapes_in_dbbox_info, layers, type, query_pts, target_cell):
    query_bbox = get_query_bbox(type, query_pts)
    if query_bbox is None:
        print("Cannot get query bbox.")
        return shapes_in_dbbox, shapes_in_dbbox_info
    sorted_shapes_in_dbbox = []
    for layer in layers:
        id, dt = layer
        lid = ly.layer(id, dt)
        it = target_cell.begin_shapes_rec_overlapping(lid, query_bbox)
        for shape_container in it:
            shape_in_cell = shape_container.shape()
            if shape_in_cell.is_box() or shape_in_cell.is_polygon() or shape_in_cell.is_path():
                absolute_dtrans = shape_container.dtrans()
                if (shape_in_cell, (id, dt), absolute_dtrans) in shapes_in_dbbox:
                    sorted_shapes_in_dbbox.append((shape_in_cell, (id, dt), absolute_dtrans))

    shapes_in_dbbox_distance = {{}}
    for shape in shapes_in_dbbox:
        if shape not in sorted_shapes_in_dbbox:
            dpolygon = shapes_in_dbbox_info[shape]["dpolygon"]
            distance = calculate_distance(query_bbox, dpolygon)
            shapes_in_dbbox_distance[shape] = distance

    sorted_shapes_in_dbbox.extend(sorted(shapes_in_dbbox_distance, key=shapes_in_dbbox_distance.get))
    sorted_shapes_in_dbbox_info = {{}}
    for shape in sorted_shapes_in_dbbox:
        sorted_shapes_in_dbbox_info[shape] = shapes_in_dbbox_info[shape]
    return sorted_shapes_in_dbbox, sorted_shapes_in_dbbox_info

def change_polygon_points(shape, solutions, output_path):
    new_points = [pya.DPoint(pt[0], pt[1]) for pt in solutions]
    new_dpolygon = db.DPolygon(new_points)
    shape.polygon = new_dpolygon
    ly.write(output_path)
    print(f"Polygon points changed and layout saved in {{output_path}}.")

def change_box_size(shape, shape_type, solutions, output_path):
    if shape_type[1] == "Center":
        new_dcenter = pya.DPoint(solutions[0][0], solutions[0][1])
        shape.box_dcenter = new_dcenter
        new_dwidth = solutions[1]
        new_dheight = solutions[2]
        shape.box_dwidth = new_dwidth
        shape.box_dheight = new_dheight
        ly.write(output_path)
        print(f"Box is changed via Center, Width, Height and layout saved in {{output_path}}.")
    elif shape_type[1] == "Points":
        new_dp1 = pya.DPoint(solutions[0][0], solutions[0][1])
        new_dp2 = pya.DPoint(solutions[1][0], solutions[1][1])
        new_box = pya.DBox(new_dp1, new_dp2)
        shape.dbox = new_box
        ly.write(output_path)
        print(f"Box is changed via two corner points and layout saved in {{output_path}}.")

def change_path_width(shape, shape_type, solutions, output_path):
    new_dwidth = solutions[0]
    new_points = solutions[1]
    new_point_list = [pya.DPoint(pt[0], pt[1]) for pt in new_points]
    new_dpath = pya.DPath(new_point_list, new_dwidth)
    shape.dpath = new_dpath
    ly.write(output_path)
    print(f"Path width changed and layout saved in {{output_path}}.")


def change_shape_in_cell(shape, shape_type, solutions, output_gds):
    shape_type = shape_type.split("_")
    if len(shape_type) ==1:
        if shape_type[0] == "polygon":
            return change_polygon_points(shape, solutions, output_gds)
        elif shape_type[0] == "delete":
            print("Deleting the shape...")
            shape.delete()
            ly.write(output_gds)
            print(f"Shape deleted and layout saved in {{output_gds}}.")
            return
    target_shape_type = shape_type[0]
    if target_shape_type == "box":
        return change_box_size(shape, shape_type, solutions, output_gds)
    elif target_shape_type == "path":
        return change_path_width(shape, shape_type, solutions, output_gds)


def change_shape_in_inst(shape, layer_id, dtrans, shape_type, solutions, output_gds):
    for inst in shape.cell.each_parent_inst():
        inst_proxy = inst.child_inst()
        inst_dtrans_as_dcplx = pya.DCplxTrans(inst_proxy.dtrans)
        if inst_dtrans_as_dcplx == dtrans:
            found_inst = inst_proxy

    if not found_inst:
        print("Cannot find the instance containing the target shape.")
        return

    original_cell = found_inst.cell
    i = 0
    while ly.cell(f"{{original_cell.name}}${{i}}"):
        i += 1
    new_cell_name = f"{{original_cell.name}}${{i}}"
    new_cell = ly.create_cell(new_cell_name)
    new_cell.copy_shapes(original_cell)
    if original_cell.child_instances() > 0:
        new_cell.copy_instances(original_cell)

    shape_in_new_cell = None
    layer_index = shape.layer

    for shape_container in new_cell.shapes(layer_index).each():
        if shape_container.polygon == shape.polygon:
            shape_in_new_cell = shape_container
            break

    if not shape_in_new_cell:
        print("Cannot find the target shape in the new cell.")
        return

    change_shape_in_cell(shape_in_new_cell, shape_type, solutions, output_gds)
    found_inst.cell_index = new_cell.cell_index()
    ly.write(output_gds)
    print(f"One instance of {{original_cell.name}} is updated to use new cell '{{new_cell_name}}'. Layout saved in {{output_gds}}.")




def change_shape(target_shape_index, shapes, shape_type, solutions, output_gds, change_in_cell=True):

    target_shape = shapes[target_shape_index]
    shape, layer_id, dtrans = target_shape
    if change_in_cell:
        change_shape_in_cell(shape, shape_type, solutions, output_gds)
    else:
        change_shape_in_inst(shape, layer_id, dtrans, shape_type, solutions, output_gds)


def generate_drc_context_report(level, shapes, shapes_info, context_layers, cell_name):
    output_parts = ["## DRC Error Context Shapes Report"]

    if "first" in level:
        output_parts.append("### Level 1: Directly Involved Shapes")
        if shapes:
            output_parts.append(
                "These are the **primary shapes** directly identified by the DRC error. "
                f"Found **{{len(shapes)}}** shape(s):"
            )
            for i, (shape, layer_id, dtrans) in enumerate(shapes):
                output_parts.append(output_definition(i, shape, shapes_info, cell_name, layer_id, dtrans, bbox=True))
        else:
            output_parts.append("> **Status:** No shapes directly associated with the error were found.")
            return "\\n".join(output_parts)

    shapes_in_dbbox, shapes_in_dbbox_info, shapes_outside, shapes_outside_info, context_outside, dbbox = (None, None, None, None, None, None)
    if shapes and ("second" in level or "third" in level):
        shapes_in_dbbox, shapes_in_dbbox_info, shapes_outside, shapes_outside_info, dbbox, dbbox_outside = get_context_of_shapes(shapes, shapes_info, context_layers, ly.cell(cell_name))

    if "second" in level:
        output_parts.append("\\n### Level 2: Immediate Context (Shapes Inside Bounding Box)")
        if shapes:
            output_parts.append(
                "These are all shapes located within the combined bounding box of the Level 1 shapes' boundary box. "
                f"This provides the **immediate local context**.\\n"
                f"**Boundary Box Analyzed:** `{{dbbox.to_s()}}`"
            )
            if shapes_in_dbbox:
                output_parts.append(f"Found **{{len(shapes_in_dbbox)}}** context shape(s):")
                for i, (shape, layer_id, dtrans) in enumerate(shapes_in_dbbox):
                    output_parts.append(output_definition(i, shape,  shapes_in_dbbox_info, cell_name, layer_id, dtrans))
            else:
                output_parts.append("> **Status:** No other context shapes were found within this bounding box.")
        else:
             output_parts.append("> **Status:** Skipped, as no Level 1 shapes were found.")

    if "third" in level:
        output_parts.append("\\n### Level 3: Neighboring Context (Nearest Shapes Outside Bounding Box)")
        if shapes:
            output_parts.append(
                "These are the nearest shapes found just outside the bounding box, "
                "providing information about **neighboring structures**.\\n"
                f"**Analysis Area:** Immediately surrounding the box `{{dbbox.to_s()}}`: `{{dbbox_outside.to_s()}}`"
            )
            if shapes_outside:
                output_parts.append(f"Found **{{len(shapes_outside)}}** nearest neighboring shape(s):")
                for i, (shape, layer_id, dtrans) in enumerate(shapes_outside):
                    output_parts.append(output_definition(i, shape, shapes_outside_info, cell_name, layer_id, dtrans))
            else:
                output_parts.append("> **Status:** No neighboring shapes were found in the immediate vicinity.")
        else:
            output_parts.append("> **Status:** Skipped, as no Level 1 shapes were found.")

    return "\\n".join(output_parts)


if __name__ == "__main__":

    input_gds = "{input_gds}"
    output_gds = "{output_gds}"
    ly = db.Layout()
    ly.read(input_gds)

    lyp_file = "{lyp_file_path}"
    layers = parse_lyp(lyp_file)

    layer_to_name = {{}}
    for info in layers:
        key = (info['layer_id'], info['datatype'])
        layer_to_name[key] = info['name']

    target_shape_level = "{target_shape_level}"
    target_shape_index = {target_shape_index}
    cell_name = "{cell_name}"
    type = "{type}"
    direct_layers = {d_layers}
    query_pts = {query_pts}
    context_layers = {c_layers}
    shape_type = "{shape_type}"
    solutions = {solutions}
    change_in_cell = {change_in_cell}


    shapes, shapes_info = query_with_pts(cell_name, type, direct_layers, query_pts)

    if shapes and target_shape_level == "first":
        change_shape(target_shape_index, shapes, shape_type, solutions, output_gds, change_in_cell)
    elif shapes and (target_shape_level == "second" or target_shape_level == "third"):
        shapes_in_dbbox, shapes_in_dbbox_info, shapes_outside, shapes_outside_info, context_outside, temp_outside = get_context_of_shapes(shapes, shapes_info, context_layers, ly.cell(cell_name))
        shapes_in_dbbox, shapes_in_dbbox_info = sort_shapes_by_proximity(shapes, shapes_info, shapes_in_dbbox, shapes_in_dbbox_info, context_layers, type, query_pts, ly.cell(cell_name))
        shapes_outside, shapes_outside_info = sort_shapes_by_proximity(shapes, shapes_info, shapes_outside, shapes_outside_info, context_layers, type, query_pts, ly.cell(cell_name))

        if target_shape_level == "second":
            change_shape(target_shape_index, shapes_in_dbbox, shape_type, solutions, output_gds, change_in_cell)
        elif target_shape_level == "third":
            change_shape(target_shape_index, shapes_outside, shape_type, solutions, output_gds, change_in_cell)

    else:
        print(f"No shapes found in cell '{{cell_name}}' matching the query.")

'''
    result_dir = os.environ.get("AGENT_RESULT_DIR")
    if not result_dir:
        error_msg = "CRITICAL ERROR: AGENT_RESULT_DIR environment variable not set. Cannot determine where to write tool script."
        print(error_msg, file=sys.stderr)
        return error_msg

    remote_path = os.path.join(result_dir, "change_shape.py")
    return run_local_code(code, local_path=remote_path, command=f"klayout -b -r {remote_path}")
