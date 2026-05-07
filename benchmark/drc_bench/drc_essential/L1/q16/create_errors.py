import klayout.db as pya
import os
import math

# --- 1. Configuration Section ---

# NOTE: The GDS layer for WELL is assumed based on common PDK practices.
# Please adjust if your specific ASAP7 tech file uses a different layer number.
WELL_LAYER_INFO = {"layer": 1, "datatype": 0, "name": "WELL"}

# Rule parameters from the DRC manual (units: nm)
RULE_MIN_ENCLOSED_AREA_NM2 = 5832

# We will inject a violation with an enclosed area smaller than the rule.
# Let's target an area of 4000 nm^2.
VIOLATION_AREA_NM2 = 4000

# Output directory for the generated GDS file
OUTPUT_DIR = "./out"

# --- 2. Violation Generation Logic ---

def create_well_a1b_violation():
    """
    Creates a GDSII file with a single shape that violates the WELL.A.1B
    (minimum enclosed area) rule.
    """
    # Ensure the output directory exists
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    dbu = 1.0  # Set database unit to 1nm for straightforward coordinate mapping

    # Create a new layout object
    layout = pya.Layout()
    # KLayout's dbu property is in microns, so we convert nm to um
    layout.dbu = dbu / 1000.0

    # Create a top-level cell with a descriptive name
    cell_name = f"top_cell"
    top_cell = layout.create_cell(cell_name)
    layer_index = layout.layer(WELL_LAYER_INFO["layer"], WELL_LAYER_INFO["datatype"])

    # --- Create the geometry for the violation ---

    # To create a hole (enclosed area), we will define an outer polygon
    # and an inner polygon, then subtract the inner from the outer.

    # A. Define the inner polygon (the hole) with the illegal area
    # We'll make it a square for simplicity.
    inner_side_length_nm = math.sqrt(VIOLATION_AREA_NM2)
    inner_side_length_dbu = int(inner_side_length_nm / dbu)
    # Center the inner box at the origin for easy placement
    inner_box = pya.Box(
        -inner_side_length_dbu / 2,
        -inner_side_length_dbu / 2,
        inner_side_length_dbu / 2,
        inner_side_length_dbu / 2
    )
    actual_area = inner_side_length_dbu * inner_side_length_dbu
    print(f"Targeting enclosed area of {VIOLATION_AREA_NM2} nm^2.")
    print(f"Creating an inner hole of size {inner_side_length_dbu}x{inner_side_length_dbu} dbu, for an actual area of {actual_area} nm^2.")

    # B. Define an outer polygon large enough to contain the inner one
    outer_side_length_dbu = inner_side_length_dbu + 100 # Add a 50nm border on all sides
    outer_box = pya.Box(
        -outer_side_length_dbu / 2,
        -outer_side_length_dbu / 2,
        outer_side_length_dbu / 2,
        outer_side_length_dbu / 2
    )
    print(f"Creating an outer WELL box of size {outer_side_length_dbu}x{outer_side_length_dbu} dbu.")

    # C. Perform the boolean subtraction to create the donut shape
    # We use pya.Region for boolean operations
    region_outer = pya.Region(outer_box)
    region_inner = pya.Region(inner_box)
    
    region_with_hole = region_outer - region_inner

    # Insert the final shape with the hole into the layout
    top_cell.shapes(layer_index).insert(region_with_hole)

    # --- Save the layout to a GDSII file ---
    filename = os.path.join(OUTPUT_DIR, f"{cell_name}.gds")
    layout.write(filename)
    
    print(f"\nSuccessfully generated violation file: {filename}")
    print(f"This file contains a WELL shape with an enclosed area of ~{actual_area} nm^2, which is less than the required {RULE_MIN_ENCLOSED_AREA_NM2} nm^2.")


if __name__ == "__main__":
    create_well_a1b_violation()