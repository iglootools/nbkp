"""Output formatting re-exports."""

from __future__ import annotations

import enum

# Re-exports — keep public API stable for existing import sites.
from .config.output import print_config_error as print_config_error
from .config.output import print_human_config as print_human_config
from .preflight.output import build_check_sections as build_check_sections
from .preflight.output import print_human_check as print_human_check
from .preflight.output import print_human_troubleshoot as print_human_troubleshoot
from .sync.output import build_run_preview_sections as build_run_preview_sections
from .sync.output import print_human_prune_results as print_human_prune_results
from .sync.output import print_human_results as print_human_results
from .sync.output import print_run_preview as print_run_preview


class OutputFormat(str, enum.Enum):
    """Output format for CLI commands."""

    HUMAN = "human"
    JSON = "json"
