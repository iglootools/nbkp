"""Probes of the filesystem features snapshots rely on: btrfs and hard links."""

import re

from nbkp.config import LocalVolume, Volume, resolve_endpoint_path
from nbkp.fsprotocol import NO_HARDLINK_FILESYSTEMS
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints


def probe_filesystem_type(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    platform: str,
    run: VolumeCommandRunner = run_on_volume,
) -> str | None:
    """
    Return the type of the filesystem holding the volume root, ``None`` when unknown.

    GNU ``stat -f``'s ``%T`` names it, but BSD ``stat`` has no such format: on a local
    volume under macOS (*platform* is ``sys.platform``), the type is read from the
    ``mount`` line of the mount point ``df`` reports instead (``apfs``, ``msdos``,
    ``exfat``). A remote host is taken to run GNU ``stat``, as it is for snapshot
    names; when it does not, the type is unknown and reported as such.

    Probed once per volume: both the btrfs check and the hard-link check
    derive from it, and on a remote volume each probe is an SSH round-trip.
    """
    path = resolve_endpoint_path(volume, None)
    if isinstance(volume, LocalVolume) and platform == "darwin":
        return _probe_macos_filesystem_type(volume, path, resolved_endpoints, run=run)
    result = run(["stat", "-f", "-c", "%T", path], volume, resolved_endpoints)
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def _probe_macos_filesystem_type(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> str | None:
    df = run(["df", "-P", path], volume, resolved_endpoints)
    mount_point = parse_df_mount_point(df.stdout) if df.returncode == 0 else None
    if mount_point is None:
        return None
    mounts = run(["mount"], volume, resolved_endpoints)
    return (
        parse_mount_filesystem_type(mounts.stdout, mount_point)
        if mounts.returncode == 0
        else None
    )


_DF_ROW = re.compile(r"^\S+\s+\d+\s+\d+\s+\d+\s+\d+%\s+(?P<mount_point>.+)$")


def parse_df_mount_point(output: str) -> str | None:
    """
    Return the mount point in ``df -P``'s output for a single path.

    The last column of the second line; it may hold spaces (``/Volumes/My Drive``).
    ``None`` when the output does not have that shape.
    """
    rows = output.splitlines()[1:2]
    match = _DF_ROW.match(rows[0]) if rows else None
    return match.group("mount_point") if match else None


def parse_mount_filesystem_type(output: str, mount_point: str) -> str | None:
    """
    Return the filesystem type of *mount_point* in macOS ``mount``'s output.

    Each line reads ``<device> on <mount point> (<type>, <option>, ...)``.
    """
    marker = f" on {mount_point} ("
    return next(
        (
            re.split(r"[,)]", line.split(marker, 1)[1], maxsplit=1)[0].strip() or None
            for line in output.splitlines()
            if marker in line
        ),
        None,
    )


def is_btrfs_filesystem(fs_type: str | None) -> bool | None:
    """
    Return whether *fs_type* is btrfs, ``None`` when it is unknown.

    Unknown is not the same as "not btrfs" (that would send the operator off to reformat
    a volume that may be fine).
    """
    return fs_type == "btrfs" if fs_type is not None else None


def supports_hard_links(fs_type: str | None) -> bool | None:
    """
    Return whether *fs_type* supports hard links, ``None`` when it is unknown.

    Unknown is not assumed fine: a hard-link sync onto FAT/exFAT would fail.
    """
    return fs_type not in NO_HARDLINK_FILESYSTEMS if fs_type is not None else None


def check_btrfs_subvolume(
    volume: Volume,
    subdir: str | None,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """On btrfs, subvolumes always have inode number 256."""
    path = resolve_endpoint_path(volume, subdir)
    result = run(["stat", "-c", "%i", path], volume, resolved_endpoints)
    return result.returncode == 0 and result.stdout.strip() == "256"


def probe_btrfs_readonly(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool | None:
    """
    Return whether the subvolume at *path* is read-only.

    ``btrfs property get`` prints ``ro=true`` or ``ro=false``; ``None`` when that cannot
    be told.
    """
    result = run(["btrfs", "property", "get", path, "ro"], volume, resolved_endpoints)
    match result.stdout.strip() if result.returncode == 0 else None:
        case "ro=true":
            return True
        case "ro=false":
            return False
        case _:
            return None


def check_btrfs_mount_option(
    volume: Volume,
    option: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    """
    Return whether the volume's filesystem is mounted with the option *option*.

    Reads the live mount options with ``findmnt``; ``False`` when it fails.
    """
    # This is the one mount query udisks2 cannot answer, which is why findmnt
    # stays the tool for all of them. udisks exposes mountpoints
    # (Filesystem.MountPoints) and *configured* fstab/crypttab options
    # (Block.Configuration), but NOT the live mount-option string of a mounted
    # filesystem. Worse, Block.Configuration only reflects fstab-supplied options,
    # so an option granted via /etc/udisks2/mount_options.conf would be invisible
    # there — breaking this check's route-agnostic guarantee (it must detect
    # user_subvol_rm_allowed however it was supplied: fstab OR mount_options.conf).
    # findmnt reads /proc/self/mountinfo (the kernel's live truth), so it sees the
    # option regardless of how it got applied. (See find_mountpoint and
    # _check_fstab_entry, which udisks *could* answer but use findmnt for symmetry.)
    result = run(
        ["findmnt", "-T", resolve_endpoint_path(volume, None), "-n", "-o", "OPTIONS"],
        volume,
        resolved_endpoints,
    )
    return result.returncode == 0 and option in result.stdout.strip().split(",")
