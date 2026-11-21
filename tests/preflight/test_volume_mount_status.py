"""Tests for how a mount-managed volume's probed mount state becomes its
preflight errors and its rendered mount status."""

from rich.text import Text

from nbkp.config import (
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.disks import MountCapabilities
from nbkp.disks.models import MountFailureKind
from nbkp.preflight.output.formatting import format_mount_status
from nbkp.preflight.status.ssh import SshEndpointDiagnostics, SshEndpointStatus
from nbkp.preflight.status.volume import (
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)

_ENC_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"
_USB_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _base_mount_caps(**overrides: object) -> MountCapabilities:
    """Build MountCapabilities with sensible defaults, then apply overrides."""
    return MountCapabilities.model_validate(
        {
            "has_fstab_entry": True,
            "fstab_target": "/mnt/encrypted",
            "device_present": True,
            "mounted": True,
            **overrides,
        }
    )


def _base_caps(
    sentinel_exists: bool = True, **mount_overrides: object
) -> VolumeCapabilities:
    """Build VolumeCapabilities with mount defaults, then apply overrides."""
    return VolumeCapabilities(
        sentinel_exists=sentinel_exists,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
        mount=_base_mount_caps(**mount_overrides),
    )


def _encrypted_mount() -> MountConfig:
    return MountConfig(
        device_uuid=_ENC_UUID,
        encryption=LuksEncryptionConfig(passphrase_id="encrypted"),
    )


def _unencrypted_mount() -> MountConfig:
    return MountConfig(device_uuid=_USB_UUID)


def _encrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="encrypted", path="/mnt/encrypted", mount=_encrypted_mount()
    )


def _active_ssh_endpoint_status() -> SshEndpointStatus:
    return SshEndpointStatus.from_diagnostics(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(),
    )


def _volume_errors(
    diag: VolumeDiagnostics, mount: MountConfig | None
) -> list[VolumeErrorKind]:
    """The errors of a local volume at the encrypted path, mounted by *mount*."""
    vol = LocalVolume(slug="v", path="/mnt/encrypted", mount=mount)
    return VolumeStatus.from_diagnostics(
        "v", vol, _active_ssh_endpoint_status(), diag
    ).errors


class TestVolumeErrorsMountUpgrades:
    """When the sentinel is missing and a mount-managed volume recorded a
    lifecycle failure, the generic SENTINEL_NOT_FOUND is upgraded to the
    most actionable VolumeErrorKind."""

    def _diag(self, **mount_overrides: object) -> VolumeDiagnostics:
        return VolumeDiagnostics(
            capabilities=_base_caps(sentinel_exists=False, **mount_overrides)
        )

    def test_not_authorized_upgrades_to_polkit_rules_missing(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                luks_unlocked=False,
                mounted=None,
                mount_failure_reason="not-authorized",
            ),
            _encrypted_mount(),
        )
        assert VolumeErrorKind.POLKIT_RULES_MISSING in errors
        assert VolumeErrorKind.SENTINEL_NOT_FOUND not in errors
        assert VolumeErrorKind.VOLUME_NOT_MOUNTED not in errors

    def test_passphrase_not_available_upgrades(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                luks_unlocked=False,
                mounted=None,
                mount_failure_reason=MountFailureKind.PASSPHRASE_NOT_AVAILABLE,
            ),
            _encrypted_mount(),
        )
        assert errors == [VolumeErrorKind.PASSPHRASE_NOT_AVAILABLE]

    def test_unlock_failed_upgrades(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                luks_unlocked=False,
                mounted=None,
                mount_failure_reason="unlock-failed",
            ),
            _encrypted_mount(),
        )
        assert VolumeErrorKind.UNLOCK_FAILED in errors
        assert VolumeErrorKind.SENTINEL_NOT_FOUND not in errors

    def test_mount_failed_upgrades(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                luks_unlocked=True,
                mounted=False,
                mount_failure_reason="mount-failed",
            ),
            _encrypted_mount(),
        )
        assert VolumeErrorKind.MOUNT_FAILED in errors
        assert VolumeErrorKind.VOLUME_NOT_MOUNTED not in errors

    def test_device_not_present(self) -> None:
        errors = _volume_errors(
            self._diag(device_present=False, mounted=None),
            _encrypted_mount(),
        )
        assert VolumeErrorKind.DEVICE_NOT_PRESENT in errors
        assert VolumeErrorKind.SENTINEL_NOT_FOUND not in errors

    def test_fstab_mismatch_when_path_declared_no_fstab(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                has_fstab_entry=False,
                mounted=False,
            ),
            _encrypted_mount(),
        )
        assert VolumeErrorKind.FSTAB_MOUNTPOINT_MISMATCH in errors

    def test_device_present_unmounted_emits_volume_not_mounted(self) -> None:
        errors = _volume_errors(
            self._diag(device_present=True, has_fstab_entry=True, mounted=False),
            _encrypted_mount(),
        )
        assert VolumeErrorKind.VOLUME_NOT_MOUNTED in errors
        assert VolumeErrorKind.DEVICE_NOT_PRESENT not in errors

    def test_sentinel_missing_when_mounted_falls_back_to_sentinel(self) -> None:
        errors = _volume_errors(
            self._diag(device_present=True, has_fstab_entry=True, mounted=True),
            _encrypted_mount(),
        )
        assert VolumeErrorKind.SENTINEL_NOT_FOUND in errors

    def test_no_mount_config_falls_back_to_sentinel(self) -> None:
        errors = _volume_errors(self._diag(), None)
        assert VolumeErrorKind.SENTINEL_NOT_FOUND in errors

    def test_sentinel_present_no_errors(self) -> None:
        diag = VolumeDiagnostics(capabilities=_base_caps(sentinel_exists=True))
        assert _volume_errors(diag, _encrypted_mount()) == []


class TestVolumeStatusFromDiagnostics:
    def test_all_checks_pass_active(self) -> None:
        diag = VolumeDiagnostics(capabilities=_base_caps())
        status = VolumeStatus.from_diagnostics(
            "encrypted", _encrypted_vol(), _active_ssh_endpoint_status(), diag
        )
        assert status.active

    def test_not_authorized_upgrades_volume_not_mounted(self) -> None:
        caps = _base_caps(
            sentinel_exists=False,
            device_present=True,
            luks_unlocked=False,
            mounted=None,
            mount_failure_reason="not-authorized",
        )
        diag = VolumeDiagnostics(capabilities=caps)
        status = VolumeStatus.from_diagnostics(
            "encrypted", _encrypted_vol(), _active_ssh_endpoint_status(), diag
        )
        assert VolumeErrorKind.POLKIT_RULES_MISSING in status.errors
        assert VolumeErrorKind.SENTINEL_NOT_FOUND not in status.errors

    def test_no_mount_config_no_mount_errors(self) -> None:
        caps = _base_caps(sentinel_exists=True)
        vol = LocalVolume(slug="plain", path="/mnt/plain")
        diag = VolumeDiagnostics(capabilities=caps)
        status = VolumeStatus.from_diagnostics(
            "plain", vol, _active_ssh_endpoint_status(), diag
        )
        assert not status.errors

    def test_device_not_present_emits_device_error(self) -> None:
        caps = _base_caps(sentinel_exists=False, device_present=False, mounted=None)
        diag = VolumeDiagnostics(capabilities=caps)
        status = VolumeStatus.from_diagnostics(
            "encrypted", _encrypted_vol(), _active_ssh_endpoint_status(), diag
        )
        assert VolumeErrorKind.DEVICE_NOT_PRESENT in status.errors


class TestFormatMountStatus:
    def test_none_caps_returns_empty(self) -> None:
        assert format_mount_status(None, _encrypted_mount()) == Text("")

    def test_none_config_returns_empty(self) -> None:
        mc = _base_mount_caps(device_present=True, mounted=True)
        assert format_mount_status(mc, None) == Text("")

    def test_encrypted_all_true(self) -> None:
        mc = _base_mount_caps(device_present=True, luks_unlocked=True, mounted=True)
        result = format_mount_status(mc, _encrypted_mount())
        assert "✓device" in result
        assert "✓luks" in result
        assert "✓mounted" in result

    def test_encrypted_device_absent_cascades_luks_to_warning(self) -> None:
        mc = _base_mount_caps(device_present=False, luks_unlocked=False, mounted=False)
        result = format_mount_status(mc, _encrypted_mount())
        assert "⚠device" in result
        assert "⚠luks" in result
        assert "⚠mounted" in result

    def test_encrypted_device_present_luks_probe_only_is_warning(self) -> None:
        mc = _base_mount_caps(device_present=True, luks_unlocked=False, mounted=False)
        result = format_mount_status(mc, _encrypted_mount())
        assert "✓device" in result
        assert "⚠luks" in result
        assert "⚠mounted" in result

    def test_encrypted_device_present_unlock_failed_is_fatal(self) -> None:
        mc = _base_mount_caps(
            device_present=True,
            luks_unlocked=False,
            mounted=None,
            mount_failure_reason="unlock-failed",
        )
        result = format_mount_status(mc, _encrypted_mount())
        assert "✓device" in result
        assert "✗luks" in result

    def test_mount_failed_renders_mounted_as_fatal(self) -> None:
        mc = _base_mount_caps(
            device_present=True,
            luks_unlocked=True,
            mounted=False,
            mount_failure_reason="mount-failed",
        )
        result = format_mount_status(mc, _encrypted_mount())
        assert "✓device" in result
        assert "✓luks" in result
        assert "✗mounted" in result

    def test_unencrypted_no_luks_column(self) -> None:
        mc = _base_mount_caps(device_present=True, mounted=True)
        result = format_mount_status(mc, _unencrypted_mount())
        assert "luks" not in result
        assert "✓device" in result
        assert "✓mounted" in result

    def test_not_probed_items_omitted(self) -> None:
        mc = _base_mount_caps(device_present=None, mounted=None)
        result = format_mount_status(mc, _unencrypted_mount())
        assert "device" not in result
        assert "mounted" not in result
