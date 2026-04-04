"""Demo CLI seed command: create temp folder with config and test data."""
# pyright: reportPossiblyUnboundVariable=false
# Docker imports are conditionally available (try/except ImportError),
# guarded at runtime by _require_docker_extra().

from __future__ import annotations

import os
import tempfile
from textwrap import dedent
from pathlib import Path
from typing import Annotated, Callable

import typer
import yaml
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
)
from rich.syntax import Syntax
from rich.text import Text

from ...config import (
    CredentialProvider,
    RsyncOptions,
)
from ...remote.resolution import resolve_all_endpoints
from ...disks.lifecycle import mount_volumes, umount_volumes
from ...disks.strategy import MountStrategy

# Docker-dependent imports are deferred to seed --docker.
# They require the 'docker' extra: pipx install nbkp[docker]
try:
    from ...remote.testkit.docker import (
        BASTION_CONTAINER_NAME,
        LUKS_PASSPHRASE,
        STORAGE_CONTAINER_NAME,
        DOCKER_DIR,
        build_docker_image,
        check_docker,
        create_docker_network,
        create_test_ssh_endpoint,
        generate_ssh_keypair,
        read_luks_metadata,
        ssh_exec,
        start_bastion_container,
        start_storage_container,
        wait_for_ssh,
    )

    _HAS_DOCKER = True
except ImportError:
    _HAS_DOCKER = False
from ...sync.testkit.seed import (
    build_chain_config,
    build_local_chain_config,
    create_seed_sentinels,
    seed_volume,
)
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


class StepProgressBar:
    """Rich progress bar for multi-step operations.

    Shows a spinner, description (current step label), visual bar,
    and M/N counter.  Result lines (✓/✗) are printed above the bar
    as each step completes.
    """

    def __init__(self, total: int) -> None:
        self._total = total
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def on_start(self, label: str) -> None:
        """Call before each step begins.

        *label* is the in-progress description shown next to the
        spinner (e.g. ``"Building Docker image..."``).
        """
        if self._progress is None:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                transient=True,
            )
            self._progress.start()
            self._task_id = self._progress.add_task(label, total=self._total)
        else:
            assert self._task_id is not None
            self._progress.update(self._task_id, description=label)

    def on_end(self, label: str, success: bool, detail: str | None = None) -> None:
        """Call after each step completes.

        *label* is the shorter result line printed above the bar
        (e.g. ``"build Docker image"``).
        """
        if self._progress is not None:
            assert self._task_id is not None
            icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
            detail_str = f" ({detail})" if detail else ""
            self._progress.console.print(f"{icon} {label}{detail_str}")
            self._progress.advance(self._task_id)

    def stop(self) -> None:
        """Stop the progress bar (idempotent)."""
        if self._progress is not None:
            self._progress.stop()


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
    rsync_opts = (
        RsyncOptions(extra_options=[f"--bwlimit={bandwidth_limit}"])
        if bandwidth_limit
        else RsyncOptions()
    )

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

    # ── Step count for progress bar ────────────────────────────
    # Docker: build image, create network, start bastion, wait bastion SSH,
    #         start storage, wait storage SSH = 6
    # LUKS:   read metadata, mount, umount = 3
    # Always: seed volumes = 1
    step_count = 1  # seed volumes
    if docker:
        step_count += 6
    if docker and luks:
        step_count += 3  # read metadata + mount + umount

    bar = StepProgressBar(step_count)

    # ── Server and bastion containers ────────────────────────
    storage_endpoint = None
    bastion_endpoint = None
    if docker:
        private_key, pub_key = generate_ssh_keypair(tmp)

        bar.on_start("Building Docker image...")
        build_docker_image()
        bar.on_end("build Docker image", True)

        bar.on_start("Creating Docker network...")
        network_name = create_docker_network()
        bar.on_end("create Docker network", True)

        bar.on_start("Starting bastion container...")
        bastion_port = start_bastion_container(pub_key, network_name)
        bar.on_end("start bastion container", True)

        bastion_endpoint = create_test_ssh_endpoint(
            "bastion", "127.0.0.1", bastion_port, private_key
        )
        bar.on_start("Waiting for bastion SSH...")
        wait_for_ssh(bastion_endpoint)
        bar.on_end("bastion SSH", True)

        bar.on_start("Starting storage container...")
        storage_port = start_storage_container(
            pub_key,
            network_name=network_name,
            network_alias="backup-server",
        )
        bar.on_end("start storage container", True)

        storage_endpoint = create_test_ssh_endpoint(
            "storage", "127.0.0.1", storage_port, private_key
        )
        bar.on_start("Waiting for storage SSH...")
        wait_for_ssh(storage_endpoint)
        bar.on_end("storage SSH", True)

    # ── Config — chain layout matching integration test ──────
    luks_uuid: str | None = None
    if docker:
        assert bastion_endpoint is not None
        assert storage_endpoint is not None
        proxied_endpoint = create_test_ssh_endpoint(
            "via-bastion",
            "backup-server",
            22,
            private_key,
            proxy_jump="bastion",
        )

        if luks:
            bar.on_start("Reading LUKS metadata...")
            meta = read_luks_metadata(storage_endpoint)
            if meta.available:
                luks_uuid = meta.uuid
                bar.on_end("read LUKS metadata", True)
            else:
                bar.on_end("read LUKS metadata", False, "dm-crypt unavailable")
                bar.stop()
                _console.print(
                    "[red]LUKS unavailable[/red]"
                    " (dm-crypt kernel module missing?)."
                    "\nUse [bold]--no-luks[/bold] to skip encrypted volume setup.",
                    highlight=False,
                )
                raise typer.Exit(1)

        config = build_chain_config(
            tmp,
            bastion_endpoint,
            proxied_endpoint,
            luks_uuid=luks_uuid,
            rsync_options=rsync_opts,
            max_snapshots=5,
            credential_provider=(
                credential_provider
                if luks_uuid is not None
                else CredentialProvider.KEYRING
            ),
        )
    else:
        # Local-only: 2-step chain (src → HL → dst)
        config = build_local_chain_config(
            tmp,
            rsync_options=rsync_opts,
            max_snapshots=5,
        )

    # ── Create sentinels and seed data ───────────────────────
    # When LUKS is active, temporarily mount the encrypted volume
    # so create_seed_sentinels can set up btrfs staging subvolumes.
    size_bytes = big_file_size * 1024 * 1024
    if docker:
        assert storage_endpoint is not None
        _ep = storage_endpoint

        def _run_remote(cmd: str) -> None:
            ssh_exec(_ep, cmd)

        remote_exec: Callable[[str], None] | None = _run_remote
    else:
        remote_exec = None

    resolved = resolve_all_endpoints(config)

    mount_strategy: dict[str, MountStrategy] = {}
    if luks_uuid is not None:
        bar.on_start("Mounting encrypted volume...")
        mount_strategy, mount_results = mount_volumes(
            config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
        )
        mount_failed = next((r for r in mount_results if not r.success), None)
        if mount_failed is not None:
            bar.on_end("mount encrypted volume", False, mount_failed.detail)
            bar.stop()
            raise typer.Exit(1)
        bar.on_end("mount encrypted volume", True)

    try:
        bar.on_start("Seeding volumes...")
        create_seed_sentinels(config, remote_exec=remote_exec)
        seed_volume(
            config.volumes["src-local-bare"],
            big_file_size_bytes=size_bytes,
        )
        bar.on_end("seed volumes", True)
    finally:
        if luks_uuid is not None:
            bar.on_start("Unmounting encrypted volume...")
            umount_volumes(
                config,
                resolved,
                mount_strategy=mount_strategy,
            )
            bar.on_end("umount encrypted volume", True)
        bar.stop()

    config_path = tmp / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            config.model_dump(by_alias=True, mode="json"),
            default_flow_style=False,
            sort_keys=False,
        )
    )

    backup_sh = tmp / "backup.sh"

    # Print summary
    rows = [
        ("Seed directory", str(tmp)),
        ("Config file", str(config_path)),
        *(
            [
                ("Bastion", f"{BASTION_CONTAINER_NAME} (port {bastion_endpoint.port})"),
                ("Storage", f"{STORAGE_CONTAINER_NAME} (port {storage_endpoint.port})"),
            ]
            if bastion_endpoint is not None and storage_endpoint is not None
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

    pfx = _cmd_prefix()
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
        if luks_uuid is not None
        else ["# No LUKS passphrase needed for this config"]
    )
    # 8-space indent matches the dedent() block below
    luks_setup_line = ("\n" + " " * 8).join(luks_setup_lines)
    commands = (
        dedent(f"""\
        CFG="{config_path}"
        SH="{backup_sh}"
        {luks_setup_line}
        # Show parsed configuration
        {pfx}nbkp config show --config $CFG

        # Show configuration as JSON
        {pfx}nbkp config show --config $CFG --output json

        # Volume and sync health checks
        {pfx}nbkp preflight check --config $CFG

        # Preview what rsync would do without changes
        {pfx}nbkp run --config $CFG --dry-run

        # Execute backup syncs
        {pfx}nbkp run --config $CFG

        # Show snapshot details
        {pfx}nbkp snapshots show --config $CFG

        # Prune old btrfs snapshots
        {pfx}nbkp snapshots prune --config $CFG

        # Mount the volumes (the standalone bash script does not handle volume management)
        {pfx}nbkp disks mount --config $CFG

        # Show the status of the volumes
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

        # Unmount the volumes
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
