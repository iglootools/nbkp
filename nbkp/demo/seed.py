"""
Seeding a demo directory: a config, sample data and, optionally, containers.

The demo runs nbkp against local volumes and, with Docker, a bastion and a
storage container reached over SSH, optionally with a LUKS-encrypted volume.
"""

import shlex
import subprocess
import tempfile
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from enum import StrEnum
from importlib.util import find_spec
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nbkp.config import Config, CredentialProvider, RsyncOptions, SshEndpoint
from nbkp.config.testkit import write_config
from nbkp.disks import MountResult, UmountResult, mount_phase, umount_volumes
from nbkp.policy import Severity
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.remote.testkit.constants import LUKS_PASSPHRASE
from nbkp.remote.testkit.errors import DockerError
from nbkp.sync.testkit.seed import (
    build_chain_config,
    build_local_chain_config,
    create_seed_sentinels,
    seed_volume,
)

# The 'docker' extra is optional; seed_demo(docker=True) reports it through
# _check_docker(has_docker) before anything here resolves these lazy names.
lazy from nbkp.remote.testkit.docker import (
    DOCKER_DIR,
    build_docker_image,
    check_docker,
    create_docker_network,
    create_test_ssh_endpoint,
    generate_ssh_keypair,
    read_luks_metadata,
    ssh_run,
    start_bastion_container,
    start_storage_container,
    wait_for_ssh,
)

_HAS_DOCKER = find_spec("docker") is not None

# What the demo config gives the `command` credential provider: it prints the
# demo passphrase whatever the {id}, which sh receives as $0 and ignores.
DEMO_CREDENTIAL_COMMAND = [
    "sh",
    "-c",
    f"printf '%s\\n' {shlex.quote(LUKS_PASSPHRASE)}",
    "{id}",
]


class SeedErrorKind(StrEnum):
    """Why seeding the demo failed: the ``kind`` of a :class:`SeedError`."""

    DOCKER_EXTRA_MISSING = "docker-extra-missing"
    DOCKER_UNAVAILABLE = "docker-unavailable"
    DOCKER_DIR_MISSING = "docker-dir-missing"
    DOCKER_FAILED = "docker-failed"
    SSH_NOT_READY = "ssh-not-ready"
    REMOTE_COMMAND_FAILED = "remote-command-failed"
    LUKS_UNAVAILABLE = "luks-unavailable"
    MOUNT_FAILED = "mount-failed"
    UMOUNT_FAILED = "umount-failed"
    FILE_OPERATION_FAILED = "file-operation-failed"


# Raised before any container starts.
_PREREQUISITE_KINDS = frozenset(
    {
        SeedErrorKind.DOCKER_EXTRA_MISSING,
        SeedErrorKind.DOCKER_UNAVAILABLE,
        SeedErrorKind.DOCKER_DIR_MISSING,
    },
)


class SeedError(Exception):
    """
    *detail* is the failing tool's message, or the missing path.

    *result* is the failed mount or umount, for the ``mount-failed`` and
    ``umount-failed`` kinds: the CLI renders the message from it.
    """

    def __init__(
        self,
        kind: SeedErrorKind,
        detail: str = "",
        *,
        result: MountResult | UmountResult | None = None,
    ) -> None:
        """Build the error from its kind and the detail that explains it."""
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.result = result

    @property
    def may_leave_containers(self) -> bool:
        """Whether the demo containers may have started before the failure."""
        return self.kind not in _PREREQUISITE_KINDS


class SeedResult(BaseModel):
    """
    What a seed produced: its directory, config file and config.

    The container SSH ports are set only when the demo started containers.
    """

    model_config = ConfigDict(frozen=True)

    base_dir: Path
    config_path: Path
    config: Config
    bastion_port: int | None = None
    storage_port: int | None = None


@dataclass(frozen=True)
class SeedStep:
    """*running* is shown while the step runs, *done* is its result line."""

    running: str
    done: str


_BUILD_IMAGE = SeedStep("Building Docker image...", "build Docker image")
_CREATE_NETWORK = SeedStep("Creating Docker network...", "create Docker network")
_START_BASTION = SeedStep("Starting bastion container...", "start bastion container")
_WAIT_BASTION = SeedStep("Waiting for bastion SSH...", "bastion SSH")
_START_STORAGE = SeedStep("Starting storage container...", "start storage container")
_WAIT_STORAGE = SeedStep("Waiting for storage SSH...", "storage SSH")
_READ_LUKS = SeedStep("Reading LUKS metadata...", "read LUKS metadata")
_MOUNT = SeedStep("Mounting encrypted volume...", "mount encrypted volume")
_UMOUNT = SeedStep("Unmounting encrypted volume...", "umount encrypted volume")
_SEED_VOLUMES = SeedStep("Seeding volumes...", "seed volumes")


def seed_steps(*, docker: bool, luks: bool) -> list[SeedStep]:
    """Return the steps :func:`seed_demo` reports through its callbacks, in order."""
    return [
        *(
            [
                _BUILD_IMAGE,
                _CREATE_NETWORK,
                _START_BASTION,
                _WAIT_BASTION,
                _START_STORAGE,
                _WAIT_STORAGE,
            ]
            if docker
            else []
        ),
        *([_READ_LUKS, _MOUNT] if docker and luks else []),
        _SEED_VOLUMES,
        *([_UMOUNT] if docker and luks else []),
    ]


@dataclass(frozen=True)
class _Steps:
    on_start: Callable[[str], None] | None
    on_end: Callable[[str, Severity, str | None], None] | None

    def start(self, step: SeedStep) -> None:
        if self.on_start is not None:
            self.on_start(step.running)

    def end(self, step: SeedStep, *, success: bool, detail: str | None = None) -> None:
        if self.on_end is not None:
            self.on_end(step.done, Severity.OK if success else Severity.ERROR, detail)

    @contextmanager
    def run(self, step: SeedStep) -> Generator[None]:
        """Report *step* around the block, as failed if the block raises."""
        self.start(step)
        try:
            yield
        except SeedError as e:
            self.end(step, success=False, detail=e.detail or None)
            raise
        except Exception as e:
            self.end(step, success=False, detail=str(e))
            raise
        self.end(step, success=True)


@dataclass(frozen=True)
class _Containers:
    private_key: Path
    bastion: SshEndpoint
    storage: SshEndpoint
    bastion_port: int
    storage_port: int


def _check_docker(*, has_docker: bool) -> None:
    """Fail before anything is created when the demo cannot use Docker."""
    if not has_docker:
        raise SeedError(SeedErrorKind.DOCKER_EXTRA_MISSING)
    try:
        check_docker()
    except DockerError as e:
        raise SeedError(SeedErrorKind.DOCKER_UNAVAILABLE, e.detail) from e
    if not DOCKER_DIR.is_dir():
        raise SeedError(SeedErrorKind.DOCKER_DIR_MISSING, str(DOCKER_DIR))


def _seed_dir(base_dir: Path | None) -> Path:
    if base_dir is not None:
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir
    else:
        return Path(tempfile.mkdtemp(prefix="nbkp-demo-"))


def seed_plan(
    base_dir: Path | None,
    *,
    docker: bool = True,
    luks: bool = True,
) -> list[str]:
    """Return the steps :func:`seed_demo` would perform, for ``--dry-run``."""
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
    base_dir: Path | None,
    *,
    docker: bool = True,
    luks: bool = True,
    big_file_size: int = 1,
    bandwidth_limit: int = 250,
    credential_provider: CredentialProvider = CredentialProvider.KEYRING,
    on_step_start: Callable[[str], None] | None = None,
    on_step_end: Callable[[str, Severity, str | None], None] | None = None,
    has_docker: bool = _HAS_DOCKER,
) -> SeedResult:
    """
    Seed *base_dir*, or a new temp dir when it is ``None``.

    *on_step_start* and *on_step_end* receive each step of :func:`seed_steps`:
    its in-progress label (e.g. ``"Building Docker image..."``), then its
    result line. *has_docker* says whether the 'docker' extra is installed.

    Every failure is a :class:`SeedError`; the containers it may have
    started are left running (:attr:`SeedError.may_leave_containers`).
    """
    steps = _Steps(on_step_start, on_step_end)
    rsync_opts = (
        RsyncOptions(extra_options=[f"--bwlimit={bandwidth_limit}"])
        if bandwidth_limit
        else RsyncOptions()
    )
    if docker:
        _check_docker(has_docker=has_docker)
    try:
        return _seed(
            _seed_dir(base_dir),
            docker=docker,
            luks=luks,
            big_file_size=big_file_size,
            rsync_opts=rsync_opts,
            credential_provider=credential_provider,
            steps=steps,
        )
    except DockerError as e:
        raise SeedError(SeedErrorKind.DOCKER_FAILED, e.detail) from e
    except TimeoutError as e:
        raise SeedError(SeedErrorKind.SSH_NOT_READY, str(e)) from e
    except subprocess.CalledProcessError as e:
        raise SeedError(
            SeedErrorKind.REMOTE_COMMAND_FAILED,
            _called_process_detail(e),
        ) from e
    # After TimeoutError, which is an OSError too.
    except OSError as e:
        raise SeedError(SeedErrorKind.FILE_OPERATION_FAILED, str(e)) from e


def _called_process_detail(e: subprocess.CalledProcessError) -> str:
    stderr = (e.stderr or "").strip()
    return f"{e.cmd!r} exited {e.returncode}" + (f": {stderr}" if stderr else "")


def _seed(
    base_dir: Path,
    *,
    docker: bool,
    luks: bool,
    big_file_size: int,
    rsync_opts: RsyncOptions,
    credential_provider: CredentialProvider,
    steps: _Steps,
) -> SeedResult:
    containers = _start_containers(base_dir, luks=luks, steps=steps) if docker else None
    luks_uuid = (
        _read_luks_uuid(containers, steps) if containers is not None and luks else None
    )
    config = (
        docker_demo_config(
            base_dir,
            bastion=containers.bastion,
            private_key=containers.private_key,
            luks_uuid=luks_uuid,
            rsync_options=rsync_opts,
            credential_provider=credential_provider,
        )
        if containers is not None
        else build_local_chain_config(
            base_dir,
            rsync_options=rsync_opts,
            max_snapshots=5,
        )
    )
    resolved = resolve_all_endpoints(config)
    mounted: AbstractContextManager[None] = (
        encrypted_volume_mounted(
            config,
            resolved,
            on_step_start=steps.on_start,
            on_step_end=steps.on_end,
        )
        if luks_uuid is not None
        else nullcontext()
    )
    with mounted:
        _seed_data(config, containers, big_file_size, steps)
    config_path = write_config(base_dir, config)
    return SeedResult(
        base_dir=base_dir,
        config_path=config_path,
        config=config,
        bastion_port=containers.bastion_port if containers else None,
        storage_port=containers.storage_port if containers else None,
    )


def _start_containers(base_dir: Path, *, luks: bool, steps: _Steps) -> _Containers:
    private_key, pub_key = generate_ssh_keypair(base_dir)

    with steps.run(_BUILD_IMAGE):
        build_docker_image()

    with steps.run(_CREATE_NETWORK):
        network_name = create_docker_network()

    with steps.run(_START_BASTION):
        bastion_port = start_bastion_container(pub_key, network_name)
    bastion = create_test_ssh_endpoint(
        "bastion",
        "127.0.0.1",
        bastion_port,
        private_key,
    )
    with steps.run(_WAIT_BASTION):
        wait_for_ssh(bastion)

    with steps.run(_START_STORAGE):
        storage_port = start_storage_container(
            pub_key,
            network_name=network_name,
            network_alias="backup-server",
            luks_enabled=luks,
        )
    storage = create_test_ssh_endpoint(
        "storage",
        "127.0.0.1",
        storage_port,
        private_key,
    )
    with steps.run(_WAIT_STORAGE):
        # Generous timeout: the storage container formats a btrfs loop image
        # and brings up dbus/udevd/udisksd (probing the shared host /dev)
        # before sshd is ready, which can exceed the 30s default on a loaded
        # machine.
        wait_for_ssh(storage, timeout=90)
    return _Containers(private_key, bastion, storage, bastion_port, storage_port)


def _read_luks_uuid(containers: _Containers, steps: _Steps) -> str:
    with steps.run(_READ_LUKS):
        meta = read_luks_metadata(containers.storage)
        if not meta.available or meta.uuid is None:
            raise SeedError(SeedErrorKind.LUKS_UNAVAILABLE)
    return meta.uuid


def docker_demo_config(
    base_dir: Path,
    *,
    bastion: SshEndpoint,
    private_key: Path,
    luks_uuid: str | None,
    rsync_options: RsyncOptions,
    credential_provider: CredentialProvider,
) -> Config:
    """
    Chain layout matching the integration test, through *bastion*.

    The storage container is reached as ``backup-server`` behind the bastion,
    with *private_key*. Without *luks_uuid* there is no encrypted volume, and
    the config keeps the default credential provider.
    """
    provider = (
        credential_provider if luks_uuid is not None else CredentialProvider.KEYRING
    )
    proxied = create_test_ssh_endpoint(
        "via-bastion",
        "backup-server",
        22,
        private_key,
        proxy_jump="bastion",
    )
    return build_chain_config(
        base_dir,
        bastion,
        proxied,
        luks_uuid=luks_uuid,
        rsync_options=rsync_options,
        max_snapshots=5,
        credential_provider=provider,
        credential_command=(
            DEMO_CREDENTIAL_COMMAND if provider == CredentialProvider.COMMAND else None
        ),
    )


@contextmanager
def encrypted_volume_mounted(
    config: Config,
    resolved: ResolvedEndpoints,
    *,
    passphrase_fn: Callable[[str], str] = lambda _: LUKS_PASSPHRASE,
    run: VolumeCommandRunner = run_on_volume,
    on_step_start: Callable[[str], None] | None = None,
    on_step_end: Callable[[str, Severity, str | None], None] | None = None,
) -> Generator[None]:
    """
    Mount the demo's encrypted volume around the block, and check the umount.

    The umount + lock also runs when mounting fails, as in
    ``disks/context.py``: the volume may already be unlocked by then.  A
    failed umount leaves the demo LUKS volume unlocked: it is reported and,
    when the block itself succeeded, turned into a ``SeedError``.  The udisks
    steps run through *run*, unlocking with the demo passphrase
    (*passphrase_fn*). The mount and umount steps are reported through
    *on_step_start* and *on_step_end*, as :func:`seed_demo` reports them.
    """
    steps = _Steps(on_step_start, on_step_end)
    try:
        with steps.run(_MOUNT):
            failed_mount = next(
                (
                    r
                    for r in mount_phase(
                        config,
                        resolved,
                        passphrase_fn=passphrase_fn,
                        run=run,
                    )
                    if not r.success
                ),
                None,
            )
            if failed_mount is not None:
                raise SeedError(
                    SeedErrorKind.MOUNT_FAILED,
                    _failure_message(failed_mount),
                    result=failed_mount,
                )
        yield
    finally:
        failed_umount = _umount_encrypted(config, resolved, steps, run=run)
    if failed_umount is not None:
        raise SeedError(
            SeedErrorKind.UMOUNT_FAILED,
            _failure_message(failed_umount),
            result=failed_umount,
        )


def _umount_encrypted(
    config: Config,
    resolved: ResolvedEndpoints,
    steps: _Steps,
    *,
    run: VolumeCommandRunner,
) -> UmountResult | None:
    """Umount + lock; returns the failed result, if any."""
    steps.start(_UMOUNT)
    failed = next(
        (r for r in umount_volumes(config, resolved, run=run) if not r.success),
        None,
    )
    steps.end(
        _UMOUNT,
        success=failed is None,
        detail=_failure_message(failed) if failed is not None else None,
    )
    return failed


def _failure_message(result: MountResult | UmountResult) -> str:
    """
    Return the failed command's own message, or else the failure kind.

    Data for the step line, not the rendered message: the CLI renders that
    from :attr:`SeedError.result`.
    """
    failure_message = result.failure.message if result.failure is not None else None
    return failure_message or str(result.failure_reason or "unknown error")


def _seed_data(
    config: Config,
    containers: _Containers | None,
    big_file_size: int,
    steps: _Steps,
) -> None:
    storage = containers.storage if containers is not None else None

    with steps.run(_SEED_VOLUMES):
        create_seed_sentinels(
            config,
            remote_exec=(
                (lambda _vol, command: ssh_run(storage, command))
                if storage is not None
                else None
            ),
        )
        seed_volume(
            config.volumes["src-local-bare"],
            big_file_size_bytes=big_file_size * 1024 * 1024,
        )
