"""Tests for nbkp.mount.lifecycle."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.mount.strategy import DirectMountStrategy, SystemdMountStrategy
from nbkp.mount.lifecycle import (
    mount_volume,
    mount_volumes,
    umount_volume,
    umount_volumes,
)


def _mock_run(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _encrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="encrypted",
        path="/mnt/encrypted",
        mount=MountConfig(
            device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
            encryption=LuksEncryptionConfig(
                mapper_name="encrypted",
                passphrase_id="encrypted",
            ),
        ),
    )


def _unencrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="usb",
        path="/mnt/usb",
        mount=MountConfig(
            device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
    )


def _systemd_strategy(
    mount_unit: str = "mnt-encrypted.mount",
    cryptsetup_path: str | None = "/usr/lib/systemd/systemd-cryptsetup",
) -> SystemdMountStrategy:
    return SystemdMountStrategy(
        mount_unit=mount_unit,
        cryptsetup_path=cryptsetup_path,
    )


def _direct_strategy(
    volume_path: str = "/mnt/encrypted",
) -> DirectMountStrategy:
    return DirectMountStrategy(volume_path=volume_path)


class TestMountVolume:
    def test_device_not_present(self) -> None:
        vol = _encrypted_vol()
        strategy = _systemd_strategy()
        with patch("nbkp.mount.lifecycle.detect_device_present", return_value=False):
            result = mount_volume(
                vol,
                vol.mount,
                {},
                lambda x: "pass",  # type: ignore[arg-type]
                strategy,
            )
        assert not result.success
        assert "not plugged in" in (result.detail or "")

    def test_already_mounted_is_noop(self) -> None:
        vol = _encrypted_vol()
        strategy = _systemd_strategy()
        with (
            patch("nbkp.mount.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.mount.lifecycle.detect_device_unlocked", return_value=True),
            # Patch run_on_volume in the strategy module so detect_mounted returns True
            patch(
                "nbkp.mount.strategy.run_on_volume",
                return_value=_mock_run(0),
            ),
        ):
            result = mount_volume(
                vol,
                vol.mount,
                {},
                lambda x: "pass",  # type: ignore[arg-type]
                strategy,
            )
        assert result.success

    def test_unencrypted_mount(self) -> None:
        vol = _unencrypted_vol()
        strategy = _systemd_strategy(mount_unit="mnt-usb.mount", cryptsetup_path=None)
        with (
            patch("nbkp.mount.lifecycle.detect_device_present", return_value=True),
            # detect_mounted -> False (systemctl is-active returns 3 for inactive)
            patch(
                "nbkp.mount.strategy.run_on_volume",
                return_value=_mock_run(3),
            ),
            patch("nbkp.mount.lifecycle.run_on_volume", return_value=_mock_run(0)),
        ):
            result = mount_volume(
                vol,
                vol.mount,
                {},
                lambda x: "pass",  # type: ignore[arg-type]
                strategy,
            )
        assert result.success

    def test_unlock_failure(self) -> None:
        vol = _encrypted_vol()
        strategy = _systemd_strategy()
        with (
            patch("nbkp.mount.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.mount.lifecycle.detect_device_unlocked", return_value=False),
            patch(
                "nbkp.mount.lifecycle.run_on_volume",
                return_value=_mock_run(1, stderr="permission denied"),
            ),
        ):
            result = mount_volume(
                vol,
                vol.mount,
                {},
                lambda x: "pass",  # type: ignore[arg-type]
                strategy,
            )
        assert not result.success
        assert "unlock failed" in (result.detail or "")

    def test_direct_strategy_mount(self) -> None:
        vol = _unencrypted_vol()
        strategy = _direct_strategy(volume_path="/mnt/usb")
        with (
            patch("nbkp.mount.lifecycle.detect_device_present", return_value=True),
            # detect_mounted -> False (mountpoint -q returns 1 for not mounted)
            patch(
                "nbkp.mount.strategy.run_on_volume",
                return_value=_mock_run(1),
            ),
            patch("nbkp.mount.lifecycle.run_on_volume", return_value=_mock_run(0)),
        ):
            result = mount_volume(
                vol,
                vol.mount,
                {},
                lambda x: "pass",  # type: ignore[arg-type]
                strategy,
            )
        assert result.success


class TestUmountVolume:
    def test_umount_and_lock(self) -> None:
        vol = _encrypted_vol()
        strategy = _systemd_strategy()
        call_count = 0

        def mock_run(cmd, volume, resolved, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_run(0)

        with (
            # detect_mounted -> True
            patch("nbkp.mount.strategy.run_on_volume", return_value=_mock_run(0)),
            patch("nbkp.mount.lifecycle.detect_device_unlocked", return_value=True),
            patch("nbkp.mount.lifecycle.run_on_volume", side_effect=mock_run),
        ):
            result = umount_volume(
                vol,
                vol.mount,
                {},
                strategy,
            )
        assert result.success
        assert call_count == 2  # umount + lock

    def test_not_mounted_still_locks(self) -> None:
        vol = _encrypted_vol()
        strategy = _systemd_strategy()
        with (
            # detect_mounted -> False
            patch("nbkp.mount.strategy.run_on_volume", return_value=_mock_run(3)),
            patch("nbkp.mount.lifecycle.detect_device_unlocked", return_value=True),
            patch("nbkp.mount.lifecycle.run_on_volume", return_value=_mock_run(0)),
        ):
            result = umount_volume(
                vol,
                vol.mount,
                {},
                strategy,
            )
        assert result.success

    def test_umount_failure(self) -> None:
        vol = _encrypted_vol()
        strategy = _systemd_strategy()
        with (
            # detect_mounted -> True
            patch("nbkp.mount.strategy.run_on_volume", return_value=_mock_run(0)),
            patch(
                "nbkp.mount.lifecycle.run_on_volume",
                return_value=_mock_run(1, stderr="busy"),
            ),
        ):
            result = umount_volume(
                vol,
                vol.mount,
                {},
                strategy,
            )
        assert not result.success
        assert result.warning is not None

    def test_unencrypted_umount(self) -> None:
        vol = _unencrypted_vol()
        strategy = _systemd_strategy(mount_unit="mnt-usb.mount", cryptsetup_path=None)
        with (
            # detect_mounted -> True
            patch("nbkp.mount.strategy.run_on_volume", return_value=_mock_run(0)),
            patch("nbkp.mount.lifecycle.run_on_volume", return_value=_mock_run(0)),
        ):
            result = umount_volume(
                vol,
                vol.mount,
                {},
                strategy,
            )
        assert result.success


class TestMountVolumes:
    def _config_with_mount(self) -> Config:
        return Config(
            volumes={
                "enc": _encrypted_vol(),
                "usb": _unencrypted_vol(),
                "plain": LocalVolume(slug="plain", path="/mnt/plain"),
            },
            sync_endpoints={
                "ep-enc": SyncEndpoint(slug="ep-enc", volume="enc"),
                "ep-usb": SyncEndpoint(slug="ep-usb", volume="usb"),
                "ep-plain": SyncEndpoint(slug="ep-plain", volume="plain"),
            },
            syncs={
                "s1": SyncConfig(slug="s1", source="ep-enc", destination="ep-usb"),
                "s2": SyncConfig(slug="s2", source="ep-usb", destination="ep-plain"),
            },
        )

    def test_only_mount_config_volumes(self) -> None:
        cfg = self._config_with_mount()
        enc_strategy = _systemd_strategy()
        usb_strategy = _systemd_strategy(
            mount_unit="mnt-usb.mount", cryptsetup_path=None
        )
        with (
            patch("nbkp.mount.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.mount.lifecycle.detect_device_unlocked", return_value=True),
            # detect_mounted -> True
            patch("nbkp.mount.strategy.run_on_volume", return_value=_mock_run(0)),
        ):
            results = mount_volumes(
                cfg,
                {},
                lambda x: "pass",
                mount_strategy={"enc": enc_strategy, "usb": usb_strategy},
            )
        # Only 2 volumes have mount config (enc, usb), not plain
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_filter_by_name(self) -> None:
        cfg = self._config_with_mount()
        usb_strategy = _systemd_strategy(
            mount_unit="mnt-usb.mount", cryptsetup_path=None
        )
        with (
            patch("nbkp.mount.lifecycle.detect_device_present", return_value=True),
            # detect_mounted -> True
            patch("nbkp.mount.strategy.run_on_volume", return_value=_mock_run(0)),
        ):
            results = mount_volumes(
                cfg,
                {},
                lambda x: "pass",
                names=["usb"],
                mount_strategy={"usb": usb_strategy},
            )
        assert len(results) == 1
        assert results[0].volume_slug == "usb"

    def test_missing_strategy_fails(self) -> None:
        cfg = self._config_with_mount()
        results = mount_volumes(
            cfg,
            {},
            lambda x: "pass",
            mount_strategy={},
        )
        assert len(results) == 2
        assert all(not r.success for r in results)
        assert all("strategy not resolved" in (r.detail or "") for r in results)


class TestUmountVolumes:
    def _config_with_mount(self) -> Config:
        return Config(
            volumes={
                "enc": _encrypted_vol(),
                "usb": _unencrypted_vol(),
                "plain": LocalVolume(slug="plain", path="/mnt/plain"),
            },
            sync_endpoints={
                "ep-enc": SyncEndpoint(slug="ep-enc", volume="enc"),
                "ep-usb": SyncEndpoint(slug="ep-usb", volume="usb"),
                "ep-plain": SyncEndpoint(slug="ep-plain", volume="plain"),
            },
            syncs={
                "s1": SyncConfig(slug="s1", source="ep-enc", destination="ep-usb"),
                "s2": SyncConfig(slug="s2", source="ep-usb", destination="ep-plain"),
            },
        )

    def test_reverse_order(self) -> None:
        cfg = self._config_with_mount()
        enc_strategy = _systemd_strategy()
        usb_strategy = _systemd_strategy(
            mount_unit="mnt-usb.mount", cryptsetup_path=None
        )
        slugs: list[str] = []
        with (
            # detect_mounted -> False
            patch("nbkp.mount.strategy.run_on_volume", return_value=_mock_run(3)),
            patch("nbkp.mount.lifecycle.detect_device_unlocked", return_value=False),
        ):
            umount_volumes(
                cfg,
                {},
                mount_strategy={"enc": enc_strategy, "usb": usb_strategy},
                on_umount_start=lambda slug: slugs.append(slug),
            )
        # Should be reversed: usb first, then enc
        assert slugs == ["usb", "enc"]
