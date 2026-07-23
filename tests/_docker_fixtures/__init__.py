"""Shared Docker test fixtures for e2e and integration tests.

Docker-harness note (needs real-Docker validation):
The mount lifecycle migrated from direct ``cryptsetup``/``mount`` to udisks2
(``udisksctl``).  For the encrypted-volume mount tests to pass against a real
container, the image at ``nbkp/remote/testkit/dockerbuild/`` (frozen in this
change) must be updated to:

* install ``udisks2`` + ``udisks2-btrfs`` + ``polkit``;
* start ``dbus-daemon --system``, ``polkitd`` and ``/usr/lib/udisks2/udisksd``
  at container init;
* create the backup user and install the generated ``50-nbkp.rules`` polkit
  file (see ``nbkp.disks.auth.generate_polkit_rules``);
* provide a loopback LUKS device with an ``/etc/fstab`` entry mapping
  ``/dev/mapper/luks-<uuid>`` to the mount path (no crypttab needed).

Tests share a single session-scoped container and unlock/mount/lock the same
encrypted volume repeatedly.  This is reliable without any between-test reset
because the container shares the host ``/dev`` and ``/run/udev`` (see the
``docker_container`` fixture) — udev keeps the dm-crypt device state in sync,
so a fresh unlock always succeeds after a prior test's lock.
"""

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
from nbkp.config.epresolution import ResolvedEndpoints
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.config import LuksEncryptionConfig, MountConfig
from nbkp.remote.testkit.docker import (  # noqa: F401
    DOCKER_DIR,
    LUKS_PASSPHRASE,
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_PATH,
    REMOTE_BTRFS_ENCRYPTED_PATH,
    REMOTE_UNENCRYPTED_PATH,
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
def luks_mapper_name() -> str:
    """Per-worker-unique LUKS device-mapper name.

    Privileged containers share the host VM kernel's device-mapper
    namespace, so a fixed ``/dev/mapper/<name>`` collides across the
    concurrent containers that pytest-xdist spins up (one per worker):
    a ``cryptsetup open`` in one worker's container fails with "Device
    <name> already exists", and a stale-cleanup ``cryptsetup close``
    in another would tear down a live mapper. A unique name per worker
    keeps each container's LUKS device independent. This is the single
    source of truth: it is passed to the container via env and used in
    every config and cleanup command that references the mapper.
    """
    return f"test-enc-{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def docker_container(
    ssh_key_pair: tuple[Path, Path],
    _docker_image: str,
    _docker_network: Any,
    luks_mapper_name: str,
) -> Generator[SshEndpoint, None, None]:
    """Start Docker container and yield SshEndpoint."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import (
        LogMessageWaitStrategy,
    )

    private_key, public_key = ssh_key_pair

    # Generous startup timeout: the entrypoint formats a btrfs loop image and
    # brings up dbus/udevd/udisksd before sshd logs "Server listening".  On a
    # loaded machine (or when several test containers boot back-to-back) the
    # default 30s is too tight and trips a spurious startup TimeoutError.
    wait_strategy = LogMessageWaitStrategy(
        "Server listening",
    ).with_startup_timeout(90)

    container = (
        DockerContainer(_docker_image)
        .with_exposed_ports(22)
        .with_volume_mapping(
            str(public_key),
            "/mnt/ssh-authorized-keys",
            "ro",
        )
        # Share the host's /dev and udev runtime.  udisks mounts a device only
        # once udev has probed its filesystem (it reads the ID_FS_TYPE udev
        # property).  With a *private* container /dev, the container's udevd
        # sees a different devtmpfs than the kernel creates dm-crypt devices on,
        # so after btrfs/loop activity (which shares the kernel across all
        # containers on Docker Desktop's LinuxKit VM) it intermittently never
        # probes a freshly-unlocked cleartext device — udisks then refuses to
        # mount it ("is not a mountable filesystem") and the encrypted-volume
        # tests fail.  Sharing /dev lets udevd operate on the same devtmpfs the
        # kernel uses, and sharing /run/udev gives udisks the resulting db.
        # This is the minimal set: --network host and a shared /run/dbus also
        # work but break the multi-container bastion e2e (port + bus conflicts).
        .with_volume_mapping("/dev", "/dev", "rw")
        .with_volume_mapping("/run/udev", "/run/udev", "rw")
        .with_env("NBKP_BACKUP_PATH", REMOTE_BACKUP_PATH)
        .with_env("NBKP_BTRFS_PATH", REMOTE_BTRFS_PATH)
        .with_env("NBKP_BTRFS_ENCRYPTED_PATH", REMOTE_BTRFS_ENCRYPTED_PATH)
        .with_env("NBKP_LUKS_PASSPHRASE", LUKS_PASSPHRASE)
        .with_env("NBKP_LUKS_MAPPER_NAME", luks_mapper_name)
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

    _detach_container_loop_devices(wrapped)
    container.stop()


def _detach_container_loop_devices(wrapped: Any) -> None:
    """Detach this container's loop devices before it is destroyed.

    The encrypted-volume LUKS image is attached with an explicit
    ``losetup`` (it needs a stable ``/dev/disk/by-uuid`` entry for the
    production mount path), which — unlike a ``mount -o loop`` — does not
    carry the autoclear flag and therefore would leak a loop device on
    the shared host VM kernel every time this container stops. Across
    repeated local runs those leaked loops accumulate and eventually
    exhaust the pool.

    We match loops by backing file: ``losetup -j`` associates by the
    file's device+inode, so from inside this container it resolves to
    *this* container's images only — sibling containers running
    concurrently (under pytest-xdist) back different inodes and are left
    untouched. Run as root via ``docker exec`` (the SSH user is
    unprivileged and ``losetup`` is not in the test sudoers allowlist).
    """
    script = r"""
mapper=$(cat /srv/luks-mapper-name 2>/dev/null || true)
[ -n "$mapper" ] && cryptsetup close "$mapper" 2>/dev/null || true
for img in /srv/btrfs-encrypted-backups.img /srv/btrfs-backups.img; do
    for dev in $(losetup -j "$img" -O NAME -n 2>/dev/null); do
        losetup -d "$dev" 2>/dev/null || true
    done
done
"""
    try:
        wrapped.exec_run(["bash", "-c", script])
    except Exception:
        # Best-effort cleanup — never fail teardown over a leaked loop.
        pass


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

    # Generous startup timeout: the entrypoint formats a btrfs loop image and
    # brings up dbus/udevd/udisksd before sshd logs "Server listening".  On a
    # loaded machine (or when several test containers boot back-to-back) the
    # default 30s is too tight and trips a spurious startup TimeoutError.
    wait_strategy = LogMessageWaitStrategy(
        "Server listening",
    ).with_startup_timeout(90)

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
    """Lazily set up LUKS and read metadata from the Docker container.

    Triggers /setup-luks.sh on first use (idempotent), so tests that
    don't request this fixture skip the ~5-10s cryptsetup overhead.
    """
    ssh_exec(docker_ssh_endpoint, "sudo /setup-luks.sh")
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
    """RemoteVolume with MountConfig pointing at /srv/btrfs-encrypted-backups.

    The mount lifecycle is now driven by udisks2 (``udisksctl``); the
    container must run ``udisksd`` + polkit and carry an fstab entry that
    maps the cleartext device (``/dev/mapper/luks-<uuid>``) to this path.
    See the module docstring for the Docker-harness requirements that need
    real-Docker validation.
    """
    return RemoteVolume(
        slug="test-encrypted",
        ssh_endpoint="test-server",
        path=REMOTE_BTRFS_ENCRYPTED_PATH,
        mount=MountConfig(
            device_uuid=luks_uuid,
            encryption=LuksEncryptionConfig(
                passphrase_id="test-luks",
            ),
        ),
    )


@pytest.fixture(scope="session")
def unencrypted_uuid(
    luks_metadata: LuksMetadata, docker_ssh_endpoint: SshEndpoint
) -> str:
    """Filesystem UUID of the container's plain (unencrypted) ext4 volume.

    Created by ``setup-luks.sh`` alongside the LUKS volume (same
    loop-device-available gate), so it shares the LUKS availability skip.
    """
    if not luks_metadata.available:
        pytest.skip("loop devices unavailable (cannot create test volumes)")
    result = ssh_exec(
        docker_ssh_endpoint,
        "cat /srv/unencrypted-uuid 2>/dev/null || true",
        check=False,
    )
    uuid = result.stdout.strip()
    if not uuid:
        pytest.skip("unencrypted test device unavailable (loop allocation failed)")
    return uuid


@pytest.fixture(scope="session")
def remote_unencrypted_volume(unencrypted_uuid: str) -> RemoteVolume:
    """RemoteVolume with a mount-managed, genuinely unencrypted ext4 device.

    ``device_uuid`` is the filesystem UUID; nbkp mounts it directly via
    ``udisksctl mount`` (no LUKS unlock), and the container's fstab maps that
    UUID to the fixed path.
    """
    return RemoteVolume(
        slug="test-unencrypted-mount",
        ssh_endpoint="test-server",
        path=REMOTE_UNENCRYPTED_PATH,
        mount=MountConfig(device_uuid=unencrypted_uuid),
    )


# ── Cleanup ─────────────────────────────────────────────────────


def _build_cleanup_script(luks_mapper_name: str) -> str:
    """Build a single bash script that cleans all test artifacts.

    Batched into one SSH round-trip instead of N individual commands,
    which significantly reduces cleanup time per test. The LUKS mapper
    name is per-worker-unique (see the ``luks_mapper_name`` fixture), so
    it is threaded in rather than read from a shared constant.
    """
    return f"""\
#!/bin/bash
set -e

# Helper: clean btrfs snapshot artifacts at a given base path
clean_btrfs_base() {{
    local base="$1"
    if [ -d "$base/snapshots" ]; then
        for snap in "$base"/snapshots/*/; do
            [ -d "$snap" ] || continue
            btrfs property set "$snap" ro false 2>/dev/null || true
            btrfs subvolume delete "$snap" 2>/dev/null || true
        done
    fi
    btrfs subvolume delete "$base/staging" 2>/dev/null || true
    rm -f "$base/latest" 2>/dev/null || true
    rm -rf "$base/snapshots" 2>/dev/null || true
}}

# Clean /srv/backups
rm -rf {REMOTE_BACKUP_PATH}/*
find {REMOTE_BACKUP_PATH} -name '.nbkp-*' -delete 2>/dev/null || true

# Clean btrfs root and subpaths
clean_btrfs_base {REMOTE_BTRFS_PATH}
clean_btrfs_base {REMOTE_BTRFS_PATH}/snapshots
rm -rf {REMOTE_BTRFS_PATH}/bare 2>/dev/null || true
find {REMOTE_BTRFS_PATH} -name '.nbkp-*' -delete 2>/dev/null || true

# Clean encrypted volume (if LUKS was set up)
if [ -f /srv/luks-uuid ]; then
    LUKS_UUID=$(cat /srv/luks-uuid)
    if [ -n "$LUKS_UUID" ]; then
        echo -n '{LUKS_PASSPHRASE}' | sudo cryptsetup open \
            --type luks "/dev/disk/by-uuid/$LUKS_UUID" \
            {luks_mapper_name} - 2>/dev/null || true
        sudo mount -o user_subvol_rm_allowed \
            /dev/mapper/{luks_mapper_name} {REMOTE_BTRFS_ENCRYPTED_PATH} \
            2>/dev/null || true

        clean_btrfs_base {REMOTE_BTRFS_ENCRYPTED_PATH}/snapshots
        btrfs subvolume delete {REMOTE_BTRFS_ENCRYPTED_PATH}/snapshots 2>/dev/null || true
        rm -rf {REMOTE_BTRFS_ENCRYPTED_PATH}/bare 2>/dev/null || true
        find {REMOTE_BTRFS_ENCRYPTED_PATH} -name '.nbkp-*' -delete 2>/dev/null || true

        sudo umount {REMOTE_BTRFS_ENCRYPTED_PATH} 2>/dev/null || true
        sudo cryptsetup close {luks_mapper_name} 2>/dev/null || true
    fi
fi
"""


@pytest.fixture(autouse=True)
def _cleanup_remote(
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Clean up /srv/backups and /srv/btrfs-backups between tests.

    All cleanup runs in a single SSH round-trip for performance.
    """
    yield

    # Only clean up if docker_ssh_endpoint was used by this test
    if "docker_ssh_endpoint" not in request.fixturenames:
        return

    server: SshEndpoint = request.getfixturevalue("docker_ssh_endpoint")
    # luks_mapper_name is a cheap session-scoped string; requesting it
    # never triggers container/LUKS setup, so it is safe to resolve here.
    mapper: str = request.getfixturevalue("luks_mapper_name")
    ssh_exec(server, _build_cleanup_script(mapper), check=False)
