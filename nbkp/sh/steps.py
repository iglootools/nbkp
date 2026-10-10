"""The steps of a generated sync function, mirroring ``nbkp.sync.modes``.

Each step is a block of shell code at indent 0.  Sync functions run in a
``set -e`` subshell (see ``nbkp_run_sync`` in the template), so any
failing command aborts the sync; steps only handle failures explicitly
where ``nbkp run`` does something other than stopping (best-effort
orphan cleanup, pruning, and removal of the new snapshot after a failed
hard-link rsync, each of which only logs a warning).
"""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from ..config import LocalVolume, RemoteVolume
from ..fsprotocol import SNAPSHOTS_DIR, STAGING_DIR
from ..sync.rsync import build_rsync_command
from .commands import snapshot_date_format, vol_cmd
from .quoting import INDENT, SNAP, SNAPSHOT_TS, format_command, quote
from .spec import SyncSpec


@dataclass(frozen=True)
class Step:
    title: str
    body: str


def build_steps(spec: SyncSpec) -> tuple[Step, ...]:
    """Ordered steps of the sync function for *spec*'s snapshot mode."""
    prune = _prune_steps(spec)
    match spec.dst_ep.snapshot_mode:
        case "hard-link":
            return (
                Step("Cleanup orphaned snapshots", _orphan_cleanup_block(spec)),
                Step(
                    "Link-dest resolution (latest snapshot for incremental backup)",
                    _link_dest_block(spec),
                ),
                Step("Create snapshot directory", _mkdir_block(spec)),
                Step("Rsync", _rsync_block(spec, f"{SNAPSHOTS_DIR}/{SNAPSHOT_TS}")),
                Step("Update latest symlink (skip if dry-run)", _symlink_block(spec)),
                *prune,
            )
        case "btrfs":
            return (
                Step("Rsync", _rsync_block(spec, STAGING_DIR)),
                Step("Btrfs snapshot (skip if dry-run)", _btrfs_snapshot_block(spec)),
                Step("Update latest symlink (skip if dry-run)", _symlink_block(spec)),
                *prune,
            )
        case _:
            return (Step("Rsync", _rsync_block(spec, None)),)


def _prune_steps(spec: SyncSpec) -> tuple[Step, ...]:
    match spec.max_snapshots:
        case None:
            return ()
        case max_snaps:
            title = f"Prune old snapshots (max: {max_snaps})"
            return (Step(title, _prune_block(spec, max_snaps)),)


def _on_dst(spec: SyncSpec, args: list[str]) -> str:
    return vol_cmd(spec.dst_vol, args, spec.resolved_endpoints)


def _readlink_latest(spec: SyncSpec) -> str:
    return f"$({_on_dst(spec, ['readlink', spec.dst_latest])} 2>/dev/null || true)"


def _list_snapshots(spec: SyncSpec, *, quiet: bool = False) -> str:
    """Valid snapshot names, oldest first (``nbkp_snapshot_names`` sorts)."""
    ls = _on_dst(spec, ["ls", spec.snapshots_dir])
    redirect = " 2>/dev/null" if quiet else ""
    return f"$({ls}{redirect} | nbkp_snapshot_names)"


def _rm_snapshot(spec: SyncSpec, name: str) -> str:
    return _on_dst(spec, ["rm", "-rf", f"{spec.snapshots_dir}/{name}"])


def _indent(text: str, levels: int) -> str:
    pad = INDENT * levels
    return "\n".join(f"{pad}{line}" if line else line for line in text.split("\n"))


def _orphan_cleanup_block(spec: SyncSpec) -> str:
    """Remove snapshots newer than ``latest`` (left by failed syncs).

    Best-effort, as in ``nbkp run``: a failed removal is only a warning.
    """
    rm = _rm_snapshot(spec, SNAP)
    return dedent(f"""\
        if [ "$NBKP_DRY_RUN" = false ]; then
            NBKP_LATEST_LINK={_readlink_latest(spec)}
            if [ -n "$NBKP_LATEST_LINK" ] && [ "$NBKP_LATEST_LINK" != "/dev/null" ]; then
                NBKP_LATEST_NAME="${{NBKP_LATEST_LINK##*/}}"
                for snap in {_list_snapshots(spec, quiet=True)}; do
                    if [ "$snap" \\> "$NBKP_LATEST_NAME" ]; then
                        nbkp_log "Removing orphaned snapshot: $snap"
                        {rm} || nbkp_log "WARN: failed to remove orphaned snapshot: $snap"
                    fi
                done
            fi
        fi""")


def _link_dest_block(spec: SyncSpec) -> str:
    """``--link-dest`` to the snapshot ``latest`` designates, as in run."""
    return dedent(f"""\
        NBKP_LATEST_LINK={_readlink_latest(spec)}
        RSYNC_LINK_DEST=""
        if [ -n "$NBKP_LATEST_LINK" ] && [ "$NBKP_LATEST_LINK" != "/dev/null" ]; then
            RSYNC_LINK_DEST="--link-dest=../${{NBKP_LATEST_LINK##*/}}"
        fi""")


def _timestamp_line(spec: SyncSpec) -> str:
    fmt = snapshot_date_format(spec.dst_vol, spec.platform)
    return f"NBKP_TS=$(date -u {quote(fmt)})"


def _mkdir_block(spec: SyncSpec) -> str:
    mkdir = _on_dst(spec, ["mkdir", "-p", f"{spec.snapshots_dir}/{SNAPSHOT_TS}"])
    return dedent(f"""\
        {_timestamp_line(spec)}
        if [ "$NBKP_DRY_RUN" = false ]; then
            {mkdir}
        fi""")


def _rsync_command(spec: SyncSpec, dest_suffix: str | None) -> list[str]:
    """``build_rsync_command`` with local volume paths made script-relative."""
    cmd = build_rsync_command(
        spec.sync,
        spec.config,
        dry_run=False,
        link_dest=None,
        progress=None,
        resolved_endpoints=spec.resolved_endpoints,
        dest_suffix=dest_suffix,
    )
    match (spec.src_vol, spec.dst_vol):
        case (RemoteVolume(), RemoteVolume()):
            return cmd
        case _:
            return [
                *cmd[:-2],
                _substitute_vol_path(cmd[-2], spec.src_vol, spec.src_vol_path),
                _substitute_vol_path(cmd[-1], spec.dst_vol, spec.dst_vol_path),
            ]


def _substitute_vol_path(
    arg: str, vol: LocalVolume | RemoteVolume, effective_path: str
) -> str:
    """Replace a local volume's configured path prefix with its effective path."""
    match vol:
        case LocalVolume() if vol.path is not None:
            return arg.replace(vol.path, effective_path, 1)
        case _:
            return arg


def _rsync_block(spec: SyncSpec, dest_suffix: str | None) -> str:
    """Rsync plus failure handling (hard-link: drop the new snapshot dir)."""
    runtime_args = [
        *(
            ['${RSYNC_LINK_DEST:+"$RSYNC_LINK_DEST"}']
            if spec.dst_ep.snapshot_mode == "hard-link"
            else []
        ),
        '${RSYNC_DRY_RUN_FLAG:+"$RSYNC_DRY_RUN_FLAG"}',
        '${RSYNC_PROGRESS_FLAGS[@]+"${RSYNC_PROGRESS_FLAGS[@]}"}',
    ]
    sep = f" \\\n{INDENT}"
    command = format_command(_rsync_command(spec, dest_suffix))
    on_failure = "\n".join(
        [
            'nbkp_log "ERROR: rsync exited with code $NBKP_RSYNC_RC"',
            *_rsync_failure_cleanup(spec),
            "return 1",
        ]
    )
    return "\n".join(
        [
            "NBKP_RSYNC_RC=0",
            f"{command}{sep}{sep.join(runtime_args)} || NBKP_RSYNC_RC=$?",
            'if [ "$NBKP_RSYNC_RC" -ne 0 ]; then',
            _indent(on_failure, 1),
            "fi",
        ]
    )


def _rsync_failure_cleanup(spec: SyncSpec) -> list[str]:
    """Hard-link: remove the new, partial snapshot dir (best-effort, as in run)."""
    match spec.dst_ep.snapshot_mode:
        case "hard-link":
            return [
                'if [ "$NBKP_DRY_RUN" = false ]; then',
                _indent(f"{_rm_snapshot(spec, SNAPSHOT_TS)} || true", 1),
                "fi",
            ]
        case _:
            return []


def _btrfs_snapshot_block(spec: SyncSpec) -> str:
    snap = _on_dst(
        spec,
        [
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            spec.staging_dir,
            f"{spec.snapshots_dir}/{SNAPSHOT_TS}",
        ],
    )
    return dedent(f"""\
        if [ "$NBKP_DRY_RUN" = false ]; then
            {_timestamp_line(spec)}
            {snap}
        fi""")


def _symlink_block(spec: SyncSpec) -> str:
    ln = _on_dst(
        spec, ["ln", "-sfn", f"{SNAPSHOTS_DIR}/{SNAPSHOT_TS}", spec.dst_latest]
    )
    return dedent(f"""\
        if [ "$NBKP_DRY_RUN" = false ]; then
            {ln}
        fi""")


def _delete_snapshot_cmds(spec: SyncSpec) -> list[str]:
    path = f"{spec.snapshots_dir}/{SNAP}"
    match spec.dst_ep.snapshot_mode:
        case "btrfs":
            return [
                _on_dst(spec, ["btrfs", "property", "set", path, "ro", "false"]),
                _on_dst(spec, ["btrfs", "subvolume", "delete", path]),
            ]
        case _:
            return [_on_dst(spec, ["rm", "-rf", path])]


def _prune_block(spec: SyncSpec, max_snapshots: int) -> str:
    """Delete the oldest snapshots beyond *max_snapshots*, never ``latest``.

    Same selection as ``prune_snapshots``: drop the latest target from the
    candidates first, then take the oldest ``excess`` of the rest.
    """
    selection = dedent(f"""\
        NBKP_SNAPS={_list_snapshots(spec)}
        NBKP_COUNT=$(printf '%s\\n' "$NBKP_SNAPS" | grep -c . || true)
        NBKP_EXCESS=$((NBKP_COUNT - {max_snapshots}))
        NBKP_LATEST_LINK={_readlink_latest(spec)}
        if [ "$NBKP_LATEST_LINK" = "/dev/null" ]; then
            NBKP_LATEST_NAME=""
        else
            NBKP_LATEST_NAME="${{NBKP_LATEST_LINK##*/}}"
        fi
        NBKP_PRUNED=0""")
    loop_body = "\n".join(
        [
            '[ "$NBKP_PRUNED" -lt "$NBKP_EXCESS" ] || break',
            '[ "$snap" != "$NBKP_LATEST_NAME" ] || continue',
            'nbkp_log "Pruning snapshot: $snap"',
            *_delete_snapshot_cmds(spec),
            "NBKP_PRUNED=$((NBKP_PRUNED + 1))",
        ]
    )
    body = "\n".join(
        [selection, "for snap in $NBKP_SNAPS; do", _indent(loop_body, 1), "done"]
    )
    return "\n".join(
        ['if [ "$NBKP_DRY_RUN" = false ]; then', _indent(_best_effort(body), 1), "fi"]
    )


def _best_effort(body: str) -> str:
    """Run *body* with ``set -e`` but turn its failure into a warning.

    Pruning is best-effort in ``nbkp run``: the new snapshot is complete and
    ``latest`` already points to it.  ``set +e`` around an explicit subshell,
    as in ``nbkp_run_sync``: errexit is ignored inside an ``if``/``||``
    condition, subshells included, so ``if ! ( set -e; ... )`` would not stop
    at the first failing command.
    """
    warn = 'nbkp_log "WARN: pruning failed (exit $NBKP_PRUNE_RC); snapshots kept"'
    return "\n".join(
        [
            "set +e",
            "(",
            _indent("set -e", 1),
            _indent(body, 1),
            ")",
            "NBKP_PRUNE_RC=$?",
            "set -e",
            'if [ "$NBKP_PRUNE_RC" -ne 0 ]; then',
            _indent(warn, 1),
            "fi",
        ]
    )
