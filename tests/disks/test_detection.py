import pytest

from nbkp.config import (
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.disks.detection import (
    DeviceProbeError,
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
    resolve_effective_path,
    resolve_target_device,
    settle_udev,
)
from tests.fakes import FakeCommandRunner, completed

_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"
_FINDMNT = ["findmnt", "--source", f"/dev/disk/by-uuid/{_UUID}", "-n", "-o", "TARGET"]


def _local_vol(path: str | None = "/mnt/disk") -> LocalVolume:
    if path is None:
        return LocalVolume(
            slug="disk",
            mount=MountConfig(device_uuid=_UUID),
        )
    return LocalVolume(slug="disk", path=path)


def _mount_config(*, encrypted: bool) -> MountConfig:
    if encrypted:
        return MountConfig(
            device_uuid=_UUID,
            encryption=LuksEncryptionConfig(passphrase_id="disk"),
        )
    return MountConfig(device_uuid=_UUID)


def _lsblk(stdout: str) -> FakeCommandRunner:
    return FakeCommandRunner().on("lsblk", returns=completed(0, stdout))


class TestSettleUdev:
    def test_runs_udevadm_settle_with_a_timeout(self) -> None:
        run = FakeCommandRunner()
        settle_udev(_local_vol(), {}, run=run)
        [cmd] = run.commands
        assert cmd[:2] == ["udevadm", "settle"]
        assert any(arg.startswith("--timeout=") for arg in cmd)

    def test_timeout_or_refusal_is_ignored(self) -> None:
        run = FakeCommandRunner().on("udevadm", returns=completed(1))
        settle_udev(_local_vol(), {}, run=run)

    def test_missing_udevadm_is_ignored(self) -> None:
        run = FakeCommandRunner().on("udevadm", returns=FileNotFoundError("udevadm"))
        settle_udev(_local_vol(), {}, run=run)


class TestDetectDevicePresent:
    def test_present(self) -> None:
        run = FakeCommandRunner().on("test", returns=completed(0))
        assert detect_device_present(_local_vol(), _UUID, {}, run=run)

    def test_not_present(self) -> None:
        run = FakeCommandRunner().on("test", returns=completed(1))
        assert not detect_device_present(_local_vol(), _UUID, {}, run=run)

    def test_passes_correct_command(self) -> None:
        run = FakeCommandRunner()
        detect_device_present(_local_vol(), "aaaa-bbbb-cccc-dddd", {}, run=run)
        assert run.commands == [["test", "-e", "/dev/disk/by-uuid/aaaa-bbbb-cccc-dddd"]]


class TestDiscoverCleartextDevice:
    def test_passes_lsblk_command(self) -> None:
        run = _lsblk(f"luks-{_UUID} crypt\n")
        discover_cleartext_device(_local_vol(), _UUID, {}, run=run)
        assert run.commands == [
            ["lsblk", "-rno", "NAME,TYPE", f"/dev/disk/by-uuid/{_UUID}"],
        ]

    def test_returns_mapper_for_crypt_child(self) -> None:
        # lsblk lists the parent (the LUKS container) and its crypt child.
        run = _lsblk(f"sdb part\nluks-{_UUID} crypt\n")
        device = discover_cleartext_device(_local_vol(), _UUID, {}, run=run)
        assert device == f"/dev/mapper/luks-{_UUID}"

    def test_locked_returns_none(self) -> None:
        # No crypt child: container is still locked.
        run = _lsblk("sdb disk\nsdb1 part\n")
        assert discover_cleartext_device(_local_vol(), _UUID, {}, run=run) is None

    def test_absent_device_returns_none(self) -> None:
        # lsblk exits 32 when none of the requested devices exists.
        run = FakeCommandRunner().on("lsblk", returns=completed(32))
        assert discover_cleartext_device(_local_vol(), _UUID, {}, run=run) is None

    def test_lsblk_failure_raises(self) -> None:
        # A failed probe is not "locked": callers must not act on a guess.
        run = FakeCommandRunner().on(
            "lsblk",
            returns=completed(127, stderr="lsblk: command not found"),
        )
        with pytest.raises(DeviceProbeError) as exc_info:
            discover_cleartext_device(_local_vol(), _UUID, {}, run=run)
        assert exc_info.value.returncode == 127


class TestResolveTargetDevice:
    def test_unencrypted_is_by_uuid(self) -> None:
        run = FakeCommandRunner()
        device = resolve_target_device(
            _local_vol(),
            _mount_config(encrypted=False),
            {},
            run=run,
        )
        assert device == f"/dev/disk/by-uuid/{_UUID}"
        assert run.commands == []

    def test_encrypted_discovers_cleartext(self) -> None:
        run = _lsblk(f"sdb part\nluks-{_UUID} crypt\n")
        device = resolve_target_device(
            _local_vol(),
            _mount_config(encrypted=True),
            {},
            run=run,
        )
        assert device == f"/dev/mapper/luks-{_UUID}"

    def test_encrypted_locked_returns_none(self) -> None:
        run = _lsblk("sdb part\n")
        assert (
            resolve_target_device(
                _local_vol(),
                _mount_config(encrypted=True),
                {},
                run=run,
            )
            is None
        )


class TestFindMountpoint:
    def test_passes_findmnt_command(self) -> None:
        run = FakeCommandRunner().on("findmnt", returns=completed(0, "/mnt/disk\n"))
        find_mountpoint(_local_vol(), "/dev/mapper/x", {}, run=run)
        assert run.commands == [
            ["findmnt", "--source", "/dev/mapper/x", "-n", "-o", "TARGET"],
        ]

    def test_mounted_returns_target(self) -> None:
        run = FakeCommandRunner().on("findmnt", returns=completed(0, "/mnt/disk\n"))
        assert find_mountpoint(_local_vol(), "/dev/mapper/x", {}, run=run) == (
            "/mnt/disk"
        )

    def test_unmounted_returns_none(self) -> None:
        run = FakeCommandRunner().on("findmnt", returns=completed(1))
        assert find_mountpoint(_local_vol(), "/dev/mapper/x", {}, run=run) is None

    def test_empty_output_returns_none(self) -> None:
        run = FakeCommandRunner().on("findmnt", returns=completed(0, "\n"))
        assert find_mountpoint(_local_vol(), "/dev/mapper/x", {}, run=run) is None

    @pytest.mark.parametrize(
        ("returncode", "stderr"),
        [
            (127, "findmnt: command not found"),
            (1, "findmnt: cannot open /proc/self/mountinfo"),
        ],
    )
    def test_findmnt_failure_raises(self, returncode: int, stderr: str) -> None:
        """A failed findmnt is not "not mounted": umount would skip the unmount."""
        run = FakeCommandRunner().on(
            "findmnt",
            returns=completed(returncode, stderr=stderr),
        )
        with pytest.raises(DeviceProbeError) as exc_info:
            find_mountpoint(_local_vol(), "/dev/mapper/x", {}, run=run)
        assert (exc_info.value.command, exc_info.value.returncode) == (
            "findmnt",
            returncode,
        )


class TestResolveEffectivePath:
    def test_declared_path_is_authoritative(self) -> None:
        # Option A: volume.path declared — returned directly, no probing.
        run = FakeCommandRunner()
        path = resolve_effective_path(
            _local_vol("/mnt/disk"),
            _mount_config(encrypted=False),
            {},
            run=run,
        )
        assert path == "/mnt/disk"
        assert run.commands == []

    def test_discovered_path_when_omitted(self) -> None:
        # Option B: no volume.path — discover via findmnt.
        run = FakeCommandRunner().on(
            "findmnt",
            returns=completed(0, "/run/media/backup/label\n"),
        )
        path = resolve_effective_path(
            _local_vol(None),
            _mount_config(encrypted=False),
            {},
            run=run,
        )
        assert path == "/run/media/backup/label"
        assert run.commands == [_FINDMNT]

    def test_omitted_path_device_locked_returns_none(self) -> None:
        run = _lsblk("sdb part\n")
        assert (
            resolve_effective_path(
                _local_vol(None),
                _mount_config(encrypted=True),
                {},
                run=run,
            )
            is None
        )
        # No cleartext device: nothing to look up a mountpoint for.
        assert run.commands_starting_with("findmnt") == []

    def test_omitted_path_unmounted_returns_none(self) -> None:
        run = FakeCommandRunner().on("findmnt", returns=completed(1))
        assert (
            resolve_effective_path(
                _local_vol(None),
                _mount_config(encrypted=False),
                {},
                run=run,
            )
            is None
        )
