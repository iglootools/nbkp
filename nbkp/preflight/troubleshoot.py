"""
What ``preflight troubleshoot`` explains, and the facts its fixes need.

Issues are first *collected* into structured :class:`TroubleshootIssue`
records (pure, order-preserving, deduplicated).  The facts their fixes need
that take live work to establish — the cleartext device of an unlocked LUKS
container (an ``lsblk`` probe, possibly over SSH), the newest snapshot of an
endpoint whose ``latest`` is broken, and the polkit rule text — are then
*gathered* into :class:`RemediationFacts`.  Rendering them, for humans or as
JSON, is :mod:`nbkp.preflight.output`'s job, and does no I/O.
"""

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field

from nbkp.config import (
    Config,
    LocalVolume,
    MountConfig,
    RemoteVolume,
    SyncConfig,
    SyncEndpoint,
    resolve_endpoint_path,
)
from nbkp.disks import (
    AuthRuleBlock,
    DeviceProbeError,
    discover_cleartext_device,
    generate_auth_rules,
)
from nbkp.fsprotocol import SNAPSHOTS_DIR, Snapshot
from nbkp.policy import Severity, Strictness
from nbkp.preflight.probes import VolumeReadError, list_snapshot_dir
from nbkp.preflight.severity import (
    PreflightErrorKind,
    PreflightWarningKind,
    severity_for_error,
)
from nbkp.preflight.status.endpoint import (
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    SourceEndpointErrorKind,
    SourceEndpointStatus,
)
from nbkp.preflight.status.ssh import SshEndpointStatus
from nbkp.preflight.status.sync import SyncErrorKind, SyncStatus
from nbkp.preflight.status.volume import VolumeErrorKind, VolumeStatus
from nbkp.remote.dispatch import VolumeCommandRunner, run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.remote.errors import ssh_connection_errors

# Cascade errors are pointers to inactive lower layers — they have no
# actionable fix at their own layer, so troubleshoot skips them.
_CASCADE_ERRORS: frozenset[PreflightErrorKind] = frozenset(
    {
        VolumeErrorKind.SSH_ENDPOINT_INACTIVE,
        SourceEndpointErrorKind.VOLUME_INACTIVE,
        DestinationEndpointErrorKind.VOLUME_INACTIVE,
        SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
        SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,
    },
)


class Layer(enum.StrEnum):
    """Where an issue originates; the value is the JSON ``layer`` field."""

    SSH_ENDPOINT = "ssh-endpoint"
    VOLUME = "volume"
    SOURCE_ENDPOINT = "source-endpoint"
    DESTINATION_ENDPOINT = "destination-endpoint"
    SYNC = "sync"


@dataclass(frozen=True)
class TroubleshootIssue:
    """One error (or warning) to explain, with what formatting its fix needs."""

    layer: Layer
    subject: str
    """Slug of the SSH endpoint / volume / sync endpoint / sync."""
    error: PreflightErrorKind | PreflightWarningKind
    severity: Severity
    ssh_status: SshEndpointStatus | None = None
    dst_status: DestinationEndpointStatus | None = None
    """The destination endpoint a warning is about, whose diagnostics its
    fix names."""
    vol_status: VolumeStatus | None = None
    sync: SyncConfig | None = None
    """A sync using the endpoint (endpoint layers) or the sync itself."""


def _ssh_issues(
    ssh_statuses: dict[str, SshEndpointStatus],
    strictness: Strictness,
) -> list[TroubleshootIssue]:
    return [
        *(
            TroubleshootIssue(
                Layer.SSH_ENDPOINT,
                status.slug,
                error,
                severity_for_error(error, strictness),
                ssh_status=status,
            )
            for status in ssh_statuses.values()
            for error in status.errors
        ),
        *(
            TroubleshootIssue(
                Layer.SSH_ENDPOINT,
                status.slug,
                warning,
                Severity.WARNING,
                ssh_status=status,
            )
            for status in ssh_statuses.values()
            for warning in status.warnings
        ),
    ]


def _volume_issues(
    vol_statuses: dict[str, VolumeStatus],
    strictness: Strictness,
) -> list[TroubleshootIssue]:
    return [
        TroubleshootIssue(
            Layer.VOLUME,
            vs.slug,
            error,
            severity_for_error(error, strictness),
            vol_status=vs,
        )
        for vs in vol_statuses.values()
        for error in vs.errors
        if error not in _CASCADE_ERRORS
    ]


def _endpoint_issues(
    layer: Layer,
    endpoints: list[
        tuple[SourceEndpointStatus | DestinationEndpointStatus, SyncConfig]
    ],
    strictness: Strictness,
) -> list[TroubleshootIssue]:
    """
    Issues of endpoints shared by several syncs, reported once.

    Deduplicated per ``(endpoint, error)`` — the first sync using the
    endpoint provides the fix context.
    """
    candidates = [
        ((ep.endpoint_slug, error), sync)
        for ep, sync in endpoints
        for error in ep.errors
        if error not in _CASCADE_ERRORS
    ]
    first = dict(reversed(candidates))
    return [
        TroubleshootIssue(
            layer,
            slug,
            error,
            severity_for_error(error, strictness),
            sync=first[key],
        )
        for key in dict.fromkeys(key for key, _ in candidates)
        for slug, error in [key]
    ]


def _destination_warning_issues(
    sync_statuses: dict[str, SyncStatus],
) -> list[TroubleshootIssue]:
    """Warnings of destination endpoints, reported once per endpoint."""
    candidates = [
        ((ss.destination_endpoint_status.endpoint_slug, warning), ss)
        for ss in sync_statuses.values()
        for warning in ss.destination_endpoint_status.warnings
    ]
    first = dict(reversed(candidates))
    return [
        TroubleshootIssue(
            Layer.DESTINATION_ENDPOINT,
            slug,
            warning,
            Severity.WARNING,
            sync=first[key].config,
            dst_status=first[key].destination_endpoint_status,
        )
        for key in dict.fromkeys(key for key, _ in candidates)
        for slug, warning in [key]
    ]


def _sync_issues(
    sync_statuses: dict[str, SyncStatus],
    strictness: Strictness,
) -> list[TroubleshootIssue]:
    return [
        TroubleshootIssue(
            Layer.SYNC,
            ss.slug,
            error,
            severity_for_error(error, strictness),
            sync=ss.config,
        )
        for ss in sync_statuses.values()
        for error in ss.errors
        if error not in _CASCADE_ERRORS
    ]


def collect_issues(
    ssh_statuses: dict[str, SshEndpointStatus],
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> list[TroubleshootIssue]:
    """Every actionable issue, layer by layer, in display order."""
    return [
        *_ssh_issues(ssh_statuses, strictness),
        *_volume_issues(vol_statuses, strictness),
        *_endpoint_issues(
            Layer.SOURCE_ENDPOINT,
            [(ss.source_endpoint_status, ss.config) for ss in sync_statuses.values()],
            strictness,
        ),
        *_endpoint_issues(
            Layer.DESTINATION_ENDPOINT,
            [
                (ss.destination_endpoint_status, ss.config)
                for ss in sync_statuses.values()
            ],
            strictness,
        ),
        *_destination_warning_issues(sync_statuses),
        *_sync_issues(sync_statuses, strictness),
    ]


@dataclass(frozen=True)
class RemediationFacts:
    """Facts the fixes show that had to be probed or generated first."""

    cleartext_devices: Mapping[str, str] = field(default_factory=dict[str, str])
    """Volume slug → cleartext device udisks actually created (e.g.
    ``/dev/mapper/<label>``).  A volume is absent when its container is locked
    or discovery failed; the fix then shows udisks's default name with a
    caveat."""
    polkit_rules: Mapping[str, AuthRuleBlock] = field(
        default_factory=dict[str, AuthRuleBlock],
    )
    """System user → the polkit rule block authorizing that user.  A user is
    absent when no volume declares a ``mount`` section."""
    newest_snapshots: Mapping[str, str | None] = field(
        default_factory=dict[str, str | None],
    )
    """Sync endpoint slug → name of its newest snapshot, ``None`` when it has
    none.  An endpoint is absent when its snapshots could not be listed; the
    fix then names a placeholder for the user to fill in."""


def polkit_user(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
    local_user: str,
) -> str:
    """
    Return the system user the polkit rule must authorize to mount *vol*.

    The SSH user of a remote volume's endpoint, or *local_user* for a local
    volume and for a remote one whose endpoint sets no user.
    """
    match vol:
        case RemoteVolume():
            ep = resolved_endpoints.get(vol.slug)
            return ep.server.user if ep and ep.server.user else local_user
        case LocalVolume():
            return local_user


# Errors whose fix names the cleartext device of an encrypted volume (an
# fstab line or a manual ``udisksctl mount``).
_NEEDS_CLEARTEXT_DEVICE: frozenset[PreflightErrorKind] = frozenset(
    {
        VolumeErrorKind.FSTAB_MOUNTPOINT_MISMATCH,
        VolumeErrorKind.MOUNT_FAILED,
        DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM,
    },
)


def _issue_volume(
    issue: TroubleshootIssue,
    config: Config,
) -> LocalVolume | RemoteVolume | None:
    match issue:
        case TroubleshootIssue(vol_status=VolumeStatus() as vs):
            return vs.config
        case TroubleshootIssue(
            error=DestinationEndpointErrorKind(),
            sync=SyncConfig() as sync,
        ):
            return config.volumes[config.destination_endpoint(sync).volume]
        case _:
            return None


def _discover_cleartext_device(
    vol: LocalVolume | RemoteVolume,
    mount: MountConfig,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> str | None:
    """
    Discover the cleartext device, or ``None`` when it cannot be discovered.

    Discovery runs ``lsblk``, which this code path cannot assume exists — it
    is the *error reporting* path, reached precisely when the host is not in
    the expected state, and it also runs on machines with no udisks at all
    (e.g. ``nbkp demo output`` on macOS).  A failed probe, a missing tool or a
    dropped connection therefore degrades to "not discovered" rather than
    propagating.
    """
    try:
        return discover_cleartext_device(
            vol,
            mount.device_uuid,
            resolved_endpoints,
            run=run,
        )
    except (*ssh_connection_errors(), DeviceProbeError):
        return None


# Errors whose fix points ``latest`` at the endpoint's newest snapshot.
_NEEDS_NEWEST_SNAPSHOT: frozenset[PreflightErrorKind] = frozenset(
    {
        SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND,
        SourceEndpointErrorKind.LATEST_SYMLINK_INVALID,
        DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND,
        DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID,
    },
)


def _issue_endpoint(issue: TroubleshootIssue, config: Config) -> SyncEndpoint | None:
    match issue:
        case TroubleshootIssue(error=SourceEndpointErrorKind(), sync=SyncConfig() as s):
            return config.source_endpoint(s)
        case TroubleshootIssue(
            error=DestinationEndpointErrorKind(),
            sync=SyncConfig() as s,
        ):
            return config.destination_endpoint(s)
        case _:
            return None


def _list_endpoint_snapshots(
    endpoint: SyncEndpoint,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> list[Snapshot] | None:
    """
    List the endpoint's snapshots, oldest first.

    ``[]`` when ``snapshots/`` is absent, and ``None`` when they could not be listed.
    Like cleartext discovery, a failure degrades instead of propagating: the fix then
    asks the user to look.
    """
    vol = config.volumes[endpoint.volume]
    if vol.path is None:
        return None
    path = f"{resolve_endpoint_path(vol, endpoint.subdir)}/{SNAPSHOTS_DIR}"
    try:
        return list_snapshot_dir(vol, path, resolved_endpoints, run=run)
    except (VolumeReadError, *ssh_connection_errors()):
        return None


def _find_newest_snapshots(
    issues: list[TroubleshootIssue],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner,
) -> dict[str, str | None]:
    """
    Map each endpoint slug to its newest snapshot name.

    ``None`` when it has none; endpoints whose snapshots could not be listed are left
    out.
    """
    endpoints = {
        ep.slug: ep
        for issue in issues
        if issue.error in _NEEDS_NEWEST_SNAPSHOT
        for ep in [_issue_endpoint(issue, config)]
        if ep is not None
    }
    listed = {
        slug: _list_endpoint_snapshots(ep, config, resolved_endpoints, run=run)
        for slug, ep in endpoints.items()
    }
    return {
        slug: snapshots[-1].name if snapshots else None
        for slug, snapshots in listed.items()
        if snapshots is not None
    }


def gather_remediation_facts(
    issues: list[TroubleshootIssue],
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
    local_user: str,
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> RemediationFacts:
    """
    Gather the facts the remediations need, probing only what an issue needs.

    Only issues whose fix shows a cleartext device trigger a discovery, once per
    encrypted volume; only a missing polkit rule triggers rule generation; only a
    missing or invalid ``latest`` triggers a snapshot listing, once per endpoint.

    Discovery runs ``lsblk`` on the volume's host through *run*
    (:func:`remote.dispatch.run_on_volume` unless a test injects a fake).
    *local_user* is the user nbkp runs as, which the polkit rule authorizes for
    local volumes.
    """
    encrypted = {
        vol.slug: (vol, vol.mount)
        for issue in issues
        if issue.error in _NEEDS_CLEARTEXT_DEVICE
        for vol in [_issue_volume(issue, config)]
        if vol is not None
        and vol.mount is not None
        and vol.mount.encryption is not None
    }
    discovered = {
        slug: _discover_cleartext_device(vol, mount, resolved_endpoints, run=run)
        for slug, (vol, mount) in encrypted.items()
    }
    users = {
        polkit_user(issue.vol_status.config, resolved_endpoints, local_user)
        for issue in issues
        if issue.error is VolumeErrorKind.POLKIT_RULES_MISSING
        and issue.vol_status is not None
    }
    rules = {user: generate_auth_rules(config, user).polkit_block() for user in users}
    return RemediationFacts(
        cleartext_devices={
            slug: device for slug, device in discovered.items() if device is not None
        },
        polkit_rules={user: block for user, block in rules.items() if block},
        newest_snapshots=_find_newest_snapshots(
            issues,
            config,
            resolved_endpoints,
            run=run,
        ),
    )
