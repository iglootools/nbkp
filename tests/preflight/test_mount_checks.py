"""Tests for mount-related preflight checks (udisks2 backend)."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from rich.text import Text

from nbkp.config import (
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.disks import MountCapabilities, MountToolCapabilities
from nbkp.disks.detection import DeviceProbeError
from nbkp.disks.models import MountFailureReason
from nbkp.disks.mount_checks import (
    FstabEntry,
    _check_fstab_entry,
    check_mount_capabilities,
    check_mount_status,
    fstab_source_matches,
)
from nbkp.disks.observation import MountObservation
from nbkp.preflight.output.formatting import format_mount_status
from nbkp.preflight.status.ssh import SshEndpointDiagnostics, SshEndpointStatus
from nbkp.preflight.status.volume import (
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
    _volume_errors,
)

_ENC_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"
_USB_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_MOUNT_TOOLS = MountToolCapabilities(
    has_udisksctl=True,
    udisksd_running=True,
    has_findmnt=True,
    has_lsblk=True,
)


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

    def test_passphrase_not_available_upgrades(self) -> None:
        errors = _volume_errors(
            self._diag(
                device_present=True,
                luks_unlocked=False,
                mounted=None,
                mount_failure_reason=MountFailureReason.PASSPHRASE_NOT_AVAILABLE,
            ),
            _encrypted_mount(),
        )
        assert errors == [VolumeError.PASSPHRASE_NOT_AVAILABLE]

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
    @patch("nbkp.disks.mount_checks.find_mountpoint")
    @patch(
        "nbkp.disks.mount_checks._check_fstab_entry",
        return_value=FstabEntry(source="/dev/mapper/luks-x", target="/mnt/encrypted"),
    )
    def test_observation_skips_runtime_probes(
        self,
        _mock_fstab: object,
        mock_findmnt: object,
        mock_discover: object,
        mock_device: object,
    ) -> None:
        """When observation is provided, runtime detection functions are not called."""
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=True,
            cleartext_device="/dev/mapper/luks-x",
            effective_path="/mnt/encrypted",
        )
        result = check_mount_capabilities(
            _encrypted_vol(), _encrypted_mount(), _MOUNT_TOOLS, {}, obs
        )

        # Runtime probes should not have been called.
        mock_device.assert_not_called()  # type: ignore[union-attr]
        mock_discover.assert_not_called()  # type: ignore[union-attr]
        mock_findmnt.assert_not_called()  # type: ignore[union-attr]

        # Values come from observation.
        assert result.device_present is True
        assert result.luks_unlocked is True
        assert result.mounted is True
        assert result.cleartext_device == "/dev/mapper/luks-x"
        assert result.effective_path == "/mnt/encrypted"
        assert result.has_fstab_entry is True

    @patch(
        "nbkp.disks.mount_checks._check_fstab_entry",
        return_value=FstabEntry(source="/dev/mapper/luks-x", target="/mnt/encrypted"),
    )
    @patch("nbkp.disks.mount_checks.find_mountpoint", return_value="/mnt/encrypted")
    @patch(
        "nbkp.disks.mount_checks.discover_cleartext_device",
        return_value="/dev/mapper/luks-x",
    )
    @patch("nbkp.disks.mount_checks.detect_device_present", return_value=True)
    def test_probes_when_no_observation(
        self,
        mock_device: object,
        mock_discover: object,
        mock_findmnt: object,
        _mock_fstab: object,
    ) -> None:
        """Without an observation, runtime detection functions are called once."""
        result = check_mount_capabilities(
            _encrypted_vol(), _encrypted_mount(), _MOUNT_TOOLS, {}, None
        )
        mock_device.assert_called_once()  # type: ignore[union-attr]
        # lsblk runs once: the cleartext device is reused for the mountpoint.
        mock_discover.assert_called_once()  # type: ignore[union-attr]
        assert result.device_present is True
        assert result.luks_unlocked is True
        assert result.mounted is True


class TestToolGating:
    """Probes for tools reported missing are skipped (state unknown)."""

    @patch("nbkp.disks.mount_checks._check_fstab_entry")
    @patch("nbkp.disks.mount_checks.find_mountpoint")
    @patch("nbkp.disks.mount_checks.discover_cleartext_device")
    @patch("nbkp.disks.mount_checks.detect_device_present", return_value=True)
    def test_missing_findmnt_and_lsblk(
        self,
        _mock_device: object,
        mock_discover: object,
        mock_findmnt: object,
        mock_fstab: object,
    ) -> None:
        tools = MountToolCapabilities(
            has_udisksctl=True, udisksd_running=True, has_findmnt=False, has_lsblk=False
        )
        result = check_mount_capabilities(
            _encrypted_vol(), _encrypted_mount(), tools, {}, None
        )
        mock_discover.assert_not_called()  # type: ignore[union-attr]
        mock_findmnt.assert_not_called()  # type: ignore[union-attr]
        mock_fstab.assert_not_called()  # type: ignore[union-attr]
        # Unknown, not False: no false FSTAB_MOUNTPOINT_MISMATCH / unlocked state.
        assert result.has_fstab_entry is None
        assert result.luks_unlocked is None
        assert result.mounted is None

    @patch("nbkp.disks.mount_checks._check_fstab_entry", return_value=None)
    @patch("nbkp.disks.mount_checks.find_mountpoint", return_value=None)
    @patch(
        "nbkp.disks.mount_checks.discover_cleartext_device",
        side_effect=DeviceProbeError("lsblk", 1, "boom"),
    )
    @patch("nbkp.disks.mount_checks.detect_device_present", return_value=True)
    def test_failed_lsblk_is_unknown(self, *_mocks: object) -> None:
        result = check_mount_capabilities(
            _encrypted_vol(), _encrypted_mount(), _MOUNT_TOOLS, {}, None
        )
        assert result.luks_unlocked is None
        assert result.mounted is None

    @patch("nbkp.disks.mount_checks.probe_mount_tools")
    @patch("nbkp.disks.mount_checks._check_fstab_entry", return_value=None)
    @patch("nbkp.disks.mount_checks.find_mountpoint", return_value=None)
    @patch("nbkp.disks.mount_checks.detect_device_present", return_value=False)
    def test_check_mount_status_does_not_probe_tools(
        self, _d: object, _f: object, _fs: object, mock_probe: object
    ) -> None:
        check_mount_status(_unencrypted_vol(), _unencrypted_mount(), {})
        mock_probe.assert_not_called()  # type: ignore[union-attr]


class TestFstabSourceMatches:
    """The fstab entry for the declared path must designate this device."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (f"UUID={_USB_UUID}", True),
            (f"UUID={_USB_UUID.upper()}", True),
            (f"/dev/disk/by-uuid/{_USB_UUID}", True),
            ("UUID=11111111-2222-3333-4444-555555555555", False),
            ("/dev/disk/by-uuid/11111111-2222-3333-4444-555555555555", False),
            # Unverifiable without extra probes: benefit of the doubt.
            ("LABEL=backup", True),
            ("/dev/sdb1", True),
        ],
    )
    def test_unencrypted(self, source: str, expected: bool) -> None:
        assert fstab_source_matches(source, _unencrypted_mount(), None) is expected

    @pytest.mark.parametrize(
        ("source", "cleartext", "expected"),
        [
            ("/dev/mapper/seagate-luks", "/dev/mapper/seagate-luks", True),
            ("/dev/mapper/other", "/dev/mapper/seagate-luks", False),
            (f"/dev/mapper/luks-{_ENC_UUID}", None, True),
            ("/dev/mapper/other", None, False),
            # The LUKS container itself cannot be mounted.
            (f"UUID={_ENC_UUID}", None, False),
            (f"/dev/disk/by-uuid/{_ENC_UUID}", None, False),
            # The inner filesystem's UUID is not in the config: accept.
            ("UUID=99999999-8888-7777-6666-555555555555", None, True),
        ],
    )
    def test_encrypted(
        self, source: str, cleartext: str | None, expected: bool
    ) -> None:
        assert fstab_source_matches(source, _encrypted_mount(), cleartext) is expected

    @patch(
        "nbkp.disks.mount_checks._check_fstab_entry",
        return_value=FstabEntry(
            source="UUID=11111111-2222-3333-4444-555555555555", target="/mnt/usb"
        ),
    )
    @patch("nbkp.disks.mount_checks.find_mountpoint", return_value=None)
    @patch("nbkp.disks.mount_checks.detect_device_present", return_value=True)
    def test_entry_for_another_device_is_a_mismatch(self, *_mocks: object) -> None:
        result = check_mount_capabilities(
            _unencrypted_vol(), _unencrypted_mount(), _MOUNT_TOOLS, {}, None
        )
        assert result.has_fstab_entry is False
        assert result.fstab_source == "UUID=11111111-2222-3333-4444-555555555555"
        caps = VolumeCapabilities(
            sentinel_exists=False,
            is_btrfs_filesystem=False,
            hardlink_supported=True,
            btrfs_user_subvol_rm=False,
            mount=result,
        )
        assert _volume_errors(
            VolumeDiagnostics(capabilities=caps), _unencrypted_mount()
        ) == [VolumeError.FSTAB_MOUNTPOINT_MISMATCH]

    @patch("nbkp.disks.mount_checks.run_on_volume")
    def test_parses_findmnt_pairs(self, mock_run: object) -> None:
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=[],
            returncode=0,
            stdout=f'SOURCE="UUID={_USB_UUID}" TARGET="/mnt/my usb"\n',
            stderr="",
        )
        entry = _check_fstab_entry(_unencrypted_vol(), "/mnt/my usb", {})
        assert entry == FstabEntry(source=f"UUID={_USB_UUID}", target="/mnt/my usb")
        cmd = mock_run.call_args[0][0]  # type: ignore[attr-defined]
        assert cmd == [
            "findmnt",
            "--fstab",
            "--target",
            "/mnt/my usb",
            "-n",
            "-P",
            "-o",
            "SOURCE,TARGET",
        ]
