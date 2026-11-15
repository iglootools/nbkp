import json
from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from nbkp.disks import MountCapabilities
from nbkp.disks.lifecycle import MountFailureReason, MountResult, UmountResult
from nbkp.disks.models import LifecycleFailure, LifecycleStep, UmountFailureReason
from nbkp.disks.observation import MountObservation
from nbkp.disks.output import (
    build_mount_status_json,
    build_mount_status_table,
    mount_failure_detail,
    mount_state_icon,
    umount_failure_detail,
    umount_failure_warning,
)
from nbkp.policy import Severity


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
    buf = StringIO()
    Console(file=buf, highlight=False, markup=True).print(table)
    return buf.getvalue()


class TestMountStatusTableSeverity:
    def test_device_missing_renders_as_warning(self) -> None:
        obs = MountObservation(
            device_present=False,
            luks_unlocked=None,
            mounted=None,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "⚠" in rendered
        assert "✗" not in rendered

    def test_mounted_false_renders_as_warning(self) -> None:
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
        obs = MountObservation(
            device_present=True,
            luks_unlocked=False,
            mounted=None,
            failure_reason=MountFailureReason.UNLOCK_FAILED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_not_authorized_renders_luks_as_error(self) -> None:
        obs = MountObservation(
            device_present=True,
            luks_unlocked=False,
            mounted=None,
            failure_reason=MountFailureReason.NOT_AUTHORIZED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_mount_failed_renders_mounted_as_error(self) -> None:
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=False,
            failure_reason=MountFailureReason.MOUNT_FAILED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_not_authorized_renders_mounted_as_error(self) -> None:
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

    def test_text_label_reaches_json_as_plain_text(self) -> None:
        """A styled label must not leak Rich markup into JSON output."""
        obs = MountObservation(
            device_present=None,
            luks_unlocked=None,
            mounted=None,
        )
        label = Text.assemble("my-vol", " ", ("(not managed)", "dim"))
        result = build_mount_status_json([(label, obs)])
        assert result[0]["volume"] == "my-vol (not managed)"


_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"


class TestMountFailureDetail:
    @pytest.mark.parametrize(
        ("reason", "failure", "expected"),
        [
            (
                MountFailureReason.DEVICE_NOT_PRESENT,
                LifecycleFailure(device_uuid=_UUID),
                f"device not plugged in (UUID: {_UUID})",
            ),
            (
                MountFailureReason.PASSPHRASE_NOT_AVAILABLE,
                LifecycleFailure(passphrase_id="usb", message="not in keyring"),
                "passphrase 'usb' not available: not in keyring",
            ),
            (
                MountFailureReason.NOT_AUTHORIZED,
                LifecycleFailure(step=LifecycleStep.UNLOCK, exit_code=1),
                "unlock not authorized — polkit rule not configured",
            ),
            (
                MountFailureReason.UDISKS_NOT_AVAILABLE,
                LifecycleFailure(
                    step=LifecycleStep.MOUNT, exit_code=127, message="not found"
                ),
                "mount failed — udisks2 not available: not found",
            ),
            (
                MountFailureReason.UDISKS_NOT_AVAILABLE,
                LifecycleFailure(command="lsblk"),
                "udisks2 not available: lsblk not found",
            ),
            (
                MountFailureReason.PROBE_FAILED,
                LifecycleFailure(command="lsblk", exit_code=1, message="boom"),
                "device state unknown: lsblk failed (exit 1): boom",
            ),
            (
                MountFailureReason.UNREACHABLE,
                LifecycleFailure(message="timed out"),
                "unreachable: timed out",
            ),
            (
                MountFailureReason.UNLOCK_FAILED,
                LifecycleFailure(step=LifecycleStep.UNLOCK),
                "unlocked device could not be resolved",
            ),
            (
                MountFailureReason.MOUNT_FAILED,
                LifecycleFailure(step=LifecycleStep.MOUNT, exit_code=32, message="x"),
                "mount failed (exit 32): x",
            ),
        ],
    )
    def test_renders_reason_and_facts(
        self, reason: MountFailureReason, failure: LifecycleFailure, expected: str
    ) -> None:
        result = MountResult("v", False, failure_reason=reason, failure=failure)
        assert mount_failure_detail(result) == expected

    def test_success_has_no_detail(self) -> None:
        assert mount_failure_detail(MountResult("v", True)) is None


class TestUmountFailureDetail:
    @pytest.mark.parametrize(
        ("reason", "failure", "detail", "warning"),
        [
            (
                UmountFailureReason.UNMOUNT_FAILED,
                LifecycleFailure(
                    step=LifecycleStep.UNMOUNT, exit_code=1, message="busy"
                ),
                "umount failed (exit 1): busy",
                "device may still be mounted, manual umount needed",
            ),
            (
                UmountFailureReason.LOCK_FAILED,
                LifecycleFailure(step=LifecycleStep.LOCK, exit_code=1, message="busy"),
                "lock failed (exit 1): busy",
                "LUKS container still unlocked, manual lock needed",
            ),
            (
                UmountFailureReason.PROBE_FAILED,
                LifecycleFailure(command="findmnt", exit_code=2, message="boom"),
                "device state unknown: findmnt failed (exit 2): boom",
                "volume may still be mounted or unlocked, check manually",
            ),
            (
                UmountFailureReason.UNREACHABLE,
                LifecycleFailure(message="timed out"),
                "unreachable: timed out",
                "volume may still be mounted, manual umount needed",
            ),
            (
                UmountFailureReason.UDISKS_NOT_AVAILABLE,
                LifecycleFailure(command="findmnt"),
                "udisks2 not available: findmnt not found",
                "volume may still be mounted, manual umount needed",
            ),
        ],
    )
    def test_renders_reason_and_facts(
        self,
        reason: UmountFailureReason,
        failure: LifecycleFailure,
        detail: str,
        warning: str,
    ) -> None:
        result = UmountResult("v", False, failure_reason=reason, failure=failure)
        assert umount_failure_detail(result) == detail
        assert umount_failure_warning(result) == warning

    def test_success_has_no_detail(self) -> None:
        result = UmountResult("v", True)
        assert umount_failure_detail(result) is None
        assert umount_failure_warning(result) is None
