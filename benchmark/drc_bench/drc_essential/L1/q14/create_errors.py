# file: create_gate_width_error.py
# usage: klayout -b -r create_gate_width_error.py

import pya


layout = pya.Layout()

layout.dbu = 0.001

print("Layout object created with DBU = {} um".format(layout.dbu))


gate_layer_info = pya.LayerInfo(7, 0)
gate_layer_index = layout.layer(gate_layer_info)

top_cell = layout.create_cell("top_cell")

print("Cell '{}' created on layer {}/{}".format(top_cell.name, gate_layer_info.layer, gate_layer_info.datatype))


min_width_nm = 40.0

violating_height_nm = 39.0

violating_height_um = violating_height_nm / 1000.0
width_um = 100.0 / 1000.0

width_dbu = width_um / layout.dbu
height_dbu = violating_height_um / layout.dbu

violating_box = pya.Box(0, 0, int(width_dbu), int(height_dbu))

print("Creating a box with DBU dimensions: {} x {}".format(int(width_dbu), int(height_dbu)))
print("Physical dimensions: {}nm x {}nm".format(width_um, violating_height_um))


shapes_container = top_cell.shapes(gate_layer_index)

shapes_container.insert(violating_box)


output_filename = "$HOME/pengju/PostEDA-Bench/benchmark/L1/asap7/q11/top_cell.gds"

layout.write(output_filename)

print("Successfully generated DRC violation and saved to '{}'".format(output_filename))