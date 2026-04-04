"""Seed orchestration: create demo environment with config and test data."""

# pyright: reportPossiblyUnboundVariable=false
# Docker imports are conditionally available (try/except ImportError),
# guarded at runtime by the CLI command.

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import yaml
from pydantic import BaseModel

from ....config import (
    Config,
    CredentialProvider,
    RsyncOptions,
)
from ....remote.resolution import resolve_all_endpoints
from ....disks.lifecycle import mount_volumes, umount_volumes
from ....disks.strategy import MountStrategy

try:
    from ....remote.testkit.docker import (
        LUKS_PASSPHRASE,
        build_docker_image,
        create_docker_network,
        create_test_ssh_endpoint,
        generate_ssh_keypair,
        read_luks_metadata,
        ssh_exec,
        start_bastion_container,
        start_storage_container,
        wait_for_ssh,
    )
except ImportError:
    pass
from ....sync.testkit.seed import (
    build_chain_config,
    build_local_chain_config,
    create_seed_sentinels,
    seed_volume,
)


class SeedResult(BaseModel):
    """Result of seeding a demo environment."""

    base_dir: Path
    config_path: Path
    config: Config
    bastion_port: Optional[int] = None
    storage_port: Optional[int] = None


def seed_demo(
    base_dir: Path,
    *,
    docker: bool = True,
    luks: bool = True,
    big_file_size: int = 1,
    bandwidth_limit: int = 250,
    credential_provider: CredentialProvider = CredentialProvider.KEYRING,
    on_step_start: Callable[[str], None] | None = None,
    on_step_end: Callable[[str, bool, str | None], None] | None = None,
) -> SeedResult:
    """Create a demo environment with config and test data.

    Parameters
    ----------
    on_step_start:
        Called before each step with an in-progress label
        (e.g. ``"Building Docker image..."``).
    on_step_end:
        Called after each step with ``(label, success, detail)``.
    """
    rsync_opts = (
        RsyncOptions(extra_options=[f"--bwlimit={bandwidth_limit}"])
        if bandwidth_limit
        else RsyncOptions()
    )

    def _start(label: str) -> None:
        if on_step_start is not None:
            on_step_start(label)

    def _end(label: str, success: bool, detail: str | None = None) -> None:
        if on_step_end is not None:
            on_step_end(label, success, detail)

    # ── Server and bastion containers ────────────────────────
    storage_endpoint = None
    bastion_endpoint = None
    bastion_port: int | None = None
    storage_port: int | None = None
    private_key: Path | None = None

    if docker:
        private_key, pub_key = generate_ssh_keypair(base_dir)

        _start("Building Docker image...")
        build_docker_image()
        _end("build Docker image", True)

        _start("Creating Docker network...")
        network_name = create_docker_network()
        _end("create Docker network", True)

        _start("Starting bastion container...")
        bastion_port = start_bastion_container(pub_key, network_name)
        _end("start bastion container", True)

        bastion_endpoint = create_test_ssh_endpoint(
            "bastion", "127.0.0.1", bastion_port, private_key
        )
        _start("Waiting for bastion SSH...")
        wait_for_ssh(bastion_endpoint)
        _end("bastion SSH", True)

        _start("Starting storage container...")
        storage_port = start_storage_container(
            pub_key,
            network_name=network_name,
            network_alias="backup-server",
        )
        _end("start storage container", True)

        storage_endpoint = create_test_ssh_endpoint(
            "storage", "127.0.0.1", storage_port, private_key
        )
        _start("Waiting for storage SSH...")
        wait_for_ssh(storage_endpoint)
        _end("storage SSH", True)

    # ── Config — chain layout matching integration test ──────
    luks_uuid: str | None = None
    if docker:
        assert bastion_endpoint is not None
        assert storage_endpoint is not None
        assert private_key is not None
        proxied_endpoint = create_test_ssh_endpoint(
            "via-bastion",
            "backup-server",
            22,
            private_key,
            proxy_jump="bastion",
        )

        if luks:
            _start("Reading LUKS metadata...")
            meta = read_luks_metadata(storage_endpoint)
            if meta.available:
                luks_uuid = meta.uuid
                _end("read LUKS metadata", True)
            else:
                _end("read LUKS metadata", False, "dm-crypt unavailable")
                raise SeedError(
                    "LUKS unavailable (dm-crypt kernel module missing?)."
                    " Use --no-luks to skip encrypted volume setup."
                )

        config = build_chain_config(
            base_dir,
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
        config = build_local_chain_config(
            base_dir,
            rsync_options=rsync_opts,
            max_snapshots=5,
        )

    # ── Create sentinels and seed data ───────────────────────
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
        _start("Mounting encrypted volume...")
        mount_strategy, mount_results = mount_volumes(
            config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
        )
        mount_failed = next((r for r in mount_results if not r.success), None)
        if mount_failed is not None:
            _end("mount encrypted volume", False, mount_failed.detail)
            raise SeedError(f"Mount failed: {mount_failed.detail}")
        _end("mount encrypted volume", True)

    try:
        _start("Seeding volumes...")
        create_seed_sentinels(config, remote_exec=remote_exec)
        seed_volume(
            config.volumes["src-local-bare"],
            big_file_size_bytes=size_bytes,
        )
        _end("seed volumes", True)
    finally:
        if luks_uuid is not None:
            _start("Unmounting encrypted volume...")
            umount_volumes(
                config,
                resolved,
                mount_strategy=mount_strategy,
            )
            _end("umount encrypted volume", True)

    config_path = base_dir / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            config.model_dump(by_alias=True, mode="json"),
            default_flow_style=False,
            sort_keys=False,
        )
    )

    return SeedResult(
        base_dir=base_dir,
        config_path=config_path,
        config=config,
        bastion_port=bastion_port,
        storage_port=storage_port,
    )


class SeedError(Exception):
    """Raised when seed fails with a user-facing message."""
