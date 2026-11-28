"""The ``nbkp sh`` command: write the config out as a standalone bash script."""

import os
import stat
import sys
from pathlib import Path
from typing import Annotated

import typer

from nbkp.cliwiring.config import load_config_or_exit, resolve_endpoints
from nbkp.cliwiring.invocation import display_path
from nbkp.cliwiring.options import (
    CONFIG_OPTION,
    EXCLUDE_LOCATION_OPTION,
    LOCATION_OPTION,
    NETWORK_OPTION,
    expand_user_path,
)
from nbkp.cliwiring.runtime import runtime_of
from nbkp.sh import ScriptOptions, VolumeWithoutPathError, generate_script


def sh_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output_file: Annotated[
        Path | None,
        typer.Option(
            "--output-file",
            "-o",
            help="Write script to file (made executable)",
            file_okay=True,
            dir_okay=False,
            callback=expand_user_path,
        ),
    ] = None,
    *,
    relative_src: Annotated[
        bool,
        typer.Option(
            "--relative-src",
            help=(
                "Make source paths relative to script location (requires --output-file)"
            ),
        ),
    ] = False,
    relative_dst: Annotated[
        bool,
        typer.Option(
            "--relative-dst",
            help=(
                "Make destination paths relative to"
                " script location"
                " (requires --output-file)"
            ),
        ),
    ] = False,
    location: LOCATION_OPTION = None,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
    portable: Annotated[
        bool,
        typer.Option(
            "--portable/--no-portable",
            help="Generate bash 3.2-compatible script",
        ),
    ] = True,
    platform: Annotated[
        str,
        typer.Option(
            "--platform",
            help=(
                "Target platform for snapshot timestamp format and for how a"
                " local volume's filesystem type is read (e.g. 'darwin',"
                " 'linux'). Defaults to the current OS."
            ),
            # sys.platform varies by OS, so the generated CLI docs would differ
            # between macOS and Linux, breaking CI's lint-clidocs.
            show_default=False,
        ),
    ] = sys.platform,
) -> None:
    """
    Compile the config into a self-contained bash script.

    The generated script performs the same operations as `run` without requiring
    Python or the config file at runtime.

    This is useful for deploying to systems without Python, or eyeballing the
    actual commands before letting anything touch your data.
    """
    if (relative_src or relative_dst) and output_file is None:
        typer.echo(
            "Error: --relative-src/--relative-dst require --output-file",
            err=True,
        )
        raise typer.Exit(2)

    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    try:
        script = generate_script(
            cfg,
            ScriptOptions(
                config_path=config,
                output_file=output_file,
                relative_src=relative_src,
                relative_dst=relative_dst,
                portable=portable,
                platform=platform,
            ),
            now=runtime.clock(),
            resolved_endpoints=resolved,
        )
    except VolumeWithoutPathError as e:
        typer.echo(_format_volume_without_path(e), err=True)
        raise typer.Exit(1) from e
    if output_file is not None:
        _write_script(output_file, script)
        typer.echo(f"Written to {display_path(output_file)}", err=True)
    else:
        typer.echo(script)


def _write_script(path: Path, script: str) -> None:
    """
    Write *script* to *path*, executable, in one atomic step.

    Written under a temporary name and renamed over *path*: bash reads a
    script as it runs, so rewriting it in place while a copy runs (from
    cron) would have that copy execute a mix of the old and new versions.
    The rename leaves it reading the old file to the end.
    """
    tmp = path.with_name(f".{path.name}.nbkp-tmp-{os.getpid()}")
    try:
        tmp.write_text(script, encoding="utf-8")
        mode = (path if path.exists() else tmp).stat().st_mode
        tmp.chmod(stat.S_IMODE(mode) | stat.S_IXUSR | stat.S_IXGRP)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _format_volume_without_path(error: VolumeWithoutPathError) -> str:
    return (
        f"Error: volume '{error.volume_slug}' has no 'path'. Generated scripts"
        " do not manage mounts, so they need each volume's path: add 'path'"
        " to the volume, and mount it with 'nbkp disks mount' before running"
        " the script."
    )
