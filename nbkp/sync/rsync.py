"""
Building and running the rsync command of a sync, local or over SSH.

Also streams rsync's output to a callback as it runs, for live progress.
"""

import codecs
import io
import locale
import os
import subprocess
from collections.abc import Callable, Iterator
from enum import StrEnum

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    SyncEndpoint,
    resolve_endpoint_path,
)
from nbkp.fsprotocol import LATEST_LINK, SNAPSHOTS_DIR
from nbkp.remote import (
    build_ssh_base_args,
    build_ssh_e_option,
    format_remote_path,
    shell_join,
)
from nbkp.remote.endpoints import ResolvedEndpoints


class ProgressMode(StrEnum):
    """
    How much transfer progress rsync reports.

    Overall progress and stats, per-file progress, both, or none.
    """

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
    """
    Return the path rsync reads the source from.

    When the source endpoint has snapshots configured (btrfs or hard-link), rsync should
    read from the ``latest/`` directory rather than the volume root. For hard-link
    snapshots, ``latest`` is a symlink: rsync's trailing slash causes it to follow the
    symlink and copy the target's contents.
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
    *,
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
    """
    Return ``--link-dest`` to the snapshot ``latest`` designates.

    ``None`` before the first one. rsync resolves a relative ``--link-dest`` against the
    destination directory, which is the new snapshot's sibling.
    """
    return f"../{latest_name}" if latest_name else None


# Before rsync 3.2.4 (preflight accepts 3.0.0 and later) the remote shell
# parses the remote rsync's arguments, so a path with a space or a shell
# metacharacter is split or expanded there. --protect-args (3.0.0 and later)
# sends them through rsync's protocol instead. Not needed remote to remote:
# rsync then runs on the remote host with local paths, quoted for its shell.
_PROTECT_ARGS = "--protect-args"


def _protected_remote_path(path: str) -> str:
    """
    Return a remote *path* with a leading ``~`` made relative to the home directory.

    With ``--protect-args`` no shell expands the ``~`` a remote volume path may start
    with, but the remote rsync starts in the SSH user's home directory, which a relative
    path resolves against.
    """
    match path.partition("/"):
        case ("~", _, rest):
            return rest.lstrip("/") or "."
        case _:
            return path


def _dest_target(base: str, dest_suffix: str | None) -> str:
    return f"{base}/{dest_suffix}/" if dest_suffix else f"{base}/"


def build_rsync_command(
    sync: SyncConfig,
    config: Config,
    *,
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
        *_base_rsync_args(
            sync,
            dry_run=dry_run,
            link_dest=link_dest,
            progress=progress,
        ),
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
            remote_src = _protected_remote_path(src_path)
            return [
                *rsync_args,
                _PROTECT_ARGS,
                *build_ssh_e_option(src_re.server, src_re.proxy_chain),
                format_remote_path(src_re.server, remote_src) + "/",
                _dest_target(dst_path, dest_suffix),
            ]
        case (LocalVolume(), RemoteVolume() as dv):
            dst_re = re[dv.slug]
            remote_dst = _protected_remote_path(dst_path)
            dst_target = format_remote_path(dst_re.server, remote_dst)
            return [
                *rsync_args,
                _PROTECT_ARGS,
                *build_ssh_e_option(dst_re.server, dst_re.proxy_chain),
                f"{src_path}/",
                _dest_target(dst_target, dest_suffix),
            ]
        case _:
            return [*rsync_args, f"{src_path}/", _dest_target(dst_path, dest_suffix)]


type ProcessRunner = Callable[
    [list[str], Callable[[str], None] | None],
    subprocess.CompletedProcess[str],
]
"""Runs a command on the orchestrator, forwarding its output to the callback
when one is given."""


def run_local_process(
    cmd: list[str],
    on_output: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run *cmd*, forwarding its merged stdout/stderr to *on_output* when given.

    Captured or forwarded, the output is read the same way (see :func:`run_streaming`),
    so the result keeps only its tail either way: without a callback (JSON output, or
    no progress) rsync's output is just as long when ``extra-options`` or the progress
    mode make it verbose.
    """
    return run_streaming(cmd, on_output or _discard)


def _discard(_chunk: str) -> None:
    pass


# os.read returns as soon as any output is available, so progress updates still
# show up immediately; the size only caps how much one forward call handles.
# Reading one character per call cost a flushing echo per character: 10 MB of
# --progress output took ~40 s to forward, while rsync stalled on the full pipe.
_STREAM_CHUNK_SIZE = 64 * 1024


# Only the end of the output is kept for the result: with --progress over
# millions of files the whole of it runs to hundreds of MB per sync, all held
# until the run ends. rsync's error, when it fails, comes
# last, so the tail is the part a failure report needs.
_STREAM_TAIL_SIZE = 64 * 1024


def run_streaming(
    cmd: list[str],
    on_output: Callable[[str], None],
    *,
    chunk_size: int = _STREAM_CHUNK_SIZE,
    tail_size: int = _STREAM_TAIL_SIZE,
) -> subprocess.CompletedProcess[str]:
    """
    Run *cmd*, forwarding its merged stdout/stderr to *on_output*.

    Reads at most *chunk_size* bytes at a time. The result's ``stdout`` holds only the
    last *tail_size* characters of it.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.stdout is not None  # noqa: S101 -- set by stdout=PIPE; narrows the type
    fd = proc.stdout.fileno()
    raw_chunks = iter(lambda: os.read(fd, chunk_size), b"")
    tail = _OutputTail(tail_size)
    try:
        for chunk in _decoded(raw_chunks):
            _forward(chunk, on_output)
            tail.append(chunk)
    except BaseException:
        # Forwarding failed (a closed pipe on our stdout, Ctrl-C): stop rsync
        # before the caller reacts, or it would go on writing into a snapshot
        # directory the caller is about to remove, and keep the drive busy.
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
    return subprocess.CompletedProcess(cmd, proc.wait(), stdout=tail.text(), stderr="")


def _decoded(raw_chunks: Iterator[bytes]) -> Iterator[str]:
    decoder = _text_decoder()
    yield from (decoder.decode(raw) for raw in raw_chunks)
    yield decoder.decode(b"", final=True)


class _OutputTail:
    """
    The last *size* characters appended.

    Mutable so that appending stays cheap: rebuilding a bounded string on
    every chunk would copy up to *size* characters per (often tiny) progress
    update.  Chunks are joined only once they hold twice *size*, which keeps
    the cost linear in the output and the memory under ``2 * size``.
    """

    def __init__(self, size: int) -> None:
        self._size = size
        self._chunks: list[str] = []
        self._length = 0

    def append(self, chunk: str) -> None:
        self._chunks.append(chunk)
        self._length += len(chunk)
        if self._length > 2 * self._size:
            self._chunks = [self.text()]
            self._length = len(self._chunks[0])

    def text(self) -> str:
        return "".join(self._chunks)[-self._size :]


def _text_decoder() -> io.IncrementalNewlineDecoder:
    r"""
    Decode bytes as a ``text=True, errors="replace"`` pipe would.

    Same encoding as ``io.TextIOWrapper``'s default, and the same
    universal-newline translation (``\r\n`` and ``\r`` become ``\n``),
    including a multi-byte character or a ``\r\n`` split across two reads.
    Undecodable bytes become U+FFFD: rsync prints file names as they are on
    disk, which need not be in the locale's encoding, and the output is only
    displayed and kept for the result, so a strict decoder would abort the
    whole run over a name it only had to show.
    """
    encoding = locale.getpreferredencoding(do_setlocale=False)
    return io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder(encoding)(errors="replace"),
        translate=True,
    )


def _forward(chunk: str, on_output: Callable[[str], None]) -> None:
    if chunk:
        on_output(chunk)


def run_rsync(
    sync: SyncConfig,
    config: Config,
    *,
    dry_run: bool = False,
    link_dest: str | None = None,
    progress: ProgressMode | None = None,
    on_output: Callable[[str], None] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dest_suffix: str | None = None,
    run_process: ProcessRunner = run_local_process,
) -> subprocess.CompletedProcess[str]:
    """
    Run rsync for *sync*.

    *run_process* runs the built command: :func:`run_local_process` by default, a fake
    in tests.
    """
    cmd = build_rsync_command(
        sync,
        config,
        dry_run=dry_run,
        link_dest=link_dest,
        progress=progress,
        resolved_endpoints=resolved_endpoints,
        dest_suffix=dest_suffix,
    )
    return run_process(cmd, on_output)
