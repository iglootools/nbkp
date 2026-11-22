"""Tests for nbkp.disks.mount_checks: the fstab and runtime mount probes."""

import pytest

from nbkp.config import (
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.disks import MountCapabilities, MountToolCapabilities
from nbkp.disks.mount_checks import (
    check_mount_capabilities,
    check_mount_status,
    fstab_source_matches,
)
from nbkp.disks.observation import MountObservation
from nbkp.preflight.status.ssh import SshEndpointDiagnostics, SshEndpointStatus
from nbkp.preflight.status.volume import (
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)
from tests.fakes import FakeCommandRunner, completed

_ENC_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"
_USB_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_MOUNT_TOOLS = MountToolCapabilities(
    has_udisksctl=True,
    udisksd_running=True,
    has_findmnt=True,
    has_lsblk=True,
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
        slug="encrypted",
        path="/mnt/encrypted",
        mount=_encrypted_mount(),
    )


def _unencrypted_vol() -> LocalVolume:
    return LocalVolume(slug="usb", path="/mnt/usb", mount=_unencrypted_mount())


def _fstab_line(source: str, target: str) -> FakeCommandRunner:
    """Script a host whose ``/etc/fstab`` maps *source* to *target*."""
    return FakeCommandRunner().on(
        "findmnt",
        "--fstab",
        returns=completed(0, f'SOURCE="{source}" TARGET="{target}"\n'),
    )


def _active_ssh_endpoint_status() -> SshEndpointStatus:
    return SshEndpointStatus.from_diagnostics(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(),
    )


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


class TestObservationReuse:
    """Verify that mount observation values bypass runtime detection probes."""

    def test_observation_skips_runtime_probes(self) -> None:
        """When observation is provided, only the fstab entry is read."""
        run = _fstab_line("/dev/mapper/luks-x", "/mnt/encrypted")
        obs = MountObservation(
            device_present=True,
            luks_unlocked=True,
            mounted=True,
            cleartext_device="/dev/mapper/luks-x",
            effective_path="/mnt/encrypted",
        )
        result = check_mount_capabilities(
            _encrypted_vol(),
            _encrypted_mount(),
            _MOUNT_TOOLS,
            {},
            obs,
            run=run,
        )

        assert [cmd[:2] for cmd in run.commands] == [["findmnt", "--fstab"]]
        assert result.device_present is True
        assert result.luks_unlocked is True
        assert result.mounted is True
        assert result.cleartext_device == "/dev/mapper/luks-x"
        assert result.effective_path == "/mnt/encrypted"
        assert result.has_fstab_entry is True

    def test_probes_when_no_observation(self) -> None:
        """Without an observation, the runtime state is probed on the host."""
        run = (
            _fstab_line("/dev/mapper/luks-x", "/mnt/encrypted")
            .on("lsblk", returns=completed(0, "sdb part\nluks-x crypt\n"))
            .on("findmnt", "--source", returns=completed(0, "/mnt/encrypted\n"))
        )
        result = check_mount_capabilities(
            _encrypted_vol(),
            _encrypted_mount(),
            _MOUNT_TOOLS,
            {},
            None,
            run=run,
        )
        # lsblk runs once: the cleartext device is reused for the mountpoint.
        assert run.commands == [
            ["test", "-e", f"/dev/disk/by-uuid/{_ENC_UUID}"],
            ["lsblk", "-rno", "NAME,TYPE", f"/dev/disk/by-uuid/{_ENC_UUID}"],
            ["findmnt", "--source", "/dev/mapper/luks-x", "-n", "-o", "TARGET"],
            [
                "findmnt",
                "--fstab",
                "--target",
                "/mnt/encrypted",
                "-n",
                "-P",
                "-o",
                "SOURCE,TARGET",
            ],
        ]
        assert result.device_present is True
        assert result.luks_unlocked is True
        assert result.mounted is True
        assert result.cleartext_device == "/dev/mapper/luks-x"
        assert result.effective_path == "/mnt/encrypted"


class TestToolGating:
    """Probes for tools reported missing are skipped (state unknown)."""

    def test_missing_findmnt_and_lsblk(self) -> None:
        tools = MountToolCapabilities(
            has_udisksctl=True,
            udisksd_running=True,
            has_findmnt=False,
            has_lsblk=False,
        )
        run = FakeCommandRunner()
        result = check_mount_capabilities(
            _encrypted_vol(),
            _encrypted_mount(),
            tools,
            {},
            None,
            run=run,
        )
        # Only the device presence probe ran: no lsblk, no findmnt.
        assert run.commands == [["test", "-e", f"/dev/disk/by-uuid/{_ENC_UUID}"]]
        # Unknown, not False: no false FSTAB_MOUNTPOINT_MISMATCH / unlocked state.
        assert result.has_fstab_entry is None
        assert result.luks_unlocked is None
        assert result.mounted is None

    def test_failed_lsblk_is_unknown(self) -> None:
        run = FakeCommandRunner().on("lsblk", returns=completed(1, stderr="boom"))
        result = check_mount_capabilities(
            _encrypted_vol(),
            _encrypted_mount(),
            _MOUNT_TOOLS,
            {},
            None,
            run=run,
        )
        assert result.device_present is True
        assert result.luks_unlocked is None
        assert result.mounted is None

    def test_failed_findmnt_is_unknown(self) -> None:
        run = FakeCommandRunner().on(
            "findmnt",
            "--source",
            returns=completed(2, stderr="boom"),
        )
        result = check_mount_capabilities(
            _unencrypted_vol(),
            _unencrypted_mount(),
            _MOUNT_TOOLS,
            {},
            None,
            run=run,
        )
        assert result.mounted is None
        assert result.effective_path is None

    def test_check_mount_status_does_not_probe_tools(self) -> None:
        run = FakeCommandRunner().on("test", "-e", returns=completed(1))
        check_mount_status(_unencrypted_vol(), _unencrypted_mount(), {}, run=run)
        # Device and fstab probes only: no ``udisksctl status``, no udisks
        # btrfs module lookup (``sh -c``), no ``which``.
        assert [cmd[:2] for cmd in run.commands] == [
            ["test", "-e"],
            ["findmnt", "--fstab"],
        ]


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
    def test_unencrypted(self, source: str, *, expected: bool) -> None:
        assert fstab_source_matches(source, _unencrypted_mount(), None) is expected

    @pytest.mark.parametrize(
        ("source", "cleartext", "expected"),
        [
            ("/dev/mapper/seagate-luks", "/dev/mapper/seagate-luks", True),
            ("/dev/mapper/other", "/dev/mapper/seagate-luks", False),
            (f"/dev/mapper/luks-{_ENC_UUID}", None, True),
            ("/dev/mapper/other", None, False),
            # Right after an unlock udisks reports the kernel name, which
            # cannot be compared with a mapper name without a probe: accept.
            (f"/dev/mapper/luks-{_ENC_UUID}", "/dev/dm-0", True),
            ("/dev/mapper/seagate-luks", "/dev/dm-0", True),
            # The LUKS container itself cannot be mounted.
            (f"UUID={_ENC_UUID}", None, False),
            (f"/dev/disk/by-uuid/{_ENC_UUID}", None, False),
            # The inner filesystem's UUID is not in the config: accept.
            ("UUID=99999999-8888-7777-6666-555555555555", None, True),
        ],
    )
    def test_encrypted(
        self,
        source: str,
        cleartext: str | None,
        *,
        expected: bool,
    ) -> None:
        assert fstab_source_matches(source, _encrypted_mount(), cleartext) is expected

    def test_entry_for_another_device_is_a_mismatch(self) -> None:
        run = _fstab_line("UUID=11111111-2222-3333-4444-555555555555", "/mnt/usb").on(
            "findmnt",
            "--source",
            returns=completed(1),
        )
        result = check_mount_capabilities(
            _unencrypted_vol(),
            _unencrypted_mount(),
            _MOUNT_TOOLS,
            {},
            None,
            run=run,
        )
        assert result.has_fstab_entry is False
        assert result.fstab_source == "UUID=11111111-2222-3333-4444-555555555555"
        vol = _unencrypted_vol()
        status = VolumeStatus.from_diagnostics(
            vol.slug,
            vol,
            _active_ssh_endpoint_status(),
            VolumeDiagnostics(
                capabilities=VolumeCapabilities(
                    sentinel_exists=False,
                    is_btrfs_filesystem=False,
                    hardlink_supported=True,
                    btrfs_user_subvol_rm=False,
                    mount=result,
                ),
            ),
        )
        assert status.errors == [VolumeErrorKind.FSTAB_MOUNTPOINT_MISMATCH]

    def test_parses_findmnt_pairs(self) -> None:
        run = _fstab_line(f"UUID={_USB_UUID}", "/mnt/my usb")
        vol = LocalVolume(slug="usb", path="/mnt/my usb", mount=_unencrypted_mount())
        result = check_mount_capabilities(
            vol,
            _unencrypted_mount(),
            _MOUNT_TOOLS,
            {},
            MountObservation(device_present=True, mounted=False),
            run=run,
        )
        assert result.fstab_source == f"UUID={_USB_UUID}"
        assert result.fstab_target == "/mnt/my usb"
        assert run.commands == [
            [
                "findmnt",
                "--fstab",
                "--target",
                "/mnt/my usb",
                "-n",
                "-P",
                "-o",
                "SOURCE,TARGET",
            ],
        ]
