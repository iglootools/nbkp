"""Tests for nbkp.disks.status: whether each volume was probed, and why not."""

from nbkp.config import Config, LocalVolume, MountConfig
from nbkp.disks.lifecycle import MountFailureKind, MountResult
from nbkp.disks.models import LifecycleFailure
from nbkp.disks.status import (
    VolumeStatus,
    VolumeStatusKind,
    mount_result_statuses,
    probe_volume_status,
    unprobed_volume_statuses,
)
from tests.fakes import FakeCommandRunner

_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_DRIVE = LocalVolume(
    slug="drive", path="/mnt/drive", mount=MountConfig(device_uuid=_UUID)
)


class TestProbeVolumeStatus:
    def test_unreachable_host_is_a_status(self) -> None:
        run = FakeCommandRunner().on("test", returns=OSError("timed out"))

        assert probe_volume_status(_DRIVE, {}, run=run) == VolumeStatus(
            "drive", VolumeStatusKind.UNREACHABLE, error_message="timed out"
        )

    def test_missing_local_tool_is_a_status(self) -> None:
        run = FakeCommandRunner().on(
            "test", returns=FileNotFoundError(2, "No such file or directory", "test")
        )

        assert probe_volume_status(_DRIVE, {}, run=run) == VolumeStatus(
            "drive", VolumeStatusKind.TOOL_NOT_INSTALLED, missing_tool="test"
        )


class TestUnprobedVolumeStatuses:
    def test_unmanaged_volume_is_listed_unprobed(self) -> None:
        plain = LocalVolume(slug="home", path="/home/user")
        config = Config(volumes={"drive": _DRIVE, "home": plain})

        assert unprobed_volume_statuses(config, {}) == [
            VolumeStatus("home", VolumeStatusKind.NOT_MANAGED)
        ]


class TestMountResultStatuses:
    def test_unreachable_result_keeps_its_error(self) -> None:
        result = MountResult(
            "drive",
            success=False,
            failure_reason=MountFailureKind.UNREACHABLE,
            failure=LifecycleFailure(message="No route to host"),
        )

        assert mount_result_statuses([result]) == [
            VolumeStatus(
                "drive", VolumeStatusKind.UNREACHABLE, error_message="No route to host"
            )
        ]

    def test_observed_result_is_probed(self) -> None:
        result = MountResult("drive", success=True, device_present=True, mounted=True)

        [status] = mount_result_statuses([result])

        assert status.kind is VolumeStatusKind.PROBED
        assert status.state.mounted is True
