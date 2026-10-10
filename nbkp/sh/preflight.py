"""Generated preflight checks, mirroring ``nbkp preflight``.

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

from __future__ import annotations

from ..config import LocalVolume, RemoteVolume
from ..fsprotocol import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from .commands import (
    BROKEN,
    DRY_RUN_PENDING,
    INACTIVE,
    guard,
    output_of,
    vol_cmd,
    which_cmd,
)
from .spec import SyncSpec

# Mirrors nbkp.preflight.snapshot_checks._NO_HARDLINK_FILESYSTEMS
_NO_HARDLINK_FILESYSTEMS = ("vfat", "msdos", "exfat")
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
            _test(vol, ["-f", sentinel], spec),
            f"{role} sentinel {sentinel} not found (or host unreachable)",
            INACTIVE,
        )
        for vol, sentinel, role in checks
    ]


def _rsync_checks(
    vol: LocalVolume | RemoteVolume, role: str, spec: SyncSpec
) -> list[str]:
    re = spec.resolved_endpoints
    version = output_of(vol, ["rsync", "--version"], re)
    return [
        guard(which_cmd(vol, "rsync", re), f"rsync not found on {role}", BROKEN),
        guard(
            f"nbkp_rsync_version_ok {version}",
            f"rsync on {role} is too old or openrsync (GNU rsync 3.0+ required)",
            BROKEN,
        ),
    ]


def _tool_checks(tools: tuple[str, ...], spec: SyncSpec) -> list[str]:
    re = spec.resolved_endpoints
    return [
        guard(
            which_cmd(spec.dst_vol, tool, re),
            f"{tool} not found on destination",
            BROKEN,
        )
        for tool in tools
    ]


def _latest_checks(
    vol: LocalVolume | RemoteVolume, path: str, role: str, var: str, spec: SyncSpec
) -> list[str]:
    """``latest`` must be a symlink to ``/dev/null`` or to a snapshot dir.

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
            f'[ "${var}" = /dev/null ] || {_test(vol, ["-d", f"{latest}/"], spec)}',
            f"{role} {LATEST_LINK} symlink target is invalid ({latest})",
            BROKEN,
        ),
    ]


def _source_snapshot_checks(spec: SyncSpec) -> list[str]:
    """A snapshot-enabled source is read through its ``latest`` symlink."""
    if spec.src_ep.snapshot_mode == "none":
        return []
    else:
        snaps = f"{spec.src_path}/{SNAPSHOTS_DIR}"
        devnull = '[ "$NBKP_SRC_LATEST" != /dev/null ]'
        return [
            *_latest_checks(
                spec.src_vol, spec.src_path, "source", "NBKP_SRC_LATEST", spec
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
                    spec.dst_vol, spec.dst_path, "destination", "NBKP_DST_LATEST", spec
                ),
            ]
        case "hard-link":
            return [
                *_tool_checks(_HARD_LINK_TOOLS, spec),
                _hardlink_support_check(spec),
                *_snapshot_dir_checks(spec),
                *_latest_checks(
                    spec.dst_vol, spec.dst_path, "destination", "NBKP_DST_LATEST", spec
                ),
            ]
        case _:
            return []


def _btrfs_checks(spec: SyncSpec) -> list[str]:
    vol, re = spec.dst_vol, spec.resolved_endpoints
    staging = spec.staging_dir
    fs_type = output_of(vol, ["stat", "-f", "-c", "%T", spec.dst_vol_path], re)
    options = output_of(
        vol, ["findmnt", "-T", spec.dst_vol_path, "-n", "-o", "OPTIONS"], re
    )
    inode = output_of(vol, ["stat", "-c", "%i", staging], re)
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


def _hardlink_support_check(spec: SyncSpec) -> str:
    """Reject FAT/exFAT; an undeterminable type is assumed fine (as in run)."""
    fs_type = output_of(
        spec.dst_vol,
        ["stat", "-f", "-c", "%T", spec.dst_vol_path],
        spec.resolved_endpoints,
    )
    rejected = "|".join(_NO_HARDLINK_FILESYSTEMS)
    return guard(
        f"! nbkp_matches {fs_type} '{rejected}'",
        f"destination volume {spec.dst_vol_path} does not support hard links",
        BROKEN,
    )


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
