import json
from io import StringIO

import pytest
from rich.console import Console, RenderableType

from nbkp.credentials import CredentialError, CredentialErrorKind
from nbkp.disks import MountCapabilities
from nbkp.disks.lifecycle import MountFailureKind, MountResult, UmountResult
from nbkp.disks.models import LifecycleFailure, LifecycleStep, UmountFailureKind
from nbkp.disks.observation import MountObservation
from nbkp.disks.output import (
    build_mount_status_table,
    error_label,
    format_volume_status_label,
    mount_failure_detail,
    mount_result_json,
    mount_state_icon,
    umount_failure_detail,
    umount_failure_warning,
    volume_status_json,
)
from nbkp.disks.status import VolumeStatus, VolumeStatusKind
from nbkp.policy import Severity


class TestMountStateIcon:
    def test_icon_is_green_check_when_true(self) -> None:
        assert mount_state_icon(value=True) == "[green]✓[/green]"

    def test_false_default_is_error(self) -> None:
        assert mount_state_icon(value=False) == "[red]✗[/red]"

    def test_false_with_warning_severity(self) -> None:
        assert (
            mount_state_icon(value=False, fail_severity=Severity.WARNING)
            == "[dark_orange]⚠[/dark_orange]"
        )

    def test_icon_is_dash_when_unknown(self) -> None:
        assert mount_state_icon(value=None) == "—"


def _render(table: RenderableType) -> str:
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
        """
        A locked drive with no recorded failure is a warning, not a failure.

        luks_unlocked=False with no recorded failure_reason is observation noise
        (plugged in but locked), not a failure.
        """
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
            mount_failure_reason=MountFailureKind.UNLOCK_FAILED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_not_authorized_renders_luks_as_error(self) -> None:
        obs = MountObservation(
            device_present=True,
            luks_unlocked=False,
            mounted=None,
            mount_failure_reason=MountFailureKind.NOT_AUTHORIZED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_mount_failed_renders_mounted_as_error(self) -> None:
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=False,
            mount_failure_reason=MountFailureKind.MOUNT_FAILED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered

    def test_not_authorized_renders_mounted_as_error(self) -> None:
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=False,
            mount_failure_reason=MountFailureKind.NOT_AUTHORIZED,
        )
        rendered = _render(build_mount_status_table([("vol", obs)]))
        assert "✗" in rendered


class TestBuildMountStatusTable:
    def test_table_has_one_row_per_volume_and_four_columns(self) -> None:
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

    def test_table_has_no_rows_when_no_volumes(self) -> None:
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


class TestVolumeStatusJson:
    def test_probed_volume_carries_its_state(self) -> None:
        obs = MountObservation(device_present=True, luks_unlocked=True, mounted=True)
        status = VolumeStatus("my-vol", VolumeStatusKind.PROBED, obs)
        assert volume_status_json(status) == {
            "volume": "my-vol",
            "status": "probed",
            "device_present": True,
            "luks_unlocked": True,
            "mounted": True,
        }

    def test_unreachable_volume_keeps_slug_and_error_apart(self) -> None:
        status = VolumeStatus(
            "my-vol",
            VolumeStatusKind.UNREACHABLE,
            error_message="timed out [x]",
        )
        assert volume_status_json(status) == {
            "volume": "my-vol",
            "status": "unreachable",
            "error": "timed out [x]",
            "device_present": None,
            "luks_unlocked": None,
            "mounted": None,
        }

    def test_json_serializable(self) -> None:
        caps = MountCapabilities(device_present=True, mounted=False)
        entry = volume_status_json(VolumeStatus("v", VolumeStatusKind.PROBED, caps))
        assert json.loads(json.dumps(entry)) == entry


class TestFormatVolumeStatusLabel:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (VolumeStatus("v", VolumeStatusKind.PROBED), "nas:v"),
            (VolumeStatus("v", VolumeStatusKind.NOT_MANAGED), "nas:v (not managed)"),
            (
                VolumeStatus("v", VolumeStatusKind.LOCATION_EXCLUDED),
                "nas:v (location excluded)",
            ),
            (
                VolumeStatus("v", VolumeStatusKind.UNREACHABLE, error_message="down"),
                "nas:v ✗ unreachable: down",
            ),
            (
                VolumeStatus(
                    "v",
                    VolumeStatusKind.TOOL_NOT_INSTALLED,
                    missing_tool="lsblk",
                ),
                "nas:v ✗ lsblk not installed",
            ),
        ],
    )
    def test_label_describes_the_volume_status(
        self,
        status: VolumeStatus,
        expected: str,
    ) -> None:
        assert format_volume_status_label("nas:v", status).plain == expected


# Real-world details that contain square brackets.  Rich reads "[...]" in a
# markup string as a style tag, so these are the strings a markup-formatted
# line would silently truncate.
_KEYRING_HINT = "unreachable: keyring missing. Install: uv tool install 'nbkp[keyring]'"


class TestErrorLabel:
    def test_detail_brackets_survive(self) -> None:
        rendered = _render(error_label("my-vol", _KEYRING_HINT))
        assert "nbkp[keyring]" in rendered

    def test_plain_text_carries_no_markup(self) -> None:
        assert error_label("my-vol", _KEYRING_HINT).plain == (
            f"my-vol ✗ {_KEYRING_HINT}"
        )

    def test_no_detail_is_bare_name(self) -> None:
        assert error_label("my-vol", None).plain == "my-vol"


_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"


class TestMountFailureDetail:
    @pytest.mark.parametrize(
        ("reason", "failure", "expected"),
        [
            (
                MountFailureKind.DEVICE_NOT_PRESENT,
                LifecycleFailure(device_uuid=_UUID),
                f"device not plugged in (UUID: {_UUID})",
            ),
            (
                MountFailureKind.PASSPHRASE_NOT_AVAILABLE,
                LifecycleFailure(
                    passphrase_id="usb",
                    credential_error=CredentialError(
                        CredentialErrorKind.ENV_UNSET,
                        env_var="NBKP_PASSPHRASE_USB",
                    ),
                ),
                (
                    "passphrase 'usb' not available: Environment variable"
                    " 'NBKP_PASSPHRASE_USB' not set. Export it with:"
                    " export NBKP_PASSPHRASE_USB=..."
                ),
            ),
            (
                MountFailureKind.NOT_AUTHORIZED,
                LifecycleFailure(step=LifecycleStep.UNLOCK, exit_code=1),
                "unlock not authorized — polkit rule not configured",
            ),
            (
                MountFailureKind.UDISKS_NOT_AVAILABLE,
                LifecycleFailure(
                    step=LifecycleStep.MOUNT,
                    exit_code=127,
                    message="not found",
                ),
                "mount failed — udisks2 not available: not found",
            ),
            (
                MountFailureKind.UDISKS_NOT_AVAILABLE,
                LifecycleFailure(command="lsblk"),
                "udisks2 not available: lsblk not found",
            ),
            (
                MountFailureKind.PROBE_FAILED,
                LifecycleFailure(command="lsblk", exit_code=1, message="boom"),
                "device state unknown: lsblk failed (exit 1): boom",
            ),
            (
                MountFailureKind.UNREACHABLE,
                LifecycleFailure(message="timed out"),
                "unreachable: timed out",
            ),
            (
                MountFailureKind.UNLOCK_FAILED,
                LifecycleFailure(step=LifecycleStep.UNLOCK),
                "unlocked device could not be resolved",
            ),
            (
                MountFailureKind.MOUNT_FAILED,
                LifecycleFailure(step=LifecycleStep.MOUNT, exit_code=32, message="x"),
                "mount failed (exit 32): x",
            ),
        ],
    )
    def test_renders_reason_and_facts(
        self,
        reason: MountFailureKind,
        failure: LifecycleFailure,
        expected: str,
    ) -> None:
        result = MountResult("v", success=False, failure_reason=reason, failure=failure)
        assert mount_failure_detail(result) == expected

    def test_success_has_no_detail(self) -> None:
        assert mount_failure_detail(MountResult("v", success=True)) is None

    def test_credential_error_json_keeps_message_and_adds_kind(self) -> None:
        """The JSON names the credential error's kind next to its first line."""
        failure = LifecycleFailure(
            passphrase_id="usb",
            credential_error=CredentialError(
                CredentialErrorKind.COMMAND_FAILED,
                command=["pass", "show", "usb"],
                exit_code=1,
                detail="no such entry",
            ),
        )
        result = MountResult(
            "v",
            success=False,
            failure_reason=MountFailureKind.PASSPHRASE_NOT_AVAILABLE,
            failure=failure,
        )
        assert mount_result_json(result)["failure"] == {
            "passphrase_id": "usb",
            "message": "Credential command failed (exit 1): pass show usb",
            "credential_error": "command-failed",
        }


class TestUmountFailureDetail:
    @pytest.mark.parametrize(
        ("reason", "failure", "detail", "warning"),
        [
            (
                UmountFailureKind.UNMOUNT_FAILED,
                LifecycleFailure(
                    step=LifecycleStep.UNMOUNT,
                    exit_code=1,
                    message="busy",
                ),
                "umount failed (exit 1): busy",
                "device may still be mounted, manual umount needed",
            ),
            (
                UmountFailureKind.LOCK_FAILED,
                LifecycleFailure(step=LifecycleStep.LOCK, exit_code=1, message="busy"),
                "lock failed (exit 1): busy",
                "LUKS container still unlocked, manual lock needed",
            ),
            (
                UmountFailureKind.PROBE_FAILED,
                LifecycleFailure(command="findmnt", exit_code=2, message="boom"),
                "device state unknown: findmnt failed (exit 2): boom",
                "volume may still be mounted or unlocked, check manually",
            ),
            (
                UmountFailureKind.UNREACHABLE,
                LifecycleFailure(message="timed out"),
                "unreachable: timed out",
                "volume may still be mounted, manual umount needed",
            ),
            (
                UmountFailureKind.UDISKS_NOT_AVAILABLE,
                LifecycleFailure(command="findmnt"),
                "udisks2 not available: findmnt not found",
                "volume may still be mounted, manual umount needed",
            ),
        ],
    )
    def test_renders_reason_and_facts(
        self,
        reason: UmountFailureKind,
        failure: LifecycleFailure,
        detail: str,
        warning: str,
    ) -> None:
        result = UmountResult(
            "v",
            success=False,
            failure_reason=reason,
            failure=failure,
        )
        assert umount_failure_detail(result) == detail
        assert umount_failure_warning(result) == warning

    def test_success_has_no_detail(self) -> None:
        result = UmountResult("v", success=True)
        assert umount_failure_detail(result) is None
        assert umount_failure_warning(result) is None
