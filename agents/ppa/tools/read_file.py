"""General-purpose tool for reading files using shell commands with strict sandbox enforcement."""

import os
import re
import subprocess
import shlex
from pathlib import Path
from typing import Optional, Tuple, List
from langchain_core.tools import tool

from .utils import (
    get_sandbox_root,
    is_path_within_sandbox,
)


# Allowed commands (whitelist approach)
ALLOWED_COMMANDS = {
    'cat', 'head', 'tail', 'grep', 'awk', 'sed', 'wc', 'sort', 'uniq',
    'cut', 'tr', 'ls', 'find', 'tree', 'jq', 'less', 'more', 'file',
    'stat', 'diff', 'strings', 'xxd', 'od',
}

# Explicitly blocked commands
BLOCKED_COMMANDS = {
    'rm', 'mv', 'cp', 'chmod', 'chown', 'chgrp', 'mkdir', 'rmdir', 'touch',
    'wget', 'curl', 'nc', 'netcat', 'ssh', 'scp', 'sftp', 'rsync', 'ftp',
    'dd', 'mkfs', 'fdisk', 'mount', 'umount', 'sudo', 'su', 'bash', 'sh',
    'zsh', 'python', 'python3', 'perl', 'ruby', 'node', 'php', 'exec',
    'eval', 'source', 'export', 'kill', 'pkill', 'killall', 'nohup',
    'tee', 'write', 'append',
}

# Blocked shell operators that could modify files or escape sandbox
BLOCKED_OPERATORS = ['>', '>>', '<<', '`', '$(',  '&&', '||', ';']

# Files that should not be accessible to the agent (evaluation/metadata files)
BLOCKED_FILES = {'info.json'}


def _validate_command(command: str, sandbox_root: str) -> Tuple[bool, str]:
    """
    Validate that a command is safe to execute within the sandbox.

    Returns (is_valid, error_message).
    """
    # Check for blocked operators
    for op in BLOCKED_OPERATORS:
        if op in command:
            # Allow pipe (|) but not others
            if op == '|':
                continue
            return False, f"Blocked operator '{op}' not allowed. Only pipe '|' is permitted."

    # Parse the command to extract the base commands
    # Split by pipe to handle piped commands
    pipe_segments = command.split('|')

    for segment in pipe_segments:
        segment = segment.strip()
        if not segment:
            continue

        # Extract the base command (first word)
        parts = segment.split()
        if not parts:
            continue

        base_cmd = parts[0].lower()

        # Remove path prefix if present
        if '/' in base_cmd:
            base_cmd = os.path.basename(base_cmd)

        # Check against blocked commands
        if base_cmd in BLOCKED_COMMANDS:
            return False, f"Command '{base_cmd}' is not allowed. This tool is for reading only."

        # Check against allowed commands
        if base_cmd not in ALLOWED_COMMANDS:
            return False, f"Command '{base_cmd}' is not in the allowed list. Allowed commands: {sorted(ALLOWED_COMMANDS)}"

    # Extract and validate all file paths in the command
    # Look for paths (anything that looks like a file/directory reference)
    path_patterns = [
        r'(?:^|\s)(/[^\s]+)',           # Absolute paths
        r'(?:^|\s)(\./[^\s]+)',         # Relative paths starting with ./
        r'(?:^|\s)(\.\./[^\s]+)',       # Relative paths starting with ../
        r'(?:^|\s)([A-Za-z_][A-Za-z0-9_./\-]+\.[A-Za-z]+)',  # File names with extensions
    ]

    potential_paths = []
    for pattern in path_patterns:
        matches = re.findall(pattern, command)
        potential_paths.extend(matches)

    # Also check arguments that might be paths
    for segment in pipe_segments:
        parts = segment.split()
        for part in parts[1:]:  # Skip the command itself
            # Skip flags
            if part.startswith('-'):
                continue
            # Skip common non-path arguments
            if part in ['.', '..']:
                continue
            # If it contains a slash or looks like a file, validate it
            if '/' in part or '.' in part:
                potential_paths.append(part)

    # Validate each potential path
    for path in potential_paths:
        # Clean up the path
        path = path.strip("'\"")

        # Skip if it's a flag or looks like a regex pattern
        if path.startswith('-') or path.startswith('^') or path.startswith('$'):
            continue

        # Check if accessing a blocked file
        filename = os.path.basename(path)
        if filename in BLOCKED_FILES:
            return False, f"Access to '{filename}' is not allowed. This file is reserved for evaluation."

        # Resolve the path
        if os.path.isabs(path):
            full_path = path
        else:
            full_path = os.path.normpath(os.path.join(sandbox_root, path))

        # Check if within sandbox
        try:
            resolved_path = os.path.realpath(full_path)
            resolved_sandbox = os.path.realpath(sandbox_root)

            if not resolved_path.startswith(resolved_sandbox + os.sep) and resolved_path != resolved_sandbox:
                return False, f"Path '{path}' resolves outside sandbox. All file operations must be within sandbox."
        except Exception:
            # If we can't resolve, check the normalized path
            if not full_path.startswith(sandbox_root):
                return False, f"Path '{path}' appears to be outside sandbox."

    return True, ""


@tool
def read_file(command: str) -> str:
    """
    **PURPOSE:** Execute a shell command to read file contents in the sandbox.
    This is a general-purpose file reading tool that supports standard Unix
    text processing commands.

    **SECURITY (ENFORCED):**
    - Only whitelisted read-only commands are allowed
    - All file paths must be within the sandbox directory
    - BLOCKED operators: >, >>, <<, `, $(, &&, ||, ;
    - ALLOWED operator: | (pipe only, for chaining read commands)

    **ALLOWED COMMANDS:**
    cat, head, tail, grep, awk, sed, wc, sort, uniq, cut, tr, ls, find,
    tree, jq, less, more, file, stat, diff, strings

    **PARAMETERS:**
    - `command` (str): A shell command to read/process files.
      Common patterns:
        - `cat <file>` - Display entire file content
        - `head -n 50 <file>` - Display first 50 lines
        - `tail -n 50 <file>` - Display last 50 lines
        - `grep <pattern> <file>` - Search for pattern
        - `grep -A 5 -B 5 <pattern> <file>` - Search with context
        - `cat <file> | grep <pattern>` - Filter file content
        - `ls -la <dir>` - List directory contents
        - `jq '.<key>' <file.json>` - Extract JSON fields

    **EXAMPLES (command values):**
    - Read config file: "cat config/gcd.mk"
    - Check timing: "grep 'worst slack max' report/6_finish.rpt"
    - Read area from JSON: "jq '.finish__design__die__area' log/6_report.json"
    - Read documentation: "cat FlowVariables.md"
    - List report files: "find . -type f -name '*.rpt'"
    - Search variable: "grep PARAM_NAME config/gcd.mk"

    **OUTPUT:** Returns the command output (stdout) or error message.
    """
    sandbox_root = get_sandbox_root()

    if not sandbox_root:
        return "ERROR: Sandbox root not configured (PPA_SANDBOX_ROOT not set)"

    # Validate command
    is_valid, error_msg = _validate_command(command, sandbox_root)
    if not is_valid:
        return f"ERROR: {error_msg}"

    try:
        # Execute command in sandbox directory
        result = subprocess.run(
            command,
            shell=True,
            cwd=sandbox_root,
            capture_output=True,
            text=True,
            timeout=60
        )

        output = result.stdout
        errors = result.stderr

        if result.returncode != 0:
            if errors:
                return f"Command failed (exit {result.returncode}):\n{errors}"
            elif not output:
                return f"Command returned no output (exit {result.returncode})"

        # Truncate very long output
        max_chars = 50000
        if len(output) > max_chars:
            output = output[:max_chars] + f"\n\n... [Output truncated, {len(output) - max_chars} more characters]"

        return output if output else "(empty output)"

    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out after 60 seconds"
    except Exception as e:
        return f"ERROR: {e}"


@tool
def list_files(directory: str = ".", level: int = 2) -> str:
    """
    **PURPOSE:** List files in the sandbox directory.

    **PARAMETERS:**
    - `directory` (str): Directory to list (relative to sandbox). Default: "." (sandbox root)
    - `level` (int): Depth level to list. Default: 2. Use 1 for top-level only.

    **OUTPUT:** Returns tree-style listing of files.
    """
    sandbox_root = get_sandbox_root()

    if not sandbox_root:
        return "ERROR: Sandbox root not configured"

    target_dir = os.path.join(sandbox_root, directory) if directory != "." else sandbox_root

    # Check if within sandbox
    valid, error = is_path_within_sandbox(target_dir, sandbox_root)
    if not valid:
        return f"ERROR: {error}"

    if not os.path.isdir(target_dir):
        return f"ERROR: Directory does not exist: {directory}"

    # Sanitize level
    level = max(1, min(level, 5))  # Clamp between 1 and 5

    try:
        # Use relative path for display (run from sandbox_root)
        relative_dir = "." if directory == "." else directory

        # Try tree first (exclude blocked files)
        exclude_pattern = ' '.join(f'-I "{f}"' for f in BLOCKED_FILES)
        result = subprocess.run(
            f'tree -L {level} --noreport {exclude_pattern} "{relative_dir}"',
            shell=True,
            cwd=sandbox_root,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0 and result.stdout:
            return result.stdout

        # Fallback to find (exclude blocked files)
        exclude_find = ' '.join(f'-not -name "{f}"' for f in BLOCKED_FILES)
        result = subprocess.run(
            f'find "{relative_dir}" -maxdepth {level} -type f {exclude_find} | sort',
            shell=True,
            cwd=sandbox_root,
            capture_output=True,
            text=True,
            timeout=30
        )

        return result.stdout if result.stdout else "(no files found)"

    except Exception as e:
        return f"ERROR: {e}"
