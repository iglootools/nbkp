"""CLI sh command."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from ..config import NetworkType
from ..scriptgen import ScriptOptions, generate_script
from .app import app
from .common import load_config_or_exit, resolve_endpoints


@app.command()
def sh(
    config: Annotated[
        Optional[str],
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    output_file: Annotated[
        Optional[str],
        typer.Option(
            "--output-file",
            "-o",
            help="Write script to file (made executable)",
        ),
    ] = None,
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
    location: Annotated[
        Optional[list[str]],
        typer.Option(
            "--location",
            "-l",
            help="Prefer endpoints at these locations",
        ),
    ] = None,
    exclude_location: Annotated[
        Optional[list[str]],
        typer.Option(
            "--exclude-location",
            "-L",
            help="Exclude endpoints at these locations",
        ),
    ] = None,
    network: Annotated[
        Optional[NetworkType],
        typer.Option(
            "--network",
            "-N",
            help="Prefer private (LAN) or public (WAN) endpoints",
        ),
    ] = None,
    portable: Annotated[
        bool,
        typer.Option(
            "--portable/--no-portable",
            help=("Generate bash 3.2-compatible script (default: enabled)"),
        ),
    ] = True,
    platform: Annotated[
        str,
        typer.Option(
            "--platform",
            help=(
                "Target platform for snapshot timestamp format"
                " (e.g. 'darwin', 'linux'). Defaults to the current OS."
            ),
            # sys.platform varies by OS, so the generated CLI docs would differ
            # between macOS and Linux, breaking CI's clidocs-check.
            show_default=False,
        ),
    ] = sys.platform,
) -> None:
    """Compile the config into a self-contained bash script. The generated script performs the same operations as `run` without requiring Python or the config file at runtime.

    This is useful for deploying to systems without Python, or eyeballing the actual commands before letting anything touch your data.
    """
    if (relative_src or relative_dst) and output_file is None:
        typer.echo(
            "Error: --relative-src/--relative-dst require --output-file",
            err=True,
        )
        raise typer.Exit(2)

    cfg = load_config_or_exit(config)
    resolved = resolve_endpoints(cfg, location, exclude_location, network)
    script = generate_script(
        cfg,
        ScriptOptions(
            config_path=config,
            output_file=(os.path.abspath(output_file) if output_file else None),
            relative_src=relative_src,
            relative_dst=relative_dst,
            portable=portable,
            platform=platform,
        ),
        resolved_endpoints=resolved,
    )
    if output_file is not None:
        path = Path(output_file)
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        typer.echo(f"Written to {output_file}", err=True)
    else:
        typer.echo(script)
