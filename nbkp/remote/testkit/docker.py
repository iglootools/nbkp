"""Docker helpers for the developer test CLI seed command."""

import contextlib
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

from nbkp.config import SshConnectionOptions, SshEndpoint
from nbkp.remote.fabricssh import run_remote_command
from nbkp.remote.testkit.constants import (
    LUKS_MAPPER_NAME,
    LUKS_PASSPHRASE,
    NETWORK_NAME,
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_ENCRYPTED_PATH,
    REMOTE_BTRFS_PATH,
)
from nbkp.remote.testkit.errors import (
    DockerError,
    DockerErrorKind,
    SentinelCreationError,
    SshNotReadyError,
)

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
    """
    Whether the test container set up its LUKS volume, and the volume's UUID.

    *uuid* is ``None`` when LUKS is not *available*.
    """

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
    """
    Build an SSH endpoint for a test container.

    All test containers use ``testuser``, disabled host-key checking, and ``/dev/null``
    as the known-hosts file.
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
    """Raise :class:`DockerError` (``UNAVAILABLE``) unless the Docker daemon answers."""
    try:
        client = dockerlib.from_env()
        # docker leaves ping()'s *args and **kwargs unannotated; retire when it
        # annotates them.
        client.ping()  # pyright: ignore[reportUnknownMemberType]
    except docker_errors.DockerException as exc:
        raise DockerError(DockerErrorKind.UNAVAILABLE, str(exc)) from exc


SEED_SSH_DIR = "ssh"
"""The directory under a seed dir that holds the demo key pair."""


def generate_ssh_keypair(
    seed_dir: Path,
) -> tuple[Path, Path]:
    """
    Generate an SSH key pair readable by its owner alone.

    The key logs into a privileged container with the host's /dev, and the seed dir may
    be a predictable path under /tmp, so the key is never readable by other users, not
    even until a chmod.
    """
    ssh_dir = seed_dir / SEED_SSH_DIR
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
            ),
        )
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )
    public_key_path.write_text(f"{pub_bytes.decode()} nbkp-demo\n", encoding="utf-8")

    return private_key_path, public_key_path


@_as_docker_error
def create_docker_network() -> str:
    """Recreate the network, force-disconnecting containers left on the old one."""
    client = dockerlib.from_env()
    try:
        old = client.networks.get(NETWORK_NAME)
        old.reload()
        for cid in list(old.attrs.get("Containers") or {}):
            # The container may have left the network on its own meanwhile.
            with contextlib.suppress(docker_errors.NotFound):
                old.disconnect(cid, force=True)
        old.remove()
    except docker_errors.NotFound:
        pass
    client.networks.create(NETWORK_NAME, driver="bridge")
    return NETWORK_NAME


@_as_docker_error
def build_docker_image() -> None:
    """
    Build the demo server image from ``dockerbuild``, without the build cache.

    Raises :class:`DockerError` (``IMAGE_BUILD_FAILED``) when the build fails.
    """
    client = dockerlib.from_env()
    try:
        client.images.build(
            path=str(DOCKER_DIR),
            tag=_IMAGE_TAG,
            nocache=True,
        )
    except docker_errors.BuildError as exc:
        raise DockerError(DockerErrorKind.IMAGE_BUILD_FAILED, str(exc)) from exc


@dataclass(frozen=True)
class ContainerSpec:
    """
    How a demo or test container runs, apart from its name, ports and network.

    The demo starts its containers through :func:`start_container` and the Docker test
    fixtures through testcontainers, but both build them from one spec, so a change to
    a container needs one edit. *binds* maps each host path to its container path and
    mode (``ro`` or ``rw``).
    """

    environment: dict[str, str]
    binds: dict[str, tuple[str, str]]
    privileged: bool = False


def storage_container_spec(
    pub_key: Path,
    *,
    luks_mapper_name: str = LUKS_MAPPER_NAME,
    luks_enabled: bool = False,
) -> ContainerSpec:
    """
    Spec of the storage container: sshd, rsync, btrfs, udisks and a LUKS volume.

    *luks_enabled* sets up the LUKS volume at startup; otherwise ``/setup-luks.sh``
    does it on demand. Each concurrent container needs its own *luks_mapper_name*,
    since privileged containers share the host kernel's device-mapper namespace.
    """
    return ContainerSpec(
        environment={
            "NBKP_BACKUP_PATH": REMOTE_BACKUP_PATH,
            "NBKP_BTRFS_PATH": REMOTE_BTRFS_PATH,
            "NBKP_BTRFS_ENCRYPTED_PATH": REMOTE_BTRFS_ENCRYPTED_PATH,
            "NBKP_LUKS_PASSPHRASE": LUKS_PASSPHRASE,
            "NBKP_LUKS_MAPPER_NAME": luks_mapper_name,
            "NBKP_LUKS_ENABLED": "1" if luks_enabled else "0",
        },
        binds={
            str(pub_key): (SSH_AUTHORIZED_KEYS_PATH, "ro"),
            # Share the host's /dev and udev runtime.  udisks mounts a device
            # only once udev has probed its filesystem (it reads the ID_FS_TYPE
            # udev property).  With a *private* container /dev, the container's
            # udevd sees a different devtmpfs than the kernel creates dm-crypt
            # devices on, so after btrfs/loop activity (which shares the kernel
            # across all containers on Docker Desktop's LinuxKit VM) it
            # intermittently never probes a freshly-unlocked cleartext device —
            # udisks then refuses to mount it ("… is not a mountable filesystem")
            # and the encrypted-volume mount fails.  Sharing /dev lets udevd
            # operate on the same devtmpfs the kernel uses, and sharing /run/udev
            # gives udisks the resulting db.  This is the minimal set: --network
            # host and a shared /run/dbus also work but break the multi-container
            # bastion topology (port and bus conflicts).
            "/dev": ("/dev", "rw"),
            "/run/udev": ("/run/udev", "rw"),
        },
        privileged=True,
    )


def bastion_container_spec(pub_key: Path) -> ContainerSpec:
    """Spec of the bastion: the same image running sshd alone, as a jump proxy."""
    return ContainerSpec(
        environment={"NBKP_BASTION_ONLY": "1"},
        binds={str(pub_key): (SSH_AUTHORIZED_KEYS_PATH, "ro")},
    )


@_as_docker_error
def start_container(
    name: str,
    spec: ContainerSpec,
    network_name: str,
    *,
    network_alias: str | None = None,
) -> int:
    """
    Replace any container named *name*; return the host port mapped to its SSH.

    The image must already be built via build_docker_image().
    """
    client = dockerlib.from_env()

    try:
        old = client.containers.get(name)
        old.remove(force=True)
    except docker_errors.NotFound:
        pass

    container = client.containers.run(
        _IMAGE_TAG,
        detach=True,
        name=name,
        privileged=spec.privileged,
        ports=_SSH_PORT_ON_LOOPBACK,
        environment=spec.environment,
        volumes={
            host: {"bind": path, "mode": mode}
            for host, (path, mode) in spec.binds.items()
        },
    )

    network = client.networks.get(network_name)
    aliases = [network_alias] if network_alias else None
    # docker leaves connect()'s *args and **kwargs unannotated; retire when it
    # annotates them.
    network.connect(container, aliases=aliases)  # pyright: ignore[reportUnknownMemberType]

    container.reload()
    port_info = container.attrs["NetworkSettings"]["Ports"]["22/tcp"]
    return int(port_info[0]["HostPort"])


def wait_for_ssh(
    server: SshEndpoint,
    timeout_seconds: int = 30,
) -> None:
    """Poll SSH until the daemon sends its banner."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(
                (server.host, server.port),
                timeout=2,
            ) as sock:
                data = sock.recv(256)
                if data.startswith(b"SSH-"):
                    return
        except OSError:
            pass
        time.sleep(1)
    raise SshNotReadyError(timeout_seconds)


def ssh_run(
    server: SshEndpoint,
    command: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run *command* on *server*, each element one argument.

    A path or a value in it is never parsed by the remote shell.
    """
    result = run_remote_command(server, command)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            result.stdout,
            result.stderr,
        )
    return result


def ssh_exec(
    server: SshEndpoint,
    command: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run a fixed shell script on *server*.

    :func:`ssh_run` takes anything built from a path or a value.
    """
    return ssh_run(server, ["sh", "-c", command], check=check)


def create_sentinels(
    server: SshEndpoint,
    path: str,
    sentinels: list[str],
) -> None:
    """
    Create an empty file named after each of *sentinels* in *path* on *server*.

    Raises :class:`SentinelCreationError` at the first one that fails.
    """
    for sentinel_name in sentinels:
        result = ssh_run(server, ["touch", f"{path}/{sentinel_name}"], check=False)
        if result.returncode != 0:
            raise SentinelCreationError(sentinel_name, result.stderr)


# Tests drive LUKS through the production lifecycle functions
# (``mount_volume`` / ``umount_volume``), which delegate to udisks
# (``udisksctl``) authorized by the installed polkit rule.  Only
# ``read_luks_metadata`` remains here as infrastructure for fixture setup.


def read_luks_metadata(server: SshEndpoint) -> LuksMetadata:
    """
    Read the LUKS metadata of the test container.

    Returns ``LuksMetadata(available=False)`` if LUKS was not set up (e.g. dm-crypt
    kernel module unavailable on the Docker host).
    """
    available_result = ssh_exec(
        server,
        "cat /srv/luks-available 2>/dev/null || echo 0",
        check=False,
    )
    if available_result.stdout.strip() != "1":
        return LuksMetadata(available=False)
    else:
        uuid_result = ssh_exec(server, "cat /srv/luks-uuid")
        return LuksMetadata(
            available=True,
            uuid=uuid_result.stdout.strip(),
        )
