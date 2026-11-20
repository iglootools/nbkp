"""Docker helpers for the developer test CLI seed command."""

import functools
import os
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import docker as dockerlib
import docker.errors as docker_errors
lazy from cryptography.hazmat.primitives import serialization
lazy from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ...config import SshConnectionOptions, SshEndpoint
from ...remote.fabricssh import run_remote_command
from .constants import (  # noqa: F401
    BASTION_CONTAINER_NAME,
    LUKS_MAPPER_NAME,
    LUKS_PASSPHRASE,
    NETWORK_NAME,
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_ENCRYPTED_PATH,
    REMOTE_BTRFS_PATH,
    REMOTE_UNENCRYPTED_PATH,
    STORAGE_CONTAINER_NAME,
)
from .errors import DockerError, DockerErrorKind

DOCKER_DIR = Path(__file__).resolve().parent / "dockerbuild"
_IMAGE_TAG = "nbkp-demo-server:latest"

SSH_AUTHORIZED_KEYS_PATH = "/mnt/ssh-authorized-keys"

# Publish sshd on loopback only, on a random port (Docker picks one for host
# port 0): the storage container is privileged with the host's /dev mounted,
# and the demo connects through 127.0.0.1 anyway, so nothing on the LAN
# should reach it.
_SSH_PORT_ON_LOOPBACK = {"22/tcp": ("127.0.0.1", 0)}


def _as_docker_error[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Raise a Docker SDK failure of *fn* as a :class:`DockerError`."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except docker_errors.DockerException as exc:
            raise DockerError(DockerErrorKind.REQUEST_FAILED, str(exc)) from exc

    return wrapper


@dataclass(frozen=True)
class LuksMetadata:
    available: bool
    uuid: str | None = None


def create_test_ssh_endpoint(
    slug: str,
    host: str,
    port: int,
    private_key: Path,
    *,
    proxy_jump: str | None = None,
) -> SshEndpoint:
    """All test containers use ``testuser``, disabled host-key checking,
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


def check_docker() -> None:
    try:
        client = dockerlib.from_env()
        client.ping()
    except docker_errors.DockerException as exc:
        raise DockerError(DockerErrorKind.UNAVAILABLE, str(exc)) from exc


def generate_ssh_keypair(
    seed_dir: Path,
) -> tuple[Path, Path]:
    """The key logs into a privileged container with the host's /dev, and
    the seed dir may be a predictable path under /tmp, so the key is never
    readable by other users, not even until a chmod."""
    ssh_dir = seed_dir / "ssh"
    ssh_dir.mkdir(mode=0o700)
    private_key_path = ssh_dir / "id_ed25519"
    public_key_path = ssh_dir / "id_ed25519.pub"

    key = Ed25519PrivateKey.generate()
    fd = os.open(private_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            )
        )
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )
    public_key_path.write_text(f"{pub_bytes.decode()} nbkp-demo\n")

    return private_key_path, public_key_path


@_as_docker_error
def create_docker_network() -> str:
    """Recreate the network, force-disconnecting containers left on the old one."""
    client = dockerlib.from_env()
    try:
        old = client.networks.get(NETWORK_NAME)
        old.reload()
        for cid in list(old.attrs.get("Containers") or {}):
            try:
                old.disconnect(cid, force=True)
            except docker_errors.NotFound:
                pass  # The container left the network on its own meanwhile.
        old.remove()
    except docker_errors.NotFound:
        pass
    client.networks.create(NETWORK_NAME, driver="bridge")
    return NETWORK_NAME


@_as_docker_error
def build_docker_image() -> None:
    client = dockerlib.from_env()
    try:
        client.images.build(
            path=str(DOCKER_DIR),
            tag=_IMAGE_TAG,
            nocache=True,
        )
    except docker_errors.BuildError as exc:
        raise DockerError(DockerErrorKind.IMAGE_BUILD_FAILED, str(exc)) from exc


@_as_docker_error
def start_storage_container(
    pub_key: Path,
    network_name: str | None = None,
    network_alias: str | None = None,
    *,
    luks_enabled: bool = False,
) -> int:
    """Replace any existing container; return the host port mapped to its SSH.

    The image must already be built via build_docker_image().
    """
    client = dockerlib.from_env()

    try:
        old = client.containers.get(STORAGE_CONTAINER_NAME)
        old.remove(force=True)
    except docker_errors.NotFound:
        pass

    container = client.containers.run(
        _IMAGE_TAG,
        detach=True,
        name=STORAGE_CONTAINER_NAME,
        privileged=True,
        ports=_SSH_PORT_ON_LOOPBACK,
        environment={
            "NBKP_BACKUP_PATH": REMOTE_BACKUP_PATH,
            "NBKP_BTRFS_PATH": REMOTE_BTRFS_PATH,
            "NBKP_BTRFS_ENCRYPTED_PATH": REMOTE_BTRFS_ENCRYPTED_PATH,
            "NBKP_LUKS_PASSPHRASE": LUKS_PASSPHRASE,
            "NBKP_LUKS_MAPPER_NAME": LUKS_MAPPER_NAME,
            "NBKP_LUKS_ENABLED": "1" if luks_enabled else "0",
        },
        volumes={
            str(pub_key): {
                "bind": SSH_AUTHORIZED_KEYS_PATH,
                "mode": "ro",
            },
            # Share the host's /dev and udev runtime.  udisks mounts a device
            # only once udev has probed its filesystem (it reads the ID_FS_TYPE
            # udev property).  With a *private* container /dev, the container's
            # udevd sees a different devtmpfs than the kernel creates dm-crypt
            # devices on, so it intermittently never probes a freshly-unlocked
            # cleartext device — udisks then refuses to mount it ("… is not a
            # mountable filesystem") and the encrypted-volume mount fails.
            # Sharing /dev lets udevd operate on the same devtmpfs the kernel
            # uses, and sharing /run/udev gives udisks the resulting db.  This
            # mirrors the e2e/integration test container (see
            # tests/_docker_fixtures).
            "/dev": {"bind": "/dev", "mode": "rw"},
            "/run/udev": {"bind": "/run/udev", "mode": "rw"},
        },
    )

    if network_name is not None:
        network = client.networks.get(network_name)
        aliases = [network_alias] if network_alias else None
        network.connect(container, aliases=aliases)

    container.reload()
    port_info = container.attrs["NetworkSettings"]["Ports"]["22/tcp"]
    return int(port_info[0]["HostPort"])


@_as_docker_error
def start_bastion_container(
    pub_key: Path,
    network_name: str,
) -> int:
    """Replace any existing bastion; return the host port mapped to its SSH."""
    client = dockerlib.from_env()

    try:
        old = client.containers.get(BASTION_CONTAINER_NAME)
        old.remove(force=True)
    except docker_errors.NotFound:
        pass

    container = client.containers.run(
        _IMAGE_TAG,
        detach=True,
        name=BASTION_CONTAINER_NAME,
        ports=_SSH_PORT_ON_LOOPBACK,
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


def ssh_exec(
    server: SshEndpoint,
    command: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
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
    for sentinel in sentinels:
        result = ssh_exec(server, f"touch {path}/{sentinel}", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create sentinel {sentinel}: {result.stderr}")


# Tests drive LUKS through the production lifecycle functions
# (``mount_volume`` / ``umount_volume``), which delegate to udisks
# (``udisksctl``) authorized by the installed polkit rule.  Only
# ``read_luks_metadata`` remains here as infrastructure for fixture setup.


def read_luks_metadata(server: SshEndpoint) -> LuksMetadata:
    """Returns ``LuksMetadata(available=False)`` if LUKS was not set up
    (e.g. dm-crypt kernel module unavailable on the Docker host).
    """
    available_result = ssh_exec(
        server, "cat /srv/luks-available 2>/dev/null || echo 0", check=False
    )
    if available_result.stdout.strip() != "1":
        return LuksMetadata(available=False)
    else:
        uuid_result = ssh_exec(server, "cat /srv/luks-uuid")
        return LuksMetadata(
            available=True,
            uuid=uuid_result.stdout.strip(),
        )
