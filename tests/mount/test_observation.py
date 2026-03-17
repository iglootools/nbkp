"""Tests for MountObservation building from mount lifecycle results."""

from __future__ import annotations

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.mount.lifecycle import MountFailureReason, MountResult
from nbkp.mount.observation import MountObservation, build_mount_observations
from nbkp.mount.strategy import DirectMountStrategy, SystemdMountStrategy


def _config_with_volumes(*volumes: LocalVolume) -> Config:
    return Config(
        ssh_endpoints={},
        volumes={v.slug: v for v in volumes},
        sync_endpoints={},
        syncs={},
    )


_ENCRYPTED_VOLUME = LocalVolume(
    slug="enc",
    path="/mnt/encrypted",
    mount=MountConfig(
        device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
        encryption=LuksEncryptionConfig(
            mapper_name="enc",
            passphrase_id="enc-pass",
        ),
    ),
)

_PLAIN_VOLUME = LocalVolume(
    slug="plain",
    path="/mnt/plain",
    mount=MountConfig(device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
)


class TestBuildMountObservations:
    def test_success_encrypted_systemd(self) -> None:
        cfg = _config_with_volumes(_ENCRYPTED_VOLUME)
        strategy = {
            "enc": SystemdMountStrategy(
                mount_unit="mnt-encrypted.mount",
                cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
            )
        }
        results = [MountResult(volume_slug="enc", success=True)]

        obs = build_mount_observations(results, strategy, cfg)

        assert obs["enc"] == MountObservation(
            resolved_backend="systemd",
            mount_unit="mnt-encrypted.mount",
            systemd_cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
            device_present=True,
            luks_attached=True,
            mounted=True,
        )

    def test_success_plain_direct(self) -> None:
        cfg = _config_with_volumes(_PLAIN_VOLUME)
        strategy = {"plain": DirectMountStrategy(volume_path="/mnt/plain")}
        results = [MountResult(volume_slug="plain", success=True)]

        obs = build_mount_observations(results, strategy, cfg)

        assert obs["plain"] == MountObservation(
            resolved_backend="direct",
            device_present=True,
            luks_attached=None,
            mounted=True,
        )

    def test_device_not_present(self) -> None:
        cfg = _config_with_volumes(_ENCRYPTED_VOLUME)
        strategy = {"enc": SystemdMountStrategy(mount_unit="mnt-encrypted.mount")}
        results = [
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.DEVICE_NOT_PRESENT,
            )
        ]

        obs = build_mount_observations(results, strategy, cfg)

        assert obs["enc"].device_present is False
        assert obs["enc"].luks_attached is None
        assert obs["enc"].mounted is None

    def test_attach_luks_failed(self) -> None:
        cfg = _config_with_volumes(_ENCRYPTED_VOLUME)
        strategy = {"enc": SystemdMountStrategy(mount_unit="mnt-encrypted.mount")}
        results = [
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.ATTACH_LUKS_FAILED,
            )
        ]

        obs = build_mount_observations(results, strategy, cfg)

        assert obs["enc"].device_present is True
        assert obs["enc"].luks_attached is False
        assert obs["enc"].mounted is None

    def test_mount_failed_encrypted(self) -> None:
        cfg = _config_with_volumes(_ENCRYPTED_VOLUME)
        strategy = {"enc": SystemdMountStrategy(mount_unit="mnt-encrypted.mount")}
        results = [
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.MOUNT_FAILED,
            )
        ]

        obs = build_mount_observations(results, strategy, cfg)

        assert obs["enc"].device_present is True
        assert obs["enc"].luks_attached is True
        assert obs["enc"].mounted is False

    def test_mount_failed_plain(self) -> None:
        cfg = _config_with_volumes(_PLAIN_VOLUME)
        strategy = {"plain": DirectMountStrategy(volume_path="/mnt/plain")}
        results = [
            MountResult(
                volume_slug="plain",
                success=False,
                failure_reason=MountFailureReason.MOUNT_FAILED,
            )
        ]

        obs = build_mount_observations(results, strategy, cfg)

        assert obs["plain"].device_present is True
        assert obs["plain"].luks_attached is None
        assert obs["plain"].mounted is False

    def test_strategy_not_resolved_skipped(self) -> None:
        cfg = _config_with_volumes(_PLAIN_VOLUME)
        strategy = {"plain": DirectMountStrategy(volume_path="/mnt/plain")}
        results = [
            MountResult(
                volume_slug="plain",
                success=False,
                failure_reason=MountFailureReason.STRATEGY_NOT_RESOLVED,
            )
        ]

        obs = build_mount_observations(results, strategy, cfg)

        assert "plain" not in obs

    def test_no_result_for_volume_skipped(self) -> None:
        cfg = _config_with_volumes(_PLAIN_VOLUME)
        strategy = {"plain": DirectMountStrategy(volume_path="/mnt/plain")}
        results: list[MountResult] = []

        obs = build_mount_observations(results, strategy, cfg)

        assert obs == {}

    def test_multiple_volumes(self) -> None:
        cfg = _config_with_volumes(_ENCRYPTED_VOLUME, _PLAIN_VOLUME)
        strategy = {
            "enc": SystemdMountStrategy(mount_unit="mnt-encrypted.mount"),
            "plain": DirectMountStrategy(volume_path="/mnt/plain"),
        }
        results = [
            MountResult(volume_slug="enc", success=True),
            MountResult(
                volume_slug="plain",
                success=False,
                failure_reason=MountFailureReason.DEVICE_NOT_PRESENT,
            ),
        ]

        obs = build_mount_observations(results, strategy, cfg)

        assert obs["enc"].mounted is True
        assert obs["plain"].device_present is False
