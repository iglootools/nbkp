"""Generate a standalone bash script from nbkp config.

Compiles a Config into a self-contained shell script that performs
the same sync operations as ``nbkp run``, with all paths and
options baked in.  The generated script accepts ``--dry-run``
and ``--progress`` flags at runtime.
"""

from __future__ import annotations

import importlib.resources
import os
import shlex
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from textwrap import dedent

from jinja2 import Environment, Template

from .config import (
    Config,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
    SyncConfig,
)
from .remote.ssh import build_ssh_base_args
from .sync.btrfs import STAGING_DIR
from .sync.rsync import build_rsync_command

# ── Public API ────────────────────────────────────────────────


@dataclass(frozen=True)
class ScriptOptions:
    """Options for script generation."""

    config_path: str | None = None
    output_file: str | None = None
    relative_src: bool = False
    relative_dst: bool = False


def generate_script(
    config: Config,
    options: ScriptOptions,
    *,
    now: datetime | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str:
    """Generate a standalone bash script from config."""
    re = resolved_endpoints or {}
    if now is None:
        now = datetime.now(timezone.utc)
    vol_paths = _build_vol_paths(config, options)
    ctx = _build_script_context(config, options, vol_paths, now, re)
    template = _load_template()
    return template.render(ctx) + "\n"


# ── Context dataclasses ──────────────────────────────────────


@dataclass(frozen=True)
class _SyncContext:
    slug: str
    fn_name: str
    enabled: bool
    has_btrfs: bool = False
    has_hard_link: bool = False
    has_prune: bool = False
    max_snapshots: int | None = None
    preflight: str = ""
    link_dest: str = ""
    rsync: str = ""
    snapshot: str = ""
    prune: str = ""
    orphan_cleanup: str = ""
    hl_mkdir: str = ""
    symlink: str = ""
    hl_prune: str = ""
    predecessors: tuple[str, ...] = ()
    disabled_body: str = ""


# ── Template loading ─────────────────────────────────────────


def _load_template() -> Template:
    """Load the Jinja2 template with custom delimiters."""
    tpl_text = (
        importlib.resources.files("nbkp.templates")
        .joinpath("backup.sh.j2")
        .read_text(encoding="utf-8")
    )
    env = Environment(
        variable_start_string="${{",
        variable_end_string="}}",
        block_start_string="<%",
        block_end_string="%>",
        comment_start_string="<#",
        comment_end_string="#>",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    return env.from_string(tpl_text)


# ── Path helpers ─────────────────────────────────────────────


def _build_vol_paths(
    config: Config,
    options: ScriptOptions,
) -> dict[str, str]:
    """Compute volume slug -> effective path."""
    src_slugs = {s.source.volume for s in config.syncs.values()}
    dst_slugs = {s.destination.volume for s in config.syncs.values()}

    vol_paths: dict[str, str] = {}
    for slug, vol in config.volumes.items():
        match vol:
            case RemoteVolume():
                vol_paths[slug] = vol.path
            case LocalVolume():
                should_relativize = (
                    slug in src_slugs and options.relative_src
                ) or (slug in dst_slugs and options.relative_dst)
                if should_relativize and options.output_file:
                    output_dir = os.path.dirname(options.output_file)
                    rel = os.path.relpath(vol.path, output_dir)
                    vol_paths[slug] = f"${{NBKP_SCRIPT_DIR}}/{rel}"
                else:
                    vol_paths[slug] = vol.path
    return vol_paths


def _vol_path(
    vol_paths: dict[str, str],
    slug: str,
    subdir: str | None = None,
) -> str:
    base = vol_paths[slug]
    if subdir:
        return f"{base}/{subdir}"
    return base


def _substitute_vol_path(
    arg: str,
    vol: LocalVolume | RemoteVolume,
    vol_paths: dict[str, str],
    slug: str,
) -> str:
    """Replace absolute volume path prefix with vol_paths."""
    match vol:
        case RemoteVolume():
            return arg
        case LocalVolume():
            return arg.replace(vol.path, vol_paths[slug], 1)


# ── Shell formatting helpers ─────────────────────────────────


def _sq(s: str) -> str:
    """Shell-quote (single quotes, no variable expansion)."""
    return shlex.quote(s)


def _qp(s: str) -> str:
    """Quote a path; double-quote if it contains $."""
    if "$" not in s:
        return _sq(s)
    return f'"{s}"'


def _slug_to_fn(slug: str) -> str:
    return f"sync_{slug.replace('-', '_')}"


def _format_shell_command(
    cmd: list[str],
    cont_indent: str = "        ",
) -> str:
    """Format command list with backslash continuations."""
    parts = [_qp(arg) for arg in cmd]
    if len(parts) <= 3:
        return " ".join(parts)
    sep = f" \\\n{cont_indent}"
    return parts[0] + sep + sep.join(parts[1:])


# ── SSH command helpers ──────────────────────────────────────


def _format_remote_test(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint],
    test_args: list[str],
) -> str:
    ssh_args = build_ssh_base_args(server, proxy_chain)
    remote_cmd = "test " + " ".join(shlex.quote(a) for a in test_args)
    return " ".join(_sq(a) for a in ssh_args) + " " + _sq(remote_cmd)


def _format_remote_check(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint],
    cmd: list[str],
) -> str:
    ssh_args = build_ssh_base_args(server, proxy_chain)
    remote_cmd = " ".join(shlex.quote(a) for a in cmd)
    return (
        " ".join(_sq(a) for a in ssh_args)
        + " "
        + _sq(remote_cmd)
        + " >/dev/null 2>&1"
    )


def _format_remote_command_str(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint],
    cmd: list[str],
) -> str:
    ssh_args = build_ssh_base_args(server, proxy_chain)
    remote_cmd = " ".join(shlex.quote(a) for a in cmd)
    return " ".join(_sq(a) for a in ssh_args) + " " + _sq(remote_cmd)


# ── Local/remote dispatch helpers ────────────────────────────


def _test_cmd(
    vol: LocalVolume | RemoteVolume,
    test_args: list[str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell expression for `test ... `."""
    match vol:
        case LocalVolume():
            return "test " + " ".join(_qp(a) for a in test_args)
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            return _format_remote_test(ep.server, ep.proxy_chain, test_args)


def _which_cmd(
    vol: LocalVolume | RemoteVolume,
    command: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell expression to check command availability."""
    match vol:
        case LocalVolume():
            return f"command -v {_sq(command)} >/dev/null 2>&1"
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            return _format_remote_check(
                ep.server, ep.proxy_chain, ["which", command]
            )


def _ls_snapshots_cmd(
    dst_vol: LocalVolume | RemoteVolume,
    snaps_dir: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell expression to list snapshot dirs."""
    match dst_vol:
        case LocalVolume():
            return f"ls {_qp(snaps_dir)}"
        case RemoteVolume():
            ep = resolved_endpoints[dst_vol.slug]
            return _format_remote_command_str(
                ep.server, ep.proxy_chain, ["ls", snaps_dir]
            )


def _snapshot_cmd(
    dst_vol: LocalVolume | RemoteVolume,
    latest: str,
    snaps_dir: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell command to create a btrfs snapshot."""
    snap_args = [
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        latest,
        f"{snaps_dir}/$NBKP_TS",
    ]
    match dst_vol:
        case LocalVolume():
            return _format_shell_command(snap_args, cont_indent="        ")
        case RemoteVolume():
            ep = resolved_endpoints[dst_vol.slug]
            remote_args = [
                "btrfs",
                "subvolume",
                "snapshot",
                "-r",
                latest,
                f"{snaps_dir}/\\$NBKP_TS",
            ]
            return _format_remote_command_str(
                ep.server, ep.proxy_chain, remote_args
            )


def _btrfs_prop_cmd(
    dst_vol: LocalVolume | RemoteVolume,
    snaps_dir: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell command to set ro=false on $snap."""
    match dst_vol:
        case LocalVolume():
            return (
                f"btrfs property set" f' {_qp(snaps_dir)}/"$snap"' f" ro false"
            )
        case RemoteVolume():
            ep = resolved_endpoints[dst_vol.slug]
            return _format_remote_command_str(
                ep.server,
                ep.proxy_chain,
                [
                    "btrfs",
                    "property",
                    "set",
                    f"{snaps_dir}/\\$snap",
                    "ro",
                    "false",
                ],
            )


def _btrfs_del_cmd(
    dst_vol: LocalVolume | RemoteVolume,
    snaps_dir: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell command to delete $snap."""
    match dst_vol:
        case LocalVolume():
            return f"btrfs subvolume delete" f' {_qp(snaps_dir)}/"$snap"'
        case RemoteVolume():
            ep = resolved_endpoints[dst_vol.slug]
            return _format_remote_command_str(
                ep.server,
                ep.proxy_chain,
                [
                    "btrfs",
                    "subvolume",
                    "delete",
                    f"{snaps_dir}/\\$snap",
                ],
            )


# ── Hard-link command helpers ────────────────────────────────


def _readlink_cmd(
    dst_vol: LocalVolume | RemoteVolume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell expression that outputs the symlink target."""
    match dst_vol:
        case LocalVolume():
            return f"readlink {_qp(path)}"
        case RemoteVolume():
            ep = resolved_endpoints[dst_vol.slug]
            return _format_remote_command_str(
                ep.server, ep.proxy_chain, ["readlink", path]
            )


def _rm_rf_snap_cmd(
    dst_vol: LocalVolume | RemoteVolume,
    snaps_dir: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell command to rm -rf {snaps_dir}/$snap (loop variable)."""
    match dst_vol:
        case LocalVolume():
            return f'rm -rf {_qp(snaps_dir)}/"$snap"'
        case RemoteVolume():
            ep = resolved_endpoints[dst_vol.slug]
            ssh_pfx = " ".join(
                _sq(a) for a in build_ssh_base_args(ep.server, ep.proxy_chain)
            )
            return f'{ssh_pfx} "rm -rf {snaps_dir}/$snap"'


def _mkdir_snap_cmd(
    dst_vol: LocalVolume | RemoteVolume,
    snaps_dir: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell command to mkdir -p {snaps_dir}/$NBKP_TS."""
    match dst_vol:
        case LocalVolume():
            return f'mkdir -p {_qp(snaps_dir)}/"$NBKP_TS"'
        case RemoteVolume():
            ep = resolved_endpoints[dst_vol.slug]
            ssh_pfx = " ".join(
                _sq(a) for a in build_ssh_base_args(ep.server, ep.proxy_chain)
            )
            return f'{ssh_pfx} "mkdir -p {snaps_dir}/$NBKP_TS"'


def _ln_sfn_cmd(
    dst_vol: LocalVolume | RemoteVolume,
    dest_path: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell command for ln -sfn snapshots/$NBKP_TS {dest}/latest."""
    match dst_vol:
        case LocalVolume():
            return f'ln -sfn "snapshots/$NBKP_TS"' f" {_qp(dest_path)}/latest"
        case RemoteVolume():
            ep = resolved_endpoints[dst_vol.slug]
            ssh_pfx = " ".join(
                _sq(a) for a in build_ssh_base_args(ep.server, ep.proxy_chain)
            )
            return (
                f"{ssh_pfx}"
                f' "ln -sfn snapshots/$NBKP_TS'
                f' {dest_path}/latest"'
            )


# ── Block builders (textwrap.dedent) ─────────────────────────


def _build_check_line(
    vol: LocalVolume | RemoteVolume,
    test_args: list[str],
    error_msg: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    cmd = _test_cmd(vol, test_args, resolved_endpoints)
    return f"{cmd}" f' || {{ nbkp_log "ERROR: {error_msg}"; return 1; }}'


def _build_which_line(
    vol: LocalVolume | RemoteVolume,
    command: str,
    error_msg: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    check = _which_cmd(vol, command, resolved_endpoints)
    return f"{check}" f' || {{ nbkp_log "ERROR: {error_msg}"; return 1; }}'


def _build_preflight_block(
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Build preflight check lines at indent 0."""
    src_vol = config.volumes[sync.source.volume]
    dst_vol = config.volumes[sync.destination.volume]
    src_path = _vol_path(vol_paths, sync.source.volume, sync.source.subdir)
    dst_path = _vol_path(
        vol_paths,
        sync.destination.volume,
        sync.destination.subdir,
    )

    lines: list[str] = []

    # Source endpoint sentinel
    src_sentinel = f"{src_path}/.nbkp-src"
    lines.append(
        _build_check_line(
            src_vol,
            ["-f", src_sentinel],
            f"source sentinel {src_sentinel} not found",
            resolved_endpoints,
        )
    )

    # Source snapshot: verify latest/ and snapshots/ exist
    if sync.source.snapshot_mode != "none":
        src_latest = f"{src_path}/latest"
        lines.append(
            _build_check_line(
                src_vol,
                ["-d", src_latest],
                ("source latest/ not found" f" ({src_latest})"),
                resolved_endpoints,
            )
        )
        src_snapshots = f"{src_path}/snapshots"
        lines.append(
            _build_check_line(
                src_vol,
                ["-d", src_snapshots],
                ("source snapshots/ not found" f" ({src_snapshots})"),
                resolved_endpoints,
            )
        )

    # Destination endpoint sentinel
    dst_sentinel = f"{dst_path}/.nbkp-dst"
    lines.append(
        _build_check_line(
            dst_vol,
            ["-f", dst_sentinel],
            f"destination sentinel {dst_sentinel} not found",
            resolved_endpoints,
        )
    )

    # rsync on source
    lines.append(
        _build_which_line(
            src_vol,
            "rsync",
            "rsync not found on source",
            resolved_endpoints,
        )
    )

    # rsync on destination
    lines.append(
        _build_which_line(
            dst_vol,
            "rsync",
            "rsync not found on destination",
            resolved_endpoints,
        )
    )

    # Btrfs checks
    if sync.destination.btrfs_snapshots.enabled:
        lines.append(
            _build_which_line(
                dst_vol,
                "btrfs",
                "btrfs not found on destination",
                resolved_endpoints,
            )
        )
        tmp_dir = f"{dst_path}/{STAGING_DIR}"
        lines.append(
            _build_check_line(
                dst_vol,
                ["-d", tmp_dir],
                f"destination {STAGING_DIR}/ directory not found"
                f" ({tmp_dir})",
                resolved_endpoints,
            )
        )
        snaps_dir = f"{dst_path}/snapshots"
        lines.append(
            _build_check_line(
                dst_vol,
                ["-d", snaps_dir],
                "destination snapshots/ directory not found" f" ({snaps_dir})",
                resolved_endpoints,
            )
        )

    # Hard-link checks
    if sync.destination.hard_link_snapshots.enabled:
        snaps_dir = f"{dst_path}/snapshots"
        lines.append(
            _build_check_line(
                dst_vol,
                ["-d", snaps_dir],
                "destination snapshots/ directory not found" f" ({snaps_dir})",
                resolved_endpoints,
            )
        )

    return "\n".join(lines)


def _build_link_dest_block(
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
    *,
    link_dest_prefix: str = "../",
) -> str:
    """Build link-dest resolution block at indent 0.

    ``link_dest_prefix`` is the relative path from the rsync
    destination to the snapshots directory (``../`` for hard-link
    where rsync writes to ``snapshots/{ts}/``).
    """
    dst_vol = config.volumes[sync.destination.volume]
    dest_path = _vol_path(
        vol_paths,
        sync.destination.volume,
        sync.destination.subdir,
    )
    snaps_dir = f"{dest_path}/snapshots"
    ls_cmd = _ls_snapshots_cmd(dst_vol, snaps_dir, resolved_endpoints)

    return dedent(f"""\
        NBKP_LATEST_SNAP=$({ls_cmd} 2>/dev/null | sort | tail -1)
        RSYNC_LINK_DEST=""
        if [ -n "$NBKP_LATEST_SNAP" ]; then
            RSYNC_LINK_DEST="--link-dest={link_dest_prefix}$NBKP_LATEST_SNAP"
        fi""")


def _build_rsync_block(
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
    *,
    dest_suffix: str | None = None,
    has_link_dest: bool = False,
) -> str:
    """Build rsync command block at indent 0."""
    i2 = "    "  # continuation indent within this block
    cmd = build_rsync_command(
        sync,
        config,
        dry_run=False,
        link_dest=None,
        progress=None,
        resolved_endpoints=resolved_endpoints,
        dest_suffix=dest_suffix,
    )

    # Substitute local volume paths
    src_vol = config.volumes[sync.source.volume]
    dst_vol = config.volumes[sync.destination.volume]
    match (src_vol, dst_vol):
        case (RemoteVolume(), RemoteVolume()):
            pass
        case _:
            cmd[-2] = _substitute_vol_path(
                cmd[-2],
                src_vol,
                vol_paths,
                sync.source.volume,
            )
            cmd[-1] = _substitute_vol_path(
                cmd[-1],
                dst_vol,
                vol_paths,
                sync.destination.volume,
            )

    formatted = _format_shell_command(cmd, cont_indent=i2)

    runtime_vars = [
        '${RSYNC_DRY_RUN_FLAG:+"$RSYNC_DRY_RUN_FLAG"}',
        "$RSYNC_PROGRESS_FLAGS",
    ]
    if has_link_dest:
        runtime_vars.insert(0, '${RSYNC_LINK_DEST:+"$RSYNC_LINK_DEST"}')
    runtime_suffix = f" \\\n{i2}".join(runtime_vars)
    return f"{formatted} \\\n{i2}{runtime_suffix}"


def _build_snapshot_block(
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Build btrfs snapshot block at indent 0."""
    dst_vol = config.volumes[sync.destination.volume]
    dest_path = _vol_path(
        vol_paths,
        sync.destination.volume,
        sync.destination.subdir,
    )
    tmp = f"{dest_path}/{STAGING_DIR}"
    snaps_dir = f"{dest_path}/snapshots"
    snap = _snapshot_cmd(dst_vol, tmp, snaps_dir, resolved_endpoints)

    return dedent(f"""\
        if [ "$NBKP_DRY_RUN" = false ]; then
            NBKP_TS=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
            {snap}
        fi""")


def _build_prune_block(
    sync: SyncConfig,
    config: Config,
    max_snapshots: int,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Build btrfs prune block (skip latest symlink target)."""
    dst_vol = config.volumes[sync.destination.volume]
    dest_path = _vol_path(
        vol_paths,
        sync.destination.volume,
        sync.destination.subdir,
    )
    latest_path = f"{dest_path}/latest"
    snaps_dir = f"{dest_path}/snapshots"
    ls_cmd = _ls_snapshots_cmd(dst_vol, snaps_dir, resolved_endpoints)
    rl_cmd = _readlink_cmd(dst_vol, latest_path, resolved_endpoints)
    prop_cmd = _btrfs_prop_cmd(dst_vol, snaps_dir, resolved_endpoints)
    del_cmd = _btrfs_del_cmd(dst_vol, snaps_dir, resolved_endpoints)

    # fmt: off
    pipe_while = (
        'echo "$NBKP_SNAPS"'
        ' | head -n "$NBKP_EXCESS"'
        " | while IFS= read -r snap; do"
    )
    # fmt: on
    return dedent(f"""\
        if [ "$NBKP_DRY_RUN" = false ]; then
            NBKP_SNAPS=$({ls_cmd} | sort)
            NBKP_COUNT=$(echo "$NBKP_SNAPS" | wc -l | tr -d ' ')
            NBKP_EXCESS=$((NBKP_COUNT - {max_snapshots}))
            NBKP_LATEST_LINK=$({rl_cmd} 2>/dev/null || true)
            NBKP_LATEST_NAME="${{NBKP_LATEST_LINK##*/}}"
            if [ "$NBKP_EXCESS" -gt 0 ]; then
                {pipe_while}
                    if [ "$snap" != "$NBKP_LATEST_NAME" ]; then
                        nbkp_log "Pruning snapshot: $snap"
                        {prop_cmd}
                        {del_cmd}
                    fi
                done
            fi
        fi""")


def _build_hard_link_orphan_cleanup_block(
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Build orphan cleanup block for hard-link snapshots."""
    dst_vol = config.volumes[sync.destination.volume]
    dest_path = _vol_path(
        vol_paths,
        sync.destination.volume,
        sync.destination.subdir,
    )
    latest_path = f"{dest_path}/latest"
    snaps_dir = f"{dest_path}/snapshots"
    rl_cmd = _readlink_cmd(dst_vol, latest_path, resolved_endpoints)
    ls_cmd = _ls_snapshots_cmd(dst_vol, snaps_dir, resolved_endpoints)
    rm_cmd = _rm_rf_snap_cmd(dst_vol, snaps_dir, resolved_endpoints)

    return dedent(f"""\
        NBKP_LATEST_LINK=$({rl_cmd} 2>/dev/null || true)
        if [ -n "$NBKP_LATEST_LINK" ]; then
            NBKP_LATEST_NAME="${{NBKP_LATEST_LINK##*/}}"
            for snap in $({ls_cmd} 2>/dev/null | sort); do
                if [ "$snap" \\> "$NBKP_LATEST_NAME" ]; then
                    nbkp_log "Removing orphaned snapshot: $snap"
                    {rm_cmd}
                fi
            done
        fi""")


def _build_hard_link_mkdir_block(
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Build snapshot directory creation block."""
    dst_vol = config.volumes[sync.destination.volume]
    dest_path = _vol_path(
        vol_paths,
        sync.destination.volume,
        sync.destination.subdir,
    )
    snaps_dir = f"{dest_path}/snapshots"
    mkdir_cmd = _mkdir_snap_cmd(dst_vol, snaps_dir, resolved_endpoints)

    return dedent(f"""\
        NBKP_TS=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
        {mkdir_cmd}""")


def _build_hard_link_symlink_block(
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Build latest symlink update block."""
    dst_vol = config.volumes[sync.destination.volume]
    dest_path = _vol_path(
        vol_paths,
        sync.destination.volume,
        sync.destination.subdir,
    )
    ln_cmd = _ln_sfn_cmd(dst_vol, dest_path, resolved_endpoints)

    return dedent(f"""\
        if [ "$NBKP_DRY_RUN" = false ]; then
            {ln_cmd}
        fi""")


def _build_hard_link_prune_block(
    sync: SyncConfig,
    config: Config,
    max_snapshots: int,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Build hard-link prune block (rm -rf, skip latest)."""
    dst_vol = config.volumes[sync.destination.volume]
    dest_path = _vol_path(
        vol_paths,
        sync.destination.volume,
        sync.destination.subdir,
    )
    latest_path = f"{dest_path}/latest"
    snaps_dir = f"{dest_path}/snapshots"
    ls_cmd = _ls_snapshots_cmd(dst_vol, snaps_dir, resolved_endpoints)
    rl_cmd = _readlink_cmd(dst_vol, latest_path, resolved_endpoints)
    rm_cmd = _rm_rf_snap_cmd(dst_vol, snaps_dir, resolved_endpoints)

    # fmt: off
    pipe_while = (
        'echo "$NBKP_SNAPS"'
        ' | head -n "$NBKP_EXCESS"'
        " | while IFS= read -r snap; do"
    )
    # fmt: on
    return dedent(f"""\
        if [ "$NBKP_DRY_RUN" = false ]; then
            NBKP_SNAPS=$({ls_cmd} | sort)
            NBKP_COUNT=$(echo "$NBKP_SNAPS" | wc -l | tr -d ' ')
            NBKP_EXCESS=$((NBKP_COUNT - {max_snapshots}))
            NBKP_LATEST_LINK=$({rl_cmd} 2>/dev/null || true)
            NBKP_LATEST_NAME="${{NBKP_LATEST_LINK##*/}}"
            if [ "$NBKP_EXCESS" -gt 0 ]; then
                {pipe_while}
                    if [ "$snap" != "$NBKP_LATEST_NAME" ]; then
                        nbkp_log "Pruning snapshot: $snap"
                        {rm_cmd}
                    fi
                done
            fi
        fi""")


# ── Volume check builder ────────────────────────────────────


def _build_volume_check(
    slug: str,
    vol: LocalVolume | RemoteVolume,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    vpath = vol_paths[slug]
    sentinel = f"{vpath}/.nbkp-vol"
    match vol:
        case LocalVolume():
            test_cmd = f"test -f {_qp(sentinel)}"
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            test_cmd = _format_remote_test(
                ep.server, ep.proxy_chain, ["-f", sentinel]
            )
    return (
        f"{test_cmd}"
        f" || {{ nbkp_log"
        f' "WARN: volume {slug}:'
        f' sentinel {sentinel} not found";'
        f" }}"
    )


# ── Disabled sync body ───────────────────────────────────────


def _build_disabled_body(
    slug: str,
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Build the commented-out function body for a disabled sync."""
    enabled_sync = SyncConfig(
        slug=sync.slug,
        source=sync.source,
        destination=sync.destination,
        enabled=True,
        rsync_options=sync.rsync_options,
        filters=sync.filters,
        filter_file=sync.filter_file,
    )
    ctx = _build_sync_context(
        slug,
        enabled_sync,
        config,
        vol_paths,
        resolved_endpoints,
    )

    # Render the function body the same way the template would
    lines = _render_enabled_function(ctx)
    return "\n".join(
        f"# {line}" if line.strip() else "#" for line in lines.split("\n")
    )


def _render_enabled_function(ctx: _SyncContext) -> str:
    """Render a sync function body (for disabled commenting)."""
    parts: list[str] = []
    parts.append("")
    parts.append(f"{ctx.fn_name}() {{")
    parts.append(f'    nbkp_log "Starting sync: {ctx.slug}"')
    parts.append("")
    parts.append("    # Pre-flight checks")
    for line in ctx.preflight.split("\n"):
        parts.append(f"    {line}" if line else "")
    if ctx.has_hard_link:
        parts.append("")
        parts.append("    # Cleanup orphaned snapshots")
        for line in ctx.orphan_cleanup.split("\n"):
            parts.append(f"    {line}" if line else "")
        parts.append("")
        parts.append(
            "    # Link-dest resolution"
            " (latest snapshot for incremental backup)"
        )
        for line in ctx.link_dest.split("\n"):
            parts.append(f"    {line}" if line else "")
        parts.append("")
        parts.append("    # Create snapshot directory")
        for line in ctx.hl_mkdir.split("\n"):
            parts.append(f"    {line}" if line else "")
    if ctx.has_btrfs:
        parts.append("")
        parts.append(
            "    # Link-dest resolution"
            " (latest snapshot for incremental backup)"
        )
        for line in ctx.link_dest.split("\n"):
            parts.append(f"    {line}" if line else "")
    parts.append("")
    parts.append("    # Rsync")
    for line in ctx.rsync.split("\n"):
        parts.append(f"    {line}" if line else "")
    if ctx.has_btrfs:
        parts.append("")
        parts.append("    # Btrfs snapshot (skip if dry-run)")
        for line in ctx.snapshot.split("\n"):
            parts.append(f"    {line}" if line else "")
        parts.append("")
        parts.append("    # Update latest symlink (skip if dry-run)")
        for line in ctx.symlink.split("\n"):
            parts.append(f"    {line}" if line else "")
        if ctx.has_prune:
            parts.append("")
            parts.append(
                f"    # Prune old snapshots" f" (max: {ctx.max_snapshots})"
            )
            for line in ctx.prune.split("\n"):
                parts.append(f"    {line}" if line else "")
    if ctx.has_hard_link:
        parts.append("")
        parts.append("    # Update latest symlink (skip if dry-run)")
        for line in ctx.symlink.split("\n"):
            parts.append(f"    {line}" if line else "")
        if ctx.has_prune:
            parts.append("")
            parts.append(
                f"    # Prune old snapshots" f" (max: {ctx.max_snapshots})"
            )
            for line in ctx.hl_prune.split("\n"):
                parts.append(f"    {line}" if line else "")
    parts.append("")
    parts.append(f'    nbkp_log "Completed sync: {ctx.slug}"')
    parts.append("}")
    return "\n".join(parts)


# ── Context builders ─────────────────────────────────────────


def _build_sync_context(
    slug: str,
    sync: SyncConfig,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
) -> _SyncContext:
    """Build a _SyncContext with all pre-computed blocks."""
    has_btrfs = sync.destination.btrfs_snapshots.enabled
    has_hard_link = sync.destination.hard_link_snapshots.enabled
    btrfs_cfg = sync.destination.btrfs_snapshots
    hl_cfg = sync.destination.hard_link_snapshots

    has_prune = (has_btrfs and btrfs_cfg.max_snapshots is not None) or (
        has_hard_link and hl_cfg.max_snapshots is not None
    )
    max_snaps = (
        btrfs_cfg.max_snapshots
        if has_btrfs
        else hl_cfg.max_snapshots if has_hard_link else None
    )

    preflight = _build_preflight_block(
        sync, config, vol_paths, resolved_endpoints
    )

    # Link-dest: only for hard-link (removed from btrfs)
    link_dest = (
        _build_link_dest_block(
            sync,
            config,
            vol_paths,
            resolved_endpoints,
            link_dest_prefix="../",
        )
        if has_hard_link
        else ""
    )

    # Rsync block
    if has_hard_link:
        rsync = _build_rsync_block(
            sync,
            config,
            vol_paths,
            resolved_endpoints,
            dest_suffix="snapshots/$NBKP_TS",
            has_link_dest=True,
        )
    elif has_btrfs:
        rsync = _build_rsync_block(
            sync,
            config,
            vol_paths,
            resolved_endpoints,
            dest_suffix=STAGING_DIR,
        )
    else:
        rsync = _build_rsync_block(
            sync,
            config,
            vol_paths,
            resolved_endpoints,
            dest_suffix=None,
        )

    # Btrfs blocks
    snapshot = (
        _build_snapshot_block(sync, config, vol_paths, resolved_endpoints)
        if has_btrfs
        else ""
    )
    prune = (
        _build_prune_block(
            sync,
            config,
            max_snaps,
            vol_paths,
            resolved_endpoints,
        )
        if has_btrfs and max_snaps is not None
        else ""
    )

    # Hard-link blocks
    orphan_cleanup = (
        _build_hard_link_orphan_cleanup_block(
            sync, config, vol_paths, resolved_endpoints
        )
        if has_hard_link
        else ""
    )
    hl_mkdir = (
        _build_hard_link_mkdir_block(
            sync, config, vol_paths, resolved_endpoints
        )
        if has_hard_link
        else ""
    )
    symlink = (
        _build_hard_link_symlink_block(
            sync, config, vol_paths, resolved_endpoints
        )
        if has_hard_link or has_btrfs
        else ""
    )
    hl_prune = (
        _build_hard_link_prune_block(
            sync,
            config,
            max_snaps,
            vol_paths,
            resolved_endpoints,
        )
        if has_hard_link and max_snaps is not None
        else ""
    )

    return _SyncContext(
        slug=slug,
        fn_name=_slug_to_fn(slug),
        enabled=sync.enabled,
        has_btrfs=has_btrfs,
        has_hard_link=has_hard_link,
        has_prune=has_prune,
        max_snapshots=max_snaps,
        preflight=preflight,
        link_dest=link_dest,
        rsync=rsync,
        snapshot=snapshot,
        prune=prune,
        orphan_cleanup=orphan_cleanup,
        hl_mkdir=hl_mkdir,
        symlink=symlink,
        hl_prune=hl_prune,
    )


def _build_script_context(
    config: Config,
    options: ScriptOptions,
    vol_paths: dict[str, str],
    now: datetime,
    resolved_endpoints: ResolvedEndpoints,
) -> dict[str, object]:
    """Build the full template context dict."""
    timestamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    config_line = (
        f"# Config: {options.config_path}"
        if options.config_path
        else "# Config: <stdin>"
    )
    has_script_dir = any("$" in p for p in vol_paths.values())

    volume_checks = [
        _build_volume_check(slug, vol, vol_paths, resolved_endpoints)
        for slug, vol in config.volumes.items()
    ]

    from .sync.ordering import sort_syncs, sync_predecessors

    pred_map = sync_predecessors(config.syncs)

    syncs: list[_SyncContext] = []
    for slug in sort_syncs(config.syncs):
        sync = config.syncs[slug]
        ctx = _build_sync_context(
            slug, sync, config, vol_paths, resolved_endpoints
        )
        pred_fns = tuple(
            _slug_to_fn(p) for p in sorted(pred_map.get(slug, set()))
        )
        if sync.enabled:
            syncs.append(replace(ctx, predecessors=pred_fns))
        else:
            disabled_body = _build_disabled_body(
                slug,
                sync,
                config,
                vol_paths,
                resolved_endpoints,
            )
            syncs.append(
                _SyncContext(
                    slug=ctx.slug,
                    fn_name=ctx.fn_name,
                    enabled=False,
                    disabled_body=disabled_body,
                )
            )

    return {
        "timestamp": timestamp,
        "config_line": config_line,
        "has_script_dir": has_script_dir,
        "volume_checks": volume_checks,
        "syncs": syncs,
    }
