"""
Tests for which issues troubleshoot collects.

Cascade errors only point at a lower layer, so they are skipped; an endpoint
several syncs share is explained once, with the first sync as fix context.
"""

from nbkp.config import LocalVolume, SyncConfig
from nbkp.preflight import (
    DestinationEndpointErrorKind,
    Layer,
    SourceEndpointErrorKind,
    SyncErrorKind,
    VolumeErrorKind,
    collect_issues,
)
from nbkp.preflight.status.endpoint import (
    DestinationEndpointDiagnostics,
    DestinationEndpointStatus,
    SourceEndpointStatus,
)
from nbkp.preflight.status.ssh import SshEndpointDiagnostics, SshEndpointStatus
from nbkp.preflight.status.sync import SyncStatus
from nbkp.preflight.status.volume import VolumeStatus

_LOCALHOST = SshEndpointStatus(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(),
    errors=[],
)


def _volume(slug: str, errors: list[VolumeErrorKind]) -> VolumeStatus:
    return VolumeStatus(
        slug=slug,
        config=LocalVolume(slug=slug, path=f"/mnt/{slug}"),
        ssh_endpoint_status=_LOCALHOST,
        diagnostics=None,
        errors=errors,
    )


def _sync(
    slug: str,
    *,
    source: SourceEndpointStatus,
    destination: DestinationEndpointStatus,
    errors: list[SyncErrorKind],
) -> SyncStatus:
    return SyncStatus(
        slug=slug,
        config=SyncConfig(
            slug=slug,
            source=source.endpoint_slug,
            destination=destination.endpoint_slug,
        ),
        source_endpoint_status=source,
        destination_endpoint_status=destination,
        errors=errors,
    )


def _source(slug: str, errors: list[SourceEndpointErrorKind]) -> SourceEndpointStatus:
    return SourceEndpointStatus(
        endpoint_slug=slug,
        volume_status=_volume("src", []),
        diagnostics=None,
        errors=errors,
    )


def _missing_sentinel_destination(slug: str) -> DestinationEndpointStatus:
    return DestinationEndpointStatus(
        endpoint_slug=slug,
        volume_status=_volume("dst", []),
        diagnostics=DestinationEndpointDiagnostics(
            endpoint_slug=slug,
            sentinel_exists=False,
            endpoint_writable=False,
        ),
        errors=[DestinationEndpointErrorKind.SENTINEL_NOT_FOUND],
    )


class TestCollectIssues:
    def test_shared_destination_reported_once_with_first_sync(self) -> None:
        shared = _missing_sentinel_destination("ep-shared")
        statuses = {
            slug: _sync(
                slug,
                source=_source(f"ep-{slug}", []),
                destination=shared,
                errors=[SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
            )
            for slug in ("first", "second")
        }

        issues = collect_issues({}, {}, statuses)

        assert [(i.layer, i.subject, i.error) for i in issues] == [
            (
                Layer.DESTINATION_ENDPOINT,
                "ep-shared",
                DestinationEndpointErrorKind.SENTINEL_NOT_FOUND,
            ),
        ]
        assert issues[0].sync == statuses["first"].config

    def test_cascade_errors_give_no_issue(self) -> None:
        inactive_volume = _volume("dst", [VolumeErrorKind.SSH_ENDPOINT_INACTIVE])
        destination = DestinationEndpointStatus(
            endpoint_slug="ep-dst",
            volume_status=inactive_volume,
            diagnostics=None,
            errors=[DestinationEndpointErrorKind.VOLUME_INACTIVE],
        )
        sync = _sync(
            "s",
            source=_source("ep-src", [SourceEndpointErrorKind.VOLUME_INACTIVE]),
            destination=destination,
            errors=[
                SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
                SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,
            ],
        )

        issues = collect_issues({}, {"dst": inactive_volume}, {"s": sync})

        assert issues == []
