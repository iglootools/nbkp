import shlex
from pathlib import Path
from textwrap import dedent
from typing import Annotated

import typer
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from ...clidisplay import StepProgressBar
from ...config import (
    CredentialProvider,
)
from ...remote.testkit.constants import (
    BASTION_CONTAINER_NAME,
    LUKS_PASSPHRASE,
    NETWORK_NAME,
    STORAGE_CONTAINER_NAME,
)
from ..seed import SeedError, SeedResult, seed_demo, seed_plan, seed_steps
from . import app, console as _console


def _luks_setup_instructions(provider: CredentialProvider) -> list[str]:
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


@app.command("seed")
def seed_cmd(
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
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="List the steps without creating directories or containers.",
        ),
    ] = False,
) -> None:
    """Create a temp folder with config and test data."""
    if dry_run:
        _print_plan(seed_plan(base_dir, docker=docker, luks=luks))
        return
    with StepProgressBar(len(seed_steps(docker=docker, luks=luks))) as bar:
        try:
            result = seed_demo(
                base_dir,
                docker=docker,
                luks=luks,
                big_file_size=big_file_size,
                bandwidth_limit=bandwidth_limit,
                credential_provider=credential_provider,
                on_step_start=bar.on_start,
                on_step_end=bar.on_end,
            )
        except SeedError as e:
            _console.print(Text(str(e), style="red"), highlight=False)
            raise typer.Exit(1)

    _print_summary(result, docker, credential_provider)


def _print_plan(steps: list[str]) -> None:
    _console.print(Text("Dry run — nothing created. Seed would:", style="bold"))
    for step in steps:
        _console.print(Text(f"  - {step}"), highlight=False)


def _print_summary(
    result: SeedResult,
    docker: bool,
    credential_provider: CredentialProvider,
) -> None:
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
    summary = Text("\n").join(
        Text.assemble((f"{label:<{label_w}}  ", "bold"), value) for label, value in rows
    )
    _console.print(Panel(summary, border_style="blue", padding=(0, 1)))

    has_luks = any(
        v.mount is not None and v.mount.encryption is not None
        for v in result.config.volumes.values()
    )
    _print_commands(result, docker, has_luks, credential_provider)


_SUGGESTED_COMMANDS = dedent("""\
    # Show parsed configuration
    nbkp config show --config "$CFG"

    # Show configuration as JSON
    nbkp config show --config "$CFG" --output json

    # Disk and sync health checks
    nbkp preflight check --config "$CFG"

    # Preview what rsync would do without changes
    nbkp run --config "$CFG" --dry-run

    # Execute backup syncs
    nbkp run --config "$CFG"

    # Show snapshot details
    nbkp snapshots show --config "$CFG"

    # Prune old btrfs snapshots
    nbkp snapshots prune --config "$CFG"

    # Mount the disks (the standalone bash script does not handle disk management)
    nbkp disks mount --config "$CFG"

    # Show the status of the disks
    nbkp disks status --config "$CFG"

    # Generate standalone bash script to stdout
    nbkp sh --config "$CFG"

    # Write script to file, validate, and run
    nbkp sh --config "$CFG" -o "$SH" \\
      && bash -n "$SH" \\
      && "$SH" --dry-run \\
      && "$SH"

    # With relative paths (src and dst)
    nbkp sh --config "$CFG" -o "$SH" --relative-src --relative-dst \\
      && bash -n "$SH" \\
      && "$SH" --dry-run \\
      && "$SH"

    # Unmount the disks
    nbkp disks umount --config "$CFG\"""")


def _docker_teardown() -> str:
    return dedent(f"""\
        # Teardown containers and network
        docker rm -f {STORAGE_CONTAINER_NAME} {BASTION_CONTAINER_NAME}
        docker network rm {NETWORK_NAME}""")


def commands_script(
    result: SeedResult,
    docker: bool,
    has_luks: bool,
    credential_provider: CredentialProvider,
) -> str:
    """The suggested shell session, assembled from dedented blocks.

    Blocks are joined *after* dedenting — splicing multi-line text into a
    dedent template would need the template's indent hardcoded — and the
    paths are shell-quoted, with every use of ``$CFG`` / ``$SH`` in quotes.
    """
    variables = "\n".join(
        [
            f"CFG={shlex.quote(str(result.config_path))}",
            f"SH={shlex.quote(str(result.base_dir / 'backup.sh'))}",
        ]
    )
    luks_setup = "\n".join(
        _luks_setup_instructions(credential_provider)
        if has_luks
        else ["# No LUKS passphrase needed for this config"]
    )
    return "\n\n".join(
        [
            variables,
            luks_setup,
            _SUGGESTED_COMMANDS,
            *([_docker_teardown()] if docker else []),
        ]
    )


def _print_commands(
    result: SeedResult,
    docker: bool,
    has_luks: bool,
    credential_provider: CredentialProvider,
) -> None:
    _console.print(
        Panel(
            Syntax(
                commands_script(result, docker, has_luks, credential_provider),
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
