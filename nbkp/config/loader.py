"""Finding the config file on disk and loading it into a validated :class:`Config`."""

import os
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path

import yaml
from platformdirs import site_config_dir, user_config_dir
from pydantic import ValidationError

from nbkp.config.protocol import Config

_APP = "nbkp"
_FILENAME = "config.yaml"

type ConfigDirFn = Callable[[str], str]
"""Where the platform keeps an app's config directory, given the app name
(``platformdirs.user_config_dir`` / ``site_config_dir``)."""
_CONFIG_REFERENCE_URL = (
    "https://github.com/iglootools/nbkp/blob/main/docs/config-reference.md"
)
_CONFIG_EXAMPLES_URL = (
    "https://github.com/iglootools/nbkp/tree/main/docs/config-examples"
)


class ConfigErrorKind(StrEnum):
    """Why the config could not be loaded: the ``reason`` of a :class:`ConfigError`."""

    FILE_NOT_FOUND = "file-not-found"
    UNREADABLE = "unreadable"
    NO_CONFIG_FOUND = "no-config-found"
    INVALID_YAML = "invalid-yaml"
    NOT_A_MAPPING = "not-a-mapping"
    VALIDATION = "validation"
    INVALID_SSH_CONFIG = "invalid-ssh-config"


class ConfigError(Exception):
    """
    The config could not be loaded; *reason* says why.

    *path* is the file at fault (the SSH config for ``INVALID_SSH_CONFIG``),
    *searched* the paths looked through when no file was found, and *detail*
    the underlying error.
    """

    def __init__(
        self,
        reason: ConfigErrorKind,
        *,
        path: str | Path | None = None,
        searched: tuple[Path, ...] = (),
        detail: str | None = None,
    ) -> None:
        """Build the error from its reason and the context that explains it."""
        self.reason = reason
        self.path = path
        self.searched = searched
        self.detail = detail
        super().__init__(self._message())

    def _message(self) -> str:
        match self.reason:
            case ConfigErrorKind.FILE_NOT_FOUND:
                return f"Config file not found: {self.path}"
            case ConfigErrorKind.UNREADABLE:
                return f"Cannot read config file {self.path}: {self.detail}"
            case ConfigErrorKind.NO_CONFIG_FOUND:
                searched = ", ".join(str(p) for p in self.searched)
                return (
                    f"No config file found. Searched: {searched}."
                    f" Create one at {self.searched[0]} or pass --config PATH"
                    f" (format: {_CONFIG_REFERENCE_URL}). Example configs to start"
                    f" from: {_CONFIG_EXAMPLES_URL}."
                )
            case ConfigErrorKind.INVALID_YAML:
                return f"Invalid YAML in {self.path}: {self.detail}"
            case ConfigErrorKind.NOT_A_MAPPING:
                return f"Config file {self.path} must be a YAML mapping"
            case ConfigErrorKind.VALIDATION:
                return f"Invalid config {self.path}: {self.detail}"
            case ConfigErrorKind.INVALID_SSH_CONFIG:
                return f"Invalid SSH config {self.path}: {self.detail}"


def _xdg_config_home(environ: Mapping[str, str], home: Path) -> Path:
    """
    Return ``$XDG_CONFIG_HOME`` when valid, else the default under *home*.

    The XDG spec says an empty or relative ``$XDG_CONFIG_HOME`` is invalid and must be
    ignored: honouring it would search the working directory.
    """
    xdg = Path(environ.get("XDG_CONFIG_HOME", ""))
    return xdg if xdg.is_absolute() else home / ".config"


def _config_search_paths(
    environ: Mapping[str, str],
    home: Path,
    user_dir: ConfigDirFn,
    site_dir: ConfigDirFn,
) -> list[Path]:
    """
    Config file search paths in priority order.

    Order: XDG > platform user config > platform site config.
    On Linux, XDG and platform user config resolve to the same path
    and are deduped (dict.fromkeys preserves insertion order).
    Added explicitly so that ~/.config/nbkp/config.yml works on Mac OS X
    (for which user_config_dir(_APP) defaults to ~/Library/Application Support/nbkp).
    """
    candidates = [
        _xdg_config_home(environ, home) / _APP / _FILENAME,
        Path(user_dir(_APP)) / _FILENAME,
        Path(site_dir(_APP)) / _FILENAME,
    ]
    return list(dict.fromkeys(candidates))


def find_config_file(
    config_path: str | Path | None = None,
    environ: Mapping[str, str] = os.environ,
    *,
    home: Path | None = None,
    user_dir: ConfigDirFn = user_config_dir,
    site_dir: ConfigDirFn = site_config_dir,
) -> Path:
    """
    Find the configuration file using search order.

    Order: explicit path > XDG > platform user config > platform site config.
    The search reads *environ*, *home* (the user's home directory, by
    default the process's) and the platform directories, so a caller can
    keep it off the real ones.
    """
    if config_path is not None:
        p = Path(config_path)
        if not p.is_file():
            raise ConfigError(ConfigErrorKind.FILE_NOT_FOUND, path=config_path)
        else:
            return p
    else:
        search = _config_search_paths(
            environ,
            home if home is not None else Path.home(),
            user_dir,
            site_dir,
        )
        found = next((p for p in search if p.is_file()), None)
        if found is not None:
            return found
        else:
            raise ConfigError(ConfigErrorKind.NO_CONFIG_FOUND, searched=tuple(search))


def load_config(
    config_path: str | Path | None = None,
    environ: Mapping[str, str] = os.environ,
    *,
    home: Path | None = None,
    user_dir: ConfigDirFn = user_config_dir,
    site_dir: ConfigDirFn = site_config_dir,
) -> Config:
    """
    Load and validate the config file.

    Raises ``ConfigError`` when the file is missing or unreadable, is not a YAML
    mapping, or fails validation; ``reason`` says which.
    """
    path = find_config_file(
        config_path,
        environ,
        home=home,
        user_dir=user_dir,
        site_dir=site_dir,
    )
    try:
        with Path(path).open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as e:
        raise ConfigError(
            ConfigErrorKind.UNREADABLE,
            path=path,
            detail=e.strerror or str(e),
        ) from e
    except yaml.YAMLError as e:
        raise ConfigError(ConfigErrorKind.INVALID_YAML, path=path, detail=str(e)) from e
    except UnicodeDecodeError as e:
        raise ConfigError(
            ConfigErrorKind.INVALID_YAML,
            path=path,
            detail=f"not UTF-8 (byte {e.object[e.start]:#04x} at offset {e.start})",
        ) from e

    if not isinstance(raw, dict):
        raise ConfigError(ConfigErrorKind.NOT_A_MAPPING, path=path)
    else:
        try:
            config = Config.model_validate(raw)
        except ValidationError as e:
            raise ConfigError(
                ConfigErrorKind.VALIDATION,
                path=path,
                detail=str(e),
            ) from e
        return config
