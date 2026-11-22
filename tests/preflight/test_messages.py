from enum import StrEnum

import pytest

from nbkp.preflight import (
    DestinationEndpointErrorKind,
    SourceEndpointErrorKind,
    SshEndpointErrorKind,
    SyncErrorKind,
    VolumeErrorKind,
    format_problem,
    format_problems,
)
from nbkp.preflight.severity import PreflightErrorKind, PreflightWarningKind
from nbkp.preflight.status.ssh import SshEndpointWarningKind
from nbkp.snapshots.errors import SnapshotOp
from nbkp.snapshots.models import SnapshotSkipReason
from nbkp.snapshots.output import format_operation, format_skip_reason

_PREFLIGHT_KINDS: list[type[StrEnum]] = [
    VolumeErrorKind,
    SshEndpointErrorKind,
    SshEndpointWarningKind,
    SyncErrorKind,
    SourceEndpointErrorKind,
    DestinationEndpointErrorKind,
]


@pytest.mark.parametrize(
    "kind",
    [member for enum in _PREFLIGHT_KINDS for member in enum],
    ids=lambda kind: f"{type(kind).__name__}.{kind.name}",
)
def test_every_preflight_kind_has_a_sentence(
    kind: PreflightErrorKind | PreflightWarningKind,
) -> None:
    assert format_problem(kind)


def test_kinds_sharing_a_token_keep_their_own_sentence() -> None:
    assert format_problem(SourceEndpointErrorKind.SENTINEL_NOT_FOUND) == (
        ".nbkp-src sentinel not found"
    )
    assert format_problem(DestinationEndpointErrorKind.SENTINEL_NOT_FOUND) == (
        ".nbkp-dst sentinel not found"
    )


def test_problems_join_in_order() -> None:
    assert format_problems(
        [SyncErrorKind.DISABLED, SyncErrorKind.SOURCE_ENDPOINT_INACTIVE]
    ) == ("disabled, source endpoint inactive")


@pytest.mark.parametrize("op", list(SnapshotOp))
def test_every_snapshot_operation_has_a_sentence(op: SnapshotOp) -> None:
    assert format_operation(op)


@pytest.mark.parametrize("reason", list(SnapshotSkipReason))
def test_every_skip_reason_has_a_sentence(reason: SnapshotSkipReason) -> None:
    assert format_skip_reason(reason)
