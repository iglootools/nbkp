"""Rsync command building and execution."""

from __future__ import annotations

import shlex
import subprocess
from enum import Enum
from typing import Callable

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from ..remote import (
    build_ssh_base_args,
    build_ssh_e_option,
    format_remote_path,
)
from .btrfs import LATEST_LINK


class ProgressMode(str, Enum):
    """Rsync progress reporting mode."""

    NONE = "none"
    OVERALL = "overall"
    PER_FILE = "per-file"
    FULL = "full"


_DEFAULT_RSYNC_OPTIONS: list[str] = [
    "-a",
    "--delete",
    "--delete-excluded",
    "--partial-dir=.rsync-partial",
    "--safe-links",
    "--filter=H .nbkp-*",  # hide sentinels from transfer
    "--filter=P .nbkp-*",  # protect sentinels from deletion
]


def resolve_path(
    volume: LocalVolume | RemoteVolume, subdir: str | None
) -> str:
    """Resolve the full path for a volume with optional subdir."""
    if subdir:
        return f"{volume.path}/{subdir}"
    else:
        return volume.path


def resolve_source_path(
    volume: LocalVolume | RemoteVolume,
    source: SyncEndpoint,
) -> str:
    """Resolve source path, appending /latest for snapshots.

    When the source endpoint has snapshots configured (btrfs or
    hard-link), rsync should read from the ``latest/`` directory
    rather than the volume root.  For hard-link snapshots,
    ``latest`` is a symlink — rsync's trailing slash causes it
    to follow the symlink and copy the target's contents.
    """
    base = resolve_path(volume, source.subdir)
    if source.snapshot_mode != "none":
        return f"{base}/{LATEST_LINK}"
    return base


def _base_rsync_args(
    sync: SyncConfig,
    dry_run: bool,
    link_dest: str | None,
    progress: ProgressMode | None = None,
) -> list[str]:
    """Build common rsync flags."""
    rsync_opts = sync.rsync_options
    options = (
        rsync_opts.default_options_override
        if rsync_opts.default_options_override is not None
        else _DEFAULT_RSYNC_OPTIONS
    )
    args = ["rsync"] + list(options)
    if rsync_opts.checksum:
        args.append("--checksum")
    if rsync_opts.compress:
        args.append("--compress")
    args.extend(rsync_opts.extra_options)
    match progress:
        case ProgressMode.OVERALL:
            args.extend(
                [
                    "--info=progress2",
                    "--stats",
                    "--human-readable",
                ]
            )
        case ProgressMode.PER_FILE:
            args.extend(
                [
                    "-v",
                    "--progress",
                    "--human-readable",
                ]
            )
        case ProgressMode.FULL:
            args.extend(
                [
                    "-v",
                    "--progress",
                    "--info=progress2",
                    "--stats",
                    "--human-readable",
                ]
            )
        case ProgressMode.NONE | None:
            pass
    if dry_run:
        args.append("--dry-run")
    if link_dest:
        args.append(f"--link-dest={link_dest}")
    return args


def _filter_args(sync: SyncConfig) -> list[str]:
    """Build rsync --filter arguments."""
    args: list[str] = []
    for rule in sync.filters:
        args.append(f"--filter={rule}")
    if sync.filter_file:
        args.append(f"--filter=merge {sync.filter_file}")
    return args


def build_rsync_command(
    sync: SyncConfig,
    config: Config,
    dry_run: bool = False,
    link_dest: str | None = None,
    progress: ProgressMode | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dest_suffix: str | None = None,
) -> list[str]:
    """Build the rsync command for a sync operation.

    Returns the full command as a list of args, potentially
    wrapped in SSH for remote-to-remote syncs.
    """
    re = resolved_endpoints or {}
    src_vol = config.volumes[sync.source.volume]
    dst_vol = config.volumes[sync.destination.volume]

    src_path = resolve_source_path(src_vol, sync.source)
    dst_path = resolve_path(dst_vol, sync.destination.subdir)

    match (src_vol, dst_vol):
        case (RemoteVolume() as sv, RemoteVolume() as dv):
            dst_ep = re[dv.slug]
            return _build_remote_same_server(
                sync,
                dst_ep.server,
                src_path,
                dst_path,
                dry_run,
                link_dest,
                progress,
                proxy_chain=dst_ep.proxy_chain,
                dest_suffix=dest_suffix,
            )
        case (RemoteVolume() as sv, LocalVolume()):
            src_ep = re[sv.slug]
            rsync_args = _base_rsync_args(sync, dry_run, link_dest, progress)
            rsync_args.extend(_filter_args(sync))
            rsync_args.extend(
                build_ssh_e_option(
                    src_ep.server,
                    src_ep.proxy_chain,
                )
            )
            rsync_args.append(
                format_remote_path(src_ep.server, src_path) + "/"
            )
            dst_target = (
                f"{dst_path}/{dest_suffix}/" if dest_suffix else f"{dst_path}/"
            )
            rsync_args.append(dst_target)
            return rsync_args
        case (LocalVolume(), RemoteVolume() as dv):
            dst_ep = re[dv.slug]
            rsync_args = _base_rsync_args(sync, dry_run, link_dest, progress)
            rsync_args.extend(_filter_args(sync))
            rsync_args.extend(
                build_ssh_e_option(
                    dst_ep.server,
                    dst_ep.proxy_chain,
                )
            )
            rsync_args.append(f"{src_path}/")
            dst_remote = format_remote_path(dst_ep.server, dst_path)
            dst_target = (
                f"{dst_remote}/{dest_suffix}/"
                if dest_suffix
                else f"{dst_remote}/"
            )
            rsync_args.append(dst_target)
            return rsync_args
        case _:
            rsync_args = _base_rsync_args(sync, dry_run, link_dest, progress)
            rsync_args.extend(_filter_args(sync))
            rsync_args.append(f"{src_path}/")
            dst_target = (
                f"{dst_path}/{dest_suffix}/" if dest_suffix else f"{dst_path}/"
            )
            rsync_args.append(dst_target)
            return rsync_args


def _build_remote_same_server(
    sync: SyncConfig,
    server: SshEndpoint,
    src_path: str,
    dst_path: str,
    dry_run: bool,
    link_dest: str | None,
    progress: ProgressMode | None = None,
    proxy_chain: list[SshEndpoint] | None = None,
    dest_suffix: str | None = None,
) -> list[str]:
    """Build rsync command when both volumes are on the same server.

    SSH into the server once and run rsync with local paths.
    """
    rsync_args = _base_rsync_args(sync, dry_run, link_dest, progress)
    rsync_args.extend(_filter_args(sync))
    rsync_args.append(f"{src_path}/")
    dst_target = (
        f"{dst_path}/{dest_suffix}/" if dest_suffix else f"{dst_path}/"
    )
    rsync_args.append(dst_target)

    inner_command = shlex.join(rsync_args)
    return build_ssh_base_args(server, proxy_chain) + [inner_command]


def run_rsync(
    sync: SyncConfig,
    config: Config,
    dry_run: bool = False,
    link_dest: str | None = None,
    progress: ProgressMode | None = None,
    on_output: Callable[[str], None] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dest_suffix: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Build and execute the rsync command for a sync."""
    cmd = build_rsync_command(
        sync,
        config,
        dry_run,
        link_dest,
        progress,
        resolved_endpoints,
        dest_suffix=dest_suffix,
    )
    if on_output is None:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        assert proc.stdout is not None
        output_chunks: list[str] = []

        # Stream one character at a time so rsync progress
        # updates that rely on carriage returns are visible
        # immediately.
        while True:
            ch = proc.stdout.read(1)
            if ch:
                output_chunks.append(ch)
                on_output(ch)
            elif proc.poll() is not None:
                break

        return subprocess.CompletedProcess(
            cmd,
            proc.wait(),
            stdout="".join(output_chunks),
            stderr="",
        )
