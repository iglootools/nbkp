"""Scenarios about whether a sync can run at all: disabled, unreachable,
excluded, sentinels missing."""

from ....config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from ... import (
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    SourceEndpointDiagnostics,
    SourceEndpointErrorKind,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SyncErrorKind,
    SyncStatus,
    VolumeErrorKind,
    VolumeStatus,
)
from ..common import (
    LOCALHOST_SSH_STATUS,
    inactive_dst_ep_status,
    standalone_ssh_status,
)
from .scenario import (
    BOTH_ENDPOINTS_INACTIVE,
    LAPTOP_SRC,
    Scenario,
    ScenarioStatuses,
    SharedStatuses,
    healthy_caps,
    sync_from_laptop,
    volume_status,
)

# Standalone endpoint whose host refuses our key (AUTH_FAILED).
_AUTH_FAILED_SSH = SshEndpointStatus.from_diagnostics(
    slug="auth-fail",
    diagnostics=SshEndpointDiagnostics(
        ssh_reachable=False,
        ssh_auth_failed=True,
        ssh_error="Authentication failed.",
    ),
)


def _standalone_statuses(_config: Config, _shared: SharedStatuses) -> ScenarioStatuses:
    # bastion reachable, bastion2 unreachable, auth-fail refuses our key
    return ScenarioStatuses(
        standalone_ssh_endpoints=(
            standalone_ssh_status("bastion"),
            standalone_ssh_status("bastion2", reachable=False),
            standalone_ssh_status("nas-public"),
            _AUTH_FAILED_SSH,
        )
    )


STANDALONE_ENDPOINTS = Scenario(
    statuses=_standalone_statuses,
    ssh_endpoints=(SshEndpoint(slug="auth-fail", host="10.0.0.9", user="backup"),),
)


def _disabled_statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="disabled-sync",
                config=config.syncs["disabled-sync"],
                source_endpoint_status=shared.laptop_src_inactive,
                destination_endpoint_status=inactive_dst_ep_status(
                    "dst-disabled", shared.usb_drive
                ),
                errors=[SyncErrorKind.DISABLED],
            ),
        )
    )


DISABLED_SYNC = Scenario(
    statuses=_disabled_statuses,
    volumes=(LocalVolume(slug="usb-1", path="/mnt/usb-1"),),
    sync_endpoints=(SyncEndpoint(slug="dst-disabled", volume="usb-1"),),
    syncs=(
        SyncConfig(
            slug="disabled-sync",
            source=LAPTOP_SRC,
            destination="dst-disabled",
            enabled=False,
        ),
    ),
)


def _unavailable_statuses(config: Config, shared: SharedStatuses) -> ScenarioStatuses:
    # SSH unreachable on destination (Layer 1), source vol sentinel missing
    # (Layer 2)
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="unavailable-volumes",
                config=config.syncs["unavailable-volumes"],
                source_endpoint_status=shared.laptop_src_inactive,
                destination_endpoint_status=inactive_dst_ep_status(
                    "dst-unavail", shared.nas_unreachable
                ),
                errors=list(BOTH_ENDPOINTS_INACTIVE),
            ),
        )
    )


UNAVAILABLE_VOLUMES = Scenario(
    statuses=_unavailable_statuses,
    sync_endpoints=(
        # Subdir (not volume root) so it doesn't overlap the other nas-backup
        # destination endpoints (dst-rsync-missing, …).
        SyncEndpoint(slug="dst-unavail", volume="nas-backup", subdir="unavail"),
    ),
    syncs=(sync_from_laptop("unavailable-volumes", "dst-unavail"),),
)


def _missing_sentinels_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # Endpoint sentinels missing (Layer 3)
    usb2 = volume_status(config, "usb-2", LOCALHOST_SSH_STATUS, healthy_caps())
    return ScenarioStatuses(
        syncs=(
            SyncStatus(
                slug="missing-sentinels",
                config=config.syncs["missing-sentinels"],
                source_endpoint_status=SourceEndpointStatus(
                    endpoint_slug=LAPTOP_SRC,
                    volume_status=shared.laptop_active,
                    diagnostics=SourceEndpointDiagnostics(
                        endpoint_slug=LAPTOP_SRC,
                        sentinel_exists=False,
                    ),
                    errors=[SourceEndpointErrorKind.SENTINEL_NOT_FOUND],
                ),
                destination_endpoint_status=DestinationEndpointStatus(
                    endpoint_slug="dst-sentinels",
                    volume_status=usb2,
                    diagnostics=DestinationEndpointDiagnostics(
                        endpoint_slug="dst-sentinels",
                        sentinel_exists=False,
                        endpoint_writable=True,
                    ),
                    errors=[DestinationEndpointErrorKind.SENTINEL_NOT_FOUND],
                ),
                errors=list(BOTH_ENDPOINTS_INACTIVE),
            ),
        )
    )


MISSING_SENTINELS = Scenario(
    statuses=_missing_sentinels_statuses,
    volumes=(LocalVolume(slug="usb-2", path="/mnt/usb-2"),),
    sync_endpoints=(SyncEndpoint(slug="dst-sentinels", volume="usb-2"),),
    syncs=(sync_from_laptop("missing-sentinels", "dst-sentinels"),),
)

# home-nas: location excluded
_HOME_NAS_SSH = SshEndpointStatus.from_diagnostics(
    slug="home-only",
    diagnostics=SshEndpointDiagnostics(location_excluded=True),
)


def _location_excluded_statuses(
    config: Config, shared: SharedStatuses
) -> ScenarioStatuses:
    # SshEndpointErrorKind.LOCATION_EXCLUDED (Layer 1 error → cascade to Layer 2)
    home_nas = VolumeStatus(
        slug="home-nas",
        config=config.volumes["home-nas"],
        ssh_endpoint_status=_HOME_NAS_SSH,
        diagnostics=None,
        errors=[VolumeErrorKind.SSH_ENDPOINT_INACTIVE],
    )
    return ScenarioStatuses(
        volumes=(home_nas,),
        syncs=(
            SyncStatus(
                slug="location-excluded",
                config=config.syncs["location-excluded"],
                source_endpoint_status=shared.laptop_src_inactive,
                destination_endpoint_status=inactive_dst_ep_status(
                    "dst-loc-excluded", home_nas
                ),
                errors=list(BOTH_ENDPOINTS_INACTIVE),
            ),
        ),
    )


LOCATION_EXCLUDED = Scenario(
    statuses=_location_excluded_statuses,
    ssh_endpoints=(
        SshEndpoint(slug="home-only", host="192.168.1.50", location="home"),
    ),
    volumes=(RemoteVolume(slug="home-nas", ssh_endpoint="home-only", path="/mnt/nas"),),
    sync_endpoints=(SyncEndpoint(slug="dst-loc-excluded", volume="home-nas"),),
    syncs=(sync_from_laptop("location-excluded", "dst-loc-excluded"),),
)
