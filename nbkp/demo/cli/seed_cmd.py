"""The ``nbkp-demo seed`` command."""

import shlex
from pathlib import Path
from textwrap import dedent
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from nbkp.clidisplay import dry_run_labeled
from nbkp.cliwiring.progress import StepProgressBar
from nbkp.config import CredentialProvider
from nbkp.disks import MountResult, UmountResult
from nbkp.disks.output import mount_failure_detail, umount_failure_detail
lazy from nbkp.demo.seed import (
    SeedError,
    SeedErrorKind,
    SeedResult,
    seed_demo,
    seed_plan,
    seed_steps,
)
lazy from nbkp.remote.testkit.constants import (
    BASTION_CONTAINER_NAME,
    LUKS_PASSPHRASE,
    NETWORK_NAME,
    STORAGE_CONTAINER_NAME,
)

_console = Console()


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
                "# The config's credential-command prints the passphrase.",
                f"# Passphrase: {LUKS_PASSPHRASE}",
            ]


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
    *,
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
            _print_seed_error(e, docker=docker)
            raise typer.Exit(1) from e

    _print_summary(result, docker=docker, credential_provider=credential_provider)


def format_seed_error(e: SeedError) -> str:
    """Return the message shown to the user for a seed failure, with what to do."""
    match e.kind:
        case SeedErrorKind.DOCKER_EXTRA_MISSING:
            return (
                "Docker support requires the 'docker' extra."
                " Install it with: uv tool install 'nbkp[docker]'"
                " (or use --no-docker)."
            )
        case SeedErrorKind.DOCKER_UNAVAILABLE:
            return f"Docker is not available: {e.detail}"
        case SeedErrorKind.DOCKER_DIR_MISSING:
            return f"Docker directory not found: {e.detail}"
        case SeedErrorKind.DOCKER_FAILED:
            return f"Docker failed: {e.detail}"
        case SeedErrorKind.SSH_NOT_READY:
            return f"A demo container did not start SSH: {e.detail}"
        case SeedErrorKind.REMOTE_COMMAND_FAILED:
            return f"A command in a demo container failed: {e.detail}"
        case SeedErrorKind.LUKS_UNAVAILABLE:
            return (
                "LUKS unavailable (dm-crypt kernel module missing?)."
                " Use --no-luks to skip encrypted volume setup."
            )
        case SeedErrorKind.MOUNT_FAILED:
            return f"Mount failed: {_lifecycle_failure(e)}"
        case SeedErrorKind.UMOUNT_FAILED:
            return (
                f"Umount failed: {_lifecycle_failure(e)}. The encrypted volume"
                " may still be mounted; run nbkp disks umount on the seeded config."
            )
        case SeedErrorKind.FILE_OPERATION_FAILED:
            return f"Writing the demo files failed: {e.detail}"


def _lifecycle_failure(e: SeedError) -> str:
    """Render the failed mount or umount *e* carries, else its detail."""
    match e.result:
        case MountResult():
            detail = mount_failure_detail(e.result)
        case UmountResult():
            detail = umount_failure_detail(e.result)
        case None:
            detail = None
    return detail or e.detail or "unknown error"


def _print_seed_error(e: SeedError, *, docker: bool) -> None:
    _console.print(Text(format_seed_error(e), style="red"), highlight=False)
    if docker and e.may_leave_containers:
        _console.print(
            Text("The demo containers may still be running. To remove them:"),
        )
        _console.print(Text(_docker_teardown()), highlight=False)


def _print_plan(steps: list[str]) -> None:
    for step in steps:
        _console.print(dry_run_labeled(f"Would {step}"), highlight=False)


def _print_summary(
    result: SeedResult,
    *,
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
    _print_commands(
        result,
        docker=docker,
        has_luks=has_luks,
        credential_provider=credential_provider,
    )


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
    *,
    docker: bool,
    has_luks: bool,
    credential_provider: CredentialProvider,
) -> str:
    """
    Build the suggested shell session from dedented blocks.

    Blocks are joined *after* dedenting — splicing multi-line text into a
    dedent template would need the template's indent hardcoded — and the
    paths are shell-quoted, with every use of ``$CFG`` / ``$SH`` in quotes.
    """
    variables = "\n".join(
        [
            f"CFG={shlex.quote(str(result.config_path))}",
            f"SH={shlex.quote(str(result.base_dir / 'backup.sh'))}",
        ],
    )
    luks_setup = "\n".join(
        _luks_setup_instructions(credential_provider)
        if has_luks
        else ["# No LUKS passphrase needed for this config"],
    )
    return "\n\n".join(
        [
            variables,
            luks_setup,
            _SUGGESTED_COMMANDS,
            *([_docker_teardown()] if docker else []),
        ],
    )


def _print_commands(
    result: SeedResult,
    *,
    docker: bool,
    has_luks: bool,
    credential_provider: CredentialProvider,
) -> None:
    _console.print(
        Panel(
            Syntax(
                commands_script(
                    result,
                    docker=docker,
                    has_luks=has_luks,
                    credential_provider=credential_provider,
                ),
                "bash",
                theme="monokai",
                background_color="default",
                word_wrap=True,
            ),
            title="[bold]Try[/bold]",
            border_style="green",
            padding=(0, 1),
        ),
    )
