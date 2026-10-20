"""Utility functions for DRC tools."""

import ast
import subprocess
import os

from pydantic import BeforeValidator


def get_ignore_case(d, key, default=None):
    """Get a value from a dictionary with case-insensitive key matching."""
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return default


_LITERAL_ERRORS = (ValueError, TypeError, SyntaxError, MemoryError, RecursionError)


def parse_numbers(value):
    """Turn the numbers of a tool argument that arrive as text into numbers.

    The tools describe points and ranges as Python tuples, e.g.
    [(llx, lly), (urx, ury)] or (10, 20), and a model that writes them so does
    not always deliver lists of numbers. JSON tool calls, and parsers that
    read array arguments with json.loads such as vLLM's qwen3_coder, carry the
    whole value as a string: "(10, 20)". vLLM's Gemma 4 parser splits a tuple
    at its commas, '(0.04', '-0.258)', and returns the numbers of an untyped
    list as strings: '0.125'. Each is read here as lists of numbers. What
    departs from the descriptions, such as a list in one bracket too many or
    a quoted list without tuples, is left for the tool to reject.
    """
    if isinstance(value, str) and "(" in value:
        try:
            literal = ast.literal_eval(value)
        except _LITERAL_ERRORS:
            return value
        return parse_numbers(literal) if isinstance(literal, (list, tuple)) else value
    if isinstance(value, (list, tuple)):
        if any(isinstance(v, str) and "(" in v for v in value):
            try:
                value = ast.literal_eval(_as_source(value))
            except _LITERAL_ERRORS:
                pass
        return [parse_numbers(v) for v in value]
    if isinstance(value, str):
        for number_type in (int, float):
            try:
                return number_type(value)
            except ValueError:
                pass
    return value


# Annotate a tool argument with this to run parse_numbers on it before the
# argument is checked against its type.
numbers_from_text = BeforeValidator(parse_numbers)


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
