"""Utility functions for DRC tools."""

import ast
import subprocess
import os


def get_ignore_case(d, key, default=None):
    """Get a value from a dictionary with case-insensitive key matching."""
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return default


def parse_numbers(value):
    """Turn the numeric strings in a (nested) list into numbers.

    vLLM's Gemma 4 tool parser returns the numbers inside a list argument as
    strings ('0.125') unless the tool schema types the list's items, which a
    bare `list` parameter does not. It knows no tuples either: it splits a
    point written as (0.04,-0.258) at the comma into '(0.04' and '-0.258)',
    which are joined back here into [0.04, -0.258].
    """
    if isinstance(value, (list, tuple)):
        if any(isinstance(v, str) and "(" in v for v in value):
            try:
                value = ast.literal_eval(_as_source(value))
            except (ValueError, SyntaxError):
                pass
        return [parse_numbers(v) for v in value]
    if isinstance(value, str):
        for number_type in (int, float):
            try:
                return number_type(value)
            except ValueError:
                pass
    return value


def _as_source(value):
    """Write a list the parser split back out as the text the model wrote."""
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_as_source(v) for v in value) + "]"
    return str(value)


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
