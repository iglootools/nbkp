"""Tests for mount.output display helpers."""

from __future__ import annotations

import json

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

    def test_false(self) -> None:
        assert mount_state_icon(False) == "[red]\u2717[/red]"

    def test_none(self) -> None:
        assert mount_state_icon(None) == "\u2014"


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
