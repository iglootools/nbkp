"""Tests for mount.output display helpers."""

from __future__ import annotations

import json

from rich.console import Console
from io import StringIO

from nbkp.clihelpers import Severity
from nbkp.disks.lifecycle import MountFailureReason
from nbkp.disks.observation import MountObservation
from nbkp.disks.output import (
    build_mount_status_json,
    build_mount_status_table,
    mount_state_icon,
)
from nbkp.preflight.status import MountCapabilities


class TestMountStateIcon:
    def test_true(self) -> None:
        assert mount_state_icon(True) == "[green]✓[/green]"

    def test_false_default_is_error(self) -> None:
        assert mount_state_icon(False) == "[red]✗[/red]"

    def test_false_with_warning_severity(self) -> None:
        assert (
            mount_state_icon(False, fail_severity=Severity.WARNING)
            == "[dark_orange]⚠[/dark_orange]"
        )

    def test_none(self) -> None:
        assert mount_state_icon(None) == "—"


def _render(table) -> str:  # type: ignore[no-untyped-def]
    """Render a Rich renderable to plain text for substring assertions."""
    buf = StringIO()
    Console(file=buf, highlight=False, markup=True).print(table)
    return buf.getvalue()


class TestMountStatusTableSeverity:
    def test_device_missing_renders_as_warning(self) -> None:
        """device_present=False shows the warning icon, not the error icon."""
        obs = MountObservation(
            device_present=False,
            luks_unlocked=None,
            mounted=None,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "⚠" in rendered
        assert "✗" not in rendered

    def test_mounted_false_renders_as_warning(self) -> None:
        """mounted=False (with device present, no failure reason) -> warning."""
        obs = MountObservation(
            device_present=True,
            luks_unlocked=None,
            mounted=False,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "⚠" in rendered
        assert "✗" not in rendered

    def test_luks_false_without_failure_reason_is_warning(self) -> None:
        """luks_unlocked=False with no recorded failure_reason is observation
        noise (plugged in but locked), not a failure -> warning."""
        obs = MountObservation(
            device_present=True,
            luks_unlocked=False,
            mounted=False,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "⚠" in rendered
        assert "✗" not in rendered

    def test_unlock_failed_renders_luks_as_error(self) -> None:
        """A real LUKS-stage failure (UNLOCK_FAILED) renders an error on LUKS."""
        obs = MountObservation(
            device_present=True,
            luks_unlocked=False,
            mounted=None,
            failure_reason=MountFailureReason.UNLOCK_FAILED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_not_authorized_renders_luks_as_error(self) -> None:
        """NOT_AUTHORIZED at the unlock step -> error on LUKS column."""
        obs = MountObservation(
            device_present=True,
            luks_unlocked=False,
            mounted=None,
            failure_reason=MountFailureReason.NOT_AUTHORIZED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_mount_failed_renders_mounted_as_error(self) -> None:
        """MOUNT_FAILED happens during the mount step -> error on Mounted."""
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=False,
            failure_reason=MountFailureReason.MOUNT_FAILED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_not_authorized_renders_mounted_as_error(self) -> None:
        """NOT_AUTHORIZED at the mount step -> error on Mounted column."""
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=False,
            failure_reason=MountFailureReason.NOT_AUTHORIZED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered


class TestBuildMountStatusTable:
    def test_columns(self) -> None:
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=True,
        )
        table = build_mount_status_table([("my-vol", obs)])
        assert table.title == "Volume Mount Status:"
        assert len(table.rows) == 1
        # Name / Device / Unlocked / Mounted — no Strategy column.
        assert len(table.columns) == 4
        assert [c.header for c in table.columns] == [
            "Name",
            "Device",
            "Unlocked",
            "Mounted",
        ]

    def test_with_mount_capabilities(self) -> None:
        caps = MountCapabilities(
            device_present=True,
            luks_unlocked=None,
            mounted=True,
        )
        table = build_mount_status_table([("my-vol", caps)])
        assert len(table.rows) == 1

    def test_custom_title(self) -> None:
        obs = MountObservation(device_present=False)
        table = build_mount_status_table([("v", obs)], title="Custom:")
        assert table.title == "Custom:"

    def test_empty(self) -> None:
        table = build_mount_status_table([])
        assert len(table.rows) == 0

    def test_multiple_entries(self) -> None:
        entries = [
            (
                "vol-a",
                MountObservation(
                    device_present=True,
                    luks_unlocked=True,
                    mounted=True,
                ),
            ),
            ("vol-b", MountObservation(device_present=False)),
        ]
        table = build_mount_status_table(entries)
        assert len(table.rows) == 2


class TestBuildMountStatusJson:
    def test_with_mount_observation(self) -> None:
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=True,
        )
        result = build_mount_status_json([("my-vol", obs)])
        assert result == [
            {
                "volume": "my-vol",
                "device_present": True,
                "luks_unlocked": True,
                "mounted": True,
            }
        ]

    def test_with_mount_capabilities(self) -> None:
        caps = MountCapabilities(
            device_present=True,
            luks_unlocked=None,
            mounted=False,
        )
        result = build_mount_status_json([("vol", caps)])
        assert result == [
            {
                "volume": "vol",
                "device_present": True,
                "luks_unlocked": None,
                "mounted": False,
            }
        ]

    def test_json_serializable(self) -> None:
        obs = MountObservation(
            device_present=True,
            luks_unlocked=None,
            mounted=True,
        )
        result = build_mount_status_json([("vol", obs)])
        serialized = json.dumps(result)
        assert json.loads(serialized) == result

    def test_empty(self) -> None:
        assert build_mount_status_json([]) == []
