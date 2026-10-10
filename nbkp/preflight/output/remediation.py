"""Per-error remediation instructions printed by ``preflight troubleshoot``.

Every line goes through :func:`say` (or :func:`print_cmd` for shell
commands): config values — paths, hosts, passphrase ids — are not authored by
nbkp, so they are assembled into a ``Text`` (never parsed as Rich markup), and
indentation is computed from a nesting *level* at the call site rather than
hardcoded into the strings.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from textwrap import dedent

from rich.console import Console
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text

from ...clihelpers.invocation import Invocation
from ...config import (
    Config,
    LocalVolume,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
)
from ...config.output import endpoint_path, host_label
from ...credentials import passphrase_env_var
from ...disks.auth import generate_auth_rules
from ...disks.detection import DeviceProbeError, discover_cleartext_device
from ...disks.udisks import cleartext_mapper_name
from ...fsprotocol import (
    DESTINATION_SENTINEL,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from ...remote.endpoints import ResolvedEndpoints
from ...remote.ssh import format_proxy_jump_chain, ssh_prefix, wrap_cmd
from ..status import (
    DestinationEndpointError,
    SourceEndpointError,
    SshEndpointError,
    SshEndpointStatus,
    SshEndpointWarning,
    SyncError,
    VolumeError,
    VolumeStatus,
)

Part = str | tuple[str, str] | Text
"""A line fragment: plain text, ``(text, style)``, or a ``Text``."""

_INDENT_WIDTH = 2

# Nesting levels: the error line sits at ERROR, its fix at FIX, numbered
# sub-steps at STEP and their commands at STEP_CMD.
HEADER, ERROR, FIX, STEP, STEP_CMD = range(5)


@dataclass(frozen=True)
class TroubleshootContext:
    """Everything the fix printers need besides the error itself."""

    config: Config
    resolved_endpoints: ResolvedEndpoints = field(default_factory=dict)
    invocation: Invocation = field(default_factory=Invocation)
    """Config path and endpoint flags, so suggested commands copy-paste."""
    local_user: str = "<user>"
    """User the polkit rule authorizes for local volumes (and remote ones
    whose SSH endpoint sets no user).  Supplied by the CLI."""


def say(console: Console, level: int, *parts: Part) -> None:
    """Print one line at nesting *level*; *parts* are never parsed as markup."""
    console.print(
        Padding(Text.assemble(*parts), (0, 0, 0, level * _INDENT_WIDTH)),
        highlight=False,
    )


def print_cmd(console: Console, cmd: str, level: int = FIX) -> None:
    """Print a shell command with bash syntax highlighting at nesting *level*."""
    syntax = Syntax(cmd, "bash", theme="monokai", background_color="default")
    console.print(Padding(syntax, (0, 0, 0, level * _INDENT_WIDTH)))


def _say_lines(console: Console, level: int, block: str) -> None:
    """Print each line of a ``dedent`` block at *level*."""
    for line in block.splitlines():
        say(console, level, line)


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

_UDISKSCTL_DETAILS = dedent("""\
    Mount management uses udisks2 (udisksctl).
    Install: sudo apt install udisks2
    (add udisks2-btrfs for btrfs volumes)
    Check: which udisksctl""")

_UDISKSD_DETAILS = dedent("""\
    Mount management talks to udisksd over D-Bus.
    Start it: sudo systemctl enable --now udisks2
    On headless hosts ensure dbus and udisksd are up
    Check: systemctl status udisks2""")

_LSBLK_DETAILS = dedent("""\
    Needed to discover the unlocked device of encrypted volumes.
    Install: sudo apt install util-linux
    Check: which lsblk""")

_UDISKS_BTRFS_DETAILS = dedent("""\
    Recommended to mount btrfs volumes via udisks.
    Install: sudo apt install udisks2-btrfs
    Then restart udisks2: sudo systemctl restart udisks2""")

_UDISKS_MOUNT_OPTIONS = dedent("""\
    [defaults]
    btrfs_allow=user_subvol_rm_allowed
    btrfs_defaults=user_subvol_rm_allowed""")


# ── Shared fixes ──────────────────────────────────────────────


def _print_titled(console: Console, title: Part, details: str) -> None:
    """A fix headline followed by indented detail lines."""
    say(console, FIX, title)
    _say_lines(console, STEP, details)


def _print_install(console: Console, what: str, host: str, recipe: str) -> None:
    say(console, FIX, f"Install {what} on ", host, ":")
    print_cmd(console, recipe, STEP)


def _print_sentinel_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    path: str,
    sentinel: str,
    ctx: TroubleshootContext,
) -> None:
    """Print sentinel creation fix with mount reminder."""
    say(console, FIX, "Ensure the volume is mounted, then:")
    print_cmd(console, wrap_cmd(["mkdir", "-p", path], vol, ctx.resolved_endpoints))
    print_cmd(
        console,
        wrap_cmd(["touch", f"{path}/{sentinel}"], vol, ctx.resolved_endpoints),
    )


def _print_reset_latest(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> None:
    print_cmd(
        console,
        wrap_cmd(
            ["ln", "-sfn", "/dev/null", f"{path}/{LATEST_LINK}"],
            vol,
            ctx.resolved_endpoints,
        ),
    )


def _print_chown(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> None:
    print_cmd(
        console,
        wrap_cmd(
            ["sudo", "chown", "<user>:<group>", path], vol, ctx.resolved_endpoints
        ),
    )


def _print_mkdir(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> None:
    print_cmd(console, wrap_cmd(["mkdir", "-p", path], vol, ctx.resolved_endpoints))


# ── Layer 1: SSH endpoints ────────────────────────────────────


def _proxy_chain(config: Config, server: SshEndpoint) -> list[SshEndpoint] | None:
    return (
        [config.ssh_endpoints[s] for s in server.proxy_jump_chain]
        if server.proxy_jump_chain
        else None
    )


def _ssh_copy_id_cmd(server: SshEndpoint, proxy_chain: list[SshEndpoint] | None) -> str:
    user_host = f"{server.user}@{server.host}" if server.user else server.host
    return shlex.join(
        [
            "ssh-copy-id",
            *(
                ["-o", f"ProxyJump={format_proxy_jump_chain(proxy_chain)}"]
                if proxy_chain
                else []
            ),
            *(["-p", str(server.port)] if server.port != 22 else []),
            *(["-i", server.key] if server.key else []),
            user_host,
        ]
    )


def _print_ssh_auth_steps(
    console: Console,
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None,
) -> None:
    """Key setup steps: ensure/generate a key, copy it, verify."""
    echo_ok = shlex.join([*ssh_prefix(server, proxy_chain), "echo", "ok"])
    say(
        console,
        STEP,
        "1. Ensure the key exists:" if server.key else "1. Generate a key:",
    )
    print_cmd(
        console,
        shlex.join(["ls", "-l", server.key]) if server.key else "ssh-keygen -t ed25519",
        STEP_CMD,
    )
    say(console, STEP, "2. Copy it to the server:")
    print_cmd(console, _ssh_copy_id_cmd(server, proxy_chain), STEP_CMD)
    say(console, STEP, "3. Verify passwordless login:")
    print_cmd(console, echo_ok, STEP_CMD)


def _print_ssh_unreachable(
    console: Console, status: SshEndpointStatus, ctx: TroubleshootContext
) -> None:
    """Connectivity checks plus key setup, with the recorded cause if any."""
    server = ctx.config.ssh_endpoints.get(status.slug)
    cause = status.diagnostics.ssh_error
    if server is None:
        say(console, FIX, "SSH endpoint is unreachable.")
    else:
        proxy_chain = _proxy_chain(ctx.config, server)
        say(console, FIX, "Server ", server.host, " is unreachable.")
        if cause is not None:
            say(console, FIX, "Cause: ", (cause, "italic"))
        say(console, FIX, "Verify connectivity:")
        print_cmd(
            console,
            shlex.join([*ssh_prefix(server, proxy_chain), "echo", "ok"]),
            STEP,
        )
        say(console, FIX, "If authentication fails:")
        _print_ssh_auth_steps(console, server, proxy_chain)


def _print_ssh_auth_failed(
    console: Console, status: SshEndpointStatus, ctx: TroubleshootContext
) -> None:
    """The host answered but refused the key or its host key changed."""
    server = ctx.config.ssh_endpoints.get(status.slug)
    host = server.host if server is not None else status.slug
    cause = status.diagnostics.ssh_error
    say(console, FIX, "Server ", host, " refused the connection.")
    if cause is not None:
        say(console, FIX, "Cause: ", (cause, "italic"))
    say(
        console,
        FIX,
        "If the host key changed (reinstalled host), verify it out of band, then:",
    )
    print_cmd(console, shlex.join(["ssh-keygen", "-R", host]), STEP)
    if server is not None:
        say(console, FIX, "If the key is not authorized:")
        _print_ssh_auth_steps(console, server, _proxy_chain(ctx.config, server))


def print_ssh_endpoint_error_fix(
    console: Console,
    status: SshEndpointStatus,
    error: SshEndpointError,
    ctx: TroubleshootContext,
) -> None:
    """Print fix instructions for an SSH endpoint error."""
    slug = status.slug
    match error:
        case SshEndpointError.UNREACHABLE:
            _print_ssh_unreachable(console, status, ctx)
        case SshEndpointError.AUTH_FAILED:
            _print_ssh_auth_failed(console, status, ctx)
        case SshEndpointError.LOCATION_EXCLUDED:
            _print_location_excluded(console, ctx)
        case SshEndpointError.RSYNC_NOT_FOUND:
            _print_install(console, "rsync", slug, _RSYNC_INSTALL)
        case SshEndpointError.RSYNC_TOO_OLD:
            say(
                console,
                FIX,
                "rsync 3.0+ is required on ",
                slug,
                ". Install or upgrade:",
            )
            print_cmd(console, _RSYNC_INSTALL, STEP)
        case SshEndpointError.BTRFS_NOT_FOUND:
            _print_install(console, "btrfs-progs", slug, _BTRFS_INSTALL)
        case SshEndpointError.STAT_NOT_FOUND:
            _print_install(console, "coreutils (stat)", slug, _COREUTILS_INSTALL)
        case SshEndpointError.FINDMNT_NOT_FOUND:
            _print_install(console, "util-linux (findmnt)", slug, _UTIL_LINUX_INSTALL)
        case SshEndpointError.UDISKSCTL_NOT_FOUND:
            _print_titled(
                console,
                Text.assemble("udisksctl not found on ", slug, "."),
                _UDISKSCTL_DETAILS,
            )
        case SshEndpointError.UDISKSD_NOT_RUNNING:
            _print_titled(
                console,
                Text.assemble(
                    "udisksd (the udisks2 daemon) is not running on ", slug, "."
                ),
                _UDISKSD_DETAILS,
            )
        case SshEndpointError.LSBLK_NOT_FOUND:
            _print_titled(
                console, Text.assemble("lsblk not found on ", slug, "."), _LSBLK_DETAILS
            )


def _print_location_excluded(console: Console, ctx: TroubleshootContext) -> None:
    excluded = ", ".join(ctx.invocation.exclude_locations)
    say(
        console,
        FIX,
        "All SSH endpoints for volumes on this host are at an excluded location",
        *([" (", excluded, ")"] if excluded else []),
        ". Remove --exclude-location or add an endpoint at a different location.",
    )


def print_ssh_endpoint_warning_fix(
    console: Console,
    status: SshEndpointStatus,
    warning: SshEndpointWarning,
) -> None:
    """Print advice for a non-fatal SSH endpoint warning."""
    match warning:
        case SshEndpointWarning.UDISKS_BTRFS_MODULE_MISSING:
            _print_titled(
                console,
                Text.assemble(
                    "udisks2 btrfs module not installed on ", status.slug, "."
                ),
                _UDISKS_BTRFS_DETAILS,
            )


# ── Layer 2: Volumes ─────────────────────────────────────────


def print_volume_error_fix(
    console: Console,
    vol_status: VolumeStatus,
    error: VolumeError,
    ctx: TroubleshootContext,
) -> None:
    """Print fix instructions for a volume error."""
    vol = vol_status.config
    match error:
        case VolumeError.SENTINEL_NOT_FOUND:
            _print_sentinel_fix(
                console, vol, vol.path or "<volume-path>", VOLUME_SENTINEL, ctx
            )
        case VolumeError.VOLUME_NOT_MOUNTED:
            say(console, FIX, "Volume is not mounted. Mount it with:")
            print_cmd(
                console,
                ctx.invocation.command("disks", "mount", "-n", vol_status.slug),
                STEP,
            )
        case VolumeError.DEVICE_NOT_PRESENT:
            _print_device_not_present_fix(console, vol.mount)
        case VolumeError.FSTAB_MOUNTPOINT_MISMATCH:
            _print_fstab_mountpoint_mismatch_fix(console, vol_status, ctx)
        case VolumeError.POLKIT_RULES_MISSING:
            _print_polkit_rules_missing_fix(console, vol, ctx)
        case VolumeError.PASSPHRASE_NOT_AVAILABLE:
            _print_passphrase_not_available_fix(console, vol.mount, ctx)
        case VolumeError.UNLOCK_FAILED:
            _print_unlock_failed_fix(console, vol.mount, ctx)
        case VolumeError.MOUNT_FAILED:
            _print_mount_failed_fix(console, vol, vol.mount, ctx)
        case VolumeError.SSH_ENDPOINT_INACTIVE:
            say(console, FIX, "Fix the SSH endpoint errors reported above.")


def _print_device_not_present_fix(console: Console, mount: MountConfig | None) -> None:
    uuid = mount.device_uuid if mount else "<uuid>"
    say(console, FIX, "Plug in the drive and verify:")
    print_cmd(console, shlex.join(["ls", "-la", f"/dev/disk/by-uuid/{uuid}"]))
    say(console, FIX, "Or with systemd:")
    print_cmd(console, shlex.join(["udevadm", "info", f"/dev/disk/by-uuid/{uuid}"]))


def _cleartext_device(
    vol: LocalVolume | RemoteVolume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
) -> tuple[str, bool]:
    """Cleartext device path to print in a fix, and whether it was discovered.

    ``luks-<uuid>`` is only udisks's default: a LUKS2 header label or an
    ``/etc/crypttab`` entry renames the mapper, so deriving the name from the
    container UUID produces an fstab line that never matches on such a host.
    Prefer the device udisks actually created, which requires the container to
    be unlocked; fall back to the derived default when it is locked, and let
    the caller say so.

    Discovery runs ``lsblk``, which this code path cannot assume exists — it is
    the *error reporting* path, reached precisely when the host is not in the
    expected state, and it also renders on machines with no udisks at all (e.g.
    ``nbkp demo output`` on macOS).  Any failure therefore degrades to the
    derived name rather than propagating.
    """
    try:
        discovered = discover_cleartext_device(
            vol, mount.device_uuid, resolved_endpoints
        )
    except (OSError, DeviceProbeError):
        discovered = None
    return (
        (discovered, True)
        if discovered is not None
        else (f"/dev/mapper/{cleartext_mapper_name(mount.device_uuid)}", False)
    )


def _print_mapper_name_caveat(console: Console, level: int) -> None:
    """Warn that a derived mapper name may not be the real one."""
    say(
        console,
        level,
        "The container is locked, so the device above is udisks's default name."
        "  A LUKS2 header label or crypttab entry renames it — unlock the volume"
        " and check `lsblk` before writing it into fstab.",
    )


def _print_fstab_line(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    mount: MountConfig | None,
    path: str,
    fs_and_options: str,
    ctx: TroubleshootContext,
) -> None:
    """An fstab line naming the right device (cleartext mapper if encrypted)."""
    match mount:
        case MountConfig(encryption=None):
            print_cmd(
                console, f"UUID={mount.device_uuid}  {path}  {fs_and_options}", STEP
            )
        case MountConfig():
            device, discovered = _cleartext_device(vol, mount, ctx.resolved_endpoints)
            print_cmd(console, f"{device}  {path}  {fs_and_options}", STEP)
            if not discovered:
                _print_mapper_name_caveat(console, STEP)
        case None:
            print_cmd(console, f"UUID=<fs-uuid>  {path}  {fs_and_options}", STEP)


def _print_fstab_mountpoint_mismatch_fix(
    console: Console,
    vol_status: VolumeStatus,
    ctx: TroubleshootContext,
) -> None:
    """Print fix for a declared path with no fstab entry for this device.

    The volume declares a fixed ``path`` but no ``/etc/fstab`` entry maps the
    device to that path (none at all, or one naming another device), so
    udisks would mount it at its own ``/run/media/<user>/<label>`` location
    instead.  Two remediations: add/fix the fstab entry, or drop ``path``.
    """
    vol = vol_status.config
    host = host_label(vol, ctx.resolved_endpoints)
    path = vol.path or "<volume-path>"
    caps = vol_status.diagnostics.capabilities if vol_status.diagnostics else None
    other_source = caps.mount.fstab_source if caps and caps.mount else None
    say(
        console, FIX, "No /etc/fstab entry maps the device to ", path, " on ", host, "."
    )
    if other_source is not None:
        say(
            console,
            FIX,
            "The entry for ",
            path,
            " mounts ",
            other_source,
            ", a different device.",
        )
    say(
        console,
        FIX,
        "With a fixed 'path', udisks must mount the device there; without a"
        " matching fstab entry it would mount at /run/media/<user>/<label>.",
    )
    say(console, FIX, "Option A — add an /etc/fstab entry (no crypttab needed):")
    _print_fstab_line(
        console, vol, vol.mount, path, "<FS>  noauto,nofail,x-udisks-auth  0 0", ctx
    )
    say(
        console,
        STEP,
        "Replace <FS> with the volume's filesystem type (e.g. btrfs, ext4);"
        " for btrfs also add user_subvol_rm_allowed to the options.",
    )
    say(
        console,
        FIX,
        "Option B — remove 'path' from the volume config to use the mountpoint"
        " udisks discovers (/run/media/<user>/<label>).",
    )


def _print_user_subvol_rm_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    ctx: TroubleshootContext,
) -> None:
    """Print fix for a btrfs volume not mounted with user_subvol_rm_allowed.

    The option is required for snapshot pruning.  nbkp does not pass it to
    udisks at mount time — udisks rejects any non-allowlisted mount option
    (``OptionNotPermitted``), which would fail the mount — so the option must
    come from operator config: ``/etc/fstab`` (udisks honors fstab verbatim) or,
    for the discovered ``/run/media`` model, the udisks mount-options allowlist.
    """
    path = vol.path or "<volume-path>"
    say(
        console,
        FIX,
        "The btrfs volume must be mounted with user_subvol_rm_allowed"
        " (needed for snapshot pruning).  Remount now (ephemeral):",
    )
    print_cmd(
        console,
        wrap_cmd(
            ["sudo", "mount", "-o", "remount,user_subvol_rm_allowed", path],
            vol,
            ctx.resolved_endpoints,
        ),
    )
    match vol.mount:
        case None:
            # Externally-mounted volume: fstab is the only persistence mechanism.
            say(
                console,
                FIX,
                "To persist, add user_subvol_rm_allowed to the /etc/fstab options for ",
                path,
                ".",
            )
        case MountConfig():
            _print_udisks_persistence(console, vol, path, ctx)


def _print_udisks_persistence(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> None:
    """The two persistence routes for a udisks-managed btrfs volume."""
    say(console, FIX, "To persist (udisks-managed volume), use ONE of:")
    say(console, FIX, "Option A — /etc/fstab (udisks honors fstab options):")
    _print_fstab_line(
        console,
        vol,
        vol.mount,
        path,
        "btrfs  noauto,nofail,x-udisks-auth,user_subvol_rm_allowed  0 0",
        ctx,
    )
    say(
        console,
        FIX,
        "Option B — /etc/udisks2/mount_options.conf, then restart udisksd"
        " (for the discovered /run/media mountpoint):",
    )
    print_cmd(console, _UDISKS_MOUNT_OPTIONS, STEP)
    say(
        console,
        STEP,
        "Both keys are required: btrfs_allow permits the option, btrfs_defaults"
        " applies it.  Scope to one device with a [/dev/disk/by-uuid/<uuid>]"
        " section (the unlocked cleartext device for encrypted volumes);"
        " see man udisks2.conf.",
    )


def _passphrase_id(mount: MountConfig | None) -> str:
    return (
        mount.encryption.passphrase_id
        if mount and mount.encryption
        else "<passphrase-id>"
    )


def _print_passphrase_not_available_fix(
    console: Console,
    mount: MountConfig | None,
    ctx: TroubleshootContext,
) -> None:
    """Print fix for a passphrase that could not be retrieved."""
    pid = _passphrase_id(mount)
    say(
        console,
        FIX,
        "The drive is plugged in but its passphrase could not be retrieved.",
    )
    say(console, FIX, "Check credential status:")
    print_cmd(
        console,
        ctx.invocation.command("credentials", "keyring-status", endpoint_flags=False),
        STEP,
    )
    say(
        console,
        FIX,
        "Configure it for your credential provider (",
        ctx.config.credential_provider.value,
        "):",
    )
    say(console, STEP, "keyring: ", shlex.join(["keyring", "set", "nbkp", pid]))
    say(console, STEP, "env: export ", passphrase_env_var(pid), "=...")
    say(console, STEP, "command: ensure the credential-command prints it for id ", pid)


def _print_unlock_failed_fix(
    console: Console,
    mount: MountConfig | None,
    ctx: TroubleshootContext,
) -> None:
    """Print fix for a failed LUKS unlock via udisks."""
    uuid = mount.device_uuid if mount else "<uuid>"
    say(console, FIX, "udisksctl failed to unlock the LUKS container.")
    say(
        console,
        FIX,
        "Verify the passphrase from your credential provider (passphrase-id '",
        _passphrase_id(mount),
        "') is correct:",
    )
    print_cmd(
        console,
        ctx.invocation.command("credentials", "keyring-status", endpoint_flags=False),
        STEP,
    )
    say(console, FIX, "Confirm the device is a LUKS container:")
    print_cmd(
        console,
        shlex.join(["sudo", "cryptsetup", "isLuks", f"/dev/disk/by-uuid/{uuid}"]),
        STEP,
    )
    say(console, FIX, "Try unlocking manually to see the error:")
    print_cmd(
        console,
        shlex.join(["udisksctl", "unlock", "-b", f"/dev/disk/by-uuid/{uuid}"]),
        STEP,
    )


def _print_mount_failed_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    mount: MountConfig | None,
    ctx: TroubleshootContext,
) -> None:
    """Print fix for a failed udisks mount."""
    match mount:
        case MountConfig(encryption=None):
            device = f"/dev/disk/by-uuid/{mount.device_uuid}"
        case MountConfig():
            device, _ = _cleartext_device(vol, mount, ctx.resolved_endpoints)
        case None:
            device = "<device>"
    say(console, FIX, "udisksctl failed to mount the volume.")
    say(
        console,
        FIX,
        "Check the filesystem and, for a fixed 'path', that an /etc/fstab entry"
        " maps the device there (otherwise udisks mounts at"
        " /run/media/<user>/<label>).",
    )
    say(
        console,
        FIX,
        "Ensure the polkit rule is installed (see polkit rules not configured);"
        " without it udisks denies the mount over SSH.",
    )
    say(console, FIX, "Try mounting manually to see the error:")
    print_cmd(
        console,
        wrap_cmd(["udisksctl", "mount", "-b", device], vol, ctx.resolved_endpoints),
        STEP,
    )


def _resolve_volume_user(
    vol: LocalVolume | RemoteVolume, ctx: TroubleshootContext
) -> str:
    """System user for the polkit rule on a volume's host.

    The SSH endpoint user for remote volumes; otherwise the local user the
    CLI supplied.
    """
    match vol:
        case RemoteVolume():
            ep = ctx.resolved_endpoints.get(vol.slug)
            return ep.server.user if ep and ep.server.user else ctx.local_user
        case LocalVolume():
            return ctx.local_user


def _print_polkit_rules_missing_fix(
    console: Console,
    vol: LocalVolume | RemoteVolume,
    ctx: TroubleshootContext,
) -> None:
    """Print fix for missing polkit rules, including generated content."""
    user = _resolve_volume_user(vol, ctx)
    block = generate_auth_rules(ctx.config, user).polkit_block()
    say(
        console,
        FIX,
        "polkit rules not configured on ",
        host_label(vol, ctx.resolved_endpoints),
        ".",
    )
    say(
        console,
        FIX,
        "Required so udisks authorizes unlock/mount/unmount/lock without an"
        " interactive prompt (nbkp runs over SSH / in inactive sessions).",
    )
    if block is not None:
        say(console, FIX, block.install_hint)
        print_cmd(console, block.content.rstrip(), STEP)
    say(console, FIX, "Or generate it with:")
    print_cmd(
        console,
        ctx.invocation.command("disks", "setup-auth", "-u", user, endpoint_flags=False),
        STEP,
    )


# ── Layer 3: Sync endpoints ──────────────────────────────────


def print_source_endpoint_error_fix(
    console: Console,
    error: SourceEndpointError,
    sync: SyncConfig,
    ctx: TroubleshootContext,
) -> None:
    """Print fix instructions for a source endpoint error."""
    src_ep = ctx.config.source_endpoint(sync)
    src_vol = ctx.config.volumes[src_ep.volume]
    path = endpoint_path(src_vol, src_ep.subdir)
    match error:
        case SourceEndpointError.SENTINEL_NOT_FOUND:
            _print_sentinel_fix(console, src_vol, path, SOURCE_SENTINEL, ctx)
        case SourceEndpointError.LATEST_SYMLINK_NOT_FOUND:
            say(
                console,
                FIX,
                "Source has snapshots enabled but ",
                f"{path}/{LATEST_LINK}",
                " symlink does not exist. Create it:",
            )
            _print_reset_latest(console, src_vol, path, ctx)
        case SourceEndpointError.LATEST_SYMLINK_INVALID:
            say(
                console,
                FIX,
                "Source ",
                f"{path}/{LATEST_LINK}",
                " symlink points to an invalid target. Ensure the upstream sync"
                " has run at least once, or reset it:",
            )
            _print_reset_latest(console, src_vol, path, ctx)
        case SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND:
            # Endpoint dir is expected to be user-writable by this point
            # (fixed via NOT_WRITABLE if needed), so a plain mkdir suffices
            # regardless of snapshot backend.
            _print_mkdir(console, src_vol, f"{path}/{SNAPSHOTS_DIR}", ctx)
        case SourceEndpointError.VOLUME_INACTIVE:
            say(console, FIX, "Fix the volume errors reported above.")


def print_destination_endpoint_error_fix(
    console: Console,
    error: DestinationEndpointError,
    sync: SyncConfig,
    ctx: TroubleshootContext,
) -> None:
    """Print fix instructions for a destination endpoint error."""
    dst_ep = ctx.config.destination_endpoint(sync)
    dst_vol = ctx.config.volumes[dst_ep.volume]
    path = endpoint_path(dst_vol, dst_ep.subdir)
    match error:
        case (
            DestinationEndpointError.SENTINEL_NOT_FOUND
            | DestinationEndpointError.NOT_WRITABLE
            | DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND
            | DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE
            | DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND
            | DestinationEndpointError.LATEST_SYMLINK_INVALID
            | DestinationEndpointError.VOLUME_INACTIVE
        ):
            _print_destination_layout_fix(console, error, dst_vol, path, ctx)
        case _:
            _print_destination_btrfs_fix(console, error, dst_vol, path, ctx)


def _print_destination_layout_fix(
    console: Console,
    error: DestinationEndpointError,
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> None:
    """Fixes for the directory layout shared by every destination."""
    snapshots = f"{path}/{SNAPSHOTS_DIR}"
    latest = f"{path}/{LATEST_LINK}"
    match error:
        case DestinationEndpointError.SENTINEL_NOT_FOUND:
            _print_sentinel_fix(console, vol, path, DESTINATION_SENTINEL, ctx)
        case DestinationEndpointError.NOT_WRITABLE:
            say(
                console,
                FIX,
                "The destination endpoint directory ",
                f"{path}/",
                " is not writable. Fix permissions:",
            )
            _print_chown(console, vol, path, ctx)
        case DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND:
            # Endpoint dir is expected to be user-writable by this point
            # (fixed via NOT_WRITABLE if needed), so a plain mkdir suffices
            # regardless of snapshot backend.
            _print_mkdir(console, vol, snapshots, ctx)
        case DestinationEndpointError.SNAPSHOTS_DIR_NOT_WRITABLE:
            say(
                console,
                FIX,
                f"The destination {SNAPSHOTS_DIR}/ directory (",
                snapshots,
                ") is not writable. Fix permissions:",
            )
            _print_chown(console, vol, snapshots, ctx)
        case DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND:
            say(
                console,
                FIX,
                "Destination has snapshots enabled but ",
                latest,
                " symlink does not exist. Create it:",
            )
            _print_reset_latest(console, vol, path, ctx)
        case DestinationEndpointError.LATEST_SYMLINK_INVALID:
            say(
                console,
                FIX,
                "Destination ",
                latest,
                " symlink points to an invalid target. Reset it:",
            )
            _print_reset_latest(console, vol, path, ctx)
        case _:
            say(console, FIX, "Fix the volume errors reported above.")


def _print_destination_btrfs_fix(
    console: Console,
    error: DestinationEndpointError,
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> None:
    """Fixes for snapshot-backend (btrfs / hard-link) capability errors."""
    staging = f"{path}/{STAGING_DIR}"
    snapshots = f"{path}/{SNAPSHOTS_DIR}"
    match error:
        case DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME:
            for cmd in (
                ["sudo", "btrfs", "subvolume", "create", staging],
                ["sudo", "mkdir", snapshots],
                ["sudo", "chown", "<user>:<group>", staging, snapshots],
            ):
                print_cmd(console, wrap_cmd(cmd, vol, ctx.resolved_endpoints))
        case DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND:
            # Endpoint dir is expected to be user-writable by this point
            # (fixed via NOT_WRITABLE if needed), so subvolume create runs
            # without sudo (kernel 5.8+).
            print_cmd(
                console,
                wrap_cmd(
                    ["btrfs", "subvolume", "create", staging],
                    vol,
                    ctx.resolved_endpoints,
                ),
            )
        case DestinationEndpointError.STAGING_SUBVOL_NOT_WRITABLE:
            say(
                console,
                FIX,
                f"The destination {STAGING_DIR}/ directory (",
                staging,
                ") is not writable. Fix permissions:",
            )
            _print_chown(console, vol, staging, ctx)
        case DestinationEndpointError.VOL_NOT_BTRFS:
            say(console, FIX, "The destination is not on a btrfs filesystem.")
            say(
                console,
                FIX,
                "Move it to a btrfs volume, or use hard-link-snapshots instead.",
            )
        case DestinationEndpointError.VOL_FS_TYPE_UNKNOWN:
            say(
                console,
                FIX,
                "`stat -f` failed on the volume, so its filesystem type is unknown.",
            )
            say(console, FIX, "Check that the volume path exists and is readable:")
            print_cmd(
                console,
                wrap_cmd(
                    ["stat", "-f", "-c", "%T", vol.path or "<volume-path>"],
                    vol,
                    ctx.resolved_endpoints,
                ),
                STEP,
            )
        case DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM:
            _print_user_subvol_rm_fix(console, vol, ctx)
        case DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT:
            say(
                console,
                FIX,
                "The destination filesystem does not support hard links (e.g."
                " FAT/exFAT). Use a filesystem like ext4, xfs, or btrfs, or use"
                " btrfs-snapshots instead.",
            )
        case _:
            pass


# ── Layer 4: Syncs ───────────────────────────────────────────


def print_sync_error_fix(
    console: Console,
    sync: SyncConfig,
    error: SyncError,
    ctx: TroubleshootContext,
) -> None:
    """Print fix instructions for a sync-level error."""
    match error:
        case SyncError.DISABLED:
            say(console, FIX, "Enable the sync in the configuration file.")
        case SyncError.ENDPOINT_HOST_ERRORS:
            say(
                console,
                FIX,
                "A host this sync runs on is missing required tools. Fix the SSH"
                " endpoint errors reported above (for local volumes, under"
                " 'localhost').",
            )
        case SyncError.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM:
            src_ep = ctx.config.source_endpoint(sync)
            path = endpoint_path(ctx.config.volumes[src_ep.volume], src_ep.subdir)
            say(
                console,
                FIX,
                "Source ",
                f"{path}/{LATEST_LINK}",
                " points to /dev/null but there is no upstream sync that writes"
                " to this endpoint. Either run the upstream sync first or reset"
                " the symlink to point to a valid snapshot.",
            )
        case SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING:
            say(
                console,
                FIX,
                "The source endpoint's latest symlink points to /dev/null (no"
                " snapshot yet). In dry-run mode, the upstream sync does not"
                " create a real snapshot, so this sync is skipped. Run without"
                " --dry-run to execute the full chain.",
            )
        case (
            SyncError.SOURCE_ENDPOINT_INACTIVE | SyncError.DESTINATION_ENDPOINT_INACTIVE
        ):
            say(console, FIX, "Fix the endpoint errors reported above.")
