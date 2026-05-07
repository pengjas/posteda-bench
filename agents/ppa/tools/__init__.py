"""PPA Tools package for Power, Performance, Area optimization."""

# File reading tools
from .read_file import (
    read_file,
    list_files,
)

# File editing tools
from .edit_file import (
    edit_file,
    write_file,
)

# OpenROAD execution tools
from .run_openroad import (
    run_openroad_flow,
)

# PPA metrics reporting
from .report_ppa import (
    report_ppa,
)

# Utility functions
from .utils import (
    get_sandbox_root,
    is_path_within_sandbox,
    EDITABLE_EXTENSIONS,
)

__all__ = [
    # Reading tools
    'read_file',
    'list_files',
    # Editing tools
    'edit_file',
    'write_file',
    # Execution tools
    'run_openroad_flow',
    # Metrics reporting
    'report_ppa',
    # Utilities
    'get_sandbox_root',
    'is_path_within_sandbox',
    'EDITABLE_EXTENSIONS',
]
