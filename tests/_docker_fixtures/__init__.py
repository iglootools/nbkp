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
    SshEndpoint,
    SyncConfig,
)
from nbkp.testkit.docker import (  # noqa: F401
    DOCKER_DIR,
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_PATH,
    create_sentinels,
    create_test_ssh_endpoint,
    generate_ssh_keypair,
    prepare_btrfs_snapshot_based_backup_dst,
    prepare_hardlinks_snapshot_based_backup_dst,
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

    # Clean btrfs paths — delete snapshot subvolumes first,
    # then staging subvolume and latest symlink
    snapshots_result = ssh_exec(
        server,
        f"ls {REMOTE_BTRFS_PATH}/snapshots 2>/dev/null || true",
        check=False,
    )
    if snapshots_result.stdout.strip():
        for snap in snapshots_result.stdout.strip().split("\n"):
            snap = snap.strip()
            if snap:
                run(
                    "btrfs property set"
                    f" {REMOTE_BTRFS_PATH}/snapshots/{snap}"
                    " ro false 2>/dev/null || true"
                )
                run(
                    "btrfs subvolume delete"
                    f" {REMOTE_BTRFS_PATH}/snapshots/{snap}"
                    " 2>/dev/null || true"
                )

    # Delete staging subvolume if it exists
    run(f"btrfs subvolume delete {REMOTE_BTRFS_PATH}/staging 2>/dev/null || true")
    # Remove latest symlink
    run(f"rm -f {REMOTE_BTRFS_PATH}/latest 2>/dev/null || true")
    run(f"rm -rf {REMOTE_BTRFS_PATH}/snapshots 2>/dev/null || true")

    # Clean chain subpath (btrfs subvolume with its own
    # staging + snapshots + latest symlink, used by chain test)
    chain = f"{REMOTE_BTRFS_PATH}/chain"
    chain_snaps = ssh_exec(
        server,
        f"ls {chain}/snapshots 2>/dev/null || true",
        check=False,
    )
    if chain_snaps.stdout.strip():
        for snap in chain_snaps.stdout.strip().split("\n"):
            snap = snap.strip()
            if snap:
                run(
                    "btrfs property set"
                    f" {chain}/snapshots/{snap}"
                    " ro false 2>/dev/null || true"
                )
                run(
                    "btrfs subvolume delete"
                    f" {chain}/snapshots/{snap}"
                    " 2>/dev/null || true"
                )
    run(f"btrfs subvolume delete {chain}/staging 2>/dev/null || true")
    run(f"rm -f {chain}/latest 2>/dev/null || true")
    run(f"btrfs subvolume delete {chain} 2>/dev/null || true")

    # Clean bare subpath on btrfs (regular dir, used by chain test)
    run(f"rm -rf {REMOTE_BTRFS_PATH}/bare 2>/dev/null || true")

    run(f"find {REMOTE_BTRFS_PATH} -name '.nbkp-*' -delete")
