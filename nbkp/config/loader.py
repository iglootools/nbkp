"""YAML configuration loading, parsing, and validation."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

import yaml
from platformdirs import site_config_dir, user_config_dir

from .protocol import Config

_APP = "nbkp"
_FILENAME = "config.yaml"


class ConfigErrorReason(StrEnum):
    """Structured error codes for configuration failures."""

    FILE_NOT_FOUND = "file-not-found"
    NO_CONFIG_FOUND = "no-config-found"
    INVALID_YAML = "invalid-yaml"
    NOT_A_MAPPING = "not-a-mapping"
    VALIDATION = "validation"
    CYCLIC_DEPENDENCY = "cyclic-dependency"


class ConfigError(Exception):
    """Raised when configuration is invalid."""

    def __init__(self, message: str, reason: ConfigErrorReason) -> None:
        super().__init__(message)
        self.reason = reason


def _config_search_paths() -> list[Path]:
    """Config file search paths in priority order.

    Order: XDG > platform user config > platform site config.
    On Linux, XDG and platform user config resolve to the same path
    and are deduped (dict.fromkeys preserves insertion order).
    Added explicitly so that ~/.config/nbkp/config.yml works on Mac OS X
    (for which user_config_dir(_APP) defaults to ~/Library/Application Support/nbkp).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    candidates = [
        Path(xdg) / _APP / _FILENAME,  # XDG (all platforms)
        Path(user_config_dir(_APP)) / _FILENAME,  # platform user config
        Path(site_config_dir(_APP)) / _FILENAME,  # platform site config
    ]
    return list(dict.fromkeys(candidates))


def find_config_file(config_path: str | None = None) -> Path:
    """Find the configuration file using search order.

    Order: explicit path > XDG > platform user config > platform site config
    """
    if config_path is not None:
        p = Path(config_path)
        if not p.is_file():
            raise ConfigError(
                f"Config file not found: {config_path}",
                reason=ConfigErrorReason.FILE_NOT_FOUND,
            )
        else:
            return p
    else:
        search = _config_search_paths()
        found = next((p for p in search if p.is_file()), None)
        if found is not None:
            return found
        else:
            raise ConfigError(
                f"No config file found. Searched: {', '.join(str(p) for p in search)}",
                reason=ConfigErrorReason.NO_CONFIG_FOUND,
            )


def load_config(config_path: str | None = None) -> Config:
    """Load and validate configuration from a YAML file."""
    path = find_config_file(config_path)
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(
            f"Invalid YAML in {path}: {e}",
            reason=ConfigErrorReason.INVALID_YAML,
        ) from e

    if not isinstance(raw, dict):
        raise ConfigError(
            "Config file must be a YAML mapping",
            reason=ConfigErrorReason.NOT_A_MAPPING,
        )
    else:
        try:
            config = Config.model_validate(raw)
        except Exception as e:
            raise ConfigError(str(e), reason=ConfigErrorReason.VALIDATION) from e
        return config
