"""Shared Docker test fixtures for e2e and integration tests."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Generator

import docker as dockerlib
import pytest

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
    resolve_all_endpoints,
)
from nbkp.config import LuksEncryptionConfig, MountConfig
from nbkp.mount.strategy import DirectMountStrategy
from nbkp.remote.testkit.docker import (  # noqa: F401
    DOCKER_DIR,
    LUKS_MAPPER_NAME,
    LUKS_PASSPHRASE,
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_PATH,
    REMOTE_BTRFS_ENCRYPTED_PATH,
    LuksMetadata,
    create_sentinels,
    create_test_ssh_endpoint,
    generate_ssh_keypair,
    prepare_btrfs_snapshot_based_backup_dst,
    prepare_hardlinks_snapshot_based_backup_dst,
    read_luks_metadata,
    ssh_exec,
    wait_for_ssh,
)


def assert_sentinels_after_sync(
    sync: SyncConfig,
    config: Config,
    ssh_endpoint: SshEndpoint,
    *,
    dest_suffix: str | None = None,
) -> None:
    """Assert sentinel files are handled correctly after sync.

    - .nbkp-src must NOT exist at the rsync destination
    - .nbkp-vol must NOT exist at the rsync destination
      (when it differs from the volume root)
    - .nbkp-dst must still exist at the destination
      sync endpoint path
    """
    dst_ep = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst_ep.volume]
    vol_path = dst_vol.path
    subdir = dst_ep.subdir

    # Where rsync actually wrote files
    if dest_suffix:
        if subdir:
            rsync_target = f"{vol_path}/{subdir}/{dest_suffix}"
        else:
            rsync_target = f"{vol_path}/{dest_suffix}"
    elif subdir:
        rsync_target = f"{vol_path}/{subdir}"
    else:
        rsync_target = vol_path

    # Where .nbkp-dst lives
    if subdir:
        sentinel_dir = f"{vol_path}/{subdir}"
    else:
        sentinel_dir = vol_path

    def _remote_exists(path: str) -> bool:
        r = ssh_exec(ssh_endpoint, f"test -f {path}", check=False)
        return r.returncode == 0

    def _check_exists(path: str) -> bool:
        match dst_vol:
            case LocalVolume():
                return Path(path).exists()
            case RemoteVolume():
                return _remote_exists(path)

    # 1. .nbkp-src must NOT be at the rsync target
    assert not _check_exists(f"{rsync_target}/.nbkp-src"), (
        f".nbkp-src was copied to rsync target {rsync_target}"
    )

    # 2. .nbkp-vol must NOT be at the rsync target
    #    (unless rsync target IS the volume root)
    if rsync_target != vol_path:
        assert not _check_exists(f"{rsync_target}/.nbkp-vol"), (
            f".nbkp-vol found at rsync target {rsync_target}"
        )

    # 3. .nbkp-dst must still exist at the sentinel dir
    assert _check_exists(f"{sentinel_dir}/.nbkp-dst"), (
        f".nbkp-dst missing from {sentinel_dir}"
    )


def direct_strategy_for(volume: RemoteVolume) -> DirectMountStrategy:
    """Build a ``DirectMountStrategy`` from a volume's mount config."""
    return DirectMountStrategy(volume_path=volume.path)


def resolved_endpoints_for(
    server: SshEndpoint,
    volume: RemoteVolume,
) -> ResolvedEndpoints:
    """Build minimal ``ResolvedEndpoints`` for a single remote volume."""
    config = Config(
        ssh_endpoints={"test-server": server},
        volumes={volume.slug: volume},
    )
    return resolve_all_endpoints(config)


def _docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        client = dockerlib.from_env()
        client.ping()
        return True
    except dockerlib.errors.DockerException:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker not available")


@pytest.fixture(scope="session")
def ssh_key_pair() -> Generator[tuple[Path, Path], None, None]:
    """Generate an ephemeral ed25519 SSH key pair for tests."""
    tmpdir = Path(tempfile.mkdtemp(prefix="nbkp-test-ssh-"))
    pair = generate_ssh_keypair(tmpdir)

    yield pair

    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(scope="session")
def _docker_image() -> str:
    """Build the Docker image and return its tag."""
    from testcontainers.core.image import DockerImage

    image = DockerImage(
        path=str(DOCKER_DIR),
        tag="nbkp-test-server:latest",
    )
    image.build()
    return str(image)


@pytest.fixture(scope="session")
def _docker_network() -> Generator[Any, None, None]:
    """Create a Docker bridge network for inter-container comms."""
    client = dockerlib.from_env()
    name = f"nbkp-test-{uuid.uuid4().hex[:8]}"
    network = client.networks.create(name, driver="bridge")

    yield network

    try:
        network.remove()
    except dockerlib.errors.APIError:
        pass


@pytest.fixture(scope="session")
def docker_container(
    ssh_key_pair: tuple[Path, Path],
    _docker_image: str,
    _docker_network: Any,
) -> Generator[SshEndpoint, None, None]:
    """Start Docker container and yield SshEndpoint."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import (
        LogMessageWaitStrategy,
    )

    private_key, public_key = ssh_key_pair

    wait_strategy = LogMessageWaitStrategy(
        "Server listening",
    ).with_startup_timeout(30)

    container = (
        DockerContainer(_docker_image)
        .with_exposed_ports(22)
        .with_volume_mapping(
            str(public_key),
            "/mnt/ssh-authorized-keys",
            "ro",
        )
        .with_env("NBKP_BACKUP_PATH", REMOTE_BACKUP_PATH)
        .with_env("NBKP_BTRFS_PATH", REMOTE_BTRFS_PATH)
        .with_env("NBKP_BTRFS_ENCRYPTED_PATH", REMOTE_BTRFS_ENCRYPTED_PATH)
        .with_env("NBKP_LUKS_PASSPHRASE", LUKS_PASSPHRASE)
        .with_env("NBKP_LUKS_MAPPER_NAME", LUKS_MAPPER_NAME)
        .with_kwargs(privileged=True)
        .waiting_for(wait_strategy)
    )
    container.start()

    # Connect to network with alias for bastion access
    wrapped = container.get_wrapped_container()
    _docker_network.connect(wrapped, aliases=["backup-server"])

    server = create_test_ssh_endpoint(
        "test-server",
        container.get_container_host_ip(),
        int(container.get_exposed_port(22)),
        private_key,
    )

    wait_for_ssh(server, timeout=30)

    yield server

    container.stop()


@pytest.fixture(scope="session")
def bastion_container(
    ssh_key_pair: tuple[Path, Path],
    _docker_image: str,
    _docker_network: Any,
) -> Generator[SshEndpoint, None, None]:
    """Start a bastion (jump proxy) container."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import (
        LogMessageWaitStrategy,
    )

    private_key, public_key = ssh_key_pair

    wait_strategy = LogMessageWaitStrategy(
        "Server listening",
    ).with_startup_timeout(30)

    container = (
        DockerContainer(_docker_image)
        .with_exposed_ports(22)
        .with_volume_mapping(
            str(public_key),
            "/mnt/ssh-authorized-keys",
            "ro",
        )
        .with_env("NBKP_BASTION_ONLY", "1")
        .waiting_for(wait_strategy)
    )
    container.start()

    wrapped = container.get_wrapped_container()
    _docker_network.connect(wrapped)

    server = create_test_ssh_endpoint(
        "bastion",
        container.get_container_host_ip(),
        int(container.get_exposed_port(22)),
        private_key,
    )

    wait_for_ssh(server, timeout=30)
    yield server

    container.stop()


@pytest.fixture(scope="session")
def proxied_ssh_endpoint(
    ssh_key_pair: tuple[Path, Path],
    bastion_container: SshEndpoint,
    docker_container: SshEndpoint,
) -> SshEndpoint:
    """SshEndpoint that routes through the bastion."""
    private_key, _ = ssh_key_pair
    return create_test_ssh_endpoint(
        "proxied-server",
        "backup-server",
        22,
        private_key,
        proxy_jump="bastion",
    )


@pytest.fixture(scope="session")
def docker_ssh_endpoint(
    docker_container: SshEndpoint,
) -> SshEndpoint:
    """SshEndpoint pointing at the Docker container."""
    return docker_container


@pytest.fixture(scope="session")
def docker_remote_volume() -> RemoteVolume:
    """RemoteVolume pointing at /srv/backups on the container."""
    return RemoteVolume(
        slug="test-remote",
        ssh_endpoint="test-server",
        path=REMOTE_BACKUP_PATH,
    )


@pytest.fixture(scope="session")
def remote_btrfs_volume() -> RemoteVolume:
    """RemoteVolume pointing at /srv/btrfs-backups."""
    return RemoteVolume(
        slug="test-btrfs",
        ssh_endpoint="test-server",
        path=REMOTE_BTRFS_PATH,
    )


@pytest.fixture(scope="session")
def remote_hardlink_volume() -> RemoteVolume:
    """RemoteVolume pointing at /srv/backups."""
    return RemoteVolume(
        slug="test-hl",
        ssh_endpoint="test-server",
        path=REMOTE_BACKUP_PATH,
    )


# ── LUKS / encrypted volume fixtures ────────────────────────────


@pytest.fixture(scope="session")
def luks_metadata(docker_ssh_endpoint: SshEndpoint) -> LuksMetadata:
    """Read LUKS setup metadata from the Docker container."""
    return read_luks_metadata(docker_ssh_endpoint)


@pytest.fixture(scope="session")
def luks_uuid(luks_metadata: LuksMetadata) -> str:
    """LUKS container UUID — skips test session if LUKS unavailable."""
    if not luks_metadata.available:
        pytest.skip("LUKS not available (dm-crypt kernel module missing?)")
    assert luks_metadata.uuid is not None
    return luks_metadata.uuid


@pytest.fixture(scope="session")
def remote_encrypted_volume(luks_uuid: str) -> RemoteVolume:
    """RemoteVolume with MountConfig pointing at /mnt/encrypted-backup.

    Uses ``strategy="direct"`` because Docker containers run sshd as
    PID 1 (no systemd).
    """
    return RemoteVolume(
        slug="test-encrypted",
        ssh_endpoint="test-server",
        path=REMOTE_BTRFS_ENCRYPTED_PATH,
        mount=MountConfig(
            strategy="direct",
            device_uuid=luks_uuid,
            encryption=LuksEncryptionConfig(
                mapper_name=LUKS_MAPPER_NAME,
                passphrase_id="test-luks",
            ),
        ),
    )


@pytest.fixture(scope="session")
def remote_encrypted_volume_unencrypted(luks_uuid: str) -> RemoteVolume:
    """RemoteVolume with MountConfig but no encryption (unencrypted mount)."""
    return RemoteVolume(
        slug="test-unencrypted-mount",
        ssh_endpoint="test-server",
        path=REMOTE_BTRFS_ENCRYPTED_PATH,
        mount=MountConfig(device_uuid=luks_uuid),
    )


# ── Cleanup ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _cleanup_remote(
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Clean up /srv/backups and /srv/btrfs-backups between tests."""
    yield

    # Only clean up if docker_ssh_endpoint was used by this test
    if "docker_ssh_endpoint" not in request.fixturenames:
        return

    server: SshEndpoint = request.getfixturevalue("docker_ssh_endpoint")

    def run(cmd: str) -> None:
        ssh_exec(server, cmd, check=False)

    # Clean /srv/backups (glob * skips dotfiles, so also remove
    # sentinels)
    run(f"rm -rf {REMOTE_BACKUP_PATH}/*")
    run(f"find {REMOTE_BACKUP_PATH} -name '.nbkp-*' -delete")

    # Clean btrfs snapshot artifacts at a given base path:
    # snapshot subvolumes, staging subvolume, latest symlink.
    def _clean_btrfs_base(base: str) -> None:
        snaps = ssh_exec(
            server,
            f"ls {base}/snapshots 2>/dev/null || true",
            check=False,
        )
        if snaps.stdout.strip():
            for snap in snaps.stdout.strip().split("\n"):
                snap = snap.strip()
                if snap:
                    run(
                        "btrfs property set"
                        f" {base}/snapshots/{snap}"
                        " ro false 2>/dev/null || true"
                    )
                    run(
                        "btrfs subvolume delete"
                        f" {base}/snapshots/{snap}"
                        " 2>/dev/null || true"
                    )
        run(f"btrfs subvolume delete {base}/staging 2>/dev/null || true")
        run(f"rm -f {base}/latest 2>/dev/null || true")
        run(f"rm -rf {base}/snapshots 2>/dev/null || true")

    # Clean btrfs root (used by direct integration tests)
    _clean_btrfs_base(REMOTE_BTRFS_PATH)

    # Clean btrfs subpath used by chain tests — inner snapshot/staging
    # artifacts live under the endpoint subdir.
    _clean_btrfs_base(f"{REMOTE_BTRFS_PATH}/snapshots")

    # Clean bare subpath on btrfs (regular dir, used by chain test)
    run(f"rm -rf {REMOTE_BTRFS_PATH}/bare 2>/dev/null || true")

    run(f"find {REMOTE_BTRFS_PATH} -name '.nbkp-*' -delete")

    # Clean encrypted volume — try to mount for btrfs cleanup, then
    # umount and close LUKS.  Each step is idempotent (|| true).
    #
    # Open LUKS if closed (read UUID from entrypoint-saved file)
    run(
        f"LUKS_UUID=$(cat /srv/luks-uuid 2>/dev/null) && "
        f'[ -n "$LUKS_UUID" ] && '
        f"echo -n '{LUKS_PASSPHRASE}' | sudo cryptsetup open"
        f" --type luks /dev/disk/by-uuid/$LUKS_UUID"
        f" {LUKS_MAPPER_NAME} - 2>/dev/null || true"
    )
    # Mount if not already mounted
    run(
        f"sudo mount -o user_subvol_rm_allowed"
        f" /dev/mapper/{LUKS_MAPPER_NAME} {REMOTE_BTRFS_ENCRYPTED_PATH}"
        f" 2>/dev/null || true"
    )
    # Clean btrfs artifacts on the encrypted volume (chain test puts
    # btrfs subvolumes here).  Same pattern as /srv/btrfs-backups
    # cleanup above.
    enc_snap_base = f"{REMOTE_BTRFS_ENCRYPTED_PATH}/snapshots"
    enc_snaps_result = ssh_exec(
        server,
        f"ls {enc_snap_base}/snapshots 2>/dev/null || true",
        check=False,
    )
    if enc_snaps_result.stdout.strip():
        for snap in enc_snaps_result.stdout.strip().split("\n"):
            snap = snap.strip()
            if snap:
                run(
                    "btrfs property set"
                    f" {enc_snap_base}/snapshots/{snap}"
                    " ro false 2>/dev/null || true"
                )
                run(
                    "btrfs subvolume delete"
                    f" {enc_snap_base}/snapshots/{snap}"
                    " 2>/dev/null || true"
                )
    run(f"btrfs subvolume delete {enc_snap_base}/staging 2>/dev/null || true")
    run(f"rm -f {enc_snap_base}/latest 2>/dev/null || true")
    run(f"rm -rf {enc_snap_base}/snapshots 2>/dev/null || true")
    run(f"btrfs subvolume delete {enc_snap_base} 2>/dev/null || true")
    run(f"rm -rf {REMOTE_BTRFS_ENCRYPTED_PATH}/bare 2>/dev/null || true")
    run(
        f"find {REMOTE_BTRFS_ENCRYPTED_PATH} -name '.nbkp-*' -delete 2>/dev/null || true"
    )
    # Umount and close LUKS
    run(f"sudo umount {REMOTE_BTRFS_ENCRYPTED_PATH} 2>/dev/null || true")
    run(f"sudo cryptsetup close {LUKS_MAPPER_NAME} 2>/dev/null || true")
