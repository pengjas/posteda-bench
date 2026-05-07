"""Utility functions for PPA tools with security validation."""

import os
import subprocess
import re
from pathlib import Path
from typing import Optional, Tuple


# Allowed file extensions for reading
READABLE_EXTENSIONS = {
    # Config files
    '.mk', '.sdc', '.tcl', '.json',
    # Source files
    '.v', '.sv', '.vh', '.svh',
    # Report files
    '.rpt', '.log', '.txt',
    # Intermediate files
    '.def', '.lef', '.lib', '.spef',
    # Other text files
    '.rtlil', '.constr', '.guide',
}

# Allowed file extensions for editing
EDITABLE_EXTENSIONS = {
    '.mk',   # Makefile configs
    '.sdc',  # Timing constraints
    '.v',    # Verilog source
    '.sv',   # SystemVerilog source
    '.tcl',  # Tcl scripts
}

# Directory names that are allowed to access
ALLOWED_DIRECTORIES = {
    'config', 'src', 'result', 'report', 'log', 'object', 'objects', 'results', 'reports', 'logs'
}

# Patterns to detect potentially dangerous content
DANGEROUS_PATTERNS = [
    r'\$\(shell\s+.*\)',     # Make shell command execution
    r'`.*`',                  # Backtick command substitution
    r'\|\s*sh\b',             # Piping to shell
    r'\|\s*bash\b',           # Piping to bash
    r';\s*rm\s+-rf',          # Destructive rm commands
    r'>\s*/dev/',             # Writing to device files
]


def get_sandbox_root() -> Optional[str]:
    """Get the sandbox root directory from environment variable."""
    sandbox_root = os.environ.get("PPA_SANDBOX_ROOT")
    if not sandbox_root:
        # Fallback to current working directory
        sandbox_root = os.getcwd()
    return sandbox_root


def normalize_path(file_path: str) -> str:
    """Normalize a path to absolute form, resolving symlinks."""
    return str(Path(file_path).resolve())


def is_path_within_sandbox(file_path: str, sandbox_root: Optional[str] = None) -> Tuple[bool, str]:
    """
    Check if a file path is within the sandbox directory.

    Args:
        file_path: The path to check
        sandbox_root: Optional sandbox root (uses env var if not provided)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if sandbox_root is None:
        sandbox_root = get_sandbox_root()

    if sandbox_root is None:
        return False, "Sandbox root not configured (PPA_SANDBOX_ROOT environment variable not set)"

    try:
        # Normalize both paths
        normalized_path = normalize_path(file_path)
        normalized_sandbox = normalize_path(sandbox_root)

        # Check if the file path starts with sandbox root
        if not normalized_path.startswith(normalized_sandbox):
            return False, f"Path '{file_path}' is outside sandbox '{sandbox_root}'"

        return True, ""
    except Exception as e:
        return False, f"Error validating path: {e}"


def is_extension_allowed(file_path: str, allowed_extensions: set) -> Tuple[bool, str]:
    """
    Check if file extension is in the allowed set.

    Args:
        file_path: Path to the file
        allowed_extensions: Set of allowed extensions

    Returns:
        Tuple of (is_valid, error_message)
    """
    ext = Path(file_path).suffix.lower()
    if ext not in allowed_extensions:
        return False, f"File extension '{ext}' not allowed. Allowed: {sorted(allowed_extensions)}"
    return True, ""


def is_readable_file(file_path: str, sandbox_root: Optional[str] = None) -> Tuple[bool, str]:
    """
    Validate if a file can be read.

    Args:
        file_path: Path to the file
        sandbox_root: Optional sandbox root

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check sandbox
    valid, error = is_path_within_sandbox(file_path, sandbox_root)
    if not valid:
        return False, error

    # Check extension
    valid, error = is_extension_allowed(file_path, READABLE_EXTENSIONS)
    if not valid:
        return False, error

    # Check file exists
    if not os.path.isfile(file_path):
        return False, f"File does not exist: {file_path}"

    return True, ""


def is_editable_file(file_path: str, sandbox_root: Optional[str] = None) -> Tuple[bool, str]:
    """
    Validate if a file can be edited.

    Args:
        file_path: Path to the file
        sandbox_root: Optional sandbox root

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check sandbox
    valid, error = is_path_within_sandbox(file_path, sandbox_root)
    if not valid:
        return False, error

    # Check extension
    valid, error = is_extension_allowed(file_path, EDITABLE_EXTENSIONS)
    if not valid:
        return False, error

    # Check file exists for editing (not for creating new files)
    if not os.path.isfile(file_path):
        return False, f"File does not exist: {file_path}"

    return True, ""


def check_content_safety(content: str) -> Tuple[bool, str]:
    """
    Check if content is safe to write (no dangerous patterns).

    Args:
        content: The content to check

    Returns:
        Tuple of (is_safe, warning_message)
    """
    warnings = []
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            warnings.append(f"Potentially dangerous pattern detected: {pattern}")

    if warnings:
        return False, "; ".join(warnings)
    return True, ""


def run_command(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 300,
    capture_output: bool = True
) -> Tuple[str, str, int]:
    """
    Run a shell command safely.

    Args:
        command: The command to run
        cwd: Working directory
        timeout: Timeout in seconds
        capture_output: Whether to capture output

    Returns:
        Tuple of (stdout, stderr, return_code)
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=capture_output,
            text=True,
            timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {timeout} seconds", -1
    except Exception as e:
        return "", f"Command execution error: {e}", -1


def list_sandbox_files(
    sandbox_root: Optional[str] = None,
    pattern: str = "*",
    recursive: bool = True
) -> list:
    """
    List files in the sandbox directory.

    Args:
        sandbox_root: Sandbox root directory
        pattern: Glob pattern to filter files
        recursive: Whether to search recursively

    Returns:
        List of file paths relative to sandbox root
    """
    if sandbox_root is None:
        sandbox_root = get_sandbox_root()

    if sandbox_root is None:
        return []

    sandbox_path = Path(sandbox_root)
    if recursive:
        files = list(sandbox_path.rglob(pattern))
    else:
        files = list(sandbox_path.glob(pattern))

    # Return relative paths as strings, filtering out directories
    return [str(f.relative_to(sandbox_path)) for f in files if f.is_file()]


def get_file_info(file_path: str) -> dict:
    """
    Get information about a file.

    Args:
        file_path: Path to the file

    Returns:
        Dictionary with file information
    """
    try:
        path = Path(file_path)
        stat = path.stat()
        return {
            "name": path.name,
            "extension": path.suffix,
            "size_bytes": stat.st_size,
            "size_human": _format_size(stat.st_size),
            "exists": True,
            "is_file": path.is_file(),
            "is_dir": path.is_dir(),
        }
    except FileNotFoundError:
        return {"exists": False, "error": "File not found"}
    except Exception as e:
        return {"exists": False, "error": str(e)}


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
