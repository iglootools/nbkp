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
from rich.syntax import Syntax
from rich.text import Text

from ..config import (
    CredentialProvider,
    RsyncOptions,
)
from ..remote.resolution import resolve_all_endpoints
from ..mount.detection import resolve_mount_strategy
from ..mount.lifecycle import mount_volumes, umount_volumes

# Docker-dependent imports are deferred to seed --docker.
# They require the 'docker' extra: pipx install nbkp[docker]
try:
    from ..remote.testkit.docker import (
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
from ..sync.testkit.seed import (
    build_chain_config,
    build_local_chain_config,
    create_seed_sentinels,
    seed_volume,
)
from .app import app, console as _console


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

    # Server and bastion containers
    storage_endpoint = None
    bastion_endpoint = None
    if docker:
        private_key, pub_key = generate_ssh_keypair(tmp)

        with _console.status("Building Docker image..."):
            build_docker_image()

        with _console.status("Creating Docker network..."):
            network_name = create_docker_network()

        with _console.status("Starting bastion container..."):
            bastion_port = start_bastion_container(pub_key, network_name)
        bastion_endpoint = create_test_ssh_endpoint(
            "bastion", "127.0.0.1", bastion_port, private_key
        )
        with _console.status("Waiting for bastion SSH..."):
            wait_for_ssh(bastion_endpoint)

        with _console.status("Starting storage container..."):
            storage_port = start_storage_container(
                pub_key,
                network_name=network_name,
                network_alias="backup-server",
            )
        storage_endpoint = create_test_ssh_endpoint(
            "storage", "127.0.0.1", storage_port, private_key
        )
        with _console.status("Waiting for storage SSH..."):
            wait_for_ssh(storage_endpoint)

    # Config — chain layout matching integration test
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
            with _console.status("Reading LUKS metadata..."):
                meta = read_luks_metadata(storage_endpoint)
            if meta.available:
                luks_uuid = meta.uuid
            else:
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

    # Create sentinels and seed data.
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
    mount_strategy = resolve_mount_strategy(config, resolved, names=None)

    if luks_uuid is not None:
        with _console.status("Mounting encrypted volume..."):
            mount_results = mount_volumes(
                config,
                resolved,
                lambda _: LUKS_PASSPHRASE,
                mount_strategy=mount_strategy,
            )
            for r in mount_results:
                if not r.success:
                    _console.print(
                        f"[red]Mount failed:[/red] {r.volume_slug}: {r.detail}"
                    )
                    raise typer.Exit(1)

    try:
        with _console.status("Setting up volumes..."):
            create_seed_sentinels(config, remote_exec=remote_exec)
            seed_volume(
                config.volumes["src-local-bare"],
                big_file_size_bytes=size_bytes,
            )
    finally:
        if luks_uuid is not None:
            with _console.status("Unmounting encrypted volume..."):
                umount_volumes(
                    config,
                    resolved,
                    mount_strategy=mount_strategy,
                )

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
        {pfx}nbkp check --config $CFG

        # Preview what rsync would do without changes
        {pfx}nbkp run --config $CFG --dry-run

        # Execute backup syncs
        {pfx}nbkp run --config $CFG

        # Prune old btrfs snapshots
        {pfx}nbkp prune --config $CFG

        # Mount the volumes (the standalone bash script does not handle volume management)
        {pfx}nbkp volumes mount --config $CFG

        # Show the status of the volumes
        {pfx}nbkp volumes status --config $CFG

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
        nbkp volumes umount --config $CFG""")
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
