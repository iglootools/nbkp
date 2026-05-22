"""Tests for mount.output display helpers."""

from __future__ import annotations

import json

from rich.console import Console
from io import StringIO

from nbkp.clihelpers import Severity
from nbkp.disks.observation import MountObservation
from nbkp.disks.output import (
    build_mount_status_json,
    build_mount_status_table,
    mount_state_icon,
)
from nbkp.preflight.status import MountCapabilities


class TestMountStateIcon:
    def test_true(self) -> None:
        assert mount_state_icon(True) == "[green]\u2713[/green]"

    def test_false_default_is_error(self) -> None:
        assert mount_state_icon(False) == "[red]\u2717[/red]"

    def test_false_with_warning_severity(self) -> None:
        assert (
            mount_state_icon(False, fail_severity=Severity.WARNING)
            == "[dark_orange]\u26a0[/dark_orange]"
        )

    def test_none(self) -> None:
        assert mount_state_icon(None) == "\u2014"


def _render(table) -> str:  # type: ignore[no-untyped-def]
    """Render a Rich renderable to plain text for substring assertions."""
    buf = StringIO()
    Console(file=buf, highlight=False, markup=True).print(table)
    return buf.getvalue()


class TestMountStatusTableSeverity:
    def test_device_missing_renders_as_warning(self) -> None:
        """device_present=False shows the warning icon, not the error icon."""
        obs = MountObservation(
            resolved_backend="systemd",
            device_present=False,
            luks_attached=None,
            mounted=None,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "\u26a0" in rendered
        assert "\u2717" not in rendered

    def test_mounted_false_renders_as_warning(self) -> None:
        """mounted=False (with device present) shows warning too \u2014 drive is
        plugged in but not mounted, which is non-fatal observation state."""
        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=None,
            mounted=False,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        # device \u2713 green, mounted \u26a0 orange (no LUKS column rendering for None)
        assert "\u26a0" in rendered
        assert "\u2717" not in rendered

    def test_luks_false_without_failure_reason_is_warning(self) -> None:
        """luks_attached=False with no recorded failure_reason means the
        probe found ``/dev/mapper/<name>`` missing but no attach was
        attempted (e.g. ``disks status`` on a plugged-in but locked
        drive).  That's observation noise, not a failure \u2192 \u26a0.
        """
        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=False,
            mounted=False,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        # device \u2713 green, luks \u26a0 orange, mounted \u26a0 orange
        assert "\u26a0" in rendered
        assert "\u2717" not in rendered

    def test_luks_attach_failed_renders_as_error(self) -> None:
        """A real LUKS-stage failure (ATTACH_LUKS_FAILED) renders \u2717 on the LUKS column."""
        from nbkp.disks.lifecycle import MountFailureReason

        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=False,
            mounted=None,
            failure_reason=MountFailureReason.ATTACH_LUKS_FAILED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        # device \u2713, luks \u2717, mounted \u2014
        assert "\u2717" in rendered

    def test_sudoers_refused_renders_luks_as_error(self) -> None:
        """SUDOERS_REFUSED happens during the LUKS-attach step \u2192 \u2717 on LUKS."""
        from nbkp.disks.lifecycle import MountFailureReason

        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=False,
            mounted=None,
            failure_reason=MountFailureReason.SUDOERS_REFUSED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "\u2717" in rendered

    def test_mount_failed_renders_mounted_as_error(self) -> None:
        """MOUNT_FAILED happens during the mount step \u2192 \u2717 on Mounted column."""
        from nbkp.disks.lifecycle import MountFailureReason

        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=True,
            mounted=False,
            failure_reason=MountFailureReason.MOUNT_FAILED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        # device \u2713, luks \u2713, mounted \u2717
        assert "\u2717" in rendered

    def test_polkit_refused_renders_mounted_as_error(self) -> None:
        """POLKIT_REFUSED happens during the mount step \u2192 \u2717 on Mounted."""
        from nbkp.disks.lifecycle import MountFailureReason

        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=True,
            mounted=False,
            failure_reason=MountFailureReason.POLKIT_REFUSED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "\u2717" in rendered


class TestBuildMountStatusTable:
    def test_with_mount_observation(self) -> None:
        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=True,
            mounted=True,
        )
        table = build_mount_status_table([("my-vol", obs)])
        assert table.title == "Volume Mount Status:"
        assert len(table.rows) == 1
        assert len(table.columns) == 5

    def test_with_mount_capabilities(self) -> None:
        caps = MountCapabilities(
            resolved_backend="direct",
            device_present=True,
            luks_attached=None,
            mounted=True,
        )
        table = build_mount_status_table([("my-vol", caps)])
        assert len(table.rows) == 1

    def test_custom_title(self) -> None:
        obs = MountObservation(resolved_backend="systemd", device_present=False)
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
                    resolved_backend="systemd",
                    device_present=True,
                    luks_attached=True,
                    mounted=True,
                ),
            ),
            (
                "vol-b",
                MountObservation(
                    resolved_backend="direct",
                    device_present=False,
                ),
            ),
        ]
        table = build_mount_status_table(entries)
        assert len(table.rows) == 2


class TestBuildMountStatusJson:
    def test_with_mount_observation(self) -> None:
        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=True,
            mounted=True,
        )
        result = build_mount_status_json([("my-vol", obs)])
        assert result == [
            {
                "volume": "my-vol",
                "strategy": "systemd",
                "device_present": True,
                "luks_attached": True,
                "mounted": True,
            }
        ]

    def test_with_mount_capabilities(self) -> None:
        caps = MountCapabilities(
            resolved_backend="direct",
            device_present=True,
            luks_attached=None,
            mounted=False,
        )
        result = build_mount_status_json([("vol", caps)])
        assert result == [
            {
                "volume": "vol",
                "strategy": "direct",
                "device_present": True,
                "luks_attached": None,
                "mounted": False,
            }
        ]

    def test_json_serializable(self) -> None:
        obs = MountObservation(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=None,
            mounted=True,
        )
        result = build_mount_status_json([("vol", obs)])
        serialized = json.dumps(result)
        assert json.loads(serialized) == result

    def test_empty(self) -> None:
        assert build_mount_status_json([]) == []
