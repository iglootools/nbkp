"""Docker helpers for the developer test CLI seed command."""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import docker as dockerlib
import docker.errors as docker_errors
import typer

from ...config import SshConnectionOptions, SshEndpoint
from ...fsprotocol import SNAPSHOTS_DIR, STAGING_DIR
from ...remote.fabricssh import run_remote_command

DOCKER_DIR = Path(__file__).resolve().parent / "dockerbuild"
STORAGE_CONTAINER_NAME = "nbkp-demo"
BASTION_CONTAINER_NAME = "nbkp-demo-bastion"
_IMAGE_TAG = "nbkp-demo-server:latest"
_NETWORK_NAME = "nbkp-demo-net"

# ── Standard remote paths inside the test container ──────────

REMOTE_BACKUP_PATH = "/srv/backups"
REMOTE_BTRFS_PATH = "/srv/btrfs-backups"
REMOTE_ENCRYPTED_PATH = "/srv/btrfs-encrypted-backups"
SSH_AUTHORIZED_KEYS_PATH = "/mnt/ssh-authorized-keys"

# ── LUKS test constants ──────────────────────────────────────

LUKS_PASSPHRASE = "test-passphrase"
LUKS_MAPPER_NAME = "test-encrypted"


@dataclass(frozen=True)
class LuksMetadata:
    """LUKS setup metadata read from the test container."""

    available: bool
    uuid: str | None = None
    loop_device: str | None = None


# ── SSH endpoint factory ─────────────────────────────────────


def create_test_ssh_endpoint(
    slug: str,
    host: str,
    port: int,
    private_key: Path,
    *,
    proxy_jump: str | None = None,
) -> SshEndpoint:
    """Create an SshEndpoint with standard test connection options.

    All test containers use ``testuser``, disabled host-key checking,
    and ``/dev/null`` as the known-hosts file.
    """
    return SshEndpoint(
        slug=slug,
        host=host,
        port=port,
        user="testuser",
        key=str(private_key),
        proxy_jump=proxy_jump,
        connection_options=SshConnectionOptions(
            strict_host_key_checking=False,
            known_hosts_file="/dev/null",
        ),
    )


# ── Docker lifecycle ─────────────────────────────────────────


def check_docker() -> None:
    """Verify Docker daemon is reachable."""
    try:
        client = dockerlib.from_env()
        client.ping()
    except docker_errors.DockerException as exc:
        typer.echo(
            f"Error: Docker is not available: {exc}",
            err=True,
        )
        raise typer.Exit(1)


def generate_ssh_keypair(
    seed_dir: Path,
) -> tuple[Path, Path]:
    """Generate Ed25519 SSH key pair in seed_dir/ssh/."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    ssh_dir = seed_dir / "ssh"
    ssh_dir.mkdir()
    private_key_path = ssh_dir / "id_ed25519"
    public_key_path = ssh_dir / "id_ed25519.pub"

    key = Ed25519PrivateKey.generate()
    private_key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    private_key_path.chmod(0o600)
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )
    public_key_path.write_text(f"{pub_bytes.decode()} nbkp-demo\n")

    return private_key_path, public_key_path


def create_docker_network() -> str:
    """Create a Docker bridge network for container communication."""
    client = dockerlib.from_env()
    try:
        old = client.networks.get(_NETWORK_NAME)
        old.reload()
        for cid in list(old.attrs.get("Containers") or {}):
            try:
                old.disconnect(cid, force=True)
            except docker_errors.APIError:
                pass
        old.remove()
    except docker_errors.NotFound:
        pass
    client.networks.create(_NETWORK_NAME, driver="bridge")
    return _NETWORK_NAME


def remove_docker_network() -> None:
    """Remove the Docker bridge network."""
    client = dockerlib.from_env()
    try:
        network = client.networks.get(_NETWORK_NAME)
        network.remove()
    except docker_errors.NotFound:
        pass


def build_docker_image() -> None:
    """Build the Docker image used by all seed containers."""
    client = dockerlib.from_env()
    try:
        client.images.build(
            path=str(DOCKER_DIR),
            tag=_IMAGE_TAG,
            nocache=True,
        )
    except docker_errors.BuildError as exc:
        typer.echo(
            f"Error: Docker image build failed: {exc}",
            err=True,
        )
        raise typer.Exit(1)


def start_storage_container(
    pub_key: Path,
    network_name: str | None = None,
    network_alias: str | None = None,
) -> int:
    """Destroy old container, start new. Return SSH port.

    The image must already be built via build_docker_image().
    """
    client = dockerlib.from_env()

    # Remove existing container if any
    try:
        old = client.containers.get(STORAGE_CONTAINER_NAME)
        old.remove(force=True)
    except docker_errors.NotFound:
        pass

    # Start container
    container = client.containers.run(
        _IMAGE_TAG,
        detach=True,
        name=STORAGE_CONTAINER_NAME,
        privileged=True,
        ports={"22/tcp": None},
        volumes={
            str(pub_key): {
                "bind": SSH_AUTHORIZED_KEYS_PATH,
                "mode": "ro",
            }
        },
    )

    if network_name is not None:
        network = client.networks.get(network_name)
        aliases = [network_alias] if network_alias else None
        network.connect(container, aliases=aliases)

    # Get mapped port
    container.reload()
    port_info = container.attrs["NetworkSettings"]["Ports"]["22/tcp"]
    return int(port_info[0]["HostPort"])


def start_bastion_container(
    pub_key: Path,
    network_name: str,
) -> int:
    """Start a bastion (jump proxy) container. Return SSH port."""
    client = dockerlib.from_env()

    # Remove existing container if any
    try:
        old = client.containers.get(BASTION_CONTAINER_NAME)
        old.remove(force=True)
    except docker_errors.NotFound:
        pass

    container = client.containers.run(
        _IMAGE_TAG,
        detach=True,
        name=BASTION_CONTAINER_NAME,
        ports={"22/tcp": None},
        environment={"NBKP_BASTION_ONLY": "1"},
        volumes={
            str(pub_key): {
                "bind": SSH_AUTHORIZED_KEYS_PATH,
                "mode": "ro",
            }
        },
    )

    network = client.networks.get(network_name)
    network.connect(container)

    # Get mapped port
    container.reload()
    port_info = container.attrs["NetworkSettings"]["Ports"]["22/tcp"]
    return int(port_info[0]["HostPort"])


def wait_for_ssh(
    server: SshEndpoint,
    timeout: int = 30,
) -> None:
    """Poll SSH until the daemon sends its banner."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(
                (server.host, server.port), timeout=2
            ) as sock:
                data = sock.recv(256)
                if data.startswith(b"SSH-"):
                    return
        except OSError:
            pass
        time.sleep(1)
    raise TimeoutError(f"SSH not ready after {timeout}s")


# ── Remote command helpers ───────────────────────────────────


def ssh_exec(
    server: SshEndpoint,
    command: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command on the container via SSH."""
    result = run_remote_command(server, ["sh", "-c", command])
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            result.stdout,
            result.stderr,
        )
    return result


def create_sentinels(
    server: SshEndpoint,
    path: str,
    sentinels: list[str],
) -> None:
    """Create sentinel files on the container via SSH."""
    for sentinel in sentinels:
        result = ssh_exec(server, f"touch {path}/{sentinel}", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create sentinel {sentinel}: {result.stderr}")


def prepare_btrfs_snapshot_based_backup_dst(
    server: SshEndpoint,
    path: str,
) -> None:
    """Create btrfs destination structure.

    Creates the ``staging`` btrfs subvolume and the ``snapshots``
    directory under *path*.
    """
    ssh_exec(server, f"btrfs subvolume create {path}/{STAGING_DIR}")
    ssh_exec(server, f"mkdir -p {path}/{SNAPSHOTS_DIR}")


def prepare_hardlinks_snapshot_based_backup_dst(
    server: SshEndpoint,
    path: str,
) -> None:
    """Create hard-link destination structure.

    Creates the ``snapshots`` directory under *path*.
    """
    ssh_exec(server, f"mkdir -p {path}/{SNAPSHOTS_DIR}")


# ── LUKS helpers ─────────────────────────────────────────────
#
# The Docker test container runs sshd as PID 1 (not systemd).
# Tests use ``DirectMountStrategy`` and the production lifecycle
# functions (``mount_volume`` / ``umount_volume``) for LUKS
# operations.  Only ``read_luks_metadata`` remains here as
# infrastructure for fixture setup.


def read_luks_metadata(server: SshEndpoint) -> LuksMetadata:
    """Read LUKS setup metadata from the test container.

    Returns ``LuksMetadata(available=False)`` if LUKS was not set up
    (e.g. dm-crypt kernel module unavailable on the Docker host).
    """
    available_result = ssh_exec(
        server, "cat /srv/luks-available 2>/dev/null || echo 0", check=False
    )
    if available_result.stdout.strip() != "1":
        return LuksMetadata(available=False)
    else:
        uuid_result = ssh_exec(server, "cat /srv/luks-uuid")
        loop_result = ssh_exec(server, "cat /srv/luks-loop-device")
        return LuksMetadata(
            available=True,
            uuid=uuid_result.stdout.strip(),
            loop_device=loop_result.stdout.strip(),
        )
