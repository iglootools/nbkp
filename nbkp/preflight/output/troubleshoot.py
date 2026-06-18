"""Troubleshoot output: per-error remediation instructions for all 4 layers."""

from __future__ import annotations

import getpass
from textwrap import dedent

from rich.console import Console
from rich.padding import Padding
from rich.syntax import Syntax

from ...config import (
    Config,
    LocalVolume,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
)
from ...config.epresolution import ResolvedEndpoints
from ...config.output import (
    endpoint_path,
    host_label,
)
from ...disks.auth import generate_auth_rules
from ...disks.udisks import cleartext_mapper_name
from ...remote.ssh import (
    format_proxy_jump_chain,
    ssh_prefix,
    wrap_cmd,
)
from ...fsprotocol import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from ..status import (
    DestinationEndpointError,
    SourceEndpointError,
    SshEndpointError,
    SshEndpointStatus,
    SyncError,
    SyncStatus,
    VolumeError,
    VolumeStatus,
)


# Cascade errors are pointers to inactive lower layers — they have no
# actionable fix at their own layer, so troubleshoot skips them.
_CASCADE_VOLUME_ERRORS: frozenset[VolumeError] = frozenset(
    {VolumeError.SSH_ENDPOINT_INACTIVE}
)
_CASCADE_SRC_EP_ERRORS: frozenset[SourceEndpointError] = frozenset(
    {SourceEndpointError.VOLUME_INACTIVE}
)
_CASCADE_DST_EP_ERRORS: frozenset[DestinationEndpointError] = frozenset(
    {DestinationEndpointError.VOLUME_INACTIVE}
)
_CASCADE_SYNC_ERRORS: frozenset[SyncError] = frozenset(
    {SyncError.SOURCE_ENDPOINT_INACTIVE, SyncError.DESTINATION_ENDPOINT_INACTIVE}
)

_INDENT = "  "

_RSYNC_INSTALL = dedent("""\
    Ubuntu/Debian: sudo apt install rsync
    Fedora/RHEL:   sudo dnf install rsync
    macOS:         brew install rsync""")

_BTRFS_INSTALL = dedent("""\
    Ubuntu/Debian: sudo apt install btrfs-progs
    Fedora/RHEL:   sudo dnf install btrfs-progs""")

_COREUTILS_INSTALL = dedent("""\
    Ubuntu/Debian: sudo apt install coreutils
    Fedora/RHEL:   sudo dnf install coreutils""")

_UTIL_LINUX_INSTALL = dedent("""\
    Ubuntu/Debian: sudo apt install util-linux
    Fedora/RHEL:   sudo dnf install util-linux""")


def _print_cmd(
    console: Console,
    cmd: str,
    indent: int = 2,
) -> None:
    """Print a shell command with bash syntax highlighting.

    ``indent`` is the number of ``_INDENT`` levels (each 2 spaces).
    """
    syntax = Syntax(
        cmd,
        "bash",
        theme="monokai",
        background_color="default",
    )
    pad = len(_INDENT) * indent
    console.print(Padding(syntax, (0, 0, 0, pad)))


def _print_sentinel_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    path: str,
    sentinel: str,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print sentinel creation fix with mount reminder."""
    p2 = _INDENT * 2
    console.print(f"{p2}Ensure the volume is mounted, then:")
    _print_cmd(
        console,
        wrap_cmd(f"mkdir -p {path}", vol, resolved_endpoints),
    )
    _print_cmd(
        console,
        wrap_cmd(
            f"touch {path}/{sentinel}",
            vol,
            resolved_endpoints,
        ),
    )


def _print_ssh_troubleshoot(
    console: Console,
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> None:
    """Print SSH connectivity troubleshooting instructions."""
    p2 = _INDENT * 2
    p3 = _INDENT * 3
    ssh_cmd = " ".join(ssh_prefix(server, proxy_chain))
    port_flag = f"-p {server.port} " if server.port != 22 else ""
    proxy_opt = ""
    if proxy_chain:
        jump_str = format_proxy_jump_chain(proxy_chain)
        proxy_opt = f"-o ProxyJump={jump_str} "
    user_host = f"{server.user}@{server.host}" if server.user else server.host
    console.print(f"{p2}Server {server.host} is unreachable.")
    console.print(f"{p2}Verify connectivity:")
    _print_cmd(console, f"{ssh_cmd} echo ok", indent=3)
    console.print(f"{p2}If authentication fails:")
    if server.key:
        console.print(f"{p3}1. Ensure the key exists:")
        _print_cmd(console, f"ls -l {server.key}", indent=4)
        console.print(f"{p3}2. Copy it to the server:")
        _print_cmd(
            console,
            f"ssh-copy-id {proxy_opt}{port_flag}-i {server.key} {user_host}",
            indent=4,
        )
    else:
        console.print(f"{p3}1. Generate a key:")
        _print_cmd(console, "ssh-keygen -t ed25519", indent=4)
        console.print(f"{p3}2. Copy it to the server:")
        _print_cmd(
            console,
            f"ssh-copy-id {proxy_opt}{port_flag}{user_host}",
            indent=4,
        )
    console.print(f"{p3}3. Verify passwordless login:")
    _print_cmd(console, f"{ssh_cmd} echo ok", indent=4)


# ── Per-layer troubleshoot fix functions ──────────────────────


def _print_ssh_endpoint_error_fix(
    console: Console,
    ssh_status: SshEndpointStatus,
    error: SshEndpointError,
    config: Config,
) -> None:
    """Print fix instructions for an SSH endpoint error."""
    p2 = _INDENT * 2
    slug = ssh_status.slug
    # Try to find a matching SshEndpoint config for troubleshooting
    server = config.ssh_endpoints.get(slug)
    match error:
        case SshEndpointError.UNREACHABLE:
            if server is not None:
                proxy_chain = (
                    [config.ssh_endpoints[s] for s in server.proxy_jump_chain]
                    if server.proxy_jump_chain
                    else None
                )
                _print_ssh_troubleshoot(console, server, proxy_chain)
            else:
                console.print(f"{p2}SSH endpoint is unreachable.")
        case SshEndpointError.LOCATION_EXCLUDED:
            console.print(
                f"{p2}All SSH endpoints for volumes on this"
                " host are at an excluded location."
                " Remove --exclude-location or add an"
                " endpoint at a different location."
            )
        case SshEndpointError.RSYNC_NOT_FOUND:
            console.print(f"{p2}Install rsync on {slug}:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SshEndpointError.RSYNC_TOO_OLD:
            console.print(f"{p2}rsync 3.0+ is required on {slug}. Install or upgrade:")
            _print_cmd(console, _RSYNC_INSTALL, indent=3)
        case SshEndpointError.BTRFS_NOT_FOUND:
            console.print(f"{p2}Install btrfs-progs on {slug}:")
            _print_cmd(console, _BTRFS_INSTALL, indent=3)
        case SshEndpointError.STAT_NOT_FOUND:
            console.print(f"{p2}Install coreutils (stat) on {slug}:")
            _print_cmd(console, _COREUTILS_INSTALL, indent=3)
        case SshEndpointError.FINDMNT_NOT_FOUND:
            console.print(f"{p2}Install util-linux (findmnt) on {slug}:")
            _print_cmd(console, _UTIL_LINUX_INSTALL, indent=3)
        case SshEndpointError.UDISKSCTL_NOT_FOUND:
            _print_mount_error(
                console,
                f"udisksctl not found on {slug}.",
                "Mount management uses udisks2 (udisksctl).\n"
                "Install: sudo apt install udisks2\n"
                "(add udisks2-btrfs for btrfs volumes)\n"
                "Check: which udisksctl",
            )
        case SshEndpointError.UDISKSD_NOT_RUNNING:
            _print_mount_error(
                console,
                f"udisksd (the udisks2 daemon) is not running on {slug}.",
                "Mount management talks to udisksd over D-Bus.\n"
                "Start it: sudo systemctl enable --now udisks2\n"
                "On headless hosts ensure dbus and udisksd are up\n"
                "Check: systemctl status udisks2",
            )
        case SshEndpointError.LSBLK_NOT_FOUND:
            _print_mount_error(
                console,
                f"lsblk not found on {slug}.",
                "Install: sudo apt install util-linux\nCheck: which lsblk",
            )
        case SshEndpointError.UDISKS_BTRFS_MODULE_MISSING:
            _print_mount_error(
                console,
                f"udisks2 btrfs module not installed on {slug}.",
                "Required to mount btrfs volumes via udisks.\n"
                "Install: sudo apt install udisks2 udisks2-btrfs\n"
                "Then restart udisks2: sudo systemctl restart udisks2",
            )


def _print_volume_error_fix(
    console: Console,
    vol_status: VolumeStatus,
    error: VolumeError,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix instructions for a volume error."""
    vol = vol_status.config
    match error:
        case VolumeError.SENTINEL_NOT_FOUND:
            _print_sentinel_fix(
                console,
                vol,
                vol.path or "<volume-path>",
                VOLUME_SENTINEL,
                resolved_endpoints,
            )
        case VolumeError.VOLUME_NOT_MOUNTED:
            _print_mount_error(
                console,
                "Volume is not mounted.",
                f"Mount the volume with: nbkp disks mount -n {vol_status.slug}",
            )
        case VolumeError.DEVICE_NOT_PRESENT:
            _print_device_not_present_fix(console, vol.mount)
        case VolumeError.FSTAB_MOUNTPOINT_MISMATCH:
            _print_fstab_mountpoint_mismatch_fix(
                console,
                vol,
                resolved_endpoints,
            )
        case VolumeError.POLKIT_RULES_MISSING:
            _print_polkit_rules_missing_fix(
                console,
                vol,
                config,
                resolved_endpoints,
            )
        case VolumeError.PASSPHRASE_NOT_AVAILABLE:
            _print_passphrase_not_available_fix(console, vol.mount)
        case VolumeError.UNLOCK_FAILED:
            _print_unlock_failed_fix(console, vol.mount)
        case VolumeError.MOUNT_FAILED:
            _print_mount_failed_fix(console, vol, vol.mount, resolved_endpoints)


def _print_source_endpoint_error_fix(
    console: Console,
    error: SourceEndpointError,
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix instructions for a source endpoint error."""
    p2 = _INDENT * 2
    src_ep = config.source_endpoint(sync)
    src_vol = config.volumes[src_ep.volume]
    match error:
        case SourceEndpointError.SENTINEL_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            _print_sentinel_fix(
                console,
                src_vol,
                path,
                SOURCE_SENTINEL,
                resolved_endpoints,
            )
        case SourceEndpointError.LATEST_SYMLINK_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            console.print(
                f"{p2}Source has snapshots enabled"
                f" but {path}/{LATEST_LINK} symlink"
                " does not exist. Create it:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"ln -sfn /dev/null {path}/{LATEST_LINK}",
                    src_vol,
                    resolved_endpoints,
                ),
            )
        case SourceEndpointError.LATEST_SYMLINK_INVALID:
            path = endpoint_path(src_vol, src_ep.subdir)
            console.print(
                f"{p2}Source {path}/{LATEST_LINK}"
                " symlink points to an invalid"
                " target. Ensure the upstream"
                " sync has run at least once,"
                " or reset it:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"ln -sfn /dev/null {path}/{LATEST_LINK}",
                    src_vol,
                    resolved_endpoints,
                ),
            )
        case SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND:
            path = endpoint_path(src_vol, src_ep.subdir)
            # Endpoint dir is expected to be user-writable by this
            # point (fixed via NOT_WRITABLE if needed), so a plain
            # mkdir suffices regardless of snapshot backend.
            _print_cmd(
                console,
                wrap_cmd(
                    f"mkdir -p {path}/{SNAPSHOTS_DIR}", src_vol, resolved_endpoints
                ),
            )


def _print_destination_endpoint_error_fix(
    console: Console,
    error: DestinationEndpointError,
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix instructions for a destination endpoint error."""
    p2 = _INDENT * 2
    dst_ep = config.destination_endpoint(sync)
    dst_vol = config.volumes[dst_ep.volume]
    match error:
        case DestinationEndpointError.SENTINEL_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            _print_sentinel_fix(
                console,
                dst_vol,
                path,
                DESTINATION_SENTINEL,
                resolved_endpoints,
            )
        case DestinationEndpointError.NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination endpoint directory"
                f" {path}/ is not writable. Fix permissions:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"sudo chown <user>:<group> {path}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            cmds = [
                f"sudo btrfs subvolume create {path}/{STAGING_DIR}",
                f"sudo mkdir {path}/{SNAPSHOTS_DIR}",
                "sudo chown <user>:<group>"
                f" {path}/{STAGING_DIR}"
                f" {path}/{SNAPSHOTS_DIR}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
                )
        case DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            # Endpoint dir is expected to be user-writable by this
            # point (fixed via NOT_WRITABLE if needed), so subvolume
            # create runs without sudo (kernel 5.8+).
            _print_cmd(
                console,
                wrap_cmd(
                    f"btrfs subvolume create {path}/{STAGING_DIR}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case DestinationEndpointError.STAGING_SUBVOL_NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination {STAGING_DIR}/"
                f" directory ({path}/{STAGING_DIR})"
                " is not writable. Fix permissions:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"sudo chown <user>:<group> {path}/{STAGING_DIR}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            # Endpoint dir is expected to be user-writable by this
            # point (fixed via NOT_WRITABLE if needed), so a plain
            # mkdir suffices regardless of snapshot backend.
            _print_cmd(
                console,
                wrap_cmd(
                    f"mkdir -p {path}/{SNAPSHOTS_DIR}", dst_vol, resolved_endpoints
                ),
            )
        case DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}The destination {SNAPSHOTS_DIR}/"
                f" directory ({path}/{SNAPSHOTS_DIR})"
                " is not writable. Fix permissions:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"sudo chown <user>:<group> {path}/{SNAPSHOTS_DIR}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}Destination has snapshots enabled"
                f" but {path}/{LATEST_LINK} symlink"
                " does not exist. Create it:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"ln -sfn /dev/null {path}/{LATEST_LINK}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case DestinationEndpointError.LATEST_SYMLINK_INVALID:
            path = endpoint_path(dst_vol, dst_ep.subdir)
            console.print(
                f"{p2}Destination {path}/{LATEST_LINK}"
                " symlink points to an invalid"
                " target. Reset it:"
            )
            _print_cmd(
                console,
                wrap_cmd(
                    f"ln -sfn /dev/null {path}/{LATEST_LINK}",
                    dst_vol,
                    resolved_endpoints,
                ),
            )
        case DestinationEndpointError.VOL_NOT_BTRFS:
            console.print(f"{p2}The destination is not on a btrfs filesystem.")
        case DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM:
            _print_user_subvol_rm_fix(console, dst_vol, resolved_endpoints)
        case DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT:
            console.print(
                f"{p2}The destination filesystem does not"
                " support hard links (e.g. FAT/exFAT)."
                " Use a filesystem like ext4, xfs, or"
                " btrfs, or use btrfs-snapshots instead."
            )


def _print_sync_error_fix(
    console: Console,
    sync: SyncConfig,
    error: SyncError,
    config: Config,
) -> None:
    """Print fix instructions for a sync-level error."""
    p2 = _INDENT * 2
    match error:
        case SyncError.DISABLED:
            console.print(f"{p2}Enable the sync in the configuration file.")
        case SyncError.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM:
            src_ep = config.source_endpoint(sync)
            src_vol = config.volumes[src_ep.volume]
            path = endpoint_path(src_vol, src_ep.subdir)
            console.print(
                f"{p2}Source {path}/{LATEST_LINK}"
                " points to /dev/null but there is"
                " no upstream sync that writes to"
                " this endpoint. Either run the"
                " upstream sync first or reset the"
                " symlink to point to a valid snapshot."
            )
        case SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING:
            console.print(
                f"{p2}The source endpoint's latest symlink"
                " points to /dev/null (no snapshot yet)."
                " In dry-run mode, the upstream sync does"
                " not create a real snapshot, so this sync"
                " is skipped. Run without --dry-run to"
                " execute the full chain."
            )


# ── Mount-specific fix helpers ────────────────────────────────


def _print_mount_error(
    console: Console,
    title: str,
    details: str,
) -> None:
    """Print a mount-related error with indented details."""
    p2 = _INDENT * 2
    console.print(f"{p2}{title}")
    for line in details.splitlines():
        console.print(f"{p2}{_INDENT}{line}")


def _print_device_not_present_fix(
    console: Console,
    mount: "MountConfig | None",
) -> None:
    """Print fix for device not plugged in."""
    p2 = _INDENT * 2
    uuid = mount.device_uuid if mount else "<uuid>"
    console.print(f"{p2}Plug in the drive and verify:")
    _print_cmd(console, f"ls -la /dev/disk/by-uuid/{uuid}")
    console.print(f"{p2}Or with systemd:")
    _print_cmd(console, f"udevadm info /dev/disk/by-uuid/{uuid}")


def _print_fstab_mountpoint_mismatch_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for a declared path with no matching fstab entry.

    The volume declares a fixed ``path`` but no ``/etc/fstab`` entry maps the
    device to that path, so udisks would mount it at its own
    ``/run/media/<user>/<label>`` location instead.  Two remediations:
    add an fstab entry, or drop ``path`` to accept udisks's mountpoint.
    """
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    path = vol.path or "<volume-path>"
    console.print(f"{p2}No /etc/fstab entry maps the device to {path} on {host}.")
    console.print(
        f"{p2}With a fixed 'path', udisks must mount the device there; without"
        " a matching fstab entry it would mount at /run/media/<user>/<label>."
    )
    console.print(f"{p2}Option A — add an /etc/fstab entry (no crypttab needed):")
    if mount and mount.encryption:
        mapper = cleartext_mapper_name(mount.device_uuid)
        _print_cmd(
            console,
            f"/dev/mapper/{mapper}  {path}  <FS>  noauto,nofail,x-udisks-auth  0 0",
            indent=3,
        )
    else:
        uuid = mount.device_uuid if mount else "<fs-uuid>"
        _print_cmd(
            console,
            f"UUID={uuid}  {path}  <FS>  noauto,nofail  0 0",
            indent=3,
        )
    console.print(
        f"{p2}{_INDENT}Replace <FS> with the volume's filesystem type"
        " (e.g. btrfs, ext4); for btrfs also add user_subvol_rm_allowed"
        " to the options."
    )
    console.print(
        f"{p2}Option B — remove 'path' from the volume config to use the"
        " mountpoint udisks discovers (/run/media/<user>/<label>)."
    )


def _print_user_subvol_rm_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for a btrfs volume not mounted with user_subvol_rm_allowed.

    The option is required for snapshot pruning.  nbkp does not pass it to
    udisks at mount time — udisks rejects any non-allowlisted mount option
    (``OptionNotPermitted``), which would fail the mount — so the option must
    come from operator config: ``/etc/fstab`` (udisks honors fstab verbatim) or,
    for the discovered ``/run/media`` model, the udisks mount-options allowlist.
    """
    p2 = _INDENT * 2
    p3 = _INDENT * 3
    path = vol.path or "<volume-path>"
    mount = getattr(vol, "mount", None)

    console.print(
        f"{p2}The btrfs volume must be mounted with user_subvol_rm_allowed"
        " (needed for snapshot pruning).  Remount now (ephemeral):"
    )
    _print_cmd(
        console,
        wrap_cmd(
            f"sudo mount -o remount,user_subvol_rm_allowed {path}",
            vol,
            resolved_endpoints,
        ),
    )

    if mount is None:
        # Externally-mounted volume: fstab is the only persistence mechanism.
        console.print(
            f"{p2}To persist, add user_subvol_rm_allowed to the /etc/fstab"
            f" options for {path}."
        )
        return

    # udisks-managed volume: two persistence routes.
    console.print(f"{p2}To persist (udisks-managed volume), use ONE of:")
    if mount.encryption:
        device = f"/dev/mapper/{cleartext_mapper_name(mount.device_uuid)}"
    else:
        device = f"UUID={mount.device_uuid}"
    console.print(f"{p2}Option A — /etc/fstab (udisks honors fstab options):")
    _print_cmd(
        console,
        f"{device}  {path}  btrfs"
        "  noauto,nofail,x-udisks-auth,user_subvol_rm_allowed  0 0",
        indent=3,
    )
    console.print(
        f"{p2}Option B — /etc/udisks2/mount_options.conf, then restart udisksd"
        " (for the discovered /run/media mountpoint):"
    )
    _print_cmd(
        console,
        "[defaults]\nbtrfs_allow=user_subvol_rm_allowed\n"
        "btrfs_defaults=user_subvol_rm_allowed",
        indent=3,
    )
    console.print(
        f"{p3}Both keys are required: btrfs_allow permits the option,"
        " btrfs_defaults applies it.  Scope to one device with a"
        " [/dev/disk/by-uuid/<uuid>] section (the unlocked cleartext"
        " device for encrypted volumes); see man udisks2.conf.",
        markup=False,
    )


def _print_passphrase_not_available_fix(
    console: Console,
    mount: "MountConfig | None",
) -> None:
    """Print fix for passphrase not available."""

    p2 = _INDENT * 2
    pid = (
        mount.encryption.passphrase_id
        if mount and mount.encryption
        else "<passphrase-id>"
    )
    env_var = f"NBKP_PASSPHRASE_{pid.upper().replace('-', '_')}"
    console.print(f"{p2}Check credential status: nbkp credentials keyring-status")
    console.print(f"{p2}Configure with your credential provider:")
    console.print(f"{p2}{_INDENT}keyring: keyring set nbkp {pid}")
    console.print(f"{p2}{_INDENT}env: export {env_var}=...")
    console.print(f"{p2}{_INDENT}command: ensure <credential-command> works")


def _print_unlock_failed_fix(
    console: Console,
    mount: "MountConfig | None",
) -> None:
    """Print fix for a failed LUKS unlock via udisks."""
    p2 = _INDENT * 2
    uuid = mount.device_uuid if mount else "<uuid>"
    pid = (
        mount.encryption.passphrase_id
        if mount and mount.encryption
        else "<passphrase-id>"
    )
    console.print(f"{p2}udisksctl failed to unlock the LUKS container.")
    console.print(
        f"{p2}Verify the passphrase from your credential provider"
        f" (passphrase-id '{pid}') is correct: nbkp credentials keyring-status"
    )
    console.print(f"{p2}Confirm the device is a LUKS container:")
    _print_cmd(console, f"sudo cryptsetup isLuks /dev/disk/by-uuid/{uuid}", indent=3)
    console.print(f"{p2}Try unlocking manually to see the error:")
    _print_cmd(
        console,
        f"udisksctl unlock -b /dev/disk/by-uuid/{uuid}",
        indent=3,
    )


def _print_mount_failed_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    mount: "MountConfig | None",
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for a failed udisks mount."""
    p2 = _INDENT * 2
    if mount and mount.encryption:
        device = f"/dev/mapper/{cleartext_mapper_name(mount.device_uuid)}"
    elif mount:
        device = f"/dev/disk/by-uuid/{mount.device_uuid}"
    else:
        device = "<device>"
    console.print(f"{p2}udisksctl failed to mount the volume.")
    console.print(
        f"{p2}Check the filesystem and, for a fixed 'path', that an /etc/fstab"
        " entry maps the device there (otherwise udisks mounts at"
        " /run/media/<user>/<label>)."
    )
    console.print(
        f"{p2}Ensure the polkit rule is installed (see polkit rules not"
        " configured); without it udisks denies the mount over SSH."
    )
    console.print(f"{p2}Try mounting manually to see the error:")
    _print_cmd(
        console,
        wrap_cmd(
            f"udisksctl mount -b {device}",
            vol,
            resolved_endpoints,
        ),
        indent=3,
    )


def _resolve_volume_user(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Resolve the system user for auth rules on a volume's host.

    For remote volumes, uses the SSH endpoint user. For local volumes
    or when the SSH user is unset, falls back to the current OS user.
    """
    match vol:
        case RemoteVolume():
            ep = resolved_endpoints.get(vol.slug)
            if ep and ep.server.user:
                return ep.server.user
            else:
                return getpass.getuser()
        case LocalVolume():
            return getpass.getuser()


def _print_polkit_rules_missing_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for missing polkit rules, including generated content."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    user = _resolve_volume_user(vol, resolved_endpoints)
    block = generate_auth_rules(config, user).polkit_block()
    console.print(f"{p2}polkit rules not configured on {host}.")
    console.print(
        f"{p2}Required so udisks authorizes unlock/mount/unmount/lock without"
        " an interactive prompt (nbkp runs over SSH / in inactive sessions)."
    )
    if block is not None:
        console.print(f"{p2}{block.install_hint}")
        _print_cmd(console, block.content.rstrip(), indent=3)
    console.print(f"{p2}Or generate with: nbkp disks setup-auth -c <config>")


# ── Main troubleshoot entry point ─────────────────────────────


def print_human_troubleshoot(
    ssh_statuses: dict[str, SshEndpointStatus],
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    config: Config,
    *,
    console: Console | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> None:
    """Print troubleshooting instructions for all 4 layers.

    Iterates through SSH endpoints, volumes, sync endpoints (source
    and destination), and syncs, printing fix instructions for each
    error at the layer where it originates.
    """
    re = resolved_endpoints or {}
    if console is None:
        console = Console()

    has_issues = False

    # ── Layer 1: SSH Endpoints ────────────────────────────────
    failed_ssh = [s for s in ssh_statuses.values() if s.errors]
    for ssh_st in failed_ssh:
        has_issues = True
        console.print(f"\n[bold]SSH Endpoint {ssh_st.slug!r}:[/bold]")
        for error in ssh_st.errors:
            console.print(f"{_INDENT}{error.value}")
            _print_ssh_endpoint_error_fix(console, ssh_st, error, config)

    # ── Layer 2: Volumes ──────────────────────────────────────
    failed_vols = [vs for vs in vol_statuses.values() if vs.errors]
    for vs in failed_vols:
        actionable = [e for e in vs.errors if e not in _CASCADE_VOLUME_ERRORS]
        if not actionable:
            continue
        has_issues = True
        console.print(f"\n[bold]Volume {vs.slug!r}:[/bold]")
        for error in actionable:
            console.print(f"{_INDENT}{error.value}")
            _print_volume_error_fix(console, vs, error, config, re)

    # ── Layer 3: Sync Endpoints ───────────────────────────────
    # Collect unique source and destination endpoint statuses from syncs
    seen_src_eps: set[str] = set()
    seen_dst_eps: set[str] = set()
    for ss in sync_statuses.values():
        src_ep = ss.source_endpoint_status
        if src_ep.endpoint_slug not in seen_src_eps and src_ep.errors:
            actionable_src = [
                e for e in src_ep.errors if e not in _CASCADE_SRC_EP_ERRORS
            ]
            seen_src_eps.add(src_ep.endpoint_slug)
            if actionable_src:
                has_issues = True
                console.print(
                    f"\n[bold]Source Endpoint {src_ep.endpoint_slug!r}:[/bold]"
                )
                for error in actionable_src:
                    console.print(f"{_INDENT}{error.value}")
                    _print_source_endpoint_error_fix(
                        console,
                        error,
                        ss.config,
                        config,
                        re,
                    )

        dst_ep = ss.destination_endpoint_status
        if dst_ep.endpoint_slug not in seen_dst_eps and dst_ep.errors:
            actionable_dst = [
                e for e in dst_ep.errors if e not in _CASCADE_DST_EP_ERRORS
            ]
            seen_dst_eps.add(dst_ep.endpoint_slug)
            if actionable_dst:
                has_issues = True
                console.print(
                    f"\n[bold]Destination Endpoint {dst_ep.endpoint_slug!r}:[/bold]"
                )
                for error in actionable_dst:
                    console.print(f"{_INDENT}{error.value}")
                    _print_destination_endpoint_error_fix(
                        console,
                        error,
                        ss.config,
                        config,
                        re,
                    )

    # ── Layer 4: Syncs ────────────────────────────────────────
    failed_syncs = [ss for ss in sync_statuses.values() if ss.errors]
    for ss in failed_syncs:
        actionable_sync = [e for e in ss.errors if e not in _CASCADE_SYNC_ERRORS]
        if not actionable_sync:
            continue
        has_issues = True
        console.print(f"\n[bold]Sync {ss.slug!r}:[/bold]")
        for sync_error in actionable_sync:
            console.print(f"{_INDENT}{sync_error.value}")
            _print_sync_error_fix(console, ss.config, sync_error, config)

    if not has_issues:
        console.print("No issues found. All volumes and syncs are active.")
