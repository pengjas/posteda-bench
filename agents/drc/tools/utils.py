"""Utility functions for DRC tools."""

import subprocess
import os


def get_ignore_case(d, key, default=None):
    """Get a value from a dictionary with case-insensitive key matching."""
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return default


def run_local_code(code: str = None, local_path: str = None, command: str = None, display_output: bool = True) -> str:
    """
    Write code to a file and/or execute a command.

    Args:
        code: Python code to write to the file
        local_path: Path where to write the code
        command: Shell command to execute
        display_output: Whether to print output to console

    Returns:
        Command output or error message
    """
    if code and local_path:
        try:
            dir_name = os.path.dirname(local_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(code)
        except IOError as e:
            error_message = f"Error writing to file {local_path}: {e}"
            if display_output:
                print(error_message)
            return error_message

    output = ""
    if command:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, cwd='.')

        output = result.stdout
        errors = result.stderr

        if display_output:
            if errors:
                print(f"Errors:\n{errors}")
            if output.strip():
                print(f"Output:\n{output}")

        if errors:
            return errors

    return output
