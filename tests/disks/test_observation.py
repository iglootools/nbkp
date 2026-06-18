"""Tests for MountObservation building from mount lifecycle results."""

from __future__ import annotations

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.disks.lifecycle import MountFailureReason, MountResult
from nbkp.disks.observation import (
    MountObservation,
    apply_effective_paths,
    build_mount_observations,
)

_ENCRYPTED_VOLUME = LocalVolume(
    slug="enc",
    path="/mnt/encrypted",
    mount=MountConfig(
        device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
        encryption=LuksEncryptionConfig(passphrase_id="enc-pass"),
    ),
)

_PLAIN_VOLUME = LocalVolume(
    slug="plain",
    path="/mnt/plain",
    mount=MountConfig(device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
)

# A mount-managed volume that omits path (Option B: discovered mountpoint).
_DISCOVERED_VOLUME = LocalVolume(
    slug="usb",
    mount=MountConfig(device_uuid="cccccccc-dddd-eeee-ffff-000000000000"),
)


def _config_with_volumes(*volumes: LocalVolume) -> Config:
    return Config(
        ssh_endpoints={},
        volumes={v.slug: v for v in volumes},
        sync_endpoints={},
        syncs={},
    )


class TestBuildMountObservations:
    def test_success_encrypted(self) -> None:
        results = [
            MountResult(
                volume_slug="enc",
                success=True,
                device_present=True,
                luks_unlocked=True,
                mounted=True,
                cleartext_device="/dev/mapper/luks-x",
                effective_path="/mnt/encrypted",
            )
        ]
        obs = build_mount_observations(results)
        assert obs["enc"] == MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=True,
            cleartext_device="/dev/mapper/luks-x",
            effective_path="/mnt/encrypted",
        )

    def test_success_plain(self) -> None:
        results = [
            MountResult(
                volume_slug="plain",
                success=True,
                device_present=True,
                luks_unlocked=None,
                mounted=True,
                effective_path="/mnt/plain",
            )
        ]
        obs = build_mount_observations(results)
        assert obs["plain"].device_present is True
        assert obs["plain"].luks_unlocked is None
        assert obs["plain"].mounted is True

    def test_device_not_present(self) -> None:
        results = [
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.DEVICE_NOT_PRESENT,
                device_present=False,
            )
        ]
        obs = build_mount_observations(results)
        assert obs["enc"].device_present is False
        assert obs["enc"].luks_unlocked is None
        assert obs["enc"].mounted is None
        assert obs["enc"].failure_reason == MountFailureReason.DEVICE_NOT_PRESENT

    def test_unlock_failed_propagates_failure_reason(self) -> None:
        results = [
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.UNLOCK_FAILED,
                device_present=True,
                luks_unlocked=False,
            )
        ]
        obs = build_mount_observations(results)
        assert obs["enc"].device_present is True
        assert obs["enc"].luks_unlocked is False
        assert obs["enc"].failure_reason == MountFailureReason.UNLOCK_FAILED

    def test_not_authorized_propagated(self) -> None:
        results = [
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.NOT_AUTHORIZED,
                device_present=True,
                luks_unlocked=False,
            )
        ]
        obs = build_mount_observations(results)
        assert obs["enc"].failure_reason == MountFailureReason.NOT_AUTHORIZED
        assert obs["enc"].mount_failure_reason == "not_authorized"

    def test_mount_failed(self) -> None:
        results = [
            MountResult(
                volume_slug="plain",
                success=False,
                failure_reason=MountFailureReason.MOUNT_FAILED,
                device_present=True,
                mounted=False,
            )
        ]
        obs = build_mount_observations(results)
        assert obs["plain"].device_present is True
        assert obs["plain"].mounted is False
        assert obs["plain"].failure_reason == MountFailureReason.MOUNT_FAILED

    def test_unreachable_skipped(self) -> None:
        results = [
            MountResult(
                volume_slug="enc",
                success=False,
                failure_reason=MountFailureReason.UNREACHABLE,
            )
        ]
        obs = build_mount_observations(results)
        assert "enc" not in obs

    def test_no_results(self) -> None:
        assert build_mount_observations([]) == {}

    def test_multiple_volumes(self) -> None:
        results = [
            MountResult(volume_slug="enc", success=True, mounted=True),
            MountResult(
                volume_slug="plain",
                success=False,
                failure_reason=MountFailureReason.DEVICE_NOT_PRESENT,
                device_present=False,
            ),
        ]
        obs = build_mount_observations(results)
        assert obs["enc"].mounted is True
        assert obs["plain"].device_present is False


class TestApplyEffectivePaths:
    def test_fills_discovered_path_when_omitted(self) -> None:
        cfg = _config_with_volumes(_DISCOVERED_VOLUME)
        observations = {
            "usb": MountObservation(
                device_present=True,
                mounted=True,
                effective_path="/run/media/backup/label",
            )
        }
        updated = apply_effective_paths(cfg, observations)
        assert updated.volumes["usb"].path == "/run/media/backup/label"

    def test_declared_path_left_untouched(self) -> None:
        cfg = _config_with_volumes(_PLAIN_VOLUME)
        observations = {
            "plain": MountObservation(
                device_present=True,
                mounted=True,
                effective_path="/somewhere/else",
            )
        }
        updated = apply_effective_paths(cfg, observations)
        # Declared path wins; not overwritten by the observation.
        assert updated.volumes["plain"].path == "/mnt/plain"

    def test_no_effective_path_returns_same_config(self) -> None:
        cfg = _config_with_volumes(_DISCOVERED_VOLUME)
        observations = {
            "usb": MountObservation(device_present=False, effective_path=None)
        }
        updated = apply_effective_paths(cfg, observations)
        assert updated is cfg

    def test_no_observations_returns_same_config(self) -> None:
        cfg = _config_with_volumes(_DISCOVERED_VOLUME)
        assert apply_effective_paths(cfg, {}) is cfg
