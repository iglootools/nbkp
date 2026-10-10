"""Seed orchestration: create demo environment with config and test data."""

# pyright: reportPossiblyUnboundVariable=false
# Docker imports are conditionally available (try/except ImportError),
# guarded at runtime by _require_docker().

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from ....config import (
    Config,
    CredentialProvider,
    RsyncOptions,
    SshEndpoint,
)
from ....config.epresolution import ResolvedEndpoints
from ....disks.lifecycle import mount_volumes, umount_volumes
from ....policy import Severity
from ....remote.resolution import resolve_all_endpoints

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

    _HAS_DOCKER = True
except ImportError:
    # The 'docker' extra is optional; seed_demo(docker=True) reports it.
    _HAS_DOCKER = False
from ....sync.testkit.seed import (
    build_chain_config,
    build_local_chain_config,
    create_seed_sentinels,
    seed_volume,
)


class SeedError(Exception):
    """Raised when seed fails with a user-facing message."""


class SeedResult(BaseModel):
    """Result of seeding a demo environment."""

    model_config = ConfigDict(frozen=True)

    base_dir: Path
    config_path: Path
    config: Config
    bastion_port: int | None = None
    storage_port: int | None = None


@dataclass(frozen=True)
class _Steps:
    """Progress callbacks around each seed step."""

    on_start: Callable[[str], None] | None
    on_end: Callable[[str, Severity, str | None], None] | None

    def start(self, label: str) -> None:
        if self.on_start is not None:
            self.on_start(label)

    def end(self, label: str, success: bool, detail: str | None = None) -> None:
        if self.on_end is not None:
            self.on_end(label, Severity.OK if success else Severity.ERROR, detail)


@dataclass(frozen=True)
class _Containers:
    """The running demo containers and how to reach them."""

    private_key: Path
    bastion: SshEndpoint
    storage: SshEndpoint
    bastion_port: int
    storage_port: int


def _require_docker() -> None:
    if not _HAS_DOCKER:
        raise SeedError(
            "Docker support requires the 'docker' extra."
            " Install it with: uv tool install 'nbkp[docker]'"
            " (or use --no-docker)."
        )


def seed_plan(
    base_dir: Path | None,
    *,
    docker: bool = True,
    luks: bool = True,
) -> list[str]:
    """Steps :func:`seed_demo` would perform, for ``demo seed --dry-run``."""
    target = str(base_dir) if base_dir is not None else "a new temp dir (nbkp-demo-*)"
    return [
        f"create seed directory {target}",
        *(
            [
                "build the Docker image",
                "create the Docker network",
                "start the bastion and storage containers",
            ]
            if docker
            else []
        ),
        *(
            ["read LUKS metadata", "mount, seed and umount the encrypted volume"]
            if docker and luks
            else []
        ),
        "create sentinels and seed test data",
        "write config.yaml",
    ]


def seed_demo(
    base_dir: Path,
    *,
    docker: bool = True,
    luks: bool = True,
    big_file_size: int = 1,
    bandwidth_limit: int = 250,
    credential_provider: CredentialProvider = CredentialProvider.KEYRING,
    on_step_start: Callable[[str], None] | None = None,
    on_step_end: Callable[[str, Severity, str | None], None] | None = None,
) -> SeedResult:
    """Create a demo environment with config and test data.

    Parameters
    ----------
    on_step_start:
        Called before each step with an in-progress label
        (e.g. ``"Building Docker image..."``).
    on_step_end:
        Called after each step with ``(label, severity, detail)``.
    """
    steps = _Steps(on_step_start, on_step_end)
    rsync_opts = (
        RsyncOptions(extra_options=[f"--bwlimit={bandwidth_limit}"])
        if bandwidth_limit
        else RsyncOptions()
    )
    if docker:
        _require_docker()
    containers = _start_containers(base_dir, luks, steps) if docker else None
    luks_uuid = (
        _read_luks_uuid(containers, steps) if containers is not None and luks else None
    )
    config = (
        _docker_config(base_dir, containers, luks_uuid, rsync_opts, credential_provider)
        if containers is not None
        else build_local_chain_config(
            base_dir, rsync_options=rsync_opts, max_snapshots=5
        )
    )
    resolved = resolve_all_endpoints(config)
    with _encrypted_volume_mounted(config, resolved, luks_uuid is not None, steps):
        _seed_data(config, containers, big_file_size, steps)
    config_path = _write_config(base_dir, config)
    return SeedResult(
        base_dir=base_dir,
        config_path=config_path,
        config=config,
        bastion_port=containers.bastion_port if containers else None,
        storage_port=containers.storage_port if containers else None,
    )


def _start_containers(base_dir: Path, luks: bool, steps: _Steps) -> _Containers:
    """Build the image, start bastion + storage, wait for their SSH."""
    private_key, pub_key = generate_ssh_keypair(base_dir)

    steps.start("Building Docker image...")
    build_docker_image()
    steps.end("build Docker image", True)

    steps.start("Creating Docker network...")
    network_name = create_docker_network()
    steps.end("create Docker network", True)

    steps.start("Starting bastion container...")
    bastion_port = start_bastion_container(pub_key, network_name)
    steps.end("start bastion container", True)
    bastion = create_test_ssh_endpoint(
        "bastion", "127.0.0.1", bastion_port, private_key
    )
    steps.start("Waiting for bastion SSH...")
    wait_for_ssh(bastion)
    steps.end("bastion SSH", True)

    steps.start("Starting storage container...")
    storage_port = start_storage_container(
        pub_key,
        network_name=network_name,
        network_alias="backup-server",
        luks_enabled=luks,
    )
    steps.end("start storage container", True)
    storage = create_test_ssh_endpoint(
        "storage", "127.0.0.1", storage_port, private_key
    )
    steps.start("Waiting for storage SSH...")
    # Generous timeout: the storage container formats a btrfs loop image and
    # brings up dbus/udevd/udisksd (probing the shared host /dev) before sshd
    # is ready, which can exceed the 30s default on a loaded machine.
    wait_for_ssh(storage, timeout=90)
    steps.end("storage SSH", True)
    return _Containers(private_key, bastion, storage, bastion_port, storage_port)


def _read_luks_uuid(containers: _Containers, steps: _Steps) -> str:
    steps.start("Reading LUKS metadata...")
    meta = read_luks_metadata(containers.storage)
    if not meta.available or meta.uuid is None:
        steps.end("read LUKS metadata", False, "dm-crypt unavailable")
        raise SeedError(
            "LUKS unavailable (dm-crypt kernel module missing?)."
            " Use --no-luks to skip encrypted volume setup."
        )
    steps.end("read LUKS metadata", True)
    return meta.uuid


def _docker_config(
    base_dir: Path,
    containers: _Containers,
    luks_uuid: str | None,
    rsync_opts: RsyncOptions,
    credential_provider: CredentialProvider,
) -> Config:
    """Chain layout matching the integration test, through the bastion."""
    proxied = create_test_ssh_endpoint(
        "via-bastion",
        "backup-server",
        22,
        containers.private_key,
        proxy_jump="bastion",
    )
    return build_chain_config(
        base_dir,
        containers.bastion,
        proxied,
        luks_uuid=luks_uuid,
        rsync_options=rsync_opts,
        max_snapshots=5,
        credential_provider=(
            credential_provider if luks_uuid is not None else CredentialProvider.KEYRING
        ),
    )


@contextmanager
def _encrypted_volume_mounted(
    config: Config,
    resolved: ResolvedEndpoints,
    enabled: bool,
    steps: _Steps,
) -> Generator[None, None, None]:
    """Mount the encrypted volume around seeding, and check the umount.

    A failed umount leaves the demo LUKS volume unlocked: it is reported and,
    when seeding itself succeeded, turned into a ``SeedError``.
    """
    if not enabled:
        yield
        return
    steps.start("Mounting encrypted volume...")
    failed_mount = next(
        (
            r
            for r in mount_volumes(config, resolved, lambda _: LUKS_PASSPHRASE)
            if not r.success
        ),
        None,
    )
    if failed_mount is not None:
        steps.end("mount encrypted volume", False, failed_mount.detail)
        raise SeedError(f"Mount failed: {failed_mount.detail}")
    steps.end("mount encrypted volume", True)
    try:
        yield
    finally:
        failed_umount = _umount_encrypted(config, resolved, steps)
    if failed_umount is not None:
        raise SeedError(
            f"Umount failed: {failed_umount}. The encrypted volume may still be"
            " mounted; run nbkp disks umount on the seeded config."
        )


def _umount_encrypted(
    config: Config, resolved: ResolvedEndpoints, steps: _Steps
) -> str | None:
    """Umount + lock; returns the failure detail, if any."""
    steps.start("Unmounting encrypted volume...")
    failed = next((r for r in umount_volumes(config, resolved) if not r.success), None)
    detail = (failed.detail or "unknown error") if failed is not None else None
    steps.end("umount encrypted volume", failed is None, detail)
    return detail


def _seed_data(
    config: Config,
    containers: _Containers | None,
    big_file_size: int,
    steps: _Steps,
) -> None:
    """Create sentinels everywhere and seed the bare source volume."""
    storage = containers.storage if containers is not None else None

    def _run_remote(_vol: object, cmd: str) -> None:
        assert storage is not None
        ssh_exec(storage, cmd)

    steps.start("Seeding volumes...")
    create_seed_sentinels(
        config, remote_exec=_run_remote if storage is not None else None
    )
    seed_volume(
        config.volumes["src-local-bare"],
        big_file_size_bytes=big_file_size * 1024 * 1024,
    )
    steps.end("seed volumes", True)


def _write_config(base_dir: Path, config: Config) -> Path:
    config_path = base_dir / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            config.model_dump(by_alias=True, mode="json"),
            default_flow_style=False,
            sort_keys=False,
        )
    )
    return config_path
