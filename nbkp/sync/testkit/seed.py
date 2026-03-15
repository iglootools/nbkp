"""Seed filesystem helpers: sentinels and sample data."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from ...config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    RsyncOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from ...remote.testkit.constants import (
    LUKS_MAPPER_NAME,
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_PATH,
    REMOTE_BTRFS_ENCRYPTED_PATH,
)
from ...fsprotocol import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from ..rsync import resolve_path

_CHUNK_SIZE = 1024 * 1024  # 1 MB

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


def create_seed_sentinels(
    config: Config,
    remote_exec: Callable[[str], None] | None = None,
) -> None:
    """Create volume, source, and destination sentinels.

    For local volumes, creates directories and sentinel files
    directly.  For remote volumes, uses *remote_exec(command)*
    to run shell commands on the remote host.
    """
    # Volume sentinels (.nbkp-vol)
    for vol in config.volumes.values():
        match vol:
            case LocalVolume():
                vol_path = Path(vol.path)
                vol_path.mkdir(parents=True, exist_ok=True)
                (vol_path / VOLUME_SENTINEL).touch()
            case RemoteVolume():
                if remote_exec is not None:
                    remote_exec(f"mkdir -p {vol.path}")
                    remote_exec(f"touch {vol.path}/{VOLUME_SENTINEL}")

    # Sync endpoint sentinels — iterate unique endpoints
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
                dst_vol, dst_ep, DESTINATION_SENTINEL, remote_exec
            )


def _create_endpoint_sentinels(
    vol: LocalVolume | RemoteVolume,
    ep: SyncEndpoint,
    sentinel_name: str,
    remote_exec: Callable[[str], None] | None,
) -> None:
    """Create sentinels and snapshot infrastructure for an endpoint."""
    btrfs = ep.btrfs_snapshots
    hard_link = ep.hard_link_snapshots

    match vol:
        case LocalVolume():
            path = Path(vol.path)
            if ep.subdir:
                path = path / ep.subdir
            path.mkdir(parents=True, exist_ok=True)
            (path / sentinel_name).touch()
            if hard_link.enabled:
                (path / SNAPSHOTS_DIR).mkdir(exist_ok=True)
                latest = path / LATEST_LINK
                if not latest.exists():
                    latest.symlink_to(DEVNULL_TARGET)
            elif btrfs.enabled:
                if not (path / STAGING_DIR).exists():
                    subprocess.run(
                        [
                            "btrfs",
                            "subvolume",
                            "create",
                            str(path / STAGING_DIR),
                        ],
                        check=True,
                    )
                (path / SNAPSHOTS_DIR).mkdir(exist_ok=True)
                latest = path / LATEST_LINK
                if not latest.exists():
                    latest.symlink_to(DEVNULL_TARGET)
        case RemoteVolume():
            if remote_exec is not None:
                rp = vol.path
                if ep.subdir:
                    rp = f"{rp}/{ep.subdir}"
                remote_exec(f"mkdir -p {rp}")
                remote_exec(f"touch {rp}/{sentinel_name}")
                if hard_link.enabled:
                    remote_exec(f"mkdir -p {rp}/{SNAPSHOTS_DIR}")
                    remote_exec(
                        f"test -e {rp}/{LATEST_LINK}"
                        f" || ln -sfn {DEVNULL_TARGET}"
                        f" {rp}/{LATEST_LINK}"
                    )
                elif btrfs.enabled:
                    remote_exec(
                        f"test -e {rp}/{STAGING_DIR}"
                        " || btrfs subvolume create"
                        f" {rp}/{STAGING_DIR}"
                    )
                    remote_exec(f"mkdir -p {rp}/{SNAPSHOTS_DIR}")
                    remote_exec(
                        f"test -e {rp}/{LATEST_LINK}"
                        f" || ln -sfn {DEVNULL_TARGET}"
                        f" {rp}/{LATEST_LINK}"
                    )


def create_seed_data(
    config: Config,
    big_file_size_mb: int = 0,
    remote_exec: Callable[[str], None] | None = None,
) -> None:
    """Generate sample files in source volumes.

    Creates a handful of small files in each unique source
    path.  When *big_file_size_mb* > 0, an additional large
    zeroed file is written to slow down syncs for manual
    testing.

    For remote source volumes, uses *remote_exec(command)*
    to create files on the remote host.
    """
    size_bytes = big_file_size_mb * 1024 * 1024

    unique_sources: dict[str, tuple[LocalVolume | RemoteVolume, str | None]] = {}
    for s in config.syncs.values():
        src_ep = config.source_endpoint(s)
        src_vol = config.volumes[src_ep.volume]
        key = resolve_path(src_vol, src_ep.subdir)
        if key not in unique_sources:
            unique_sources[key] = (src_vol, src_ep.subdir)

    for vol, subdir in unique_sources.values():
        seed_volume(
            vol,
            subdir,
            big_file_size_bytes=size_bytes,
            remote_exec=remote_exec,
        )


def seed_volume(
    vol: LocalVolume | RemoteVolume,
    subdir: str | None = None,
    *,
    big_file_size_bytes: int = 0,
    remote_exec: Callable[[str], None] | None = None,
) -> None:
    """Write sample files into a single source volume."""
    match vol:
        case LocalVolume():
            base = Path(vol.path)
            path = base / subdir if subdir else base
            path.mkdir(parents=True, exist_ok=True)
            for name, content in _SAMPLE_FILES:
                file_path = path / name
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content)
            if big_file_size_bytes:
                _write_zeroed_file(
                    path / "large-file.bin",
                    big_file_size_bytes,
                )
        case RemoteVolume():
            if remote_exec is None:
                return
            rp = vol.path
            if subdir:
                rp = f"{rp}/{subdir}"
            remote_exec(f"mkdir -p {rp}")
            for name, content in _SAMPLE_FILES:
                if "/" in name:
                    parent = name.rsplit("/", 1)[0]
                    remote_exec(f"mkdir -p {rp}/{parent}")
                remote_exec(f"printf %s {shlex.quote(content)} > {rp}/{name}")
            if big_file_size_bytes:
                remote_exec(f"truncate -s {big_file_size_bytes} {rp}/large-file.bin")


def build_local_chain_config(
    local_dir: Path,
    *,
    rsync_options: RsyncOptions | None = None,
    max_snapshots: int | None = None,
) -> Config:
    """Build a 2-step local-only chain config.

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
) -> Config:
    """Build a 6-hop chain config across local and remote volumes.

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
                    strategy="direct",
                    device_uuid=luks_uuid,
                    encryption=LuksEncryptionConfig(
                        mapper_name=LUKS_MAPPER_NAME,
                        passphrase_id="test-luks",
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
        # step-1 source: bare local origin
        "ep-src-local-bare": SyncEndpoint(
            slug="ep-src-local-bare",
            volume="src-local-bare",
        ),
        # step-1 dest / step-2 source: local HL snapshots
        "ep-stage-local-hl": SyncEndpoint(
            slug="ep-stage-local-hl",
            volume="stage-local-hl-snapshots",
            hard_link_snapshots=hl,
        ),
        # step-2 dest / step-3 source: remote bare
        "ep-stage-remote-bare": SyncEndpoint(
            slug="ep-stage-remote-bare",
            volume="stage-remote-bare",
        ),
        # step-3 dest / step-4 source: remote btrfs snapshots
        "ep-stage-remote-btrfs": SyncEndpoint(
            slug="ep-stage-remote-btrfs",
            volume="stage-remote-btrfs-snapshots",
            btrfs_snapshots=btrfs,
        ),
        # step-4 dest / step-5 source: remote btrfs bare
        "ep-stage-remote-btrfs-bare": SyncEndpoint(
            slug="ep-stage-remote-btrfs-bare",
            volume="stage-remote-btrfs-bare",
        ),
        # step-5 dest / step-6 source: remote HL snapshots
        "ep-stage-remote-hl": SyncEndpoint(
            slug="ep-stage-remote-hl",
            volume="stage-remote-hl-snapshots",
            hard_link_snapshots=hl,
        ),
        # step-6 dest: bare local terminus
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
        ssh_endpoints={
            "bastion": bastion_endpoint,
            "via-bastion": proxied_endpoint,
        },
        volumes=volumes,
        sync_endpoints=sync_endpoints,
        syncs=syncs,
    )
