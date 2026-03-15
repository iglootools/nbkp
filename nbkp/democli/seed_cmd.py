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
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    RsyncOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)

# Docker-dependent imports are deferred to seed --docker.
# They require the 'docker' extra: pipx install nbkp[docker]
try:
    from ..remote.testkit.docker import (
        BASTION_CONTAINER_NAME,
        STORAGE_CONTAINER_NAME,
        DOCKER_DIR,
        REMOTE_BACKUP_PATH,
        REMOTE_BTRFS_PATH,
        build_docker_image,
        check_docker,
        create_docker_network,
        create_test_ssh_endpoint,
        generate_ssh_keypair,
        ssh_exec,
        start_bastion_container,
        start_storage_container,
        wait_for_ssh,
    )

    _HAS_DOCKER = True
except ImportError:
    _HAS_DOCKER = False
from ..sync.testkit.seed import (
    SEED_EXCLUDE_FILTERS,
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
            "--docker",
            help="Start a Docker container for remote syncs.",
        ),
    ] = False,
    bandwidth_limit: Annotated[
        int,
        typer.Option(
            "--bandwidth-limit",
            help="Rsync bandwidth limit in KiB/s"
            " (e.g. 100 for ~100 KiB/s)."
            " Set to 0 to disable.",
        ),
    ] = 250,
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
    hl_dst = HardLinkSnapshotConfig(enabled=True, max_snapshots=5)

    ssh_endpoints: dict[str, SshEndpoint] = {}
    volumes: dict[str, LocalVolume | RemoteVolume] = {
        "src-local-bare": LocalVolume(
            slug="src-local-bare",
            path=str(tmp / "src-local-bare"),
        ),
        "stage-local-hl-snapshots": LocalVolume(
            slug="stage-local-hl-snapshots",
            path=str(tmp / "stage-local-hl-snapshots"),
        ),
        "dst-local-bare": LocalVolume(
            slug="dst-local-bare",
            path=str(tmp / "dst-local-bare"),
        ),
    }
    sync_endpoints: dict[str, SyncEndpoint] = {
        "ep-src-local": SyncEndpoint(
            slug="ep-src-local",
            volume="src-local-bare",
        ),
        "ep-stage-local-hl": SyncEndpoint(
            slug="ep-stage-local-hl",
            volume="stage-local-hl-snapshots",
            hard_link_snapshots=hl_dst,
        ),
        "ep-dst-local": SyncEndpoint(
            slug="ep-dst-local",
            volume="dst-local-bare",
        ),
    }
    syncs: dict[str, SyncConfig] = {
        # local→local, HL destination
        "step-1": SyncConfig(
            slug="step-1",
            source="ep-src-local",
            destination="ep-stage-local-hl",
            rsync_options=rsync_opts,
            filters=SEED_EXCLUDE_FILTERS,
        ),
    }

    if docker:
        assert storage_endpoint is not None
        assert bastion_endpoint is not None
        btrfs_snapshots_path = f"{REMOTE_BTRFS_PATH}/snapshots"
        btrfs_bare_path = f"{REMOTE_BTRFS_PATH}/bare"
        btrfs_dst = BtrfsSnapshotConfig(enabled=True, max_snapshots=5)

        ssh_endpoints["bastion"] = bastion_endpoint
        ssh_endpoints["storage"] = storage_endpoint
        ssh_endpoints["via-bastion"] = create_test_ssh_endpoint(
            "via-bastion",
            "backup-server",
            22,
            private_key,
            proxy_jump="bastion",
        )
        volumes.update(
            {
                "stage-remote-bare": RemoteVolume(
                    slug="stage-remote-bare",
                    ssh_endpoint="via-bastion",
                    path=f"{REMOTE_BACKUP_PATH}/bare",
                ),
                "stage-remote-btrfs-snapshots": RemoteVolume(
                    slug="stage-remote-btrfs-snapshots",
                    ssh_endpoint="via-bastion",
                    path=btrfs_snapshots_path,
                ),
                "stage-remote-btrfs-bare": RemoteVolume(
                    slug="stage-remote-btrfs-bare",
                    ssh_endpoint="via-bastion",
                    path=btrfs_bare_path,
                ),
                "stage-remote-hl-snapshots": RemoteVolume(
                    slug="stage-remote-hl-snapshots",
                    ssh_endpoint="via-bastion",
                    path=f"{REMOTE_BACKUP_PATH}/hl",
                ),
            }
        )
        sync_endpoints.update(
            {
                "ep-remote-bare": SyncEndpoint(
                    slug="ep-remote-bare",
                    volume="stage-remote-bare",
                ),
                "ep-remote-btrfs": SyncEndpoint(
                    slug="ep-remote-btrfs",
                    volume="stage-remote-btrfs-snapshots",
                    btrfs_snapshots=btrfs_dst,
                ),
                "ep-remote-btrfs-bare": SyncEndpoint(
                    slug="ep-remote-btrfs-bare",
                    volume="stage-remote-btrfs-bare",
                ),
                "ep-remote-hl": SyncEndpoint(
                    slug="ep-remote-hl",
                    volume="stage-remote-hl-snapshots",
                    hard_link_snapshots=hl_dst,
                ),
            }
        )
        syncs.update(
            {
                # local→remote (bastion), bare dest
                "step-2": SyncConfig(
                    slug="step-2",
                    source="ep-stage-local-hl",
                    destination="ep-remote-bare",
                    rsync_options=rsync_opts,
                    filters=SEED_EXCLUDE_FILTERS,
                ),
                # remote→remote (bastion), btrfs dest
                "step-3": SyncConfig(
                    slug="step-3",
                    source="ep-remote-bare",
                    destination="ep-remote-btrfs",
                    rsync_options=rsync_opts,
                    filters=SEED_EXCLUDE_FILTERS,
                ),
                # remote→remote (bastion), bare on btrfs
                "step-4": SyncConfig(
                    slug="step-4",
                    source="ep-remote-btrfs",
                    destination="ep-remote-btrfs-bare",
                    rsync_options=rsync_opts,
                    filters=SEED_EXCLUDE_FILTERS,
                ),
                # remote→remote (bastion), HL dest
                "step-5": SyncConfig(
                    slug="step-5",
                    source="ep-remote-btrfs-bare",
                    destination="ep-remote-hl",
                    rsync_options=rsync_opts,
                    filters=SEED_EXCLUDE_FILTERS,
                ),
                # remote (bastion)→local, bare dest
                "step-6": SyncConfig(
                    slug="step-6",
                    source="ep-remote-hl",
                    destination="ep-dst-local",
                    rsync_options=rsync_opts,
                    filters=SEED_EXCLUDE_FILTERS,
                ),
            }
        )
    else:
        # Local-only: step-2 goes directly to dst
        syncs["step-2"] = SyncConfig(
            slug="step-2",
            source="ep-stage-local-hl",
            destination="ep-dst-local",
            rsync_options=rsync_opts,
            filters=SEED_EXCLUDE_FILTERS,
        )

    config = Config(
        ssh_endpoints=ssh_endpoints,
        volumes=volumes,
        sync_endpoints=sync_endpoints,
        syncs=syncs,
    )

    # Create sentinels and seed data
    size_bytes = big_file_size * 1024 * 1024
    if docker:
        assert storage_endpoint is not None
        _ep = storage_endpoint

        def _run_remote(cmd: str) -> None:
            ssh_exec(_ep, cmd)

        remote_exec: Callable[[str], None] | None = _run_remote
    else:
        remote_exec = None

    with _console.status("Setting up volumes..."):
        create_seed_sentinels(config, remote_exec=remote_exec)
        seed_volume(
            config.volumes["src-local-bare"],
            big_file_size_bytes=size_bytes,
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
    commands = (
        dedent(f"""\
        CFG="{config_path}"
        SH="{backup_sh}"

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
          && $SH""")
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
