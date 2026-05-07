import pya


layout = pya.Layout()

layout.dbu = 0.001


top_cell = layout.create_cell("top_cell")

active_layer_info = pya.LayerInfo(11, 0)
active_layer_index = layout.layer(active_layer_info)

nselect_layer_info = pya.LayerInfo(12, 0)
nselect_layer_index = layout.layer(nselect_layer_info)



box_width = 108
box_height = 513
spacing = 91

box1 = pya.Box(0, 0, box_width, box_height)

left2 = box1.right + spacing
right2 = left2 + box_width
box2 = pya.Box(left2, 0, right2, box_height)

margin = 50 
nselect_box = pya.Box(
    box1.left - margin,
    box1.bottom - margin,
    box2.right + margin,
    box2.top + margin
)


active_shapes = top_cell.shapes(active_layer_index)
active_shapes.insert(box1)
active_shapes.insert(box2)

nselect_shapes = top_cell.shapes(nselect_layer_index)
nselect_shapes.insert(nselect_box)


output_file = "$HOME/pengju/PostEDA-Bench/benchmark/L1/asap7/q8/top_cell.gds"

layout.write(output_file)

print(f"??????DRC???GDS??: {output_file}")
print(f" - ACTIVE? (11/0) ??: {spacing} nm (???? > 92 nm)")
print(f" - ACTIVE????????27nm????。")
print(f" - ???NSELECT? (12/0) ???ACTIVE。")