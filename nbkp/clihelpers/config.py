"""Config loading helpers for CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer

from ..config import Config, ConfigError, load_config
from ..config.output import print_config_error


def load_config_or_exit(
    config_path: str | Path | None,
) -> Config:
    """Load config or exit with code 2 on error."""
    try:
        return load_config(config_path)
    except ConfigError as e:
        print_config_error(e)
        raise typer.Exit(2)
