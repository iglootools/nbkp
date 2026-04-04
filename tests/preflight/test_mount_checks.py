"""Tests for mount-related preflight checks."""

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
    _mount_errors,
    _mount_unit_mismatches,
    _cryptsetup_service_mismatches,
)

_MOUNT_DEFAULTS = dict(
    resolved_backend="systemd",
    mount_unit="mnt-encrypted.mount",
    has_mount_unit_config=True,
    mount_unit_what="/dev/mapper/encrypted",
    mount_unit_where="/mnt/encrypted",
    has_cryptsetup_service_config=True,
    cryptsetup_service_exec_start=(
        "/usr/lib/systemd/systemd-cryptsetup attach encrypted"
        " /dev/disk/by-uuid/5941f273-f73c-44c5-a3ef-fae7248db1b6"
        " /dev/stdin luks"
    ),
    has_polkit_rules=True,
    has_sudoers_rules=True,
)


def _base_mount_caps(**overrides: object) -> MountCapabilities:
    """Build MountCapabilities with sensible defaults, then apply overrides."""
    return MountCapabilities(**{**_MOUNT_DEFAULTS, **overrides})


def _base_caps(**mount_overrides: object) -> VolumeCapabilities:
    """Build VolumeCapabilities with mount defaults, then apply overrides."""
    return VolumeCapabilities(
        sentinel_exists=True,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
        mount=_base_mount_caps(**mount_overrides),
    )


def _encrypted_mount() -> MountConfig:
    return MountConfig(
        device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
        encryption=LuksEncryptionConfig(
            mapper_name="encrypted",
            passphrase_id="encrypted",
        ),
    )


def _unencrypted_mount() -> MountConfig:
    return MountConfig(
        device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )


def _encrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="encrypted",
        path="/mnt/encrypted",
        mount=_encrypted_mount(),
    )


def _unencrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="usb",
        path="/mnt/usb",
        mount=_unencrypted_mount(),
    )


def _active_ssh_endpoint_status() -> SshEndpointStatus:
    """Build an active (no errors) SshEndpointStatus for localhost."""
    return SshEndpointStatus.from_diagnostics(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(),
    )


class TestMountErrors:
    def test_all_caps_present_no_errors(self) -> None:
        errors = _mount_errors(_base_mount_caps(), _encrypted_mount())
        assert errors == []

    def test_mount_unit_not_configured(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(has_mount_unit_config=False), _encrypted_mount()
        )
        assert VolumeError.MOUNT_UNIT_NOT_CONFIGURED in errors

    def test_mount_unit_mismatch_encrypted(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(mount_unit_what="/dev/mapper/wrong"),
            _encrypted_mount(),
        )
        assert VolumeError.MOUNT_UNIT_MISMATCH in errors

    def test_mount_unit_mismatch_unencrypted(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(
                mount_unit_what="/dev/disk/by-uuid/wrong-uuid",
                has_cryptsetup_service_config=None,
                has_sudoers_rules=None,
            ),
            _unencrypted_mount(),
        )
        assert VolumeError.MOUNT_UNIT_MISMATCH in errors

    def test_cryptsetup_service_not_configured(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(has_cryptsetup_service_config=False),
            _encrypted_mount(),
        )
        assert VolumeError.CRYPTSETUP_SERVICE_NOT_CONFIGURED in errors

    def test_cryptsetup_service_mismatch(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(
                cryptsetup_service_exec_start="attach wrong-mapper /dev/disk/by-uuid/wrong"
            ),
            _encrypted_mount(),
        )
        assert VolumeError.CRYPTSETUP_SERVICE_MISMATCH in errors

    def test_polkit_rules_missing(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(has_polkit_rules=False), _encrypted_mount()
        )
        assert VolumeError.POLKIT_RULES_MISSING in errors

    def test_sudoers_rules_missing(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(has_sudoers_rules=False), _encrypted_mount()
        )
        assert VolumeError.SUDOERS_RULES_MISSING in errors

    def test_unencrypted_skips_encryption_checks(self) -> None:
        """Unencrypted volumes should not trigger encryption-specific errors."""
        mc = _base_mount_caps(
            has_cryptsetup_service_config=None,
            has_sudoers_rules=None,
            mount_unit_what="/dev/disk/by-uuid/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        errors = _mount_errors(mc, _unencrypted_mount())
        assert errors == []

    def test_none_caps_not_probed(self) -> None:
        """When capability is None (not probed), no error should be generated."""
        mc = _base_mount_caps(
            has_mount_unit_config=None,
            has_polkit_rules=None,
        )
        errors = _mount_errors(mc, _encrypted_mount())
        assert VolumeError.MOUNT_UNIT_NOT_CONFIGURED not in errors
        assert VolumeError.POLKIT_RULES_MISSING not in errors

    def test_none_mount_caps_no_errors(self) -> None:
        """When mount capabilities are None, no mount errors should be generated."""
        errors = _mount_errors(None, _encrypted_mount())
        assert errors == []


class TestMountUnitMismatches:
    def test_encrypted_correct_what(self) -> None:
        mc = _base_mount_caps(mount_unit_what="/dev/mapper/encrypted")
        assert not _mount_unit_mismatches(mc, _encrypted_mount())

    def test_encrypted_wrong_what(self) -> None:
        mc = _base_mount_caps(mount_unit_what="/dev/mapper/wrong")
        assert _mount_unit_mismatches(mc, _encrypted_mount())

    def test_unencrypted_correct_what(self) -> None:
        mc = _base_mount_caps(
            mount_unit_what="/dev/disk/by-uuid/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )
        assert not _mount_unit_mismatches(mc, _unencrypted_mount())

    def test_none_what_no_mismatch(self) -> None:
        """When What is None (not probed), should not report mismatch."""
        mc = _base_mount_caps(mount_unit_what=None)
        assert not _mount_unit_mismatches(mc, _encrypted_mount())


class TestCryptsetupServiceMismatches:
    def test_correct_exec_start(self) -> None:
        assert not _cryptsetup_service_mismatches(
            _base_mount_caps(), _encrypted_mount()
        )

    def test_wrong_mapper(self) -> None:
        mc = _base_mount_caps(
            cryptsetup_service_exec_start=(
                "attach wrong /dev/disk/by-uuid/5941f273-f73c-44c5-a3ef-fae7248db1b6"
            )
        )
        assert _cryptsetup_service_mismatches(mc, _encrypted_mount())

    def test_wrong_uuid(self) -> None:
        mc = _base_mount_caps(
            cryptsetup_service_exec_start="attach encrypted /dev/disk/by-uuid/wrong"
        )
        assert _cryptsetup_service_mismatches(mc, _encrypted_mount())

    def test_no_encryption_no_mismatch(self) -> None:
        assert not _cryptsetup_service_mismatches(
            _base_mount_caps(), _unencrypted_mount()
        )


class TestVolumeStatusFromDiagnostics:
    def test_mount_errors_propagated(self) -> None:
        """VolumeStatus.from_diagnostics should detect mount errors."""
        caps = _base_caps(has_mount_unit_config=False)
        diag = VolumeDiagnostics(capabilities=caps)
        ssh_ep = _active_ssh_endpoint_status()
        status = VolumeStatus.from_diagnostics(
            "encrypted", _encrypted_vol(), ssh_ep, diag
        )
        assert VolumeError.MOUNT_UNIT_NOT_CONFIGURED in status.errors
        assert not status.active

    def test_no_mount_config_no_mount_errors(self) -> None:
        """Volumes without mount config should not have mount errors."""
        caps = VolumeCapabilities(
            sentinel_exists=True,
            is_btrfs_filesystem=False,
            hardlink_supported=True,
            btrfs_user_subvol_rm=False,
            mount=_base_mount_caps(has_mount_unit_config=False),
        )
        vol = LocalVolume(slug="plain", path="/mnt/plain")
        diag = VolumeDiagnostics(capabilities=caps)
        ssh_ep = _active_ssh_endpoint_status()
        status = VolumeStatus.from_diagnostics("plain", vol, ssh_ep, diag)
        # Volume has no mount config, so no mount errors despite mount caps
        assert not status.errors

    def test_all_checks_pass_active(self) -> None:
        """Volume with all mount checks passing should be active."""
        caps = _base_caps()
        diag = VolumeDiagnostics(capabilities=caps)
        ssh_ep = _active_ssh_endpoint_status()
        status = VolumeStatus.from_diagnostics(
            "encrypted", _encrypted_vol(), ssh_ep, diag
        )
        assert status.active


# ── Direct backend errors ───────────────────────────────────


_DIRECT_DEFAULTS = dict(
    resolved_backend="direct",
    has_sudoers_rules=True,
)


def _direct_mount_caps(**overrides: object) -> MountCapabilities:
    return MountCapabilities(**{**_DIRECT_DEFAULTS, **overrides})


class TestDirectMountErrors:
    def test_all_caps_present_no_errors(self) -> None:
        errors = _mount_errors(_direct_mount_caps(), _encrypted_mount())
        assert errors == []

    def test_sudoers_rules_missing(self) -> None:
        errors = _mount_errors(
            _direct_mount_caps(has_sudoers_rules=False), _encrypted_mount()
        )
        assert VolumeError.SUDOERS_RULES_MISSING in errors

    def test_unencrypted_skips_encryption_checks(self) -> None:
        mc = _direct_mount_caps(has_sudoers_rules=None)
        errors = _mount_errors(mc, _unencrypted_mount())
        assert VolumeError.SUDOERS_RULES_MISSING not in errors

    def test_no_systemd_errors_for_direct(self) -> None:
        """Direct backend should never produce systemd-specific errors."""
        mc = _direct_mount_caps()
        errors = _mount_errors(mc, _encrypted_mount())
        systemd_errors = {
            VolumeError.MOUNT_UNIT_NOT_CONFIGURED,
            VolumeError.MOUNT_UNIT_MISMATCH,
            VolumeError.CRYPTSETUP_SERVICE_NOT_CONFIGURED,
            VolumeError.CRYPTSETUP_SERVICE_MISMATCH,
            VolumeError.POLKIT_RULES_MISSING,
        }
        assert not systemd_errors.intersection(errors)


# ── Runtime mount state fields ─────────────────────────────


class TestMountCapabilitiesRuntimeState:
    """New runtime state fields on MountCapabilities default to None."""

    def test_defaults_to_none(self) -> None:
        mc = MountCapabilities(resolved_backend="systemd")
        assert mc.device_present is None
        assert mc.luks_attached is None
        assert mc.mounted is None

    def test_explicit_values(self) -> None:
        mc = MountCapabilities(
            resolved_backend="systemd",
            device_present=True,
            luks_attached=True,
            mounted=False,
        )
        assert mc.device_present is True
        assert mc.luks_attached is True
        assert mc.mounted is False


# ── format_mount_status ───────────────────────────────────


class TestFormatMountStatus:
    def test_none_caps_returns_empty(self) -> None:
        assert format_mount_status(None, _encrypted_mount()) == Text("")

    def test_none_config_returns_empty(self) -> None:
        mc = _base_mount_caps(device_present=True, mounted=True)
        assert format_mount_status(mc, None) == Text("")

    def test_encrypted_all_true(self) -> None:
        mc = _base_mount_caps(device_present=True, luks_attached=True, mounted=True)
        result = format_mount_status(mc, _encrypted_mount())
        assert "\u2713device" in result
        assert "\u2713luks" in result
        assert "\u2713mounted" in result

    def test_encrypted_all_false(self) -> None:
        mc = _base_mount_caps(device_present=False, luks_attached=False, mounted=False)
        result = format_mount_status(mc, _encrypted_mount())
        assert "\u2717device" in result
        assert "\u2717luks" in result
        assert "\u2717mounted" in result

    def test_unencrypted_no_luks_column(self) -> None:
        mc = _base_mount_caps(device_present=True, mounted=True)
        result = format_mount_status(mc, _unencrypted_mount())
        assert "luks" not in result
        assert "\u2713device" in result
        assert "\u2713mounted" in result

    def test_not_probed_items_omitted(self) -> None:
        mc = _base_mount_caps(device_present=None, mounted=None)
        result = format_mount_status(mc, _unencrypted_mount())
        assert "device" not in result
        assert "mounted" not in result


# ── Observation reuse ─────────────────────────────────────


class TestObservationReuse:
    """Verify that mount observation values bypass runtime detection probes."""

    @patch("nbkp.disks.mount_checks.detect_device_present")
    @patch("nbkp.disks.mount_checks.detect_luks_attached")
    @patch("nbkp.disks.mount_checks.resolve_mount_unit")
    @patch("nbkp.disks.mount_checks.detect_systemd_cryptsetup_path")
    @patch("nbkp.disks.mount_checks._check_command_available", return_value=True)
    @patch("nbkp.disks.mount_checks._check_file_exists", return_value=True)
    @patch("nbkp.disks.mount_checks._check_systemctl_cat", return_value=True)
    @patch("nbkp.disks.mount_checks._run_systemctl_show", return_value={})
    def test_systemd_observation_skips_runtime_probes(
        self,
        _mock_show: object,
        _mock_cat: object,
        _mock_file: object,
        _mock_cmd: object,
        mock_cryptsetup_path: object,
        mock_mount_unit: object,
        mock_luks: object,
        mock_device: object,
    ) -> None:
        """When observation is provided, runtime detection functions are not called."""
        from nbkp.disks.mount_checks import _check_systemd_mount_capabilities

        obs = MountObservation(
            resolved_backend="systemd",
            mount_unit="mnt-encrypted.mount",
            systemd_cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
            device_present=True,
            luks_attached=True,
            mounted=True,
        )

        mount_tools = MountToolCapabilities(
            has_systemctl=True,
            has_systemd_escape=True,
            has_systemd_cryptsetup=True,
            systemd_cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
            has_sudo=True,
            has_cryptsetup=True,
        )

        result = _check_systemd_mount_capabilities(
            _encrypted_vol(), _encrypted_mount(), mount_tools, {}, obs
        )

        # Runtime probes should not have been called
        mock_device.assert_not_called()  # type: ignore[union-attr]
        mock_luks.assert_not_called()  # type: ignore[union-attr]
        mock_mount_unit.assert_not_called()  # type: ignore[union-attr]
        mock_cryptsetup_path.assert_not_called()  # type: ignore[union-attr]

        # Values come from observation
        assert result.device_present is True
        assert result.luks_attached is True
        assert result.mounted is True
        assert result.mount_unit == "mnt-encrypted.mount"

    @patch("nbkp.disks.mount_checks.detect_device_present")
    @patch("nbkp.disks.mount_checks.detect_luks_attached")
    @patch("nbkp.disks.mount_checks.run_on_volume")
    @patch("nbkp.disks.mount_checks._check_command_available", return_value=True)
    @patch("nbkp.disks.mount_checks._check_file_exists", return_value=True)
    def test_direct_observation_skips_runtime_probes(
        self,
        _mock_file: object,
        _mock_cmd: object,
        mock_run: object,
        mock_luks: object,
        mock_device: object,
    ) -> None:
        """When observation is provided for direct backend, runtime probes are skipped."""
        from nbkp.disks.mount_checks import _check_direct_mount_capabilities

        obs = MountObservation(
            resolved_backend="direct",
            device_present=False,
            luks_attached=None,
            mounted=None,
        )

        mount_tools = MountToolCapabilities(
            has_mount_cmd=True,
            has_umount_cmd=True,
            has_mountpoint=True,
            has_sudo=True,
            has_cryptsetup=True,
        )

        result = _check_direct_mount_capabilities(
            _unencrypted_vol(), _unencrypted_mount(), mount_tools, {}, obs
        )

        mock_device.assert_not_called()  # type: ignore[union-attr]
        mock_luks.assert_not_called()  # type: ignore[union-attr]
        mock_run.assert_not_called()  # type: ignore[union-attr]

        assert result.device_present is False
        assert result.mounted is None

    @patch("nbkp.disks.mount_checks._check_command_available", return_value=True)
    @patch("nbkp.disks.mount_checks._check_file_exists", return_value=True)
    @patch("nbkp.disks.mount_checks._check_systemctl_cat", return_value=True)
    @patch("nbkp.disks.mount_checks._run_systemctl_show", return_value={})
    def test_observation_decides_backend(
        self,
        _mock_show: object,
        _mock_cat: object,
        _mock_file: object,
        mock_cmd: object,
    ) -> None:
        """Observation's resolved_backend is used instead of probing systemctl."""
        from nbkp.disks.mount_checks import (
            check_mount_capabilities as _check_mount_capabilities,
        )

        obs = MountObservation(
            resolved_backend="systemd",
            mount_unit="mnt-encrypted.mount",
            device_present=True,
            mounted=True,
        )

        mount_tools = MountToolCapabilities(
            has_systemctl=True,
            has_systemd_escape=True,
            has_sudo=True,
            has_cryptsetup=True,
            has_systemd_cryptsetup=True,
            systemd_cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
        )

        result = _check_mount_capabilities(
            _encrypted_vol(), _encrypted_mount(), mount_tools, {}, obs
        )

        # Should have resolved to systemd backend from observation
        assert result.resolved_backend == "systemd"
