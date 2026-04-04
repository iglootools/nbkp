"""Demo CLI seed command: create temp folder with config and test data."""

# pyright: reportPossiblyUnboundVariable=false
# Docker imports are conditionally available (try/except ImportError),
# guarded at runtime by _require_docker_extra().

from __future__ import annotations

import os
import tempfile
from textwrap import dedent
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from ...clihelpers import StepProgressBar

from ...config import (
    CredentialProvider,
)

# Docker-dependent imports are deferred to seed --docker.
# They require the 'docker' extra: pipx install nbkp[docker]
try:
    from ...remote.testkit.docker import (
        BASTION_CONTAINER_NAME,
        LUKS_PASSPHRASE,
        STORAGE_CONTAINER_NAME,
        DOCKER_DIR,
        check_docker,
    )

    _HAS_DOCKER = True
except ImportError:
    _HAS_DOCKER = False
from .cmd_handler.seed import SeedError, SeedResult, seed_demo
from . import app, console as _console


def _is_dev_environment() -> bool:
    """Detect whether we're running inside a Poetry/dev venv."""
    venv = os.environ.get("VIRTUAL_ENV", "")
    return venv.endswith(".venv") or "/.venv" in venv


def _cmd_prefix() -> str:
    """Return 'poetry run ' when in dev, empty string otherwise."""
    return "poetry run " if _is_dev_environment() else ""


def _luks_setup_instructions(provider: CredentialProvider) -> list[str]:
    """Return shell lines that set up the LUKS passphrase."""
    match provider:
        case CredentialProvider.ENV:
            return [
                f'export NBKP_PASSPHRASE_TEST_LUKS="{LUKS_PASSPHRASE}"',
            ]
        case CredentialProvider.KEYRING:
            return [
                "# Set the LUKS passphrase in the keyring (one-time):",
                "keyring set nbkp test-luks",
                f"#   (enter passphrase: {LUKS_PASSPHRASE})",
            ]
        case CredentialProvider.PROMPT:
            return [
                "# You will be prompted for the LUKS passphrase at runtime.",
                f"# Passphrase: {LUKS_PASSPHRASE}",
            ]
        case CredentialProvider.COMMAND:
            return [
                "# Configure a command provider in the config to retrieve",
                f"# the passphrase. Passphrase: {LUKS_PASSPHRASE}",
            ]


def _require_docker_extra() -> None:
    """Exit with install hint if docker extra is not installed."""
    if not _HAS_DOCKER:
        typer.echo(
            "Docker support requires the 'docker' extra.\n"
            "Install it with: pipx install nbkp[docker]",
            err=True,
        )
        raise typer.Exit(1)


def _step_count(docker: bool, luks: bool) -> int:
    """Compute the total number of progress bar steps."""
    # Docker: build image, create network, start bastion, wait bastion SSH,
    #         start storage, wait storage SSH = 6
    # LUKS:   read metadata, mount, umount = 3
    # Always: seed volumes = 1
    count = 1  # seed volumes
    if docker:
        count += 6
    if docker and luks:
        count += 3
    return count


@app.command()
def seed(
    big_file_size: Annotated[
        int,
        typer.Option(
            "--big-file-size",
            help="Size in MB for large files (e.g. 100, 1024)."
            " When set, large files are written at this size"
            " to slow down syncs."
            " Set to 0 to disable.",
        ),
    ] = 1,
    docker: Annotated[
        bool,
        typer.Option(
            "--docker/--no-docker",
            help="Start a Docker container for remote syncs.",
        ),
    ] = True,
    luks: Annotated[
        bool,
        typer.Option(
            "--luks/--no-luks",
            help="Use LUKS-encrypted btrfs volume (requires --docker"
            " and dm-crypt kernel module).",
        ),
    ] = True,
    bandwidth_limit: Annotated[
        int,
        typer.Option(
            "--bandwidth-limit",
            help="Rsync bandwidth limit in KiB/s"
            " (e.g. 100 for ~100 KiB/s)."
            " Set to 0 to disable.",
        ),
    ] = 250,
    credential_provider: Annotated[
        CredentialProvider,
        typer.Option(
            "--credential-provider",
            help="How LUKS passphrases are retrieved at runtime."
            " Only relevant when --luks is enabled.",
        ),
    ] = CredentialProvider.KEYRING,
    base_dir: Annotated[
        Path | None,
        typer.Option(
            "--base-dir",
            help="Use a fixed directory instead of a random"
            " temp folder. Created if it does not exist.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Create a temp folder with config and test data."""
    if docker:
        _require_docker_extra()
        check_docker()
        if not DOCKER_DIR.is_dir():  # type: ignore[possibly-undefined]
            typer.echo(
                f"Error: Docker directory not found: {DOCKER_DIR}",
                err=True,
            )
            raise typer.Exit(1)

    if base_dir is not None:
        tmp = base_dir
        tmp.mkdir(parents=True, exist_ok=True)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="nbkp-demo-"))

    with StepProgressBar(_step_count(docker, luks)) as bar:
        try:
            result = seed_demo(
                tmp,
                docker=docker,
                luks=luks,
                big_file_size=big_file_size,
                bandwidth_limit=bandwidth_limit,
                credential_provider=credential_provider,
                on_step_start=bar.on_start,
                on_step_end=bar.on_end,
            )
        except SeedError as e:
            _console.print(f"[red]{e}[/red]", highlight=False)
            raise typer.Exit(1)

    _print_summary(result, docker, credential_provider)


def _print_summary(
    result: SeedResult,
    docker: bool,
    credential_provider: CredentialProvider,
) -> None:
    """Print the summary panel and suggested commands."""
    rows = [
        ("Seed directory", str(result.base_dir)),
        ("Config file", str(result.config_path)),
        *(
            [
                ("Bastion", f"{BASTION_CONTAINER_NAME} (port {result.bastion_port})"),
                ("Storage", f"{STORAGE_CONTAINER_NAME} (port {result.storage_port})"),
            ]
            if result.bastion_port is not None and result.storage_port is not None
            else []
        ),
    ]
    label_w = max(len(r[0]) for r in rows)
    summary = Text()
    for i, (label, value) in enumerate(rows):
        if i > 0:
            summary.append("\n")
        summary.append(f"{label:<{label_w}}  ", style="bold")
        summary.append(value)
    _console.print(Panel(summary, border_style="blue", padding=(0, 1)))

    has_luks = any(
        v.mount is not None and v.mount.encryption is not None
        for v in result.config.volumes.values()
    )
    _print_commands(result, docker, has_luks, credential_provider)


def _print_commands(
    result: SeedResult,
    docker: bool,
    has_luks: bool,
    credential_provider: CredentialProvider,
) -> None:
    """Print the suggested commands panel."""
    pfx = _cmd_prefix()
    backup_sh = result.base_dir / "backup.sh"

    docker_teardown = (
        dedent(f"""

            # Teardown containers and network
            docker rm -f {STORAGE_CONTAINER_NAME} {BASTION_CONTAINER_NAME}
            docker network rm nbkp-demo-net""")
        if docker
        else ""
    )
    luks_setup_lines = (
        _luks_setup_instructions(credential_provider)
        if has_luks
        else ["# No LUKS passphrase needed for this config"]
    )
    # 8-space indent matches the dedent() block below
    luks_setup_line = ("\n" + " " * 8).join(luks_setup_lines)
    commands = (
        dedent(f"""\
        CFG="{result.config_path}"
        SH="{backup_sh}"
        {luks_setup_line}
        # Show parsed configuration
        {pfx}nbkp config show --config $CFG

        # Show configuration as JSON
        {pfx}nbkp config show --config $CFG --output json

        # Disk and sync health checks
        {pfx}nbkp preflight check --config $CFG

        # Preview what rsync would do without changes
        {pfx}nbkp run --config $CFG --dry-run

        # Execute backup syncs
        {pfx}nbkp run --config $CFG

        # Show snapshot details
        {pfx}nbkp snapshots show --config $CFG

        # Prune old btrfs snapshots
        {pfx}nbkp snapshots prune --config $CFG

        # Mount the disks (the standalone bash script does not handle disk management)
        {pfx}nbkp disks mount --config $CFG

        # Show the status of the disks
        {pfx}nbkp disks status --config $CFG

        # Generate standalone bash script to stdout
        {pfx}nbkp sh --config $CFG

        # Write script to file, validate, and run
        {pfx}nbkp sh --config $CFG -o $SH \\
          && bash -n $SH \\
          && $SH --dry-run \\
          && $SH

        # With relative paths (src and dst)
        {pfx}nbkp sh --config $CFG -o $SH --relative-src --relative-dst \\
          && bash -n $SH \\
          && $SH --dry-run \\
          && $SH

        # Unmount the disks
        nbkp disks umount --config $CFG""")
        + docker_teardown
    )
    _console.print(
        Panel(
            Syntax(
                commands,
                "bash",
                theme="monokai",
                background_color="default",
                word_wrap=True,
            ),
            title="[bold]Try[/bold]",
            border_style="green",
            padding=(0, 1),
        )
    )
