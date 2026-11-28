"""
Per-error remediation instructions shown by ``preflight troubleshoot``.

Every line goes through :func:`format_line` (or :func:`format_command` for
shell commands): config values — paths, hosts, passphrase ids — are not authored by
nbkp, so they are assembled into a ``Text`` (never parsed as Rich markup), and
indentation is computed from a nesting *level* at the call site rather than
hardcoded into the strings.

Rendering is pure: facts that take live work to establish — the cleartext
device udisks created for an unlocked LUKS container, the polkit rule to
install — are gathered beforehand by :mod:`nbkp.preflight.troubleshoot` and
handed over as :class:`~nbkp.preflight.troubleshoot.RemediationFacts`.
"""

import shlex
from dataclasses import dataclass, field
from textwrap import dedent

from rich.console import RenderableType
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text

from nbkp.config import (
    DEFAULT_SSH_PORT,
    Config,
    LocalVolume,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    passphrase_env_var,
)
from nbkp.config.output import endpoint_path, host_label
from nbkp.disks import cleartext_mapper_name
from nbkp.fsprotocol import (
    DESTINATION_SENTINEL,
    DEVNULL_TARGET,
    LATEST_LINK,
    SNAPSHOTS_DIR,
    SOURCE_SENTINEL,
    STAGING_DIR,
    VOLUME_SENTINEL,
)
from nbkp.preflight.output.commands import SuggestedCommands
from nbkp.preflight.status.endpoint import (
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    DestinationEndpointWarningKind,
    SourceEndpointErrorKind,
)
from nbkp.preflight.status.ssh import (
    SshEndpointErrorKind,
    SshEndpointStatus,
    SshEndpointWarningKind,
)
from nbkp.preflight.status.sync import SyncErrorKind
from nbkp.preflight.status.volume import VolumeErrorKind, VolumeStatus
from nbkp.preflight.troubleshoot import RemediationFacts, polkit_user
from nbkp.remote.endpoints import ResolvedEndpoint, ResolvedEndpoints
from nbkp.remote.resolution import resolve_proxy_chain
from nbkp.remote.ssh import (
    format_known_hosts_option,
    format_proxy_jump_chain,
    ssh_prefix,
    wrap_cmd,
)

Part = str | tuple[str, str] | Text
"""A line fragment: plain text, ``(text, style)``, or a ``Text``."""

type Lines = list[RenderableType]
"""Remediation lines, each already indented to its nesting level."""

_INDENT_WIDTH = 2

# Nesting levels: the error line sits at ERROR, its fix at FIX, numbered
# sub-steps at STEP and their commands at STEP_CMD.
HEADER, ERROR, FIX, STEP, STEP_CMD = range(5)


@dataclass(frozen=True)
class TroubleshootContext:
    """Everything the fixes need besides the error itself."""

    config: Config
    invocation: SuggestedCommands
    """Config path and endpoint flags, so suggested commands copy-paste."""
    resolved_endpoints: ResolvedEndpoints = field(
        default_factory=dict[str, ResolvedEndpoint],
    )
    local_user: str = "<user>"
    """User the polkit rule authorizes for local volumes (and remote ones
    whose SSH endpoint sets no user).  Supplied by the CLI."""
    facts: RemediationFacts = field(default_factory=RemediationFacts)
    """Probed and generated facts, gathered beforehand by
    :func:`nbkp.preflight.troubleshoot.gather_remediation_facts`; a fact
    missing here is rendered as unknown."""


def format_line(level: int, *parts: Part) -> Padding:
    """One line at nesting *level*; *parts* are never parsed as markup."""
    return Padding(Text.assemble(*parts), (0, 0, 0, level * _INDENT_WIDTH))


def format_command(cmd: str, level: int = FIX) -> Padding:
    """Return the shell command *cmd*, bash-highlighted, at nesting *level*."""
    syntax = Syntax(cmd, "bash", theme="monokai", background_color="default")
    return Padding(syntax, (0, 0, 0, level * _INDENT_WIDTH))


def _format_lines(level: int, block: str) -> Lines:
    return [format_line(level, line) for line in block.splitlines()]


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


def _titled(title: Part, details: str) -> Lines:
    return [format_line(FIX, title), *_format_lines(STEP, details)]


def _install(what: str, host: str, recipe: str) -> Lines:
    return [
        format_line(FIX, f"Install {what} on ", host, ":"),
        format_command(recipe, STEP),
    ]


def _sentinel_fix(
    vol: LocalVolume | RemoteVolume,
    path: str,
    sentinel_name: str,
    ctx: TroubleshootContext,
) -> Lines:
    return [
        format_line(FIX, "Ensure the volume is mounted, then:"),
        format_command(wrap_cmd(["mkdir", "-p", path], vol, ctx.resolved_endpoints)),
        format_command(
            wrap_cmd(["touch", f"{path}/{sentinel_name}"], vol, ctx.resolved_endpoints),
        ),
    ]


def _sentinel_unreadable_fix(
    vol: LocalVolume | RemoteVolume,
    path: str,
    sentinel_name: str,
    ctx: TroubleshootContext,
) -> Lines:
    """
    Explain a sentinel hidden behind a directory nbkp cannot search.

    Not a missing sentinel: creating one could shadow a sentinel that is
    already there, so the fix is access, not ``touch``.
    """
    return [
        format_line(
            FIX,
            "Cannot tell whether ",
            f"{path}/{sentinel_name}",
            " exists: a directory on its path is not searchable by the user nbkp"
            " runs as. Find it with:",
        ),
        format_command(wrap_cmd(["ls", "-ld", path], vol, ctx.resolved_endpoints)),
        format_line(FIX, "Then give that user access to it, for instance:"),
        *_chown(vol, path, ctx),
    ]


def _latest_unreadable_fix(
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> Lines:
    """
    Explain a ``latest`` symlink that is there but could not be read.

    Not a missing one: resetting it blindly could point it away from the
    snapshot it names, so the fix starts by looking at it.
    """
    latest = f"{path}/{LATEST_LINK}"
    return [
        format_line(
            FIX,
            "The ",
            latest,
            " symlink exists but its target could not be read. Inspect it:",
        ),
        format_command(wrap_cmd(["ls", "-l", latest], vol, ctx.resolved_endpoints)),
        format_line(
            FIX,
            "Then make it readable by the user nbkp runs as, or replace it"
            " with a symlink to the newest snapshot.",
        ),
    ]


_NEWEST_SNAPSHOT_PLACEHOLDER = "<newest-snapshot>"


def _reset_latest(
    vol: LocalVolume | RemoteVolume,
    path: str,
    endpoint_slug: str,
    ctx: TroubleshootContext,
) -> Lines:
    """
    Point ``latest`` at the newest snapshot, or at ``/dev/null`` when there is none.

    Only when there is none: a hard-link sync deletes every snapshot as a leftover of a
    failed run while ``latest`` points to ``/dev/null``.
    """
    newest_snapshots = ctx.facts.newest_snapshots
    known = endpoint_slug in newest_snapshots
    newest = newest_snapshots.get(endpoint_slug)
    target = (
        DEVNULL_TARGET
        if known and newest is None
        else f"{SNAPSHOTS_DIR}/{newest or _NEWEST_SNAPSHOT_PLACEHOLDER}"
    )
    command = format_command(
        wrap_cmd(
            ["ln", "-sfn", target, f"{path}/{LATEST_LINK}"],
            vol,
            ctx.resolved_endpoints,
        ),
    )
    snapshots = f"{path}/{SNAPSHOTS_DIR}/"
    match (known, newest):
        case (True, None):
            return [
                command,
                format_line(FIX, snapshots, " holds no snapshot yet, hence /dev/null."),
            ]
        case (True, str() as name):
            return [
                command,
                format_line(
                    FIX,
                    name,
                    " is the newest snapshot in ",
                    snapshots,
                    ". If a run was interrupted while writing it, pick the one before.",
                ),
            ]
        case _:
            return [
                command,
                format_line(
                    FIX,
                    f"Replace {_NEWEST_SNAPSHOT_PLACEHOLDER} with the newest"
                    " snapshot in ",
                    snapshots,
                    ". Use /dev/null only if it holds none: while latest points"
                    " to /dev/null, the next hard-link sync deletes every"
                    " snapshot there as a leftover of a failed run.",
                ),
            ]


def _chown(
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> Lines:
    return [
        format_command(
            wrap_cmd(
                ["sudo", "chown", "<user>:<group>", path],
                vol,
                ctx.resolved_endpoints,
            ),
        ),
    ]


def _mkdir(
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> Lines:
    return [
        format_command(wrap_cmd(["mkdir", "-p", path], vol, ctx.resolved_endpoints)),
    ]


def _proxy_chain(config: Config, server: SshEndpoint) -> list[SshEndpoint] | None:
    return resolve_proxy_chain(config, server) or None


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
            *(["-p", str(server.port)] if server.port != DEFAULT_SSH_PORT else []),
            *(["-i", server.key] if server.key else []),
            user_host,
        ],
    )


def _ssh_auth_steps(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None,
) -> Lines:
    echo_ok = shlex.join([*ssh_prefix(server, proxy_chain), "echo", "ok"])
    return [
        format_line(
            STEP,
            "1. Ensure the key exists:" if server.key else "1. Generate a key:",
        ),
        format_command(
            shlex.join(["ls", "-l", server.key])
            if server.key
            else "ssh-keygen -t ed25519",
            STEP_CMD,
        ),
        format_line(STEP, "2. Copy it to the server:"),
        format_command(_ssh_copy_id_cmd(server, proxy_chain), STEP_CMD),
        format_line(STEP, "3. Verify passwordless login:"),
        format_command(echo_ok, STEP_CMD),
    ]


def _ssh_unreachable(status: SshEndpointStatus, ctx: TroubleshootContext) -> Lines:
    server = ctx.config.ssh_endpoints.get(status.slug)
    cause = status.diagnostics.ssh_error
    if server is None:
        return [format_line(FIX, "SSH endpoint is unreachable.")]
    else:
        proxy_chain = _proxy_chain(ctx.config, server)
        return [
            format_line(FIX, "Server ", server.host, " is unreachable."),
            *(
                [format_line(FIX, "Cause: ", (cause, "italic"))]
                if cause is not None
                else []
            ),
            format_line(FIX, "Verify connectivity:"),
            format_command(
                shlex.join([*ssh_prefix(server, proxy_chain), "echo", "ok"]),
                STEP,
            ),
            format_line(FIX, "If authentication fails:"),
            *_ssh_auth_steps(server, proxy_chain),
        ]


def _ssh_auth_failed(status: SshEndpointStatus, ctx: TroubleshootContext) -> Lines:
    """
    Explain the fix when the host answered but refused the connection.

    It refused the key, or its host key is unknown or changed.
    """
    server = ctx.config.ssh_endpoints.get(status.slug)
    host = server.host if server is not None else status.slug
    known_hosts_file = (
        server.connection_options.known_hosts_file if server is not None else None
    )
    # known_hosts records a non-default port as "[host]:port".
    known_hosts_name = (
        host
        if server is None or server.port == DEFAULT_SSH_PORT
        else f"[{host}]:{server.port}"
    )
    cause = status.diagnostics.ssh_error
    return [
        format_line(FIX, "Server ", host, " refused the connection."),
        *(
            [format_line(FIX, "Cause: ", (cause, "italic"))]
            if cause is not None
            else []
        ),
        *(
            _unknown_host_key_steps(server, _proxy_chain(ctx.config, server))
            if server is not None
            else []
        ),
        format_line(
            FIX,
            "If the host key changed (reinstalled host), verify it out of band, then:",
        ),
        format_command(
            shlex.join(
                [
                    "ssh-keygen",
                    *(["-f", known_hosts_file] if known_hosts_file else []),
                    "-R",
                    known_hosts_name,
                ],
            ),
            STEP,
        ),
        *(
            [
                format_line(FIX, "If the key is not authorized:"),
                *_ssh_auth_steps(server, _proxy_chain(ctx.config, server)),
            ]
            if server is not None
            else []
        ),
    ]


def _unknown_host_key_steps(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None,
) -> Lines:
    """
    List the steps that add an unknown host key.

    Nbkp never trusts a host key on first use while ``strict-host-key-checking`` is on:
    the operator adds it once, after comparing the fingerprint OpenSSH shows with the
    host's own.
    """
    known_hosts_file = server.connection_options.known_hosts_file
    ssh = ssh_prefix(server, proxy_chain)
    return [
        format_line(
            FIX,
            "If the host key is not known yet, connect once with ssh, check the "
            "fingerprint it shows against the host's, and accept it:",
        ),
        format_command(
            shlex.join(
                [
                    ssh[0],
                    *(
                        ["-o", format_known_hosts_option(known_hosts_file)]
                        if known_hosts_file
                        else []
                    ),
                    *ssh[1:],
                    "true",
                ],
            ),
            STEP,
        ),
    ]


def format_ssh_endpoint_error_fix(
    status: SshEndpointStatus,
    error: SshEndpointErrorKind,
    ctx: TroubleshootContext,
) -> Lines:
    """Return the fix for the SSH endpoint error *error* on the endpoint *status*."""
    slug = status.slug
    match error:
        case SshEndpointErrorKind.UNREACHABLE:
            return _ssh_unreachable(status, ctx)
        case SshEndpointErrorKind.AUTH_FAILED:
            return _ssh_auth_failed(status, ctx)
        case SshEndpointErrorKind.LOCATION_EXCLUDED:
            return _location_excluded(ctx)
        case SshEndpointErrorKind.RSYNC_NOT_FOUND:
            return _install("rsync", slug, _RSYNC_INSTALL)
        case SshEndpointErrorKind.RSYNC_TOO_OLD:
            return [
                format_line(
                    FIX,
                    "rsync 3.0+ is required on ",
                    slug,
                    ". Install or upgrade:",
                ),
                format_command(_RSYNC_INSTALL, STEP),
            ]
        case SshEndpointErrorKind.BTRFS_NOT_FOUND:
            return _install("btrfs-progs", slug, _BTRFS_INSTALL)
        case SshEndpointErrorKind.STAT_NOT_FOUND:
            return _install("coreutils (stat)", slug, _COREUTILS_INSTALL)
        case SshEndpointErrorKind.FINDMNT_NOT_FOUND:
            return _install("util-linux (findmnt)", slug, _UTIL_LINUX_INSTALL)
        case SshEndpointErrorKind.UDISKSCTL_NOT_FOUND:
            return _titled(
                Text.assemble("udisksctl not found on ", slug, "."),
                _UDISKSCTL_DETAILS,
            )
        case SshEndpointErrorKind.UDISKSD_NOT_RUNNING:
            return _titled(
                Text.assemble(
                    "udisksd (the udisks2 daemon) is not running on ",
                    slug,
                    ".",
                ),
                _UDISKSD_DETAILS,
            )
        case SshEndpointErrorKind.LSBLK_NOT_FOUND:
            return _titled(
                Text.assemble("lsblk not found on ", slug, "."),
                _LSBLK_DETAILS,
            )


def _location_excluded(ctx: TroubleshootContext) -> Lines:
    excluded = ", ".join(ctx.invocation.exclude_locations)
    return [
        format_line(
            FIX,
            "All SSH endpoints for volumes on this host are at an excluded location",
            *([" (", excluded, ")"] if excluded else []),
            ". Remove --exclude-location or add an endpoint at a different location.",
        ),
    ]


def format_ssh_endpoint_warning_fix(
    status: SshEndpointStatus,
    warning: SshEndpointWarningKind,
) -> Lines:
    """Return the fix for the SSH endpoint warning *warning* on *status*."""
    match warning:
        case SshEndpointWarningKind.UDISKS_BTRFS_MODULE_MISSING:
            return _titled(
                Text.assemble(
                    "udisks2 btrfs module not installed on ",
                    status.slug,
                    ".",
                ),
                _UDISKS_BTRFS_DETAILS,
            )


def format_volume_error_fix(
    vol_status: VolumeStatus,
    error: VolumeErrorKind,
    ctx: TroubleshootContext,
) -> Lines:
    """Return the fix for the volume error *error* on the volume *vol_status*."""
    vol = vol_status.config
    match error:
        case VolumeErrorKind.SENTINEL_NOT_FOUND:
            return _sentinel_fix(vol, vol.path or "<volume-path>", VOLUME_SENTINEL, ctx)
        case VolumeErrorKind.SENTINEL_UNREADABLE:
            return _sentinel_unreadable_fix(
                vol,
                vol.path or "<volume-path>",
                VOLUME_SENTINEL,
                ctx,
            )
        case VolumeErrorKind.VOLUME_NOT_MOUNTED:
            return [
                format_line(FIX, "Volume is not mounted. Mount it with:"),
                format_command(
                    ctx.invocation.command("disks", "mount", "-n", vol_status.slug),
                    STEP,
                ),
            ]
        case VolumeErrorKind.DEVICE_NOT_PRESENT:
            return _device_not_present_fix(vol.mount)
        case VolumeErrorKind.FSTAB_MOUNTPOINT_MISMATCH:
            return _fstab_mountpoint_mismatch_fix(vol_status, ctx)
        case VolumeErrorKind.POLKIT_RULES_MISSING:
            return _polkit_rules_missing_fix(vol, ctx)
        case VolumeErrorKind.PASSPHRASE_NOT_AVAILABLE:
            return _passphrase_not_available_fix(vol.mount, ctx)
        case VolumeErrorKind.UNLOCK_FAILED:
            return _unlock_failed_fix(vol.mount, ctx)
        case VolumeErrorKind.MOUNT_FAILED:
            return _mount_failed_fix(vol, vol.mount, ctx)
        case VolumeErrorKind.SSH_ENDPOINT_INACTIVE:
            return [format_line(FIX, "Fix the SSH endpoint errors reported above.")]


def _device_not_present_fix(mount: MountConfig | None) -> Lines:
    uuid = mount.device_uuid if mount else "<uuid>"
    return [
        format_line(FIX, "Plug in the drive and verify:"),
        format_command(shlex.join(["ls", "-la", f"/dev/disk/by-uuid/{uuid}"])),
        format_line(FIX, "Or with systemd:"),
        format_command(shlex.join(["udevadm", "info", f"/dev/disk/by-uuid/{uuid}"])),
    ]


def cleartext_device(
    vol: LocalVolume | RemoteVolume,
    mount: MountConfig,
    facts: RemediationFacts,
) -> tuple[str, bool]:
    """
    Cleartext device path to show in a fix, and whether it was discovered.

    ``luks-<uuid>`` is only udisks's default: a LUKS2 header label or an
    ``/etc/crypttab`` entry renames the mapper, so deriving the name from the
    container UUID produces an fstab line that never matches on such a host.
    Prefer the device udisks actually created (gathered into *facts* when the
    container is unlocked); fall back to the derived default otherwise, and
    let the caller say so.
    """
    discovered = facts.cleartext_devices.get(vol.slug)
    return (
        (discovered, True)
        if discovered is not None
        else (f"/dev/mapper/{cleartext_mapper_name(mount.device_uuid)}", False)
    )


def _mapper_name_caveat(level: int) -> Lines:
    return [
        format_line(
            level,
            "The container is locked, so the device above is udisks's default name."
            "  A LUKS2 header label or crypttab entry renames it — unlock the volume"
            " and check `lsblk` before writing it into fstab.",
        ),
    ]


def _fstab_line(
    vol: LocalVolume | RemoteVolume,
    mount: MountConfig | None,
    path: str,
    fs_and_options: str,
    ctx: TroubleshootContext,
) -> Lines:
    match mount:
        case MountConfig(encryption=None):
            return [
                format_command(
                    f"UUID={mount.device_uuid}  {path}  {fs_and_options}",
                    STEP,
                ),
            ]
        case MountConfig():
            device, discovered = cleartext_device(vol, mount, ctx.facts)
            return [
                format_command(f"{device}  {path}  {fs_and_options}", STEP),
                *([] if discovered else _mapper_name_caveat(STEP)),
            ]
        case None:
            return [format_command(f"UUID=<fs-uuid>  {path}  {fs_and_options}", STEP)]


def _fstab_mountpoint_mismatch_fix(
    vol_status: VolumeStatus,
    ctx: TroubleshootContext,
) -> Lines:
    """
    Fix a declared ``path`` that no ``/etc/fstab`` entry maps the device to.

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
    return [
        format_line(
            FIX,
            "No /etc/fstab entry maps the device to ",
            path,
            " on ",
            host,
            ".",
        ),
        *(
            [
                format_line(
                    FIX,
                    "The entry for ",
                    path,
                    " mounts ",
                    other_source,
                    ", a different device.",
                ),
            ]
            if other_source is not None
            else []
        ),
        format_line(
            FIX,
            "With a fixed 'path', udisks must mount the device there; without a"
            " matching fstab entry it would mount at /run/media/<user>/<label>.",
        ),
        format_line(FIX, "Option A — add an /etc/fstab entry (no crypttab needed):"),
        *_fstab_line(
            vol,
            vol.mount,
            path,
            "<FS>  noauto,nofail,x-udisks-auth  0 0",
            ctx,
        ),
        format_line(
            STEP,
            "Replace <FS> with the volume's filesystem type (e.g. btrfs, ext4);"
            " for btrfs also add user_subvol_rm_allowed to the options.",
        ),
        format_line(
            FIX,
            "Option B — remove 'path' from the volume config to use the mountpoint"
            " udisks discovers (/run/media/<user>/<label>).",
        ),
    ]


def _user_subvol_rm_fix(
    vol: LocalVolume | RemoteVolume,
    ctx: TroubleshootContext,
) -> Lines:
    """
    Fix a mount missing the ``user_subvol_rm_allowed`` option.

    The option is required for snapshot pruning.  nbkp does not pass it to
    udisks at mount time — udisks rejects any non-allowlisted mount option
    (``OptionNotPermitted``), which would fail the mount — so the option must
    come from operator config: ``/etc/fstab`` (udisks honors fstab verbatim) or,
    for the discovered ``/run/media`` model, the udisks mount-options allowlist.
    """
    path = vol.path or "<volume-path>"
    return [
        format_line(
            FIX,
            "The btrfs volume must be mounted with user_subvol_rm_allowed"
            " (needed for snapshot pruning).  Remount now (ephemeral):",
        ),
        format_command(
            wrap_cmd(
                ["sudo", "mount", "-o", "remount,user_subvol_rm_allowed", path],
                vol,
                ctx.resolved_endpoints,
            ),
        ),
        *_user_subvol_rm_persistence(vol, path, ctx),
    ]


def _user_subvol_rm_persistence(
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> Lines:
    match vol.mount:
        case None:
            # Externally-mounted volume: fstab is the only persistence mechanism.
            return [
                format_line(
                    FIX,
                    "To persist, add user_subvol_rm_allowed to the /etc/fstab"
                    " options for ",
                    path,
                    ".",
                ),
            ]
        case MountConfig():
            return _udisks_persistence(vol, path, ctx)


def _udisks_persistence(
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> Lines:
    return [
        format_line(FIX, "To persist (udisks-managed volume), use ONE of:"),
        format_line(FIX, "Option A — /etc/fstab (udisks honors fstab options):"),
        *_fstab_line(
            vol,
            vol.mount,
            path,
            "btrfs  noauto,nofail,x-udisks-auth,user_subvol_rm_allowed  0 0",
            ctx,
        ),
        format_line(
            FIX,
            "Option B — /etc/udisks2/mount_options.conf, then restart udisksd"
            " (for the discovered /run/media mountpoint):",
        ),
        format_command(_UDISKS_MOUNT_OPTIONS, STEP),
        format_line(
            STEP,
            "Both keys are required: btrfs_allow permits the option, btrfs_defaults"
            " applies it.  Scope to one device with a [/dev/disk/by-uuid/<uuid>]"
            " section (the unlocked cleartext device for encrypted volumes);"
            " see man udisks2.conf.",
        ),
    ]


def _passphrase_id(mount: MountConfig | None) -> str:
    return (
        mount.encryption.passphrase_id
        if mount and mount.encryption
        else "<passphrase-id>"
    )


def _passphrase_not_available_fix(
    mount: MountConfig | None,
    ctx: TroubleshootContext,
) -> Lines:
    pid = _passphrase_id(mount)
    return [
        format_line(
            FIX,
            "The drive is plugged in but its passphrase could not be retrieved.",
        ),
        format_line(FIX, "Check credential status:"),
        format_command(
            ctx.invocation.command(
                "credentials",
                "keyring-status",
                endpoint_flags=False,
            ),
            STEP,
        ),
        format_line(
            FIX,
            "Configure it for your credential provider (",
            ctx.config.credential_provider.value,
            "):",
        ),
        format_line(STEP, "keyring: ", shlex.join(["keyring", "set", "nbkp", pid])),
        format_line(STEP, "env: export ", passphrase_env_var(pid), "=..."),
        format_line(
            STEP,
            "command: ensure the credential-command prints it for id ",
            pid,
        ),
    ]


def _unlock_failed_fix(
    mount: MountConfig | None,
    ctx: TroubleshootContext,
) -> Lines:
    uuid = mount.device_uuid if mount else "<uuid>"
    return [
        format_line(FIX, "udisksctl failed to unlock the LUKS container."),
        format_line(
            FIX,
            "Verify the passphrase from your credential provider (passphrase-id '",
            _passphrase_id(mount),
            "') is correct:",
        ),
        format_command(
            ctx.invocation.command(
                "credentials",
                "keyring-status",
                endpoint_flags=False,
            ),
            STEP,
        ),
        format_line(FIX, "Confirm the device is a LUKS container:"),
        format_command(
            shlex.join(["sudo", "cryptsetup", "isLuks", f"/dev/disk/by-uuid/{uuid}"]),
            STEP,
        ),
        format_line(FIX, "Try unlocking manually to see the error:"),
        format_command(
            shlex.join(["udisksctl", "unlock", "-b", f"/dev/disk/by-uuid/{uuid}"]),
            STEP,
        ),
    ]


def _mount_failed_fix(
    vol: LocalVolume | RemoteVolume,
    mount: MountConfig | None,
    ctx: TroubleshootContext,
) -> Lines:
    match mount:
        case MountConfig(encryption=None):
            device = f"/dev/disk/by-uuid/{mount.device_uuid}"
        case MountConfig():
            device, _ = cleartext_device(vol, mount, ctx.facts)
        case None:
            device = "<device>"
    return [
        format_line(FIX, "udisksctl failed to mount the volume."),
        format_line(
            FIX,
            "Check the filesystem and, for a fixed 'path', that an /etc/fstab entry"
            " maps the device there (otherwise udisks mounts at"
            " /run/media/<user>/<label>).",
        ),
        format_line(
            FIX,
            "Ensure the polkit rule is installed (see polkit rules not configured);"
            " without it udisks denies the mount over SSH.",
        ),
        format_line(FIX, "Try mounting manually to see the error:"),
        format_command(
            wrap_cmd(["udisksctl", "mount", "-b", device], vol, ctx.resolved_endpoints),
            STEP,
        ),
    ]


def _polkit_rules_missing_fix(
    vol: LocalVolume | RemoteVolume,
    ctx: TroubleshootContext,
) -> Lines:
    user = polkit_user(vol, ctx.resolved_endpoints, ctx.local_user)
    block = ctx.facts.polkit_rules.get(user)
    return [
        format_line(
            FIX,
            "polkit rules not configured on ",
            host_label(vol, ctx.resolved_endpoints),
            ".",
        ),
        format_line(
            FIX,
            "Required so udisks authorizes unlock/mount/unmount/lock without an"
            " interactive prompt (nbkp runs over SSH / in inactive sessions).",
        ),
        *(
            [
                format_line(FIX, block.install_hint),
                format_command(block.content.rstrip(), STEP),
            ]
            if block is not None
            else []
        ),
        format_line(FIX, "Or generate it with:"),
        format_command(
            ctx.invocation.command(
                "disks",
                "setup-auth",
                "-u",
                user,
                endpoint_flags=False,
            ),
            STEP,
        ),
    ]


def format_source_endpoint_error_fix(
    error: SourceEndpointErrorKind,
    sync: SyncConfig,
    ctx: TroubleshootContext,
) -> Lines:
    """Return the fix for the error *error* on the source endpoint of *sync*."""
    src_ep = ctx.config.source_endpoint(sync)
    src_vol = ctx.config.volumes[src_ep.volume]
    path = endpoint_path(src_vol, src_ep.subdir)
    match error:
        case SourceEndpointErrorKind.SENTINEL_NOT_FOUND:
            return _sentinel_fix(src_vol, path, SOURCE_SENTINEL, ctx)
        case SourceEndpointErrorKind.SENTINEL_UNREADABLE:
            return _sentinel_unreadable_fix(src_vol, path, SOURCE_SENTINEL, ctx)
        case SourceEndpointErrorKind.LATEST_SYMLINK_UNREADABLE:
            return _latest_unreadable_fix(src_vol, path, ctx)
        case SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND:
            return [
                format_line(
                    FIX,
                    "Source has snapshots enabled but ",
                    f"{path}/{LATEST_LINK}",
                    " symlink does not exist. Create it:",
                ),
                *_reset_latest(src_vol, path, src_ep.slug, ctx),
            ]
        case SourceEndpointErrorKind.LATEST_SYMLINK_INVALID:
            return [
                format_line(
                    FIX,
                    "Source ",
                    f"{path}/{LATEST_LINK}",
                    " symlink points to an invalid target. Ensure the upstream sync"
                    " has run at least once, or reset it:",
                ),
                *_reset_latest(src_vol, path, src_ep.slug, ctx),
            ]
        case SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND:
            # Endpoint dir is expected to be user-writable by this point
            # (fixed via NOT_WRITABLE if needed), so a plain mkdir suffices
            # regardless of snapshot backend.
            return _mkdir(src_vol, f"{path}/{SNAPSHOTS_DIR}", ctx)
        case SourceEndpointErrorKind.VOLUME_INACTIVE:
            return [format_line(FIX, "Fix the volume errors reported above.")]


def format_destination_endpoint_error_fix(
    error: DestinationEndpointErrorKind,
    sync: SyncConfig,
    ctx: TroubleshootContext,
) -> Lines:
    """Return the fix for the error *error* on the destination endpoint of *sync*."""
    dst_ep = ctx.config.destination_endpoint(sync)
    dst_vol = ctx.config.volumes[dst_ep.volume]
    path = endpoint_path(dst_vol, dst_ep.subdir)
    match error:
        case (
            DestinationEndpointErrorKind.SENTINEL_NOT_FOUND
            | DestinationEndpointErrorKind.SENTINEL_UNREADABLE
            | DestinationEndpointErrorKind.NOT_WRITABLE
            | DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND
            | DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_WRITABLE
            | DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            | DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID
            | DestinationEndpointErrorKind.LATEST_SYMLINK_UNREADABLE
            | DestinationEndpointErrorKind.VOLUME_INACTIVE
        ):
            return _destination_layout_fix(error, dst_vol, path, dst_ep.slug, ctx)
        case _:
            return _destination_snapshot_backend_fix(error, dst_vol, path, ctx)


def format_destination_endpoint_warning_fix(
    warning: DestinationEndpointWarningKind,
    sync: SyncConfig,
    status: DestinationEndpointStatus,
    ctx: TroubleshootContext,
) -> Lines:
    """
    Return the fix for the warning *warning* on the destination endpoint of *sync*.

    *status* is that endpoint's status, whose diagnostics name the snapshot concerned.
    """
    dst_ep = ctx.config.destination_endpoint(sync)
    dst_vol = ctx.config.volumes[dst_ep.volume]
    match warning:
        case DestinationEndpointWarningKind.LATEST_SNAPSHOT_WRITABLE:
            latest = status.diagnostics.latest if status.diagnostics else None
            target = (
                latest.raw_target
                if latest is not None and latest.raw_target is not None
                else f"{SNAPSHOTS_DIR}/<snapshot>"
            )
            snapshot = f"{endpoint_path(dst_vol, dst_ep.subdir)}/{target}"
            return [
                format_line(
                    FIX,
                    "The snapshot ",
                    snapshot,
                    " can be modified: a delete that failed after clearing its"
                    " read-only flag, or a manual change, left it writable."
                    " Make it read-only again:",
                ),
                format_command(
                    wrap_cmd(
                        ["btrfs", "property", "set", snapshot, "ro", "true"],
                        dst_vol,
                        ctx.resolved_endpoints,
                    ),
                ),
            ]


def _destination_layout_fix(
    error: DestinationEndpointErrorKind,
    vol: LocalVolume | RemoteVolume,
    path: str,
    endpoint_slug: str,
    ctx: TroubleshootContext,
) -> Lines:
    """Return the fixes for the directory layout shared by every destination."""
    snapshots = f"{path}/{SNAPSHOTS_DIR}"
    latest = f"{path}/{LATEST_LINK}"
    match error:
        case DestinationEndpointErrorKind.SENTINEL_NOT_FOUND:
            return _sentinel_fix(vol, path, DESTINATION_SENTINEL, ctx)
        case DestinationEndpointErrorKind.SENTINEL_UNREADABLE:
            return _sentinel_unreadable_fix(vol, path, DESTINATION_SENTINEL, ctx)
        case DestinationEndpointErrorKind.NOT_WRITABLE:
            return [
                format_line(
                    FIX,
                    "The destination endpoint directory ",
                    f"{path}/",
                    " is not writable. Fix permissions:",
                ),
                *_chown(vol, path, ctx),
            ]
        case DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND:
            # Endpoint dir is expected to be user-writable by this point
            # (fixed via NOT_WRITABLE if needed), so a plain mkdir suffices
            # regardless of snapshot backend.
            return _mkdir(vol, snapshots, ctx)
        case DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_WRITABLE:
            return [
                format_line(
                    FIX,
                    f"The destination {SNAPSHOTS_DIR}/ directory (",
                    snapshots,
                    ") is not writable. Fix permissions:",
                ),
                *_chown(vol, snapshots, ctx),
            ]
        case DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND:
            return [
                format_line(
                    FIX,
                    "Destination has snapshots enabled but ",
                    latest,
                    " symlink does not exist. Create it:",
                ),
                *_reset_latest(vol, path, endpoint_slug, ctx),
            ]
        case DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID:
            return [
                format_line(
                    FIX,
                    "Destination ",
                    latest,
                    " symlink points to an invalid target. Reset it:",
                ),
                *_reset_latest(vol, path, endpoint_slug, ctx),
            ]
        case DestinationEndpointErrorKind.LATEST_SYMLINK_UNREADABLE:
            return _latest_unreadable_fix(vol, path, ctx)
        case _:
            return [format_line(FIX, "Fix the volume errors reported above.")]


def _destination_snapshot_backend_fix(
    error: DestinationEndpointErrorKind,
    vol: LocalVolume | RemoteVolume,
    path: str,
    ctx: TroubleshootContext,
) -> Lines:
    staging = f"{path}/{STAGING_DIR}"
    snapshots = f"{path}/{SNAPSHOTS_DIR}"
    match error:
        case DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME:
            # staging/ exists as a plain directory, so `subvolume create` on its
            # path fails until the directory is moved out of the way.
            staging_aside = f"{staging}.old"
            return [
                format_line(
                    FIX,
                    f"The destination {STAGING_DIR}/ directory (",
                    staging,
                    ") is a plain directory, not a btrfs subvolume."
                    " Move it aside and create the subvolume in its place:",
                ),
                *[
                    format_command(wrap_cmd(cmd, vol, ctx.resolved_endpoints))
                    for cmd in (
                        ["sudo", "mv", staging, staging_aside],
                        ["sudo", "btrfs", "subvolume", "create", staging],
                        ["sudo", "mkdir", "-p", snapshots],
                        ["sudo", "chown", "<user>:<group>", staging, snapshots],
                    )
                ],
                format_line(
                    FIX,
                    "Remove ",
                    staging_aside,
                    " once the next sync has succeeded.",
                ),
            ]
        case DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND:
            # Endpoint dir is expected to be user-writable by this point
            # (fixed via NOT_WRITABLE if needed), so subvolume create runs
            # without sudo (kernel 5.8+).
            return [
                format_command(
                    wrap_cmd(
                        ["btrfs", "subvolume", "create", staging],
                        vol,
                        ctx.resolved_endpoints,
                    ),
                ),
            ]
        case DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_WRITABLE:
            return [
                format_line(
                    FIX,
                    f"The destination {STAGING_DIR}/ directory (",
                    staging,
                    ") is not writable. Fix permissions:",
                ),
                *_chown(vol, staging, ctx),
            ]
        case DestinationEndpointErrorKind.VOL_NOT_BTRFS:
            return [
                format_line(FIX, "The destination is not on a btrfs filesystem."),
                format_line(
                    FIX,
                    "Move it to a btrfs volume, or use hard-link-snapshots instead.",
                ),
            ]
        case DestinationEndpointErrorKind.VOL_FS_TYPE_UNKNOWN:
            return [
                format_line(
                    FIX,
                    "The volume's filesystem type could not be read (`stat -f`, or"
                    " `df -P` and `mount` for a local volume on macOS).",
                ),
                format_line(FIX, "Check that the volume path exists and is readable:"),
                format_command(
                    wrap_cmd(
                        ["stat", "-f", "-c", "%T", vol.path or "<volume-path>"],
                        vol,
                        ctx.resolved_endpoints,
                    ),
                    STEP,
                ),
            ]
        case DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM:
            return _user_subvol_rm_fix(vol, ctx)
        case DestinationEndpointErrorKind.VOL_NO_HARDLINK_SUPPORT:
            return [
                format_line(
                    FIX,
                    "The destination filesystem does not support hard links (e.g."
                    " FAT/exFAT). Use a filesystem like ext4, xfs, or btrfs, or use"
                    " btrfs-snapshots instead.",
                ),
            ]
        case _:
            return []


def format_sync_error_fix(
    sync: SyncConfig,
    error: SyncErrorKind,
    ctx: TroubleshootContext,
) -> Lines:
    """Return the fix for the sync-level error *error* on *sync*."""
    match error:
        case SyncErrorKind.DISABLED:
            return [format_line(FIX, "Enable the sync in the configuration file.")]
        case SyncErrorKind.ENDPOINT_HOST_ERRORS:
            return [
                format_line(
                    FIX,
                    "A host this sync runs on is missing required tools. Fix the SSH"
                    " endpoint errors reported above (for local volumes, under"
                    " 'localhost').",
                ),
            ]
        case SyncErrorKind.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM:
            src_ep = ctx.config.source_endpoint(sync)
            path = endpoint_path(ctx.config.volumes[src_ep.volume], src_ep.subdir)
            return [
                format_line(
                    FIX,
                    "Source ",
                    f"{path}/{LATEST_LINK}",
                    " points to /dev/null but no enabled upstream sync in this run"
                    " writes to this endpoint. Either run the upstream sync first"
                    " (or select it too with --sync) or reset the symlink to point"
                    " to a valid snapshot.",
                ),
            ]
        case SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING:
            return [
                format_line(
                    FIX,
                    "The source endpoint's latest symlink points to /dev/null (no"
                    " snapshot yet). In dry-run mode, the upstream sync does not"
                    " create a real snapshot, so this sync is skipped. Run without"
                    " --dry-run to execute the full chain.",
                ),
            ]
        case (
            SyncErrorKind.SOURCE_ENDPOINT_INACTIVE
            | SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE
        ):
            return [format_line(FIX, "Fix the endpoint errors reported above.")]
