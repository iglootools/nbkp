"""Tests for mount-related preflight checks."""

from __future__ import annotations

from nbkp.config import (
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.preflight.status import (
    MountCapabilities,
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
    has_systemctl=True,
    has_systemd_escape=True,
    has_sudo=True,
    has_cryptsetup=True,
    has_systemd_cryptsetup=True,
    systemd_cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
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
        has_rsync=True,
        rsync_version_ok=True,
        has_btrfs=False,
        has_stat=True,
        has_findmnt=True,
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


class TestMountErrors:
    def test_all_caps_present_no_errors(self) -> None:
        errors = _mount_errors(_base_mount_caps(), _encrypted_mount())
        assert errors == []

    def test_systemctl_not_found(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(has_systemctl=False), _encrypted_mount()
        )
        assert VolumeError.SYSTEMCTL_NOT_FOUND in errors

    def test_systemd_escape_not_found(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(has_systemd_escape=False), _encrypted_mount()
        )
        assert VolumeError.SYSTEMD_ESCAPE_NOT_FOUND in errors

    def test_sudo_not_found(self) -> None:
        errors = _mount_errors(_base_mount_caps(has_sudo=False), _encrypted_mount())
        assert VolumeError.SUDO_NOT_FOUND in errors

    def test_sudo_not_checked_unencrypted(self) -> None:
        """Unencrypted volumes should not trigger sudo check."""
        mc = _base_mount_caps(
            has_sudo=None,
            has_cryptsetup=None,
            has_systemd_cryptsetup=None,
            has_cryptsetup_service_config=None,
            has_sudoers_rules=None,
            mount_unit_what="/dev/disk/by-uuid/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        errors = _mount_errors(mc, _unencrypted_mount())
        assert VolumeError.SUDO_NOT_FOUND not in errors

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
                # Disable encryption-only checks
                has_sudo=None,
                has_cryptsetup=None,
                has_systemd_cryptsetup=None,
                has_cryptsetup_service_config=None,
                has_sudoers_rules=None,
            ),
            _unencrypted_mount(),
        )
        assert VolumeError.MOUNT_UNIT_MISMATCH in errors

    def test_cryptsetup_not_found(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(has_cryptsetup=False), _encrypted_mount()
        )
        assert VolumeError.CRYPTSETUP_NOT_FOUND in errors

    def test_systemd_cryptsetup_not_found(self) -> None:
        errors = _mount_errors(
            _base_mount_caps(has_systemd_cryptsetup=False), _encrypted_mount()
        )
        assert VolumeError.SYSTEMD_CRYPTSETUP_NOT_FOUND in errors

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
            has_sudo=None,
            has_cryptsetup=None,
            has_systemd_cryptsetup=None,
            has_cryptsetup_service_config=None,
            has_sudoers_rules=None,
            mount_unit_what="/dev/disk/by-uuid/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        errors = _mount_errors(mc, _unencrypted_mount())
        assert errors == []

    def test_none_caps_not_probed(self) -> None:
        """When capability is None (not probed), no error should be generated."""
        mc = _base_mount_caps(
            has_systemctl=None,
            has_systemd_escape=None,
            has_mount_unit_config=None,
            has_polkit_rules=None,
        )
        errors = _mount_errors(mc, _encrypted_mount())
        assert VolumeError.SYSTEMCTL_NOT_FOUND not in errors
        assert VolumeError.SYSTEMD_ESCAPE_NOT_FOUND not in errors
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
        caps = _base_caps(has_systemctl=False)
        diag = VolumeDiagnostics(capabilities=caps)
        status = VolumeStatus.from_diagnostics("encrypted", _encrypted_vol(), diag)
        assert VolumeError.SYSTEMCTL_NOT_FOUND in status.errors
        assert not status.active

    def test_no_mount_config_no_mount_errors(self) -> None:
        """Volumes without mount config should not have mount errors."""
        caps = _base_caps(has_systemctl=False)
        vol = LocalVolume(slug="plain", path="/mnt/plain")
        diag = VolumeDiagnostics(capabilities=caps)
        status = VolumeStatus.from_diagnostics("plain", vol, diag)
        assert VolumeError.SYSTEMCTL_NOT_FOUND not in status.errors

    def test_all_checks_pass_active(self) -> None:
        """Volume with all mount checks passing should be active."""
        caps = _base_caps()
        diag = VolumeDiagnostics(capabilities=caps)
        status = VolumeStatus.from_diagnostics("encrypted", _encrypted_vol(), diag)
        assert status.active


# ── Direct backend errors ───────────────────────────────────


_DIRECT_DEFAULTS = dict(
    resolved_backend="direct",
    has_sudo=True,
    has_mount_cmd=True,
    has_umount_cmd=True,
    has_mountpoint=True,
    has_cryptsetup=True,
    has_sudoers_rules=True,
)


def _direct_mount_caps(**overrides: object) -> MountCapabilities:
    return MountCapabilities(**{**_DIRECT_DEFAULTS, **overrides})


class TestDirectMountErrors:
    def test_all_caps_present_no_errors(self) -> None:
        errors = _mount_errors(_direct_mount_caps(), _encrypted_mount())
        assert errors == []

    def test_sudo_not_found(self) -> None:
        errors = _mount_errors(_direct_mount_caps(has_sudo=False), _encrypted_mount())
        assert VolumeError.SUDO_NOT_FOUND in errors

    def test_mount_cmd_not_found(self) -> None:
        errors = _mount_errors(
            _direct_mount_caps(has_mount_cmd=False), _encrypted_mount()
        )
        assert VolumeError.MOUNT_CMD_NOT_FOUND in errors

    def test_umount_cmd_not_found(self) -> None:
        errors = _mount_errors(
            _direct_mount_caps(has_umount_cmd=False), _encrypted_mount()
        )
        assert VolumeError.UMOUNT_CMD_NOT_FOUND in errors

    def test_mountpoint_not_found(self) -> None:
        errors = _mount_errors(
            _direct_mount_caps(has_mountpoint=False), _encrypted_mount()
        )
        assert VolumeError.MOUNTPOINT_CMD_NOT_FOUND in errors

    def test_cryptsetup_not_found(self) -> None:
        errors = _mount_errors(
            _direct_mount_caps(has_cryptsetup=False), _encrypted_mount()
        )
        assert VolumeError.CRYPTSETUP_NOT_FOUND in errors

    def test_unencrypted_skips_encryption_checks(self) -> None:
        mc = _direct_mount_caps(has_cryptsetup=None, has_sudoers_rules=None)
        errors = _mount_errors(mc, _unencrypted_mount())
        assert VolumeError.CRYPTSETUP_NOT_FOUND not in errors
        assert VolumeError.SUDOERS_RULES_MISSING not in errors

    def test_no_systemd_errors_for_direct(self) -> None:
        """Direct backend should never produce systemd-specific errors."""
        mc = _direct_mount_caps()
        errors = _mount_errors(mc, _encrypted_mount())
        systemd_errors = {
            VolumeError.SYSTEMCTL_NOT_FOUND,
            VolumeError.SYSTEMD_ESCAPE_NOT_FOUND,
            VolumeError.MOUNT_UNIT_NOT_CONFIGURED,
            VolumeError.MOUNT_UNIT_MISMATCH,
            VolumeError.SYSTEMD_CRYPTSETUP_NOT_FOUND,
            VolumeError.CRYPTSETUP_SERVICE_NOT_CONFIGURED,
            VolumeError.CRYPTSETUP_SERVICE_MISMATCH,
            VolumeError.POLKIT_RULES_MISSING,
        }
        assert not systemd_errors.intersection(errors)
