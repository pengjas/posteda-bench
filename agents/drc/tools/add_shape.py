"""Tool for adding shapes to a GDS layout."""

import os
import sys
from langchain_core.tools import tool

from .utils import run_local_code, get_ignore_case
from .layer_config import layers


@tool
def add_shape(input_gds: str, cell_name: str, shape_type: str, layer: str, solutions: list):
    """
    Adds a new shape (polygon, box, or path) to a specified cell on a specified layer. This tool is for creating new geometry.

    Args:
        input_gds (str): The file path to the GDS layout to be modified.
        cell_name (str): The name of the cell where the new shape will be added.
        shape_type (str): The type of shape to add. This determines the required format for the 'solutions' parameter.
                        Valid values:
                        - "polygon": `solutions` is a list of DPoints, e.g., [(x1,y1), (x2,y2), ...].
                        - "box_Center": `solutions` is [(center_x, center_y), width, height].
                        - "box_Points": `solutions` is [(llx, lly), (urx, ury)].
                        - "path_Points": `solutions` is [width, [(x1,y1), (x2,y2), ...]].
        layer (str): The name of the layer where the new shape will be added.
                     Available layers: ['well drawing', 'well pin', 'fin drawing', 'Gate drawing', 'Gate pin', 'Gate label', 'Gate net', 'Dummy drawing', 'GCut drawing', 'Active drawing', 'Nselect drawing', 'Pselect drawing', 'LIG drawing', 'LIG pin', 'LIG label', 'LIG net', 'LIG blockage', 'LISD drawing', 'LISD pin', 'LISD label', 'LISD net', 'LISD blockage', 'V0 drawing', 'V0 blockage', 'V0 PIN', 'M1 drawing', 'M1 pin', 'M1 label', 'M1 net', 'M1 blockage', 'V1 drawing', 'V1 blockage', 'V1 PIN', 'M2 drawing', 'M2 pin', 'M2 label', 'M2 net', 'M2 blockage', 'V2 drawing', 'V2 blockage', 'V2 PIN', 'M3 drawing', 'M3 pin', 'M3 label', 'M3 net', 'M3 blockage', 'V3 drawing', 'V3 blockage', 'V3 PIN', 'M4 drawing', 'M4 pin', 'M4 label', 'M4 net', 'M4 blockage', 'V4 drawing', 'V4 blockage', 'V4 PIN', 'M5 drawing', 'M5 pin', 'M5 label', 'M5 net', 'M5 blockage', 'V5 drawing', 'V5 blockage', 'V5 PIN', 'M6 drawing', 'M6 pin', 'M6 label', 'M6 net', 'M6 blockage', 'V6 drawing', 'V6 blockage', 'V6 PIN', 'M7 drawing', 'M7 pin', 'M7 label', 'M7 net', 'M7 blockage', 'V7 drawing', 'V7 blockage', 'V7 PIN', 'M8 drawing', 'M8 pin', 'M8 label', 'M8 net', 'M8 blockage', 'V8 drawing', 'V8 blockage', 'V8 PIN', 'M9 drawing', 'M9 pin', 'M9 label', 'M9 net', 'M9 blockage', 'V9 drawing', 'V9 blockage', 'V9 PIN', 'SDT drawing', 'SDT blockage', 'SLVT drawing', 'LVT drawing', 'SRAMDRC drawing', 'BOUNDARY drawing', 'TEXT drawing', 'SRAMVT drawing', 'P_SUB pin', 'Die Area'].
        solutions (list): A list containing the geometric data for the new shape.

    Returns:
        str: A confirmation message indicating success or failure of the add operation.
    """
    layer_info = get_ignore_case(layers, layer)

    output_gds = "./modified.gds"
    code = rf'''
import pdb
import pya
import klayout.db as db
import math

import xml.etree.ElementTree as ET


def add_polygon_shape(solutions, layer_index, target_cell, output_path):
    pts_list = [pya.DPoint(pt[0], pt[1]) for pt in solutions]
    new_polygon = pya.DPolygon(pts_list)
    shape_container = target_cell.shapes(layer_index)
    shape_container.insert(new_polygon)
    ly.write(output_path)
    print(f"Polygon is added at {{new_polygon.to_s()}} and saved to {{output_path}}")

def add_box_shape(solutions, shape_type, layer_index, target_cell, output_path):
    if shape_type[1] == "Center":
        new_dcenter = pya.DPoint(solutions[0][0], solutions[0][1])
        new_dwidth = solutions[1]
        new_dheight = solutions[2]
        dbox = pya.DBox(new_dcenter.x - new_dwidth / 2, new_dcenter.y - new_dheight / 2,
                        new_dcenter.x + new_dwidth / 2, new_dcenter.y + new_dheight / 2)
        shape_container = target_cell.shapes(layer_index)
        shape_container.insert(dbox)
        ly.write(output_path)
        print(f"Box {{dbox.to_s()}} is added via Center({{new_dcenter.to_s()}}), Width({{new_dwidth}}), Height({{new_dheight}}) and layout saved in {{output_path}}.")
    elif shape_type[1] == "Points":
        new_dp1 = pya.DPoint(solutions[0][0], solutions[0][1])
        new_dp2 = pya.DPoint(solutions[1][0], solutions[1][1])
        dbox = pya.DBox(new_dp1.x, new_dp1.y, new_dp2.x, new_dp2.y)
        shape_container = target_cell.shapes(layer_index)
        shape_container.insert(dbox)
        ly.write(output_path)
        print(f"Box {{dbox.to_s()}} is added via two corner points and layout saved in {{output_path}}.")


def add_path_shape(solutions, shape_type, layer_index, target_cell, output_path):
    new_dwidth = solutions[0]
    new_points = solutions[1]
    new_point_list = [pya.DPoint(pt[0], pt[1]) for pt in new_points]
    new_dpath = pya.DPath(new_point_list, new_dwidth)
    shape_container = target_cell.shapes(layer_index)
    shape_container.insert(new_dpath)
    ly.write(output_path)
    print(f"Path is added and layout saved in {{output_path}}.")

def add_shapes(shape_type, solutions, layer_index, target_cell, output_path):
    shape_type = shape_type.split("_")
    if len(shape_type) == 1:
        add_polygon_shape(solutions, layer_index, target_cell, output_path)
    target_shape_type = shape_type[0]
    if target_shape_type == "box":
        add_box_shape(solutions, shape_type, layer_index, target_cell, output_path)
    elif target_shape_type == "path":
        add_path_shape(solutions, shape_type, layer_index, target_cell, output_path)



if __name__ == "__main__":

    input_gds = "{input_gds}"
    output_path = "{output_gds}"
    ly = db.Layout()
    ly.read(input_gds)


    shape_type = "{shape_type}"
    solutions = {solutions}
    layer = {layer_info}
    cell_name = "{cell_name}"
    target_cell = ly.cell(cell_name)


    if target_cell:
        add_shapes(shape_type, solutions, ly.layer(pya.LayerInfo(layer[0][0], layer[0][1])), target_cell, output_path)



'''
    result_dir = os.environ.get("AGENT_RESULT_DIR")
    if not result_dir:
        error_msg = "CRITICAL ERROR: AGENT_RESULT_DIR environment variable not set. Cannot determine where to write tool script."
        print(error_msg, file=sys.stderr)
        return error_msg

    remote_path = os.path.join(result_dir, "add_shape.py")
    return run_local_code(code, local_path=remote_path, command=f"klayout -b -r {remote_path}")
