"""
The steps of a generated sync function, mirroring ``nbkp.sync.modes``.

Each step is a block of shell code at indent 0.  Sync functions run in a
``set -e`` subshell (see ``nbkp_run_sync`` in the template), so any
failing command aborts the sync; steps only handle failures explicitly
where ``nbkp run`` does something other than stopping (best-effort
orphan cleanup, pruning, and removal of the new snapshot after a failed
hard-link rsync, each of which only logs a warning, and files that vanished
during the transfer under ``vanished-files: warn``).
"""

import posixpath
from dataclasses import dataclass
from textwrap import dedent

from nbkp.config import LocalVolume, RemoteVolume
from nbkp.fsprotocol import LATEST_LINK, SNAPSHOTS_DIR, STAGING_DIR
from nbkp.sh.commands import snapshot_date_format, vol_cmd
from nbkp.sh.quoting import (
    INDENT,
    PID,
    SNAP,
    SNAPSHOT_TS,
    format_command,
    quote,
    quote_args,
)
from nbkp.sh.spec import SyncSpec
from nbkp.snapshots.hardlinks import build_owner_rwx_dirs_args, build_rm_tree_args
from nbkp.sync.rsync import (
    RSYNC_VANISHED_FILES_EXIT,
    build_rsync_command,
    hard_link_dest_suffix,
    hard_link_link_dest,
)


@dataclass(frozen=True)
class Step:
    """One step of a sync function: a comment title and its shell code."""

    title: str
    body: str


def build_steps(spec: SyncSpec) -> tuple[Step, ...]:
    """Return the steps of *spec*'s sync function, in run order, per snapshot mode."""
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
                Step("Rsync", _rsync_block(spec, hard_link_dest_suffix(SNAPSHOT_TS))),
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


def _readlink_latest(spec: SyncSpec, *, strict: bool = False) -> str:
    """
    ``latest``'s target; empty when it cannot be read, unless *strict*.

    A strict read fails the enclosing ``set -e`` block instead, for callers
    that would otherwise act on an empty target as "no latest".
    """
    readlink = _on_dst(spec, ["readlink", spec.dst_latest])
    return f"$({readlink})" if strict else f"$({readlink} 2>/dev/null || true)"


def _list_snapshots(spec: SyncSpec, *, quiet: bool = False) -> str:
    """List valid snapshot names, oldest first (``nbkp_snapshot_names`` sorts)."""
    ls = _on_dst(spec, ["ls", spec.snapshots_dir])
    redirect = " 2>/dev/null" if quiet else ""
    return f"$({ls}{redirect} | nbkp_snapshot_names)"


def _rm_snapshot(spec: SyncSpec, name: str) -> str:
    """
    Delete a hard-link snapshot, as ``delete_snapshot`` does.

    When the plain deletion fails (read-only directories that ``rsync -a``
    preserved), its directories get their owner's ``rwx`` and the deletion is
    retried.
    """
    path = f"{spec.snapshots_dir}/{name}"
    rm = _on_dst(spec, build_rm_tree_args(path))
    open_dirs = _on_dst(spec, build_owner_rwx_dirs_args(path))
    return f"{{ {rm} || {{ {open_dirs} && {rm}; }}; }}"


def _indent(text: str, levels: int) -> str:
    pad = INDENT * levels
    return "\n".join(f"{pad}{line}" if line else line for line in text.split("\n"))


def _orphan_cleanup_block(spec: SyncSpec) -> str:
    """
    Remove the snapshots newer than ``latest``, left by failed syncs.

    And all of them while ``latest`` points to ``/dev/null``, as in ``nbkp run``.

    As in ``nbkp run``, a failed removal fails the sync before a new snapshot
    is made, so ``latest`` never moves past an incomplete snapshot. A
    ``latest`` that cannot be read, absent or not, skips the cleanup, as
    ``nbkp run`` skips it, with a warning, when the link is absent.
    """
    rm = _rm_snapshot(spec, SNAP)
    fail = '{ nbkp_log "ERROR: failed to remove orphaned snapshot: $snap"; false; }'
    return dedent(f"""\
        if [ "$NBKP_DRY_RUN" = false ]; then
            NBKP_LATEST_LINK={_readlink_latest(spec)}
            if [ -n "$NBKP_LATEST_LINK" ]; then
                NBKP_LATEST_NAME="${{NBKP_LATEST_LINK##*/}}"
                for snap in {_list_snapshots(spec, quiet=True)}; do
                    if [ "$NBKP_LATEST_LINK" = "/dev/null" ] \\
                        || nbkp_snapshot_after "$snap" "$NBKP_LATEST_NAME"; then
                        nbkp_log "Removing orphaned snapshot: $snap"
                        {rm} || {fail}
                    fi
                done
            fi
        fi""")


# The snapshot name `latest` designates, read at runtime by _link_dest_block.
_LATEST_NAME = "${NBKP_LATEST_LINK##*/}"


def _link_dest_block(spec: SyncSpec) -> str:
    """``--link-dest`` to the snapshot ``latest`` designates, as in run."""
    return dedent(f"""\
        NBKP_LATEST_LINK={_readlink_latest(spec)}
        RSYNC_LINK_DEST=""
        if [ -n "$NBKP_LATEST_LINK" ] && [ "$NBKP_LATEST_LINK" != "/dev/null" ]; then
            RSYNC_LINK_DEST="--link-dest={hard_link_link_dest(_LATEST_NAME)}"
        fi""")


def _timestamp_line(spec: SyncSpec) -> str:
    fmt = snapshot_date_format(spec.dst_vol, spec.platform)
    return f"NBKP_TS=$(date -u {quote(fmt)})"


def _after_latest_guard(spec: SyncSpec) -> str:
    """
    Fail the sync unless ``$NBKP_TS`` sorts after ``latest``'s snapshot.

    As ``ensure_after_latest`` in ``nbkp run``: a snapshot taken with the
    clock behind would move ``latest`` back in time, and the next orphan
    cleanup would remove every complete snapshot after it; an equal name
    would reuse the latest snapshot.  ``nbkp_snapshot_after`` compares the
    names by time, whichever name form each uses. Run in dry runs too, as in
    ``nbkp run``: it only reads ``latest``.
    """
    error = (
        "ERROR: new snapshot $NBKP_TS would not be after the latest"
        " snapshot $NBKP_LATEST_NAME; is the clock behind?"
    )
    return dedent(f"""\
        NBKP_LATEST_LINK={_readlink_latest(spec)}
        if [ -n "$NBKP_LATEST_LINK" ] && [ "$NBKP_LATEST_LINK" != "/dev/null" ]; then
            NBKP_LATEST_NAME="${{NBKP_LATEST_LINK##*/}}"
            if ! nbkp_snapshot_after "$NBKP_TS" "$NBKP_LATEST_NAME"; then
                nbkp_log "{error}"
                return 1
            fi
        fi""")


def _mkdir_block(spec: SyncSpec) -> str:
    """
    Create the new snapshot dir, which must not exist yet (as in run).

    Plain ``mkdir``: names have one-second resolution here, and reusing an
    existing snapshot would have rsync write into it, and a failed rsync
    then remove it.
    """
    mkdir_parent = _on_dst(spec, ["mkdir", "-p", spec.snapshots_dir])
    mkdir = _on_dst(spec, ["mkdir", f"{spec.snapshots_dir}/{SNAPSHOT_TS}"])
    return "\n".join(
        [
            _timestamp_line(spec),
            _after_latest_guard(spec),
            'if [ "$NBKP_DRY_RUN" = false ]; then',
            _indent(mkdir_parent, 1),
            _indent(mkdir, 1),
            "fi",
        ],
    )


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
    arg: str,
    vol: LocalVolume | RemoteVolume,
    effective_path: str,
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
        ],
    )
    return "\n".join(
        [
            "NBKP_RSYNC_RC=0",
            f"{command}{sep}{sep.join(runtime_args)} || NBKP_RSYNC_RC=$?",
            *_vanished_files_check(spec),
            'if [ "$NBKP_RSYNC_RC" -ne 0 ]; then',
            _indent(on_failure, 1),
            "fi",
        ],
    )


def _vanished_files_check(spec: SyncSpec) -> list[str]:
    """``vanished-files: warn``: exit code 24 only logs a warning (as in run)."""
    match spec.sync.rsync_options.vanished_files:
        case "warn":
            code = RSYNC_VANISHED_FILES_EXIT
            message = (
                "WARN: some files vanished from the source during the transfer"
                f" (rsync exit code {code}); everything else was copied"
            )
            return [
                f'if [ "$NBKP_RSYNC_RC" -eq {code} ]; then',
                _indent(f"nbkp_log {quote(message)}", 1),
                _indent("NBKP_RSYNC_RC=0", 1),
                "fi",
            ]
        case "fail":
            return []


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
    return "\n".join(
        [
            _timestamp_line(spec),
            _after_latest_guard(spec),
            'if [ "$NBKP_DRY_RUN" = false ]; then',
            _indent(snap, 1),
            "fi",
        ],
    )


def _symlink_block(spec: SyncSpec) -> str:
    update = "\n".join(_update_latest_cmds(spec))
    return "\n".join(
        ['if [ "$NBKP_DRY_RUN" = false ]; then', _indent(update, 1), "fi"],
    )


def _update_latest_cmds(spec: SyncSpec) -> list[str]:
    """
    Point ``latest`` at the new snapshot, as ``update_latest_symlink`` does.

    A local ``latest`` is replaced atomically: the link is made under a temporary
    name and renamed over it, so an interruption leaves the old link or the new
    one, never none. ``mv`` is told not to follow ``latest`` into the snapshot it
    points to (``-T`` in GNU mv, ``-h`` in BSD mv). Two lines rather than an
    ``&&`` list, so that ``set -e`` stops on a failed ``ln`` too. A remote
    ``latest`` is replaced with ``ln -sfn``, as in ``nbkp run``.
    """
    target = f"{SNAPSHOTS_DIR}/{SNAPSHOT_TS}"
    match spec.dst_vol:
        case LocalVolume():
            tmp = f"{posixpath.dirname(spec.dst_latest)}/.{LATEST_LINK}.nbkp-tmp-{PID}"
            no_follow = "-T" if spec.platform.startswith("linux") else "-h"
            mv = quote_args(["mv", "-f", no_follow, tmp, spec.dst_latest])
            rm_tmp = quote_args(["rm", "-f", tmp])
            return [
                quote_args(["ln", "-sfn", target, tmp]),
                f"{mv} || {{ {rm_tmp}; false; }}",
            ]
        case RemoteVolume():
            return [_on_dst(spec, ["ln", "-sfn", target, spec.dst_latest])]


def _delete_snapshot_cmds(spec: SyncSpec) -> list[str]:
    path = f"{spec.snapshots_dir}/{SNAP}"
    match spec.dst_ep.snapshot_mode:
        case "btrfs":
            # A failed delete makes the snapshot read-only again, so one that
            # stays is not left writable; ``false`` then fails the ``set -e``
            # block as the delete would have.
            delete = _on_dst(spec, ["btrfs", "subvolume", "delete", path])
            restore = _on_dst(spec, ["btrfs", "property", "set", path, "ro", "true"])
            left_writable = 'nbkp_log "WARN: snapshot $snap is left writable"'
            return [
                _on_dst(spec, ["btrfs", "property", "set", path, "ro", "false"]),
                f"{delete} || {{ {restore} || {left_writable}; false; }}",
            ]
        case _:
            return [_rm_snapshot(spec, SNAP)]


# Whether $snap is newer than the snapshot `latest` designates (never while
# `latest` points to /dev/null), read by _prune_block.
_AFTER_LATEST = (
    '{ [ -n "$NBKP_LATEST_NAME" ] && nbkp_snapshot_after "$snap" "$NBKP_LATEST_NAME"; }'
)


def _prune_block(spec: SyncSpec, max_snapshots: int) -> str:
    """
    Delete the oldest snapshots beyond *max_snapshots*, never ``latest``.

    Same selection as ``prune_snapshots``: leave out the snapshots newer
    than ``latest`` (failed syncs' leftovers, possibly incomplete) from both
    the count and the candidates, drop the latest target from the candidates,
    then take the oldest ``excess`` of the rest. As there, a ``latest`` that
    cannot be read, or a listing that fails, stops the pruning (a warning)
    rather than pruning without that protection.
    """
    selection = dedent(f"""\
        NBKP_SNAPS={_list_snapshots(spec)}
        NBKP_LATEST_LINK={_readlink_latest(spec, strict=True)}
        if [ "$NBKP_LATEST_LINK" = "/dev/null" ]; then
            NBKP_LATEST_NAME=""
        else
            NBKP_LATEST_NAME="${{NBKP_LATEST_LINK##*/}}"
        fi
        NBKP_COUNT=0
        for snap in $NBKP_SNAPS; do
            {_AFTER_LATEST} || NBKP_COUNT=$((NBKP_COUNT + 1))
        done
        NBKP_EXCESS=$((NBKP_COUNT - {max_snapshots}))
        NBKP_PRUNED=0""")
    loop_body = "\n".join(
        [
            '[ "$NBKP_PRUNED" -lt "$NBKP_EXCESS" ] || break',
            f"! {_AFTER_LATEST} || continue",
            '[ "$snap" != "$NBKP_LATEST_NAME" ] || continue',
            'nbkp_log "Pruning snapshot: $snap"',
            *_delete_snapshot_cmds(spec),
            "NBKP_PRUNED=$((NBKP_PRUNED + 1))",
        ],
    )
    body = "\n".join(
        [selection, "for snap in $NBKP_SNAPS; do", _indent(loop_body, 1), "done"],
    )
    return "\n".join(
        ['if [ "$NBKP_DRY_RUN" = false ]; then', _indent(_best_effort(body), 1), "fi"],
    )


def _best_effort(body: str) -> str:
    """
    Run *body* with ``set -e`` but turn its failure into a warning.

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
        ],
    )
