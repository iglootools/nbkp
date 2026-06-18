"""Tests for mount-related preflight checks (udisks2 backend)."""

from __future__ import annotations

from unittest.mock import patch

from nbkp.config import (
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.disks.observation import MountObservation
from rich.text import Text

from nbkp.preflight.output.formatting import format_mount_status
from nbkp.preflight.status import (
    MountCapabilities,
    MountToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
    _volume_errors,
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


def _unencrypted_vol() -> LocalVolume:
    return LocalVolume(slug="usb", path="/mnt/usb", mount=_unencrypted_mount())


def _active_ssh_endpoint_status() -> SshEndpointStatus:
    """Build an active (no errors) SshEndpointStatus for localhost."""
    return SshEndpointStatus.from_diagnostics(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(),
    )


# ── _volume_errors: lifecycle-failure upgrades ─────────────────


class TestVolumeErrorsMountUpgrades:
    """When the sentinel is missing and a mount-managed volume recorded a
    lifecycle failure, the generic SENTINEL_NOT_FOUND is upgraded to the
    most actionable VolumeError."""

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
                mount_failure_reason="not_authorized",
            ),
            _encrypted_mount(),
        )
        assert VolumeError.POLKIT_RULES_MISSING in errors
        assert VolumeError.SENTINEL_NOT_FOUND not in errors
        assert VolumeError.VOLUME_NOT_MOUNTED not in errors

    def test_unlock_failed_upgrades(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                luks_unlocked=False,
                mounted=None,
                mount_failure_reason="unlock_failed",
            ),
            _encrypted_mount(),
        )
        assert VolumeError.UNLOCK_FAILED in errors
        assert VolumeError.SENTINEL_NOT_FOUND not in errors

    def test_mount_failed_upgrades(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                luks_unlocked=True,
                mounted=False,
                mount_failure_reason="mount_failed",
            ),
            _encrypted_mount(),
        )
        assert VolumeError.MOUNT_FAILED in errors
        assert VolumeError.VOLUME_NOT_MOUNTED not in errors

    def test_device_not_present(self) -> None:
        errors = _volume_errors(
            self._diag(device_present=False, mounted=None),
            _encrypted_mount(),
        )
        assert VolumeError.DEVICE_NOT_PRESENT in errors
        assert VolumeError.SENTINEL_NOT_FOUND not in errors

    def test_fstab_mismatch_when_path_declared_no_fstab(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                has_fstab_entry=False,
                mounted=False,
            ),
            _encrypted_mount(),
        )
        assert VolumeError.FSTAB_MOUNTPOINT_MISMATCH in errors

    def test_device_present_unmounted_emits_volume_not_mounted(self) -> None:
        errors = _volume_errors(
            self._diag(device_present=True, has_fstab_entry=True, mounted=False),
            _encrypted_mount(),
        )
        assert VolumeError.VOLUME_NOT_MOUNTED in errors
        assert VolumeError.DEVICE_NOT_PRESENT not in errors

    def test_sentinel_missing_when_mounted_falls_back_to_sentinel(self) -> None:
        errors = _volume_errors(
            self._diag(device_present=True, has_fstab_entry=True, mounted=True),
            _encrypted_mount(),
        )
        assert VolumeError.SENTINEL_NOT_FOUND in errors

    def test_no_mount_config_falls_back_to_sentinel(self) -> None:
        errors = _volume_errors(self._diag(), None)
        assert VolumeError.SENTINEL_NOT_FOUND in errors

    def test_sentinel_present_no_errors(self) -> None:
        diag = VolumeDiagnostics(capabilities=_base_caps(sentinel_exists=True))
        assert _volume_errors(diag, _encrypted_mount()) == []


# ── VolumeStatus.from_diagnostics integration ──────────────────


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
            mount_failure_reason="not_authorized",
        )
        diag = VolumeDiagnostics(capabilities=caps)
        status = VolumeStatus.from_diagnostics(
            "encrypted", _encrypted_vol(), _active_ssh_endpoint_status(), diag
        )
        assert VolumeError.POLKIT_RULES_MISSING in status.errors
        assert VolumeError.SENTINEL_NOT_FOUND not in status.errors

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
        assert VolumeError.DEVICE_NOT_PRESENT in status.errors


# ── MountCapabilities runtime state fields ─────────────────────


class TestMountCapabilitiesRuntimeState:
    def test_defaults_to_none(self) -> None:
        mc = MountCapabilities()
        assert mc.device_present is None
        assert mc.luks_unlocked is None
        assert mc.mounted is None
        assert mc.cleartext_device is None
        assert mc.effective_path is None
        assert mc.mount_failure_reason is None

    def test_explicit_values(self) -> None:
        mc = MountCapabilities(
            device_present=True,
            luks_unlocked=True,
            mounted=False,
        )
        assert mc.device_present is True
        assert mc.luks_unlocked is True
        assert mc.mounted is False


# ── format_mount_status ────────────────────────────────────────


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
            mount_failure_reason="unlock_failed",
        )
        result = format_mount_status(mc, _encrypted_mount())
        assert "✓device" in result
        assert "✗luks" in result

    def test_mount_failed_renders_mounted_as_fatal(self) -> None:
        mc = _base_mount_caps(
            device_present=True,
            luks_unlocked=True,
            mounted=False,
            mount_failure_reason="mount_failed",
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


# ── Observation reuse ──────────────────────────────────────────


class TestObservationReuse:
    """Verify that mount observation values bypass runtime detection probes."""

    @patch("nbkp.disks.mount_checks.detect_device_present")
    @patch("nbkp.disks.mount_checks.discover_cleartext_device")
    @patch("nbkp.disks.mount_checks.resolve_target_device")
    @patch("nbkp.disks.mount_checks.find_mountpoint")
    @patch("nbkp.disks.mount_checks._check_fstab_entry", return_value="/mnt/encrypted")
    def test_observation_skips_runtime_probes(
        self,
        _mock_fstab: object,
        mock_findmnt: object,
        mock_resolve: object,
        mock_discover: object,
        mock_device: object,
    ) -> None:
        """When observation is provided, runtime detection functions are not called."""
        from nbkp.disks.mount_checks import check_mount_capabilities

        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=True,
            cleartext_device="/dev/mapper/luks-x",
            effective_path="/mnt/encrypted",
        )
        mount_tools = MountToolCapabilities(
            has_udisksctl=True,
            udisksd_running=True,
            has_findmnt=True,
            has_lsblk=True,
        )

        result = check_mount_capabilities(
            _encrypted_vol(), _encrypted_mount(), mount_tools, {}, obs
        )

        # Runtime probes should not have been called.
        mock_device.assert_not_called()  # type: ignore[union-attr]
        mock_discover.assert_not_called()  # type: ignore[union-attr]
        mock_resolve.assert_not_called()  # type: ignore[union-attr]
        mock_findmnt.assert_not_called()  # type: ignore[union-attr]

        # Values come from observation.
        assert result.device_present is True
        assert result.luks_unlocked is True
        assert result.mounted is True
        assert result.cleartext_device == "/dev/mapper/luks-x"
        assert result.effective_path == "/mnt/encrypted"

    @patch("nbkp.disks.mount_checks._check_fstab_entry", return_value="/mnt/encrypted")
    @patch("nbkp.disks.mount_checks.find_mountpoint", return_value="/mnt/encrypted")
    @patch(
        "nbkp.disks.mount_checks.resolve_target_device",
        return_value="/dev/mapper/luks-x",
    )
    @patch(
        "nbkp.disks.mount_checks.discover_cleartext_device",
        return_value="/dev/mapper/luks-x",
    )
    @patch("nbkp.disks.mount_checks.detect_device_present", return_value=True)
    def test_probes_when_no_observation(
        self,
        mock_device: object,
        mock_discover: object,
        mock_resolve: object,
        mock_findmnt: object,
        _mock_fstab: object,
    ) -> None:
        """Without an observation, runtime detection functions are called."""
        from nbkp.disks.mount_checks import check_mount_capabilities

        mount_tools = MountToolCapabilities(
            has_udisksctl=True,
            udisksd_running=True,
            has_findmnt=True,
            has_lsblk=True,
        )
        result = check_mount_capabilities(
            _encrypted_vol(), _encrypted_mount(), mount_tools, {}, None
        )
        mock_device.assert_called_once()  # type: ignore[union-attr]
        assert result.device_present is True
        assert result.luks_unlocked is True
        assert result.mounted is True
