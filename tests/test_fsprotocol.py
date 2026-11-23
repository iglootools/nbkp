"""Tests for nbkp.fsprotocol (snapshot name format)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from nbkp.fsprotocol import (
    InvalidSnapshotNameError,
    Snapshot,
    format_snapshot_name,
    parse_snapshot_name,
)

_TS = datetime(2026, 3, 6, 14, 30, 5, 123000, tzinfo=UTC)


class TestParseSnapshotName:
    @pytest.mark.parametrize("macos_local", [False, True])
    def test_round_trips_both_forms(self, *, macos_local: bool) -> None:
        name = format_snapshot_name(_TS, macos_local=macos_local)

        assert parse_snapshot_name(name) == _TS

    def test_returns_aware_utc(self) -> None:
        parsed = parse_snapshot_name("2026-03-06T14:30:05.123Z")

        assert parsed.tzinfo is UTC

    @pytest.mark.parametrize(
        "name",
        [
            "2026-01-01T00",
            "2026-03-07T09:00:00",
            "2026-03-07T09:00:00Z",
            "2026-03-07T09:00:00.000",
            "2026-03-07T09:00:00.000+01:00",
            "2026-03-07T09:00-00.000Z",
            "2026-03-07T09:00:00.000Z.bak",
            "2026-13-07T09:00:00.000Z",
            "lost+found",
        ],
    )
    def test_rejects_other_names(self, name: str) -> None:
        with pytest.raises(InvalidSnapshotNameError) as info:
            parse_snapshot_name(name)
        assert info.value.name == name


class TestFormatSnapshotName:
    @pytest.mark.parametrize(
        "now",
        [
            datetime(2026, 3, 6, 14, 30),  # noqa: DTZ001 -- naive on purpose: rejected
            datetime(2026, 3, 6, 14, 30, tzinfo=timezone(timedelta(hours=1))),
        ],
    )
    def test_rejects_a_time_not_at_utc(self, now: datetime) -> None:
        """Its name would lack the Z, so parse_snapshot_name would not see it."""
        with pytest.raises(ValueError, match="UTC-aware"):
            format_snapshot_name(now)


class TestSnapshotCreate:
    @pytest.mark.parametrize("macos_local", [False, True])
    def test_timestamp_matches_the_name(self, *, macos_local: bool) -> None:
        """A sub-millisecond time is truncated, as the name truncates it."""
        now = _TS + timedelta(microseconds=456)

        snapshot = Snapshot.create(now, macos_local=macos_local)

        assert snapshot.timestamp == _TS
        assert snapshot == Snapshot.from_name(snapshot.name)
