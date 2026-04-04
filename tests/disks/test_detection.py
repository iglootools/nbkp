"""Tests for nbkp.disks.detection."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from nbkp.config import (
    Config,
    LocalVolume,
    MountConfig,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.disks import direct as direct_cmds
from nbkp.disks import systemd as systemd_cmds
from nbkp.disks.detection import (
    detect_device_present,
    detect_luks_attached,
    detect_systemd_cryptsetup_path,
    resolve_mount_strategy,
    resolve_mount_unit,
)


def _local_vol(path: str = "/mnt/disk") -> LocalVolume:
    return LocalVolume(slug="disk", path=path)


def _mock_run(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestDetectDevicePresent:
    def test_present(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)):
            assert detect_device_present(
                _local_vol(), "5941f273-f73c-44c5-a3ef-fae7248db1b6", {}
            )

    def test_not_present(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            assert not detect_device_present(
                _local_vol(), "5941f273-f73c-44c5-a3ef-fae7248db1b6", {}
            )

    def test_passes_correct_command(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)
        ) as mock:
            detect_device_present(_local_vol(), "aaaa-bbbb-cccc-dddd", {})
        mock.assert_called_once()
        cmd = mock.call_args[0][0]
        assert cmd == ["test", "-e", "/dev/disk/by-uuid/aaaa-bbbb-cccc-dddd"]


class TestDetectLuksAttached:
    def test_attached(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)):
            assert detect_luks_attached(_local_vol(), "seagate8tb", {})

    def test_not_attached(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            assert not detect_luks_attached(_local_vol(), "seagate8tb", {})

    def test_passes_correct_command(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)
        ) as mock:
            detect_luks_attached(_local_vol(), "mydisk", {})
        cmd = mock.call_args[0][0]
        assert cmd == ["test", "-b", "/dev/mapper/mydisk"]


class TestBuildDetectMountedCommand:
    def test_systemd(self) -> None:
        assert systemd_cmds.build_detect_mounted_command("mnt-disk.mount") == [
            "systemctl",
            "is-active",
            "mnt-disk.mount",
            "--quiet",
        ]

    def test_direct(self) -> None:
        assert direct_cmds.build_detect_mounted_command("/mnt/disk") == [
            "mountpoint",
            "-q",
            "/mnt/disk",
        ]


class TestDetectSystemdCryptsetupPath:
    def test_found_usr_lib(self) -> None:
        def mock_run(cmd, **kwargs):
            if cmd[1] == "-x" and "/usr/lib/" in cmd[2]:
                return _mock_run(0)
            return _mock_run(1)

        with patch("nbkp.remote.dispatch.subprocess.run", side_effect=mock_run):
            path = detect_systemd_cryptsetup_path(_local_vol(), {})
        assert path == "/usr/lib/systemd/systemd-cryptsetup"

    def test_found_lib(self) -> None:
        def mock_run(cmd, **kwargs):
            if cmd[1] == "-x" and cmd[2] == "/lib/systemd/systemd-cryptsetup":
                return _mock_run(0)
            return _mock_run(1)

        with patch("nbkp.remote.dispatch.subprocess.run", side_effect=mock_run):
            path = detect_systemd_cryptsetup_path(_local_vol(), {})
        assert path == "/lib/systemd/systemd-cryptsetup"

    def test_not_found(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            path = detect_systemd_cryptsetup_path(_local_vol(), {})
        assert path is None


class TestResolveMountUnit:
    def test_success(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout="mnt-seagate8tb\n"),
        ):
            unit = resolve_mount_unit(_local_vol("/mnt/seagate8tb"), {})
        assert unit == "mnt-seagate8tb.mount"

    def test_failure(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(1),
        ):
            unit = resolve_mount_unit(_local_vol("/mnt/seagate8tb"), {})
        assert unit is None

    def test_passes_volume_path(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout="mnt-data\n"),
        ) as mock:
            resolve_mount_unit(_local_vol("/mnt/data"), {})
        cmd = mock.call_args[0][0]
        assert cmd == ["systemd-escape", "--path", "/mnt/data"]


class TestResolveMountStrategy:
    def _config(self) -> Config:
        return Config(
            volumes={
                "v1": LocalVolume(
                    slug="v1",
                    path="/mnt/v1",
                    mount=MountConfig(
                        device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    ),
                ),
                "v2": LocalVolume(
                    slug="v2",
                    path="/mnt/v2",
                    mount=MountConfig(
                        device_uuid="cccccccc-dddd-eeee-ffff-000000000000",
                    ),
                ),
            },
            sync_endpoints={
                "ep1": SyncEndpoint(slug="ep1", volume="v1"),
                "ep2": SyncEndpoint(slug="ep2", volume="v2"),
            },
            syncs={
                "s1": SyncConfig(slug="s1", source="ep1", destination="ep2"),
            },
        )

    def test_ssh_timeout_skips_volume(self) -> None:
        """Unreachable volume is omitted from strategies, not a crash."""
        cfg = self._config()
        call_count = 0

        def mock_resolve(vol, mount_config, resolved):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("timed out")
            from nbkp.disks.strategy import DirectMountStrategy

            return DirectMountStrategy(volume_path=vol.path)

        with patch(
            "nbkp.disks.detection._resolve_mount_strategy",
            side_effect=mock_resolve,
        ):
            strategies = resolve_mount_strategy(cfg, {}, names=None)

        # One volume succeeded, one failed
        assert len(strategies) == 1

    def test_strategy_error_callback(self) -> None:
        """on_strategy_error callback is called for failed volumes."""
        cfg = self._config()
        errors: list[tuple[str, str]] = []

        with patch(
            "nbkp.disks.detection._resolve_mount_strategy",
            side_effect=TimeoutError("timed out"),
        ):
            strategies = resolve_mount_strategy(
                cfg,
                {},
                names=None,
                on_strategy_error=lambda slug, err: errors.append((slug, err)),
            )

        assert len(strategies) == 0
        assert len(errors) == 2
        assert all("timed out" in err for _, err in errors)
