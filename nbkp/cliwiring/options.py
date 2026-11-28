"""
Options that several commands share, each declared once.

A command takes one as ``config: CONFIG_OPTION = None`` and keeps the default
in its own signature, as Typer requires.
"""

from pathlib import Path
from typing import Annotated

import typer

from nbkp.cliwiring.output_format import OutputFormat
from nbkp.remote.endpoints import NetworkType


def expand_user_path(path: Path | None) -> Path | None:
    """
    Return *path* with ``~`` expanded, then made absolute.

    A Typer option callback, used instead of ``resolve_path``: no shell expands the
    ``~`` in ``--config=~/x.yaml`` (after ``=``) or in a systemd unit, and
    ``resolve_path`` would turn it into ``<cwd>/~/x.yaml``.
    """
    return None if path is None else path.expanduser().resolve()


CONFIG_OPTION = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help=(
            "Path to config file. Default: the first config.yaml found in"
            " $XDG_CONFIG_HOME/nbkp (~/.config/nbkp), then the platform's user"
            " and site config directories for nbkp"
        ),
        file_okay=True,
        dir_okay=False,
        callback=expand_user_path,
    ),
]

OUTPUT_OPTION = Annotated[
    OutputFormat,
    typer.Option("--output", "-o", help="Output format"),
]

DRY_RUN_OPTION = Annotated[
    bool,
    typer.Option("--dry-run", "-n", help="Perform a dry run"),
]

SYNC_OPTION = Annotated[
    list[str] | None,
    typer.Option("--sync", "-s", help="Sync name(s) to include"),
]

LOCATION_OPTION = Annotated[
    list[str] | None,
    typer.Option(
        "--location",
        "-l",
        help="Prefer endpoints at these locations",
    ),
]

EXCLUDE_LOCATION_OPTION = Annotated[
    list[str] | None,
    typer.Option(
        "--exclude-location",
        "-L",
        help="Exclude endpoints at these locations",
    ),
]

NETWORK_OPTION = Annotated[
    NetworkType | None,
    typer.Option(
        "--network",
        "-N",
        help="Prefer private (LAN) or public (WAN) endpoints",
    ),
]

MOUNT_OPTION = Annotated[
    bool,
    typer.Option(
        "--mount/--no-mount",
        help="Mount/umount volumes with mount config",
    ),
]

UMOUNT_OPTION = Annotated[
    bool,
    typer.Option(
        "--umount/--no-umount",
        help="Umount when done (use --no-umount for debugging)",
    ),
]
