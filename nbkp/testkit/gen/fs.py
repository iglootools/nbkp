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
    SyncEndpoint,
)
from ...sync.btrfs import LATEST_LINK, SNAPSHOTS_DIR, STAGING_DIR
from ...sync.rsync import resolve_path
from ...sync.symlink import DEVNULL_TARGET

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

    # Sync endpoint sentinels — iterate unique endpoints
    seen_src: set[str] = set()
    seen_dst: set[str] = set()
    for sync in config.syncs.values():
        if sync.source not in seen_src:
            seen_src.add(sync.source)
            src_ep = config.source_endpoint(sync)
            src_vol = config.volumes[src_ep.volume]
            _create_endpoint_sentinels(src_vol, src_ep, ".nbkp-src", remote_exec)
        if sync.destination not in seen_dst:
            seen_dst.add(sync.destination)
            dst_ep = config.destination_endpoint(sync)
            dst_vol = config.volumes[dst_ep.volume]
            _create_endpoint_sentinels(dst_vol, dst_ep, ".nbkp-dst", remote_exec)


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
