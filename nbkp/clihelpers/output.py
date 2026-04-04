"""Output format enum shared across CLI commands."""

from __future__ import annotations

import enum


class OutputFormat(str, enum.Enum):
    """Output format for CLI commands."""

    HUMAN = "human"
    JSON = "json"
