"""Rsync command building and execution."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from enum import Enum

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    SyncEndpoint,
)
from ..fsprotocol import LATEST_LINK
from ..remote import (
    build_ssh_base_args,
    build_ssh_e_option,
    format_remote_path,
)
from ..remote.endpoints import ResolvedEndpoints


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


def resolve_path(volume: LocalVolume | RemoteVolume, subdir: str | None) -> str:
    """Resolve the full path for a volume with optional subdir.

    The volume's ``path`` must be resolved by this point (declared in config,
    or filled from the discovered mountpoint after mounting).
    """
    if volume.path is None:
        msg = f"volume '{volume.slug}': mount path not resolved"
        raise ValueError(msg)
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
    else:
        return base


def _progress_args(progress: ProgressMode | None) -> list[str]:
    """Build rsync progress flags for a given mode."""
    match progress:
        case ProgressMode.OVERALL:
            return ["--info=progress2", "--stats", "--human-readable"]
        case ProgressMode.PER_FILE:
            return ["-v", "--progress", "--human-readable"]
        case ProgressMode.FULL:
            return [
                "-v",
                "--progress",
                "--info=progress2",
                "--stats",
                "--human-readable",
            ]
        case ProgressMode.NONE | None:
            return []


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
    return [
        "rsync",
        *options,
        *(["--checksum"] if rsync_opts.checksum else []),
        *(["--compress"] if rsync_opts.compress else []),
        *rsync_opts.extra_options,
        *_progress_args(progress),
        *(["--dry-run"] if dry_run else []),
        *([f"--link-dest={link_dest}"] if link_dest else []),
    ]


def _filter_args(sync: SyncConfig) -> list[str]:
    """Build rsync --filter arguments."""
    return [
        *[f"--filter={rule}" for rule in sync.filters],
        *([f"--filter=merge {sync.filter_file}"] if sync.filter_file else []),
    ]


def _dest_target(base: str, dest_suffix: str | None) -> str:
    """The rsync destination argument: *base* (plus suffix), trailing slash."""
    return f"{base}/{dest_suffix}/" if dest_suffix else f"{base}/"


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
    src_ep = config.source_endpoint(sync)
    dst_ep = config.destination_endpoint(sync)
    src_vol = config.volumes[src_ep.volume]
    dst_vol = config.volumes[dst_ep.volume]
    src_path = resolve_source_path(src_vol, src_ep)
    dst_path = resolve_path(dst_vol, dst_ep.subdir)
    rsync_args = [
        *_base_rsync_args(sync, dry_run, link_dest, progress),
        *_filter_args(sync),
    ]

    match (src_vol, dst_vol):
        case (RemoteVolume(), RemoteVolume() as dv):
            # Same server (enforced by config validation): SSH in once and
            # run rsync there with local paths.
            dst_re = re[dv.slug]
            local_cmd = [
                *rsync_args,
                f"{src_path}/",
                _dest_target(dst_path, dest_suffix),
            ]
            return [
                *build_ssh_base_args(dst_re.server, dst_re.proxy_chain),
                shlex.join(local_cmd),
            ]
        case (RemoteVolume() as sv, LocalVolume()):
            src_re = re[sv.slug]
            return [
                *rsync_args,
                *build_ssh_e_option(src_re.server, src_re.proxy_chain),
                format_remote_path(src_re.server, src_path) + "/",
                _dest_target(dst_path, dest_suffix),
            ]
        case (LocalVolume(), RemoteVolume() as dv):
            dst_re = re[dv.slug]
            return [
                *rsync_args,
                *build_ssh_e_option(dst_re.server, dst_re.proxy_chain),
                f"{src_path}/",
                _dest_target(format_remote_path(dst_re.server, dst_path), dest_suffix),
            ]
        case _:
            return [*rsync_args, f"{src_path}/", _dest_target(dst_path, dest_suffix)]


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
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    else:
        return _run_streaming(cmd, on_output)


def _run_streaming(
    cmd: list[str], on_output: Callable[[str], None]
) -> subprocess.CompletedProcess[str]:
    """Run *cmd*, forwarding its merged stdout/stderr to *on_output*."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    stdout = proc.stdout
    # Stream one character at a time so rsync progress updates that rely on
    # carriage returns are visible immediately; read(1) returns "" at EOF.
    output_chunks = [_forward(ch, on_output) for ch in iter(lambda: stdout.read(1), "")]
    return subprocess.CompletedProcess(
        cmd, proc.wait(), stdout="".join(output_chunks), stderr=""
    )


def _forward(chunk: str, on_output: Callable[[str], None]) -> str:
    """Pass *chunk* to *on_output* and return it, to also collect it."""
    on_output(chunk)
    return chunk
