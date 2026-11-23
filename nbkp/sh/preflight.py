"""
Generated preflight checks, mirroring ``nbkp preflight``.

Each sync gets a ``<fn>_preflight`` shell function returning:

- ``0`` — active;
- ``INACTIVE`` (2) — an expected, transient condition (missing volume or
  endpoint sentinel, unreachable host);
- ``DRY_RUN_PENDING`` (3) — the source snapshot will only exist once the
  upstream sync has really run (dry-run only); skipped without cascading;
- ``BROKEN`` (1) — an infrastructure problem.

Inactive checks come first so that an absent drive is reported as
inactive rather than as a cascade of infrastructure errors, matching the
layered model of ``nbkp preflight``.
"""

from nbkp.config import LocalVolume, RemoteVolume
from nbkp.fsprotocol import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    NO_HARDLINK_FILESYSTEMS,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from nbkp.sh.commands import (
    BROKEN,
    DRY_RUN_PENDING,
    INACTIVE,
    guard,
    output_of,
    vol_cmd,
    which_cmd,
)
from nbkp.sh.quoting import quote, shell_var
from nbkp.sh.spec import SyncSpec

# Tools the snapshot steps run on the destination host.
_BTRFS_TOOLS = ("btrfs", "stat", "findmnt", "readlink", "ls", "ln")
_HARD_LINK_TOOLS = ("stat", "readlink", "ls", "ln", "mkdir", "rm")


def build_preflight_block(spec: SyncSpec) -> str:
    """Body of the ``<fn>_preflight`` function (indent 0)."""
    lines = [
        *_sentinel_checks(spec),
        *_rsync_checks(spec.src_vol, "source", spec),
        *_rsync_checks(spec.dst_vol, "destination", spec),
        *_source_snapshot_checks(spec),
        *_destination_snapshot_checks(spec),
        guard(
            _test(spec.dst_vol, ["-w", spec.dst_path], spec),
            f"destination endpoint {spec.dst_path} not writable",
            BROKEN,
        ),
        "return 0",
    ]
    return "\n".join(lines)


def _test(vol: LocalVolume | RemoteVolume, args: list[str], spec: SyncSpec) -> str:
    return vol_cmd(vol, ["test", *args], spec.resolved_endpoints)


def _sentinel_checks(spec: SyncSpec) -> list[str]:
    """Volume and endpoint sentinels: missing means inactive, not broken."""
    checks = [
        (spec.src_vol, f"{spec.src_vol_path}/{VOLUME_SENTINEL}", "source volume"),
        (spec.dst_vol, f"{spec.dst_vol_path}/{VOLUME_SENTINEL}", "destination volume"),
        (spec.src_vol, f"{spec.src_path}/{SOURCE_SENTINEL}", "source"),
        (spec.dst_vol, f"{spec.dst_path}/{DESTINATION_SENTINEL}", "destination"),
    ]
    return [
        guard(
            _test(vol, ["-f", sentinel_path], spec),
            f"{role} sentinel {sentinel_path} not found (or host unreachable)",
            INACTIVE,
        )
        for vol, sentinel_path, role in checks
    ]


def _rsync_checks(
    vol: LocalVolume | RemoteVolume,
    role: str,
    spec: SyncSpec,
) -> list[str]:
    resolved = spec.resolved_endpoints
    version = output_of(vol, ["rsync", "--version"], resolved)
    return [
        guard(which_cmd(vol, "rsync", resolved), f"rsync not found on {role}", BROKEN),
        guard(
            f"nbkp_rsync_version_ok {version}",
            f"rsync on {role} is too old or openrsync (GNU rsync 3.0+ required)",
            BROKEN,
        ),
    ]


def _tool_checks(tools: tuple[str, ...], spec: SyncSpec) -> list[str]:
    resolved = spec.resolved_endpoints
    return [
        guard(
            which_cmd(spec.dst_vol, tool, resolved),
            f"{tool} not found on destination",
            BROKEN,
        )
        for tool in tools
    ]


def _latest_checks(
    vol: LocalVolume | RemoteVolume,
    path: str,
    role: str,
    var: str,
    spec: SyncSpec,
) -> list[str]:
    """
    Check that ``latest`` is a symlink to ``/dev/null`` or to a snapshot dir.

    As in ``nbkp run``'s preflight, a snapshot dir is ``snapshots/<timestamp>``.

    Leaves the raw target in ``$var`` for the caller's further checks.
    """
    latest = f"{path}/{LATEST_LINK}"
    target = output_of(vol, ["readlink", latest], spec.resolved_endpoints)
    return [
        guard(
            _test(vol, ["-L", latest], spec),
            f"{role} {LATEST_LINK} symlink not found ({latest})",
            BROKEN,
        ),
        f"{var}={target}",
        guard(
            f'[ "${var}" = /dev/null ] || {{ nbkp_is_snapshot_target "${var}"'
            f" {SNAPSHOTS_DIR} && {_test(vol, ['-d', f'{latest}/'], spec)}; }}",
            f"{role} {LATEST_LINK} symlink target is invalid ({latest})",
            BROKEN,
        ),
    ]


def _source_snapshot_checks(spec: SyncSpec) -> list[str]:
    """Check a snapshot-enabled source, read through its ``latest`` symlink."""
    if spec.src_ep.snapshot_mode == "none":
        return []
    else:
        snaps = f"{spec.src_path}/{SNAPSHOTS_DIR}"
        devnull = '[ "$NBKP_SRC_LATEST" != /dev/null ]'
        return [
            *_latest_checks(
                spec.src_vol,
                spec.src_path,
                "source",
                "NBKP_SRC_LATEST",
                spec,
            ),
            guard(
                _test(spec.src_vol, ["-d", snaps], spec),
                f"source {SNAPSHOTS_DIR}/ not found ({snaps})",
                BROKEN,
            ),
            # latest -> /dev/null is only acceptable when an upstream sync
            # will populate it (and in a dry-run, it will not).
            guard(
                f'{devnull} || [ "$NBKP_DRY_RUN" = false ]',
                "source snapshot not yet available (dry-run; upstream has not run)",
                DRY_RUN_PENDING,
            )
            if spec.has_upstream
            else guard(
                devnull,
                f"source {LATEST_LINK} -> /dev/null with no upstream sync",
                BROKEN,
            ),
        ]


def _destination_snapshot_checks(spec: SyncSpec) -> list[str]:
    match spec.dst_ep.snapshot_mode:
        case "btrfs":
            return [
                *_tool_checks(_BTRFS_TOOLS, spec),
                *_btrfs_checks(spec),
                *_snapshot_dir_checks(spec),
                *_latest_checks(
                    spec.dst_vol,
                    spec.dst_path,
                    "destination",
                    "NBKP_DST_LATEST",
                    spec,
                ),
                _latest_readonly_warning(spec),
            ]
        case "hard-link":
            return [
                *_tool_checks(_HARD_LINK_TOOLS, spec),
                *_hardlink_support_checks(spec),
                *_snapshot_dir_checks(spec),
                *_latest_checks(
                    spec.dst_vol,
                    spec.dst_path,
                    "destination",
                    "NBKP_DST_LATEST",
                    spec,
                ),
            ]
        case _:
            return []


def _btrfs_checks(spec: SyncSpec) -> list[str]:
    vol, resolved = spec.dst_vol, spec.resolved_endpoints
    staging = spec.staging_dir
    fs_type = output_of(vol, ["stat", "-f", "-c", "%T", spec.dst_vol_path], resolved)
    options = output_of(
        vol,
        ["findmnt", "-T", spec.dst_vol_path, "-n", "-o", "OPTIONS"],
        resolved,
    )
    inode = output_of(vol, ["stat", "-c", "%i", staging], resolved)
    return [
        guard(
            f"[ {fs_type} = btrfs ]",
            f"destination volume {spec.dst_vol_path} not on btrfs filesystem",
            BROKEN,
        ),
        guard(
            f"nbkp_contains {options} user_subvol_rm_allowed",
            f"destination volume {spec.dst_vol_path} not mounted with"
            " user_subvol_rm_allowed",
            BROKEN,
        ),
        guard(
            _test(vol, ["-d", staging], spec),
            f"destination {STAGING_DIR}/ directory not found ({staging})",
            BROKEN,
        ),
        guard(
            _test(vol, ["-w", staging], spec),
            f"destination {STAGING_DIR}/ directory not writable ({staging})",
            BROKEN,
        ),
        # A btrfs subvolume root always has inode number 256.
        guard(
            f"[ {inode} = 256 ]",
            f"destination {STAGING_DIR}/ is not a btrfs subvolume ({staging})",
            BROKEN,
        ),
    ]


def _latest_readonly_warning(spec: SyncSpec) -> str:
    """
    Warn when the snapshot ``latest`` points to has lost its read-only flag.

    Without failing, as ``nbkp preflight`` does.
    """
    snapshot = f"{spec.dst_path}/{shell_var('NBKP_DST_LATEST')}"
    ro = output_of(
        spec.dst_vol,
        ["btrfs", "property", "get", snapshot, "ro"],
        spec.resolved_endpoints,
    )
    message = quote(
        f"WARN: the snapshot {LATEST_LINK} points to is not read-only ({snapshot})",
    )
    return (
        f'if [ "$NBKP_DST_LATEST" != /dev/null ] && [ {ro} = ro=false ]; then'
        f" nbkp_log {message}; fi"
    )


def _hardlink_support_checks(spec: SyncSpec) -> list[str]:
    """
    Reject FAT/exFAT, and a filesystem whose type cannot be read (as in run).

    BSD ``stat`` cannot print the type, so a local volume under macOS reads it
    with ``nbkp_macos_fs_type`` (``df`` and ``mount``), as run's preflight does.
    """
    fs_type = (
        f'"$(nbkp_macos_fs_type {quote(spec.dst_vol_path)})"'
        if isinstance(spec.dst_vol, LocalVolume) and spec.platform == "darwin"
        else output_of(
            spec.dst_vol,
            ["stat", "-f", "-c", "%T", spec.dst_vol_path],
            spec.resolved_endpoints,
        )
    )
    rejected = "|".join(NO_HARDLINK_FILESYSTEMS)
    return [
        f"NBKP_DST_FS_TYPE={fs_type}",
        guard(
            '[ -n "$NBKP_DST_FS_TYPE" ]',
            "could not determine the filesystem type of destination volume"
            f" {spec.dst_vol_path}",
            BROKEN,
        ),
        guard(
            f"! nbkp_matches \"$NBKP_DST_FS_TYPE\" '{rejected}'",
            f"destination volume {spec.dst_vol_path} does not support hard links",
            BROKEN,
        ),
    ]


def _snapshot_dir_checks(spec: SyncSpec) -> list[str]:
    snaps = spec.snapshots_dir
    return [
        guard(
            _test(spec.dst_vol, ["-d", snaps], spec),
            f"destination {SNAPSHOTS_DIR}/ directory not found ({snaps})",
            BROKEN,
        ),
        guard(
            _test(spec.dst_vol, ["-w", snaps], spec),
            f"destination {SNAPSHOTS_DIR}/ directory not writable ({snaps})",
            BROKEN,
        ),
    ]
