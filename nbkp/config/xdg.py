"""Resolve an XDG base directory, as the XDG Base Directory spec defines it."""

from collections.abc import Mapping
from pathlib import Path


def resolve_xdg_dir(environ: Mapping[str, str], variable: str, default: Path) -> Path:
    """
    Return the directory *variable* (``XDG_CONFIG_HOME``, …) names, else *default*.

    The spec says an empty or relative value is invalid and must be ignored:
    honouring it would resolve against the working directory.
    """
    value = Path(environ.get(variable, ""))
    return value if value.is_absolute() else default
