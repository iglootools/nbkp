from ..config import Volume, resolve_endpoint_path
from ..fsprotocol import NO_HARDLINK_FILESYSTEMS
from ..remote.dispatch import VolumeCommandRunner, run_on_volume
from ..remote.endpoints import ResolvedEndpoints


def probe_filesystem_type(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> str | None:
    """The type of the filesystem holding the volume root (``stat -f``'s
    ``%T``), or ``None`` when ``stat`` fails.

    Probed once per volume: both the btrfs check and the hard-link check
    derive from it, and on a remote volume each probe is an SSH round-trip.
    """
    result = run(
        ["stat", "-f", "-c", "%T", resolve_endpoint_path(volume, None)],
        volume,
        resolved_endpoints,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def is_btrfs_filesystem(fs_type: str | None) -> bool | None:
    """``None`` when the filesystem type is unknown, which is not the same
    as "not btrfs" (that would send the operator off to reformat a volume
    that may be fine).
    """
    return fs_type == "btrfs" if fs_type is not None else None


def supports_hard_links(fs_type: str | None) -> bool:
    """An unknown filesystem type is assumed to support hard links."""
    return fs_type not in NO_HARDLINK_FILESYSTEMS


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


def check_btrfs_readonly(
    volume: Volume,
    path: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
    result = run(["btrfs", "property", "get", path, "ro"], volume, resolved_endpoints)
    return result.returncode == 0 and "ro=true" in result.stdout


def check_btrfs_mount_option(
    volume: Volume,
    option: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> bool:
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
