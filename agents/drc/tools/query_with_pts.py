"""Tool for querying shapes at specific points in a GDS layout."""

import os
import sys
from typing import List
from langchain_core.tools import tool

from .utils import run_local_code, get_ignore_case
from .layer_config import layer_relation


@tool
def query_with_pts(input_gds: str, level: list, cell_name: str, type: str, direct_layers: list, context_layers: list, query_pts: list, second_level_index_range: List = None, third_level_index_range: List = None):
    """
    Queries a GDS layout file to gather information about shapes related to a specific DRC error. This is the primary tool for 'seeing' the layout.

    It operates in three hierarchical levels, mimicking how a human would zoom in and out:
    - "first": Identifies the core shapes directly causing the DRC violation.
    - "second": Finds all other shapes within the bounding box of the core shapes (immediate context).
    - "third": Finds the nearest neighboring shapes just outside that bounding box (surrounding environment).

    The shapes for "second" and "third" levels are sorted by proximity to the DRC error location; a smaller index means the shape is closer.

    Args:
        input_gds (str): The file path to the input GDS layout that needs to be checked. This will be provided by the user or previous tool in the workflow.
        level (list): A list of strings specifying which observation levels to query.
                      Valid values are "first", "second", "third". You can request multiple levels, e.g., ["first", "second"].
        cell_name (str): The name of the cell where the DRC error is reported (e.g., "gcd").
        type (str): The geometric type of the DRC error query, as indicated in the DRC report.
                    Valid values include "edge-pair", "polygon", "edge".
        direct_layers (list): A list of layer names directly involved in the DRC error. Used for the "first" level query.
                              Available layers: ['Active', 'BOUNDARY', 'Die', 'Dummy', 'GCut', 'Gate', 'LIG', 'LISD', 'LVT', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8', 'M9', 'Nselect', 'P_SUB', 'Pselect', 'SDT', 'SLVT', 'SRAMDRC', 'SRAMVT', 'TEXT', 'V0', 'V1', 'V2', 'V3', 'V4', 'V5', 'V6', 'V7', 'V8', 'V9', 'fin', 'well'].
        context_layers (list): A broader list of layer names to provide context for the "second" and "third" levels.
                               For example, if a spacing error is in "M1", direct_layers could be ["M1"], while context_layers could be ["M1", "M2", "V1"] to see surrounding metals and vias.
        query_pts (list): A list of coordinate points that define the exact location of the DRC error, taken from the DRC report.
            - If type="edge-pair", format as a list of lists: [[x1, y1, x2, y2], [x3, y3, x4, y4]].
            - If type="polygon", format as a list of tuples: [(x1, y1), (x2, y2), ...].
            - If type="edge", format as a list of tuples: [[x1, y1, x2, y2]].
        second_level_index_range (List, optional): A tuple (start, end) specifying the index range of shapes to return for the "second" level context. The shapes are pre-sorted by proximity to the error (lower index is closer). If None, defaults to (0, 10). To query for more shapes, use subsequent ranges, e.g., (10, 20).
        third_level_index_range (List, optional): A tuple (start, end) specifying the index range of shapes to return for the "third" level context. The shapes are pre-sorted by proximity to the error (lower index is closer). If None, defaults to (0, 10). To query for more shapes, use subsequent ranges, e.g., (10, 20).

    Returns:
        str: A formatted string describing the shapes found at the requested levels, including their properties, locations, and parent cells.
    """
    d_layers = []
    c_layers = []
    for ly in direct_layers:
        d_layers.extend(get_ignore_case(layer_relation, ly, []))
    for ly in context_layers:
        c_layers.extend(get_ignore_case(layer_relation, ly, []))

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
        for edge in query_pts:
            if len(edge) != 4:
                print("As for 'edge-pair' type, each edge must be defined by four coordinates: (x1, y1, x2, y2).")
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
        for point in query_pts:
                if len(point) != 2:
                    print("As for 'polygon' type, `query_pts` should be a list of points. Each point must be defined by two coordinates: (x, y). For example: [(x1, y1), (x2, y2), ...]")
                    return
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
        if len(query_pts[0]) != 4:
            print("As for 'edge' type, the edge must be defined by four coordinates: (x1, y1, x2, y2).")
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


def generate_drc_context_report(level, shapes, shapes_info, context_layers, cell_name, type, query_pts, second_level_index_range=None, third_level_index_range=None):
    if second_level_index_range is not None and second_level_index_range[1] - second_level_index_range[0] > 10:
        second_level_index_range = (second_level_index_range[0], second_level_index_range[0] + 10)
    if third_level_index_range is not None and third_level_index_range[1] - third_level_index_range[0] > 10:
        third_level_index_range = (third_level_index_range[0], third_level_index_range[0] + 10)

    if second_level_index_range is None:
        second_level_index_range = (0, 10)
    if third_level_index_range is None:
        third_level_index_range = (0, 10)

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
        sorted_shapes_in_dbbox, sorted_shapes_in_dbbox_info = sort_shapes_by_proximity(shapes, shapes_info, shapes_in_dbbox, shapes_in_dbbox_info, context_layers, type, query_pts, ly.cell(cell_name))
        sorted_shapes_outside, sorted_shapes_outside_info = sort_shapes_by_proximity(shapes, shapes_info, shapes_outside, shapes_outside_info, context_layers, type, query_pts, ly.cell(cell_name))

    if "second" in level:
        output_parts.append("\\n### Level 2: Immediate Context (Shapes Inside Bounding Box)")
        if shapes:
            output_parts.append(
                "These are all shapes located within the combined bounding box of the Level 1 shapes. "
                f"This provides the **immediate local context**.\\n"
                f"**Boundary Box Analyzed:** `{{dbbox.to_s()}}`"
            )
            if sorted_shapes_in_dbbox:
                total_shapes = len(sorted_shapes_in_dbbox)
                start_index = second_level_index_range[0]
                end_index = min(second_level_index_range[1], total_shapes)

                report_msg = (
                    f"Found **{{total_shapes}}** context shape(s). "
                    f"Displaying shapes from index **{{start_index}}** to **{{end_index - 1}}**. A lower index indicates closer proximity to the error region."
                )
                if total_shapes > 10:
                    report_msg += (
                        "\\n> *Note: A maximum of 10 shapes are displayed per query. "
                        "To view other shapes, please query again with a different `second_level_index_range`, for example, `(10, 20)`.*"
                    )
                output_parts.append(report_msg)

                for i, (shape, layer_id, dtrans) in enumerate(sorted_shapes_in_dbbox):
                    if i < start_index or i >= end_index:
                        continue
                    output_parts.append(output_definition(i, shape, sorted_shapes_in_dbbox_info, cell_name, layer_id, dtrans))
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
            if sorted_shapes_outside:
                total_shapes = len(sorted_shapes_outside)
                start_index = third_level_index_range[0]
                end_index = min(third_level_index_range[1], total_shapes)

                report_msg = (
                    f"Found **{{total_shapes}}** nearest neighboring shape(s). "
                    f"Displaying shapes from index **{{start_index}}** to **{{end_index - 1}}**. A lower index indicates closer proximity to the error region."
                )
                if total_shapes > 10:
                    report_msg += (
                        "\\n> *Note: A maximum of 10 shapes are displayed per query. "
                        "To view other shapes, please query again with a different `third_level_index_range`, for example, `(10, 20)`.*"
                    )
                output_parts.append(report_msg)

                for i, (shape, layer_id, dtrans) in enumerate(sorted_shapes_outside):
                    if i < start_index or i >= end_index:
                        continue
                    output_parts.append(output_definition(i, shape, sorted_shapes_outside_info, cell_name, layer_id, dtrans))
            else:
                output_parts.append("> **Status:** No neighboring shapes were found in the immediate vicinity.")
        else:
            output_parts.append("> **Status:** Skipped, as no Level 1 shapes were found.")

    return "\\n".join(output_parts)


if __name__ == "__main__":

    input_gds = "{input_gds}"
    ly = db.Layout()
    ly.read(input_gds)
    lyp_file = "{lyp_file_path}"
    layers = parse_lyp(lyp_file)

    layer_to_name = {{}}
    for info in layers:
        key = (info['layer_id'], info['datatype'])
        layer_to_name[key] = info['name']

    level = {level}
    cell_name = "{cell_name}"
    type = "{type}"
    direct_layers = {d_layers}
    query_pts = {query_pts}
    context_layers = {c_layers}
    second_level_index_range = {second_level_index_range}
    third_level_index_range = {third_level_index_range}

    shapes, shapes_info = query_with_pts(cell_name, type, direct_layers, query_pts)
    output_text = generate_drc_context_report(level, shapes, shapes_info, context_layers, cell_name, type, query_pts, second_level_index_range, third_level_index_range)

    print(output_text)




'''
    result_dir = os.environ.get("AGENT_RESULT_DIR")
    if not result_dir:
        error_msg = "CRITICAL ERROR: AGENT_RESULT_DIR environment variable not set. Cannot determine where to write tool script."
        print(error_msg, file=sys.stderr)
        return error_msg

    remote_path = os.path.join(result_dir, "query_with_pts.py")
    return run_local_code(code, local_path=remote_path, command=f"klayout -b -r {remote_path}")
