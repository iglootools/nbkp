"""
Seed data for demos and tests: sample files, sentinels and chain configs.

Seeds local volumes directly and remote ones through a caller-given command
runner.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    CredentialProvider,
    HardLinkSnapshotConfig,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    RsyncOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_endpoint_path,
)
from nbkp.fsprotocol import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from nbkp.remote.testkit.constants import (
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_ENCRYPTED_PATH,
    REMOTE_BTRFS_PATH,
)

_CHUNK_SIZE = 1024 * 1024

#: Filter rules that exclude the ``excluded/`` directory seeded
#: by :func:`seed_volume`.  Reuse in test configs to verify
#: that rsync filters are honoured end-to-end.
SEED_EXCLUDE_FILTERS: list[str] = ["- excluded/"]

_SAMPLE_FILES = [
    ("sample.txt", "Sample data for backup testing\n"),
    ("photo.jpg", "fake jpeg data\n"),
    ("document.pdf", "fake pdf data\n"),
    ("excluded/cache.tmp", "temporary cached data\n"),
    ("excluded/debug.log", "debug log output\n"),
]


def _write_zeroed_file(path: Path, size_bytes: int) -> None:
    """Write a zeroed file in chunks to avoid large allocs."""
    chunk = b"\x00" * min(_CHUNK_SIZE, size_bytes)
    with path.open("wb") as f:
        remaining = size_bytes
        while remaining > 0:
            f.write(chunk[:remaining])
            remaining -= len(chunk)


def _unless_exists(path: str, *command: str) -> list[str]:
    """
    Run *command* unless *path* exists.

    The paths stay arguments, never shell text.
    """
    return ["sh", "-c", 'test -e "$1" || { shift; exec "$@"; }', "sh", path, *command]


def create_seed_sentinels(
    config: Config,
    remote_exec: Callable[[RemoteVolume, list[str]], object] | None = None,
) -> None:
    """
    Create volume, source, and destination sentinels.

    For local volumes, creates directories and sentinel files directly.  For
    remote volumes, uses *remote_exec(volume, argv)* to run each command on
    the remote host — the volume is passed so callers can route each command
    over that volume's own SSH endpoint (e.g. through a bastion), which
    matters when the volume is mounted by udisks over that same connection.
    """
    for vol in config.volumes.values():
        match vol:
            case LocalVolume():
                vol_path = Path(resolve_endpoint_path(vol, None))
                vol_path.mkdir(parents=True, exist_ok=True)
                (vol_path / VOLUME_SENTINEL).touch()
            case RemoteVolume():
                if remote_exec is not None:
                    vol_path = resolve_endpoint_path(vol, None)
                    remote_exec(vol, ["mkdir", "-p", vol_path])
                    remote_exec(vol, ["touch", f"{vol_path}/{VOLUME_SENTINEL}"])

    seen_src: set[str] = set()
    seen_dst: set[str] = set()
    for sync in config.syncs.values():
        if sync.source not in seen_src:
            seen_src.add(sync.source)
            src_ep = config.source_endpoint(sync)
            src_vol = config.volumes[src_ep.volume]
            _create_endpoint_sentinels(src_vol, src_ep, SOURCE_SENTINEL, remote_exec)
        if sync.destination not in seen_dst:
            seen_dst.add(sync.destination)
            dst_ep = config.destination_endpoint(sync)
            dst_vol = config.volumes[dst_ep.volume]
            _create_endpoint_sentinels(
                dst_vol,
                dst_ep,
                DESTINATION_SENTINEL,
                remote_exec,
            )


def _create_endpoint_sentinels(
    vol: LocalVolume | RemoteVolume,
    ep: SyncEndpoint,
    sentinel_name: str,
    remote_exec: Callable[[RemoteVolume, list[str]], object] | None,
) -> None:
    """Create sentinels and snapshot infrastructure for an endpoint."""
    match vol:
        case LocalVolume():
            _create_local_sentinels(
                Path(resolve_endpoint_path(vol, ep.subdir)),
                ep,
                sentinel_name,
            )
        case RemoteVolume():
            if remote_exec is not None:
                rp = resolve_endpoint_path(vol, ep.subdir)
                for command in _remote_sentinel_commands(rp, ep, sentinel_name):
                    remote_exec(vol, command)


def _create_local_sentinels(path: Path, ep: SyncEndpoint, sentinel_name: str) -> None:
    """Create the sentinel and, if the endpoint snapshots, the snapshot layout."""
    path.mkdir(parents=True, exist_ok=True)
    (path / sentinel_name).touch()
    match (ep.hard_link_snapshots.enabled, ep.btrfs_snapshots.enabled):
        case (True, _):
            _create_local_snapshot_layout(path)
        case (False, True):
            _create_local_btrfs_staging(path)
            _create_local_snapshot_layout(path)
        case _:
            pass  # no snapshots: the sentinel is all the endpoint needs


def _create_local_btrfs_staging(path: Path) -> None:
    """Create the btrfs staging subvolume unless it exists."""
    staging = path / STAGING_DIR
    if not staging.exists():
        subprocess.run(["btrfs", "subvolume", "create", str(staging)], check=True)


def _create_local_snapshot_layout(path: Path) -> None:
    """Create the snapshots directory, and the latest link unless it exists."""
    (path / SNAPSHOTS_DIR).mkdir(exist_ok=True)
    latest = path / LATEST_LINK
    if not latest.exists():
        latest.symlink_to(DEVNULL_TARGET)


def _remote_sentinel_commands(
    rp: str,
    ep: SyncEndpoint,
    sentinel_name: str,
) -> list[list[str]]:
    """Return the commands creating an endpoint's sentinel and snapshots at *rp*."""
    latest = f"{rp}/{LATEST_LINK}"
    staging = f"{rp}/{STAGING_DIR}"
    sentinel_commands = [["mkdir", "-p", rp], ["touch", f"{rp}/{sentinel_name}"]]
    snapshot_layout = [
        ["mkdir", "-p", f"{rp}/{SNAPSHOTS_DIR}"],
        _unless_exists(latest, "ln", "-sfn", DEVNULL_TARGET, latest),
    ]
    match (ep.hard_link_snapshots.enabled, ep.btrfs_snapshots.enabled):
        case (True, _):
            return [*sentinel_commands, *snapshot_layout]
        case (False, True):
            return [
                *sentinel_commands,
                _unless_exists(staging, "btrfs", "subvolume", "create", staging),
                *snapshot_layout,
            ]
        case _:
            return sentinel_commands


def seed_volume(
    vol: LocalVolume | RemoteVolume,
    subdir: str | None = None,
    *,
    big_file_size_bytes: int = 0,
    remote_exec: Callable[[list[str]], None] | None = None,
) -> None:
    """
    Write the sample files, ``excluded/`` included, into *vol* (under *subdir*).

    With *big_file_size_bytes*, also write a zeroed ``large-file.bin`` of that
    size. A remote volume is seeded by running each command with *remote_exec*,
    and left alone when it is ``None``.
    """
    match vol:
        case LocalVolume():
            path = Path(resolve_endpoint_path(vol, subdir))
            path.mkdir(parents=True, exist_ok=True)
            for name, content in _SAMPLE_FILES:
                file_path = path / name
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
            if big_file_size_bytes:
                _write_zeroed_file(
                    path / "large-file.bin",
                    big_file_size_bytes,
                )
        case RemoteVolume():
            if remote_exec is None:
                return
            rp = resolve_endpoint_path(vol, subdir)
            remote_exec(["mkdir", "-p", rp])
            for name, content in _SAMPLE_FILES:
                if "/" in name:
                    parent = name.rsplit("/", 1)[0]
                    remote_exec(["mkdir", "-p", f"{rp}/{parent}"])
                remote_exec(
                    [
                        "sh",
                        "-c",
                        'printf %s "$1" > "$2"',
                        "sh",
                        content,
                        f"{rp}/{name}",
                    ],
                )
            if big_file_size_bytes:
                remote_exec(
                    [
                        "truncate",
                        "-s",
                        str(big_file_size_bytes),
                        f"{rp}/large-file.bin",
                    ],
                )


def build_local_chain_config(
    local_dir: Path,
    *,
    rsync_options: RsyncOptions | None = None,
    max_snapshots: int | None = None,
) -> Config:
    """
    Build a 2-step local-only chain config.

    Volumes::

      src-local-bare            — chain origin (bare source)
      stage-local-hl-snapshots  — HL dest / HL source
      dst-local-bare            — chain terminus (bare dest)

    Optional parameters:

    - *rsync_options*: applied to every sync (e.g. bandwidth limiting).
    - *max_snapshots*: applied to the HL snapshot config.
    """
    hl = HardLinkSnapshotConfig(enabled=True, max_snapshots=max_snapshots)
    rsync_opts = rsync_options if rsync_options is not None else RsyncOptions()

    return Config(
        ssh_endpoints={},
        volumes={
            "src-local-bare": LocalVolume(
                slug="src-local-bare",
                path=str(local_dir / "src-local-bare"),
            ),
            "stage-local-hl-snapshots": LocalVolume(
                slug="stage-local-hl-snapshots",
                path=str(local_dir / "stage-local-hl-snapshots"),
            ),
            "dst-local-bare": LocalVolume(
                slug="dst-local-bare",
                path=str(local_dir / "dst-local-bare"),
            ),
        },
        sync_endpoints={
            "ep-src-local-bare": SyncEndpoint(
                slug="ep-src-local-bare",
                volume="src-local-bare",
            ),
            "ep-stage-local-hl": SyncEndpoint(
                slug="ep-stage-local-hl",
                volume="stage-local-hl-snapshots",
                hard_link_snapshots=hl,
            ),
            "ep-dst-local-bare": SyncEndpoint(
                slug="ep-dst-local-bare",
                volume="dst-local-bare",
            ),
        },
        syncs={
            "step-1": SyncConfig(
                slug="step-1",
                source="ep-src-local-bare",
                destination="ep-stage-local-hl",
                rsync_options=rsync_opts,
                filters=SEED_EXCLUDE_FILTERS,
            ),
            "step-2": SyncConfig(
                slug="step-2",
                source="ep-stage-local-hl",
                destination="ep-dst-local-bare",
                rsync_options=rsync_opts,
                filters=SEED_EXCLUDE_FILTERS,
            ),
        },
    )


def build_chain_config(
    local_dir: Path,
    bastion_endpoint: SshEndpoint,
    proxied_endpoint: SshEndpoint,
    *,
    luks_uuid: str | None = None,
    rsync_options: RsyncOptions | None = None,
    max_snapshots: int | None = None,
    credential_provider: CredentialProvider = CredentialProvider.KEYRING,
    credential_command: list[str] | None = None,
) -> Config:
    """
    Build a 6-hop chain config across local and remote volumes.

    Volumes::

      src-local-bare                — chain origin (bare source)
      stage-local-hl-snapshots      — HL dest / HL source
      stage-remote-bare             — bare dest / HL source
      stage-remote-btrfs-snapshots  — btrfs dest / btrfs source (encrypted)
      stage-remote-btrfs-bare       — bare dest / HL source
      stage-remote-hl-snapshots     — HL dest / HL source
      dst-local-bare                — chain terminus (bare dest)

    When *luks_uuid* is provided, ``stage-remote-btrfs-snapshots``
    gets a ``MountConfig`` so that ``mount_volumes`` /
    ``umount_volumes`` manage the LUKS lifecycle as part of the
    chain — same code path as ``nbkp run``.

    Optional parameters:

    - *rsync_options*: applied to every sync (e.g. bandwidth limiting).
    - *max_snapshots*: applied to both HL and btrfs snapshot configs.
    - *credential_command*: required by the ``command`` *credential_provider*.
    """
    volumes: dict[str, LocalVolume | RemoteVolume] = {
        "src-local-bare": LocalVolume(
            slug="src-local-bare",
            path=str(local_dir / "src-local-bare"),
        ),
        "stage-local-hl-snapshots": LocalVolume(
            slug="stage-local-hl-snapshots",
            path=str(local_dir / "stage-local-hl-snapshots"),
        ),
        "stage-remote-bare": RemoteVolume(
            slug="stage-remote-bare",
            ssh_endpoint="via-bastion",
            path=f"{REMOTE_BACKUP_PATH}/bare",
        ),
        "stage-remote-btrfs-snapshots": RemoteVolume(
            slug="stage-remote-btrfs-snapshots",
            ssh_endpoint="via-bastion",
            path=(
                REMOTE_BTRFS_ENCRYPTED_PATH
                if luks_uuid is not None
                else f"{REMOTE_BTRFS_PATH}/snapshots"
            ),
            mount=(
                MountConfig(
                    device_uuid=luks_uuid,
                    encryption=LuksEncryptionConfig(
                        passphrase_id="test-luks",  # noqa: S106 -- an id naming the secret, not the secret
                    ),
                )
                if luks_uuid is not None
                else None
            ),
        ),
        "stage-remote-btrfs-bare": RemoteVolume(
            slug="stage-remote-btrfs-bare",
            ssh_endpoint="via-bastion",
            path=f"{REMOTE_BTRFS_PATH}/bare",
        ),
        "stage-remote-hl-snapshots": RemoteVolume(
            slug="stage-remote-hl-snapshots",
            ssh_endpoint="via-bastion",
            path=f"{REMOTE_BACKUP_PATH}/hl",
        ),
        "dst-local-bare": LocalVolume(
            slug="dst-local-bare",
            path=str(local_dir / "dst-local-bare"),
        ),
    }

    hl = HardLinkSnapshotConfig(enabled=True, max_snapshots=max_snapshots)
    btrfs = BtrfsSnapshotConfig(enabled=True, max_snapshots=max_snapshots)

    sync_endpoints: dict[str, SyncEndpoint] = {
        "ep-src-local-bare": SyncEndpoint(
            slug="ep-src-local-bare",
            volume="src-local-bare",
        ),
        "ep-stage-local-hl": SyncEndpoint(
            slug="ep-stage-local-hl",
            volume="stage-local-hl-snapshots",
            hard_link_snapshots=hl,
        ),
        "ep-stage-remote-bare": SyncEndpoint(
            slug="ep-stage-remote-bare",
            volume="stage-remote-bare",
        ),
        "ep-stage-remote-btrfs": SyncEndpoint(
            slug="ep-stage-remote-btrfs",
            volume="stage-remote-btrfs-snapshots",
            btrfs_snapshots=btrfs,
        ),
        "ep-stage-remote-btrfs-bare": SyncEndpoint(
            slug="ep-stage-remote-btrfs-bare",
            volume="stage-remote-btrfs-bare",
        ),
        "ep-stage-remote-hl": SyncEndpoint(
            slug="ep-stage-remote-hl",
            volume="stage-remote-hl-snapshots",
            hard_link_snapshots=hl,
        ),
        "ep-dst-local-bare": SyncEndpoint(
            slug="ep-dst-local-bare",
            volume="dst-local-bare",
        ),
    }

    rsync_opts = rsync_options if rsync_options is not None else RsyncOptions()

    syncs: dict[str, SyncConfig] = {
        # local->local, HL destination
        "step-1": SyncConfig(
            slug="step-1",
            source="ep-src-local-bare",
            destination="ep-stage-local-hl",
            filters=SEED_EXCLUDE_FILTERS,
            rsync_options=rsync_opts,
        ),
        # local->remote (bastion), bare destination
        "step-2": SyncConfig(
            slug="step-2",
            source="ep-stage-local-hl",
            destination="ep-stage-remote-bare",
            filters=SEED_EXCLUDE_FILTERS,
            rsync_options=rsync_opts,
        ),
        # remote->remote same-server (bastion), btrfs destination
        "step-3": SyncConfig(
            slug="step-3",
            source="ep-stage-remote-bare",
            destination="ep-stage-remote-btrfs",
            filters=SEED_EXCLUDE_FILTERS,
            rsync_options=rsync_opts,
        ),
        # remote->remote same-server (bastion), bare dest on btrfs
        "step-4": SyncConfig(
            slug="step-4",
            source="ep-stage-remote-btrfs",
            destination="ep-stage-remote-btrfs-bare",
            filters=SEED_EXCLUDE_FILTERS,
            rsync_options=rsync_opts,
        ),
        # remote->remote same-server (bastion), HL destination
        "step-5": SyncConfig(
            slug="step-5",
            source="ep-stage-remote-btrfs-bare",
            destination="ep-stage-remote-hl",
            filters=SEED_EXCLUDE_FILTERS,
            rsync_options=rsync_opts,
        ),
        # remote (bastion)->local, bare destination
        "step-6": SyncConfig(
            slug="step-6",
            source="ep-stage-remote-hl",
            destination="ep-dst-local-bare",
            filters=SEED_EXCLUDE_FILTERS,
            rsync_options=rsync_opts,
        ),
    }

    return Config(
        credential_provider=credential_provider,
        credential_command=credential_command,
        ssh_endpoints={
            "bastion": bastion_endpoint,
            "via-bastion": proxied_endpoint,
        },
        volumes=volumes,
        sync_endpoints=sync_endpoints,
        syncs=syncs,
    )
