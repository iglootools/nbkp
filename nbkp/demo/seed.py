import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..config import (
    Config,
    CredentialProvider,
    RsyncOptions,
    SshEndpoint,
)
from ..config.testkit import write_config
from ..disks import mount_phase, umount_volumes
from ..disks.output import mount_failure_detail, umount_failure_detail
from ..policy import Severity
from ..remote.dispatch import VolumeCommandRunner, run_on_volume
from ..remote.endpoints import ResolvedEndpoints
from ..remote.resolution import resolve_all_endpoints
from ..sync.testkit.seed import (
    build_chain_config,
    build_local_chain_config,
    create_seed_sentinels,
    seed_volume,
)

# The 'docker' extra is optional; seed_demo(docker=True) reports it through
# _check_docker(has_docker) before anything here resolves these lazy names.
lazy from ..remote.testkit.docker import (
    DOCKER_DIR,
    LUKS_PASSPHRASE,
    DockerError,
    build_docker_image,
    check_docker,
    create_docker_network,
    create_test_ssh_endpoint,
    generate_ssh_keypair,
    read_luks_metadata,
    ssh_exec,
    start_bastion_container,
    start_storage_container,
    wait_for_ssh,
)

_HAS_DOCKER = find_spec("docker") is not None


class SeedError(Exception):
    """Its message is shown to the user as-is."""


class SeedResult(BaseModel):
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
    """The steps :func:`seed_demo` reports through its callbacks, in order."""
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

    def end(self, step: SeedStep, success: bool, detail: str | None = None) -> None:
        if self.on_end is not None:
            self.on_end(step.done, Severity.OK if success else Severity.ERROR, detail)

    @contextmanager
    def run(self, step: SeedStep) -> Generator[None]:
        """Report *step* around the block, as failed if the block raises."""
        self.start(step)
        try:
            yield
        except Exception as e:
            self.end(step, False, str(e))
            raise
        self.end(step, True)


@dataclass(frozen=True)
class _Containers:
    private_key: Path
    bastion: SshEndpoint
    storage: SshEndpoint
    bastion_port: int
    storage_port: int


def _check_docker(has_docker: bool) -> None:
    """Fail before anything is created when the demo cannot use Docker."""
    if not has_docker:
        raise SeedError(
            "Docker support requires the 'docker' extra."
            " Install it with: uv tool install 'nbkp[docker]'"
            " (or use --no-docker)."
        )
    try:
        check_docker()
    except DockerError as e:
        raise SeedError(f"Docker is not available: {e.detail}") from e
    if not DOCKER_DIR.is_dir():
        raise SeedError(f"Docker directory not found: {DOCKER_DIR}")


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
    """Seed *base_dir*, or a new temp dir when it is ``None``.

    *on_step_start* and *on_step_end* receive each step of :func:`seed_steps`:
    its in-progress label (e.g. ``"Building Docker image..."``), then its
    result line. *has_docker* says whether the 'docker' extra is installed.
    """
    steps = _Steps(on_step_start, on_step_end)
    rsync_opts = (
        RsyncOptions(extra_options=[f"--bwlimit={bandwidth_limit}"])
        if bandwidth_limit
        else RsyncOptions()
    )
    if docker:
        _check_docker(has_docker)
    base_dir = _seed_dir(base_dir)
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
    config_path = write_config(base_dir, config)
    return SeedResult(
        base_dir=base_dir,
        config_path=config_path,
        config=config,
        bastion_port=containers.bastion_port if containers else None,
        storage_port=containers.storage_port if containers else None,
    )


def _start_containers(base_dir: Path, luks: bool, steps: _Steps) -> _Containers:
    private_key, pub_key = generate_ssh_keypair(base_dir)

    with steps.run(_BUILD_IMAGE):
        try:
            build_docker_image()
        except DockerError as e:
            raise SeedError(f"Docker image build failed: {e.detail}") from e

    with steps.run(_CREATE_NETWORK):
        network_name = create_docker_network()

    with steps.run(_START_BASTION):
        bastion_port = start_bastion_container(pub_key, network_name)
    bastion = create_test_ssh_endpoint(
        "bastion", "127.0.0.1", bastion_port, private_key
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
        "storage", "127.0.0.1", storage_port, private_key
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
            raise SeedError(
                "LUKS unavailable (dm-crypt kernel module missing?)."
                " Use --no-luks to skip encrypted volume setup."
            )
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
    *,
    passphrase_fn: Callable[[str], str] = lambda _: LUKS_PASSPHRASE,
    run: VolumeCommandRunner = run_on_volume,
) -> Generator[None]:
    """Mount the encrypted volume around seeding, and check the umount.

    The umount + lock also runs when mounting fails, as in
    ``disks/context.py``: the volume may already be unlocked by then.  A
    failed umount leaves the demo LUKS volume unlocked: it is reported and,
    when seeding itself succeeded, turned into a ``SeedError``.  The udisks
    steps run through *run*, unlocking with the demo passphrase
    (*passphrase_fn*).
    """
    if not enabled:
        yield
        return
    try:
        with steps.run(_MOUNT):
            failed_mount = next(
                (
                    r
                    for r in mount_phase(
                        config, resolved, passphrase_fn=passphrase_fn, run=run
                    )
                    if not r.success
                ),
                None,
            )
            if failed_mount is not None:
                raise SeedError(f"Mount failed: {mount_failure_detail(failed_mount)}")
        yield
    finally:
        failed_umount = _umount_encrypted(config, resolved, steps, run=run)
    if failed_umount is not None:
        raise SeedError(
            f"Umount failed: {failed_umount}. The encrypted volume may still be"
            " mounted; run nbkp disks umount on the seeded config."
        )


def _umount_encrypted(
    config: Config,
    resolved: ResolvedEndpoints,
    steps: _Steps,
    *,
    run: VolumeCommandRunner,
) -> str | None:
    """Umount + lock; returns the failure detail, if any."""
    steps.start(_UMOUNT)
    failed = next(
        (r for r in umount_volumes(config, resolved, run=run) if not r.success), None
    )
    detail = (
        (umount_failure_detail(failed) or "unknown error")
        if failed is not None
        else None
    )
    steps.end(_UMOUNT, failed is None, detail)
    return detail


def _seed_data(
    config: Config,
    containers: _Containers | None,
    big_file_size: int,
    steps: _Steps,
) -> None:
    storage = containers.storage if containers is not None else None

    def _run_remote(_vol: object, cmd: str) -> None:
        assert storage is not None
        ssh_exec(storage, cmd)

    with steps.run(_SEED_VOLUMES):
        create_seed_sentinels(
            config, remote_exec=_run_remote if storage is not None else None
        )
        seed_volume(
            config.volumes["src-local-bare"],
            big_file_size_bytes=big_file_size * 1024 * 1024,
        )
