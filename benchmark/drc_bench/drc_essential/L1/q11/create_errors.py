import klayout.db as pya
import os


GATE_LAYER_INFO = {"layer": 1, "datatype": 0, "name": "WELL"}

RULE_MIN_SPACING_NM = 100
GATE_WIDTH_NM = 108
GATE_HEIGHT_NM = 54

VIOLATION_SPACING_NM = 20

OUTPUT_DIR = "./out"


def create_gate_s3_violation():
    """
    Create a GDSII file containing a single GATE.S.3 violation.
    """
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    dbu = 1.0

    layout = pya.Layout()
    layout.dbu = dbu / 1000.0

    cell_name = f"top_cell"
    # cell_name = f"GATE_S_3_Spacing_{VIOLATION_SPACING_NM}nm"
    top_cell = layout.create_cell(cell_name)
    layer_index = layout.layer(GATE_LAYER_INFO["layer"], GATE_LAYER_INFO["datatype"])

    width_dbu = int(GATE_WIDTH_NM / dbu)
    height_dbu = int(GATE_HEIGHT_NM / dbu)
    spacing_dbu = int(VIOLATION_SPACING_NM / dbu)

    gate1 = pya.Box(0, 0, width_dbu, height_dbu)
    top_cell.shapes(layer_index).insert(gate1)
    print(f"Creating first GATE, size: {width_dbu}x{height_dbu} dbu")

    y_start_gate2 = height_dbu + spacing_dbu

    gate2 = pya.Box(0, y_start_gate2, width_dbu, y_start_gate2 + height_dbu)
    top_cell.shapes(layer_index).insert(gate2)
    print(f"????? GATE，??? y={y_start_gate2} dbu")
    print(f"?????: {spacing_dbu} dbu (???? >= {int(RULE_MIN_SPACING_NM / dbu)} dbu)")

    filename = os.path.join(OUTPUT_DIR, f"{cell_name}.gds")
    layout.write(filename)
    
    print(f"\nSuccessfully generated violation file: {filename}")

if __name__ == "__main__":
    create_gate_s3_violation()