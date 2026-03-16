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
from ...mount.auth import POLKIT_RULES_PATH, SUDOERS_RULES_PATH, generate_auth_rules
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
    VolumeCapabilities,
    VolumeError,
    VolumeStatus,
)
from .formatting import collect_ssh_endpoint_statuses


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
        case SshEndpointError.SYSTEMCTL_NOT_FOUND:
            _print_mount_error(
                console,
                f"systemctl not found on {slug}.",
                "Mount management requires systemd."
                " Install systemd or disable mount"
                " config for volumes on this host.",
            )
        case SshEndpointError.SYSTEMD_ESCAPE_NOT_FOUND:
            _print_mount_error(
                console,
                f"systemd-escape not found on {slug}.",
                "Install: sudo apt install systemd"
                " (usually pre-installed)\n"
                "Check: which systemd-escape",
            )
        case SshEndpointError.SUDO_NOT_FOUND:
            _print_mount_error(
                console,
                f"sudo not found on {slug}.",
                "sudo is required for mount"
                " operations (cryptsetup, mount/umount).\n"
                "Install: apt install sudo\n"
                "Check: which sudo",
            )
        case SshEndpointError.CRYPTSETUP_NOT_FOUND:
            _print_mount_error(
                console,
                f"cryptsetup not found on {slug}.",
                "Install: sudo apt install cryptsetup\nCheck: which cryptsetup",
            )
        case SshEndpointError.SYSTEMD_CRYPTSETUP_NOT_FOUND:
            _print_mount_error(
                console,
                f"systemd-cryptsetup not found on {slug}.",
                "This binary is part of systemd but"
                " requires the cryptsetup package"
                " (libcryptsetup).\n"
                "Install: sudo apt install cryptsetup\n"
                "Check: ls /usr/lib/systemd/"
                "systemd-cryptsetup",
            )
        case SshEndpointError.MOUNT_CMD_NOT_FOUND:
            _print_mount_error(
                console,
                f"mount command not found on {slug}.",
                "Install: sudo apt install util-linux\nCheck: which mount",
            )
        case SshEndpointError.UMOUNT_CMD_NOT_FOUND:
            _print_mount_error(
                console,
                f"umount command not found on {slug}.",
                "Install: sudo apt install util-linux\nCheck: which umount",
            )
        case SshEndpointError.MOUNTPOINT_CMD_NOT_FOUND:
            _print_mount_error(
                console,
                f"mountpoint command not found on {slug}.",
                "Install: sudo apt install util-linux\nCheck: which mountpoint",
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
                vol.path,
                VOLUME_SENTINEL,
                resolved_endpoints,
            )
        case VolumeError.VOLUME_NOT_MOUNTED:
            _print_mount_error(
                console,
                "Volume is not mounted.",
                f"Mount the volume with: nbkp volumes mount -n {vol_status.slug}",
            )
        case VolumeError.DEVICE_NOT_PRESENT:
            _print_device_not_present_fix(console, vol.mount)
        case VolumeError.MOUNT_UNIT_NOT_CONFIGURED:
            _print_mount_unit_not_configured_fix(
                console,
                vol,
                resolved_endpoints,
            )
        case VolumeError.MOUNT_UNIT_MISMATCH:
            caps = (
                vol_status.diagnostics.capabilities if vol_status.diagnostics else None
            )
            _print_mount_unit_mismatch_fix(
                console,
                vol,
                caps,
                resolved_endpoints,
            )
        case VolumeError.CRYPTSETUP_SERVICE_NOT_CONFIGURED:
            _print_cryptsetup_service_not_configured_fix(
                console,
                vol,
                resolved_endpoints,
            )
        case VolumeError.CRYPTSETUP_SERVICE_MISMATCH:
            caps = (
                vol_status.diagnostics.capabilities if vol_status.diagnostics else None
            )
            _print_cryptsetup_service_mismatch_fix(
                console,
                vol,
                caps,
                resolved_endpoints,
            )
        case VolumeError.POLKIT_RULES_MISSING:
            _print_polkit_rules_missing_fix(
                console,
                vol,
                config,
                resolved_endpoints,
            )
        case VolumeError.SUDOERS_RULES_MISSING:
            _print_sudoers_rules_missing_fix(
                console,
                vol,
                config,
                resolved_endpoints,
            )
        case VolumeError.PASSPHRASE_NOT_AVAILABLE:
            _print_passphrase_not_available_fix(console, vol.mount)
        case VolumeError.ATTACH_LUKS_FAILED | VolumeError.MOUNT_FAILED:
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
            if src_ep.btrfs_snapshots.enabled:
                cmds = [
                    f"sudo mkdir {path}/{SNAPSHOTS_DIR}",
                    f"sudo chown <user>:<group> {path}/{SNAPSHOTS_DIR}",
                ]
            else:
                cmds = [f"mkdir -p {path}/{SNAPSHOTS_DIR}"]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, src_vol, resolved_endpoints),
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
            cmds = [
                f"sudo btrfs subvolume create {path}/{STAGING_DIR}",
                f"sudo chown <user>:<group> {path}/{STAGING_DIR}",
            ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
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
            if dst_ep.hard_link_snapshots.enabled:
                cmds = [f"mkdir -p {path}/{SNAPSHOTS_DIR}"]
            else:
                cmds = [
                    f"sudo mkdir {path}/{SNAPSHOTS_DIR}",
                    f"sudo chown <user>:<group> {path}/{SNAPSHOTS_DIR}",
                ]
            for cmd in cmds:
                _print_cmd(
                    console,
                    wrap_cmd(cmd, dst_vol, resolved_endpoints),
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
            console.print(f"{p2}Remount the btrfs volume with user_subvol_rm_allowed:")
            cmd = f"sudo mount -o remount,user_subvol_rm_allowed {dst_vol.path}"
            _print_cmd(
                console,
                wrap_cmd(cmd, dst_vol, resolved_endpoints),
            )
            console.print(
                f"{p2}To persist, add"
                " user_subvol_rm_allowed to"
                " the mount options in /etc/fstab"
                f" for {dst_vol.path}."
            )
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


def _print_mount_unit_not_configured_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for mount unit not configured in systemd."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    console.print(f"{p2}Mount unit not configured in systemd on {host}.")
    console.print(
        f"{p2}The volume path must have a corresponding"
        " fstab entry or native .mount unit."
    )
    if mount and mount.encryption:
        console.print(f"{p2}fstab example (encrypted):")
        _print_cmd(
            console,
            f"/dev/mapper/{mount.encryption.mapper_name}"
            f"  {vol.path}  btrfs  defaults,noauto  0  0",
            indent=3,
        )
    else:
        uuid = mount.device_uuid if mount else "<uuid>"
        console.print(f"{p2}fstab example (unencrypted):")
        _print_cmd(
            console,
            f"UUID={uuid}  {vol.path}  ext4  defaults,noauto  0  0",
            indent=3,
        )
    console.print(f"{p2}After editing fstab:")
    _print_cmd(console, "sudo systemctl daemon-reload", indent=3)


def _print_mount_unit_mismatch_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    caps: VolumeCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for mount unit config mismatch."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    mc = caps.mount if caps else None
    mount_unit = mc.mount_unit if mc else "<mount-unit>"
    actual_what = mc.mount_unit_what if mc else "?"
    actual_where = mc.mount_unit_where if mc else "?"
    if mount and mount.encryption:
        expected_what = f"/dev/mapper/{mount.encryption.mapper_name}"
    elif mount:
        expected_what = f"/dev/disk/by-uuid/{mount.device_uuid}"
    else:
        expected_what = "?"
    console.print(f"{p2}Mount unit config does not match on {host}.")
    console.print(f"{p2}Expected:")
    console.print(f"{p2}{_INDENT}Where={vol.path}")
    console.print(f"{p2}{_INDENT}What={expected_what}")
    console.print(f"{p2}Actual:")
    console.print(f"{p2}{_INDENT}Where={actual_where}")
    console.print(f"{p2}{_INDENT}What={actual_what}")
    console.print(f"{p2}Check with:")
    _print_cmd(
        console,
        f"systemctl show {mount_unit} -p What -p Where --no-pager",
        indent=3,
    )
    console.print(f"{p2}Fix fstab or .mount unit, then:")
    _print_cmd(console, "sudo systemctl daemon-reload", indent=3)


def _print_cryptsetup_service_not_configured_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for cryptsetup service not configured."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    mapper = mount.encryption.mapper_name if mount and mount.encryption else "<mapper>"
    uuid = mount.device_uuid if mount else "<uuid>"
    console.print(
        f"{p2}Cryptsetup service"
        f" systemd-cryptsetup@{mapper}.service"
        f" not configured in systemd on {host}."
    )
    console.print(
        f"{p2}The encrypted volume must have a"
        " corresponding crypttab entry or native"
        " service unit."
    )
    console.print(f"{p2}crypttab example:")
    _print_cmd(
        console,
        f"{mapper}  UUID={uuid}  none  luks,noauto",
        indent=3,
    )
    console.print(f"{p2}After editing crypttab:")
    _print_cmd(console, "sudo systemctl daemon-reload", indent=3)


def _print_cryptsetup_service_mismatch_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    caps: VolumeCapabilities | None,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for cryptsetup service config mismatch."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    mount = vol.mount
    mapper = mount.encryption.mapper_name if mount and mount.encryption else "<mapper>"
    uuid = mount.device_uuid if mount else "<uuid>"
    mc = caps.mount if caps else None
    actual_exec = mc.cryptsetup_service_exec_start if mc else "?"
    service = f"systemd-cryptsetup@{mapper}.service"
    console.print(f"{p2}Cryptsetup service config does not match on {host}.")
    console.print(f"{p2}Expected ExecStart to contain mapper={mapper} and UUID={uuid}.")
    console.print(f"{p2}Actual ExecStart: {actual_exec}")
    console.print(f"{p2}Check with:")
    _print_cmd(
        console,
        f"systemctl show {service} -p ExecStart --no-pager",
        indent=3,
    )
    console.print(f"{p2}Fix crypttab or service unit, then:")
    _print_cmd(console, "sudo systemctl daemon-reload", indent=3)


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
    console.print(f"{p2}Configure with your credential provider:")
    console.print(f"{p2}{_INDENT}keyring: keyring set nbkp {pid}")
    console.print(f"{p2}{_INDENT}env: export {env_var}=...")
    console.print(f"{p2}{_INDENT}command: ensure <credential-command> works")


def _print_mount_failed_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    mount: "MountConfig | None",
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for attach-luks/mount failure."""
    p2 = _INDENT * 2
    if mount and mount.encryption:
        mapper = mount.encryption.mapper_name
        uuid = mount.device_uuid
        console.print(f"{p2}Manual steps:")
        _print_cmd(
            console,
            wrap_cmd(
                f"sudo systemd-cryptsetup attach {mapper}"
                f" /dev/disk/by-uuid/{uuid} /dev/stdin luks",
                vol,
                resolved_endpoints,
            ),
            indent=3,
        )
        console.print(f"{p2}Check journal:")
        _print_cmd(
            console,
            f"journalctl -u systemd-cryptsetup@{mapper}.service",
            indent=3,
        )
    else:
        console.print(f"{p2}Check journal for mount errors.")


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
    rules = generate_auth_rules(config, user)
    console.print(f"{p2}polkit rules not configured on {host}.")
    console.print(f"{p2}Required for systemctl start/stop authorization without sudo.")
    if rules.polkit:
        console.print(f"{p2}Install to: {POLKIT_RULES_PATH}")
        _print_cmd(console, rules.polkit.rstrip(), indent=3)
    console.print(f"{p2}Or generate with: nbkp config setup-auth -c <config>")


def _print_sudoers_rules_missing_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> None:
    """Print fix for missing sudoers rules, including generated content."""
    p2 = _INDENT * 2
    host = host_label(vol, resolved_endpoints)
    user = _resolve_volume_user(vol, resolved_endpoints)
    rules = generate_auth_rules(config, user)
    console.print(f"{p2}sudoers rules not configured on {host}.")
    console.print(f"{p2}Required for passwordless sudo systemd-cryptsetup attach.")
    if rules.sudoers:
        console.print(f"{p2}Install with: sudo visudo -f {SUDOERS_RULES_PATH}")
        _print_cmd(console, rules.sudoers.rstrip(), indent=3)
    console.print(f"{p2}Or generate with: nbkp config setup-auth -c <config>")


# ── Main troubleshoot entry point ─────────────────────────────


def print_human_troubleshoot(
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
    # Collect unique SSH endpoint statuses from volume statuses
    ssh_statuses = collect_ssh_endpoint_statuses(vol_statuses, sync_statuses)
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
        has_issues = True
        console.print(f"\n[bold]Volume {vs.slug!r}:[/bold]")
        for error in vs.errors:
            console.print(f"{_INDENT}{error.value}")
            _print_volume_error_fix(console, vs, error, config, re)

    # ── Layer 3: Sync Endpoints ───────────────────────────────
    # Collect unique source and destination endpoint statuses from syncs
    seen_src_eps: set[str] = set()
    seen_dst_eps: set[str] = set()
    for ss in sync_statuses.values():
        src_ep = ss.source_endpoint_status
        if src_ep.endpoint_slug not in seen_src_eps and src_ep.errors:
            seen_src_eps.add(src_ep.endpoint_slug)
            has_issues = True
            console.print(f"\n[bold]Source Endpoint {src_ep.endpoint_slug!r}:[/bold]")
            for error in src_ep.errors:
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
            seen_dst_eps.add(dst_ep.endpoint_slug)
            has_issues = True
            console.print(
                f"\n[bold]Destination Endpoint {dst_ep.endpoint_slug!r}:[/bold]"
            )
            for error in dst_ep.errors:
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
        has_issues = True
        console.print(f"\n[bold]Sync {ss.slug!r}:[/bold]")
        for sync_error in ss.errors:
            console.print(f"{_INDENT}{sync_error.value}")
            _print_sync_error_fix(console, ss.config, sync_error, config)

    if not has_issues:
        console.print("No issues found. All volumes and syncs are active.")
