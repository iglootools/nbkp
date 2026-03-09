"""YAML configuration loading, parsing, and validation."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .protocol import Config


class ConfigError(Exception):
    """Raised when configuration is invalid."""


def find_config_file(config_path: str | None = None) -> Path:
    """Find the configuration file using search order.

    Order: explicit path > XDG_CONFIG_HOME > /etc/nbkp/
    """
    if config_path is not None:
        p = Path(config_path)
        if not p.is_file():
            raise ConfigError(f"Config file not found: {config_path}")
        else:
            return p
    else:
        xdg = os.environ.get(
            "XDG_CONFIG_HOME",
            os.path.expanduser("~/.config"),
        )
        xdg_path = Path(xdg) / "nbkp" / "config.yaml"
        etc_path = Path("/etc/nbkp/config.yaml")
        if xdg_path.is_file():
            return xdg_path
        elif etc_path.is_file():
            return etc_path
        else:
            raise ConfigError(
                "No config file found. Searched: "
                f"{xdg_path}, /etc/nbkp/config.yaml"
            )


def load_config(config_path: str | None = None) -> Config:
    """Load and validate configuration from a YAML file."""
    path = find_config_file(config_path)
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError("Config file must be a YAML mapping")
    else:
        try:
            config = Config.model_validate(raw)
        except Exception as e:
            raise ConfigError(str(e)) from e
        return config
