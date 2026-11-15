import codecs
import io
import locale
import os
import subprocess
from collections.abc import Callable
from enum import Enum

from ..config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    SyncEndpoint,
    resolve_endpoint_path,
)
from ..fsprotocol import LATEST_LINK, SNAPSHOTS_DIR
from ..remote import (
    build_ssh_base_args,
    build_ssh_e_option,
    format_remote_path,
    shell_join,
)
from ..remote.endpoints import ResolvedEndpoints


class ProgressMode(str, Enum):
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


def resolve_source_path(
    volume: LocalVolume | RemoteVolume,
    source: SyncEndpoint,
) -> str:
    """When the source endpoint has snapshots configured (btrfs or
    hard-link), rsync should read from the ``latest/`` directory
    rather than the volume root.  For hard-link snapshots,
    ``latest`` is a symlink — rsync's trailing slash causes it
    to follow the symlink and copy the target's contents.
    """
    base = resolve_endpoint_path(volume, source.subdir)
    if source.snapshot_mode != "none":
        return f"{base}/{LATEST_LINK}"
    else:
        return base


def _progress_args(progress: ProgressMode | None) -> list[str]:
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
    return [
        *[f"--filter={rule}" for rule in sync.filters],
        *([f"--filter=merge {sync.filter_file}"] if sync.filter_file else []),
    ]


def hard_link_dest_suffix(snapshot_name: str) -> str:
    """Hard-link syncs rsync straight into the new snapshot directory."""
    return f"{SNAPSHOTS_DIR}/{snapshot_name}"


def hard_link_link_dest(latest_name: str | None) -> str | None:
    """``--link-dest`` to the snapshot ``latest`` designates, or ``None`` before
    the first one.  rsync resolves a relative ``--link-dest`` against the
    destination directory, which is the new snapshot's sibling.
    """
    return f"../{latest_name}" if latest_name else None


def _dest_target(base: str, dest_suffix: str | None) -> str:
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
    """For a remote-to-remote sync the command is wrapped in SSH."""
    re = resolved_endpoints or {}
    src_ep = config.source_endpoint(sync)
    dst_ep = config.destination_endpoint(sync)
    src_vol = config.volumes[src_ep.volume]
    dst_vol = config.volumes[dst_ep.volume]
    src_path = resolve_source_path(src_vol, src_ep)
    dst_path = resolve_endpoint_path(dst_vol, dst_ep.subdir)
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
                shell_join(local_cmd),
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


# os.read returns as soon as any output is available, so progress updates still
# show up immediately; the size only caps how much one forward call handles.
# Reading one character per call cost a flushing echo per character: 10 MB of
# --progress output took ~40 s to forward, while rsync stalled on the full pipe.
_STREAM_CHUNK_SIZE = 64 * 1024


def _run_streaming(
    cmd: list[str], on_output: Callable[[str], None]
) -> subprocess.CompletedProcess[str]:
    """Run *cmd*, forwarding its merged stdout/stderr to *on_output*."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    decoder = _text_decoder()
    raw_chunks = iter(lambda: os.read(fd, _STREAM_CHUNK_SIZE), b"")
    output_chunks = [
        *(_forward(decoder.decode(raw), on_output) for raw in raw_chunks),
        _forward(decoder.decode(b"", final=True), on_output),
    ]
    proc.stdout.close()
    return subprocess.CompletedProcess(
        cmd, proc.wait(), stdout="".join(output_chunks), stderr=""
    )


def _text_decoder() -> io.IncrementalNewlineDecoder:
    """Decode bytes exactly as a ``text=True`` pipe would.

    Same encoding and strictness as ``io.TextIOWrapper``'s defaults, and the
    same universal-newline translation (``\\r\\n`` and ``\\r`` become ``\\n``),
    including a multi-byte character or a ``\\r\\n`` split across two reads.
    """
    encoding = locale.getpreferredencoding(False)
    return io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder(encoding)(), translate=True
    )


def _forward(chunk: str, on_output: Callable[[str], None]) -> str:
    if chunk:
        on_output(chunk)
    return chunk
