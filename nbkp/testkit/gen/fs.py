"""Seed filesystem helpers: sentinels and sample data."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from ...config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
)
from ...sync.btrfs import LATEST_LINK, SNAPSHOTS_DIR, STAGING_DIR

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
                (vol_path / ".nbkp-vol").touch()
            case RemoteVolume():
                if remote_exec is not None:
                    remote_exec(f"mkdir -p {vol.path}")
                    remote_exec(f"touch {vol.path}/.nbkp-vol")

    # Sync endpoint sentinels
    for sync in config.syncs.values():
        _create_source_sentinels(config, sync, remote_exec)
        _create_dest_sentinels(config, sync, remote_exec)


_SEED_SNAPSHOT_NAME = "1970-01-01T00:00:00.000Z"


def _create_source_sentinels(
    config: Config,
    sync: SyncConfig,
    remote_exec: Callable[[str], None] | None,
) -> None:
    vol = config.volumes[sync.source.volume]
    subdir = sync.source.subdir
    btrfs = sync.source.btrfs_snapshots
    hard_link = sync.source.hard_link_snapshots

    match vol:
        case LocalVolume():
            path = Path(vol.path)
            if subdir:
                path = path / subdir
            path.mkdir(parents=True, exist_ok=True)
            (path / ".nbkp-src").touch()
            if hard_link.enabled:
                snap = path / SNAPSHOTS_DIR / _SEED_SNAPSHOT_NAME
                snap.mkdir(parents=True, exist_ok=True)
                latest = path / LATEST_LINK
                if not latest.exists():
                    latest.symlink_to(f"{SNAPSHOTS_DIR}/{_SEED_SNAPSHOT_NAME}")
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
                # Seed snapshot + latest symlink
                seed = path / SNAPSHOTS_DIR / _SEED_SNAPSHOT_NAME
                if not seed.exists():
                    subprocess.run(
                        [
                            "btrfs",
                            "subvolume",
                            "snapshot",
                            "-r",
                            str(path / STAGING_DIR),
                            str(seed),
                        ],
                        check=True,
                    )
                latest = path / LATEST_LINK
                if not latest.exists():
                    latest.symlink_to(f"{SNAPSHOTS_DIR}/{_SEED_SNAPSHOT_NAME}")
        case RemoteVolume():
            if remote_exec is not None:
                rp = vol.path
                if subdir:
                    rp = f"{rp}/{subdir}"
                remote_exec(f"mkdir -p {rp}")
                remote_exec(f"touch {rp}/.nbkp-src")
                if hard_link.enabled:
                    snap_rel = f"{SNAPSHOTS_DIR}/{_SEED_SNAPSHOT_NAME}"
                    remote_exec(f"mkdir -p {rp}/{snap_rel}")
                    remote_exec(
                        f"test -e {rp}/{LATEST_LINK}"
                        f" || ln -sfn {snap_rel} {rp}/{LATEST_LINK}"
                    )
                elif btrfs.enabled:
                    remote_exec(
                        f"test -e {rp}/{STAGING_DIR}"
                        " || btrfs subvolume create"
                        f" {rp}/{STAGING_DIR}"
                    )
                    remote_exec(f"mkdir -p {rp}/{SNAPSHOTS_DIR}")
                    # Seed snapshot + latest symlink
                    seed_rel = f"{SNAPSHOTS_DIR}/{_SEED_SNAPSHOT_NAME}"
                    remote_exec(
                        f"test -e {rp}/{seed_rel}"
                        " || btrfs subvolume snapshot -r"
                        f" {rp}/{STAGING_DIR} {rp}/{seed_rel}"
                    )
                    remote_exec(
                        f"test -e {rp}/{LATEST_LINK}"
                        f" || ln -sfn {seed_rel} {rp}/{LATEST_LINK}"
                    )


def _create_dest_sentinels(
    config: Config,
    sync: SyncConfig,
    remote_exec: Callable[[str], None] | None,
) -> None:
    vol = config.volumes[sync.destination.volume]
    subdir = sync.destination.subdir
    btrfs = sync.destination.btrfs_snapshots
    hard_link = sync.destination.hard_link_snapshots

    match vol:
        case LocalVolume():
            path = Path(vol.path)
            if subdir:
                path = path / subdir
            path.mkdir(parents=True, exist_ok=True)
            (path / ".nbkp-dst").touch()
            if hard_link.enabled:
                (path / SNAPSHOTS_DIR).mkdir(exist_ok=True)
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
        case RemoteVolume():
            if remote_exec is not None:
                rp = vol.path
                if subdir:
                    rp = f"{rp}/{subdir}"
                remote_exec(f"mkdir -p {rp}")
                remote_exec(f"touch {rp}/.nbkp-dst")
                if hard_link.enabled:
                    remote_exec(f"mkdir -p {rp}/{SNAPSHOTS_DIR}")
                elif btrfs.enabled:
                    remote_exec(
                        f"test -e {rp}/{STAGING_DIR}"
                        " || btrfs subvolume create"
                        f" {rp}/{STAGING_DIR}"
                    )
                    remote_exec(f"mkdir -p {rp}/{SNAPSHOTS_DIR}")


def _volume_key(
    vol: LocalVolume | RemoteVolume,
    subdir: str | None,
) -> str:
    """Return a dedup key for a volume + subdir combination."""
    match vol:
        case LocalVolume():
            base = Path(vol.path)
            return str(base / subdir if subdir else base)
        case RemoteVolume():
            rp = vol.path
            if subdir:
                rp = f"{rp}/{subdir}"
            return rp


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

    unique_sources = {
        _volume_key(config.volumes[s.source.volume], s.source.subdir): (
            config.volumes[s.source.volume],
            s.source.subdir,
        )
        for s in config.syncs.values()
    }
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
                remote_exec(
                    f"printf %s {shlex.quote(content)}" f" > {rp}/{name}"
                )
            if big_file_size_bytes:
                remote_exec(
                    f"truncate -s {big_file_size_bytes}"
                    f" {rp}/large-file.bin"
                )
