"""General-purpose tool for editing files in the PPA sandbox."""

import os
import re
import shutil
from pathlib import Path
from typing import Optional
from langchain_core.tools import tool

from .utils import (
    get_sandbox_root,
    is_path_within_sandbox,
    EDITABLE_EXTENSIONS,
)


# Locked variables in .mk files that should NOT be modified
# These are essential for the flow to work correctly
LOCKED_MK_VARIABLES = [
    'PLATFORM',
    'DESIGN_NAME',
    'DESIGN_NICKNAME',
    'VERILOG_FILES',
    'SDC_FILE',
]


def _check_locked_variables(old_content: str, new_content: str, file_ext: str) -> tuple:
    """
    Check if the edit attempts to modify locked variables in .mk files.
    Returns (is_allowed, error_message).
    """
    if file_ext != '.mk':
        return True, ""

    for var in LOCKED_MK_VARIABLES:
        # Check if old_content contains an export line for this variable
        pattern = rf'export\s+{var}\s*='
        if re.search(pattern, old_content):
            # Check if new_content changes the variable assignment
            old_match = re.search(rf'export\s+{var}\s*=\s*(.+)', old_content)
            new_match = re.search(rf'export\s+{var}\s*=\s*(.+)', new_content)

            if old_match and new_match:
                old_val = old_match.group(1).strip()
                new_val = new_match.group(1).strip()
                if old_val != new_val:
                    return False, f"Cannot modify locked variable '{var}'. This variable is essential for the flow."

            # Check if trying to delete the variable
            if old_match and not new_match:
                return False, f"Cannot delete locked variable '{var}'. This variable is essential for the flow."

    return True, ""


@tool
def edit_file(
    file_path: str,
    old_content: str,
    new_content: str,
) -> str:
    """
    **PURPOSE:** Edit a file by replacing old_content with new_content.
    This is a general-purpose file editing tool that can modify any text file
    in the sandbox (config files, source code, etc.).

    **WHEN TO USE:** Use this tool to:
    - Modify .mk configuration files (change variables, add new settings)
    - Edit .sdc timing constraint files
    - Modify .v/.sv Verilog source files
    - Edit any other text files in the sandbox

    **PARAMETERS:**
    - `file_path` (str): Path to the file (relative to sandbox root).
    - `old_content` (str): The exact text to find and replace. Must match exactly.
    - `new_content` (str): The new text to replace with. Use empty string "" to delete.

    **IMPORTANT RULES:**
    - old_content must match EXACTLY (including whitespace and newlines)
    - Only the FIRST occurrence is replaced
    - Creates a backup file (.bak) before editing
    - To delete content, set new_content to empty string ""
    - To insert new content, find a unique anchor text and include it in both old and new

    **LOCKED VARIABLES (cannot be modified in .mk files):**
    - PLATFORM, DESIGN_NAME, DESIGN_NICKNAME
    - VERILOG_FILES, SDC_FILE
    These are essential for the flow and must not be changed.

    **EXAMPLES:**
    1. Change PARAM_NAME value in .mk file:
       ```
       edit_file(
           file_path="config/gcd.mk",
           old_content="export PARAM_NAME          = 0.6",
           new_content="export PARAM_NAME          = 0.7"
       )
       ```

    2. Add a new variable to .mk file (insert after existing line):
       ```
       edit_file(
           file_path="config/gcd.mk",
           old_content="export PARAM_NAME          = 0.6",
           new_content="export PARAM_NAME          = 0.6\\nexport xxx           = LVT"
       )
       ```

    3. Change clock period in .sdc file:
       ```
       edit_file(
           file_path="config/gcd.sdc",
           old_content="create_clock -name core_clock -period 225",
           new_content="create_clock -name core_clock -period 250"
       )
       ```

    4. Delete a line (set new_content to empty):
       ```
       edit_file(
           file_path="config/gcd.mk",
           old_content="# export PARAM_NAME           = LVT\\n",
           new_content=""
       )
       ```

    5. Uncomment a line:
       ```
       edit_file(
           file_path="config/gcd.mk",
           old_content="# export PARAM_NAME           = LVT",
           new_content="export PARAM_NAME           = LVT"
       )
       ```

    **OUTPUT:** Returns success message or error.
    """
    sandbox_root = get_sandbox_root()

    if not sandbox_root:
        return "ERROR: Sandbox root not configured (PPA_SANDBOX_ROOT not set)"

    # Resolve file path
    if os.path.isabs(file_path):
        full_path = file_path
    else:
        full_path = os.path.join(sandbox_root, file_path)

    # Validate path is within sandbox
    valid, error = is_path_within_sandbox(full_path, sandbox_root)
    if not valid:
        return f"ERROR: {error}"

    # Check file exists
    if not os.path.isfile(full_path):
        return f"ERROR: File does not exist: {file_path}"

    # Check extension is allowed
    ext = Path(full_path).suffix.lower()
    if ext not in EDITABLE_EXTENSIONS:
        return f"ERROR: File type '{ext}' not allowed for editing. Allowed: {sorted(EDITABLE_EXTENSIONS)}"

    # Check for locked variables in .mk files
    is_allowed, lock_error = _check_locked_variables(old_content, new_content, ext)
    if not is_allowed:
        return f"ERROR: {lock_error}"

    try:
        # Read current content
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check if old_content exists
        if old_content not in content:
            # Show a preview of the file to help debugging
            preview = content[:500] + "..." if len(content) > 500 else content
            return f"ERROR: old_content not found in file.\n\nFile preview:\n{preview}"

        # Create backup
        backup_path = full_path + '.bak'
        shutil.copy2(full_path, backup_path)

        # Replace (first occurrence only)
        new_file_content = content.replace(old_content, new_content, 1)

        # Write modified content
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(new_file_content)

        # Provide feedback on what changed
        if new_content == "":
            return f"SUCCESS: Deleted content from {file_path} (backup: {Path(backup_path).name})"
        else:
            return f"SUCCESS: Modified {file_path} (backup: {Path(backup_path).name})"

    except Exception as e:
        return f"ERROR: Failed to edit file: {e}"


@tool
def write_file(
    file_path: str,
    content: str,
) -> str:
    """
    **PURPOSE:** Write/overwrite entire file content.
    Use this when you need to completely replace a file's content.

    **WHEN TO USE:** Use this tool when:
    - Creating a new file
    - Completely rewriting an existing file
    - The edit_file tool's find-replace approach is too cumbersome

    **PARAMETERS:**
    - `file_path` (str): Path to the file (relative to sandbox root).
    - `content` (str): Complete new content for the file.

    **WARNING:** This will completely overwrite the file. A backup is created.

    **EXAMPLES:**
    1. Write a new .mk file:
       ```
       write_file(
           file_path="config/new_config.mk",
           content="export PLATFORM = asap7\\nexport DESIGN_NAME = gcd\\n..."
       )
       ```

    **OUTPUT:** Returns success message or error.
    """
    sandbox_root = get_sandbox_root()

    if not sandbox_root:
        return "ERROR: Sandbox root not configured (PPA_SANDBOX_ROOT not set)"

    # Resolve file path
    if os.path.isabs(file_path):
        full_path = file_path
    else:
        full_path = os.path.join(sandbox_root, file_path)

    # Validate path is within sandbox
    valid, error = is_path_within_sandbox(full_path, sandbox_root)
    if not valid:
        return f"ERROR: {error}"

    # Check extension is allowed
    ext = Path(full_path).suffix.lower()
    if ext not in EDITABLE_EXTENSIONS:
        return f"ERROR: File type '{ext}' not allowed for editing. Allowed: {sorted(EDITABLE_EXTENSIONS)}"

    # For .mk files, check that locked variables are preserved
    if ext == '.mk' and os.path.isfile(full_path):
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                old_content = f.read()
            is_allowed, lock_error = _check_locked_variables(old_content, content, ext)
            if not is_allowed:
                return f"ERROR: {lock_error}"
        except Exception:
            pass  # If we can't read old file, proceed with caution

    try:
        # Create backup if file exists
        if os.path.isfile(full_path):
            backup_path = full_path + '.bak'
            shutil.copy2(full_path, backup_path)
            backup_msg = f" (backup: {Path(backup_path).name})"
        else:
            backup_msg = " (new file)"
            # Ensure directory exists
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Write content
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return f"SUCCESS: Wrote {len(content)} bytes to {file_path}{backup_msg}"

    except Exception as e:
        return f"ERROR: Failed to write file: {e}"
