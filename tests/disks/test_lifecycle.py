"""
Tests for nbkp.disks.lifecycle (udisks2 backend).

The lifecycle runs every probe (``test -e``, ``lsblk``, ``findmnt``) and every
``udisksctl`` step through the injected command runner, so each test scripts
the host's answers on a :class:`FakeCommandRunner` and checks the result and
the commands that reached the host.  Unscripted commands succeed with empty
output: the device is present, the LUKS container locked, nothing mounted.
"""

import subprocess
from collections.abc import Callable

import paramiko
import pytest

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from nbkp.credentials import CredentialError, CredentialErrorKind
from nbkp.disks.lifecycle import (
    MountFailureKind,
    MountResult,
    UmountResult,
    mount_count,
    mount_volume,
    mount_volumes,
    run_udisks,
    umount_volume,
    umount_volumes,
)
from nbkp.disks.models import (
    LifecycleFailure,
    LifecycleStep,
    MountCapabilities,
    UmountFailureKind,
)
from nbkp.disks.plan import plan_lifecycle
from nbkp.remote.endpoints import EndpointFilter, ResolvedEndpoints
from nbkp.remote.resolution import resolve_all_endpoints
from tests.fakes import FakeCommandRunner, completed

_ENC_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"
_USB_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CLEARTEXT = f"/dev/mapper/luks-{_ENC_UUID}"

# ``lsblk`` answers for the LUKS container: no crypt child while it is locked.
_LOCKED = completed(0, "sdb part\n")
_UNLOCKED = completed(0, f"sdb part\nluks-{_ENC_UUID} crypt\n")
# ``findmnt --source`` exits 1, silently, when the device is not mounted.
_NOT_MOUNTED = completed(1)


def _mounted_at(path: str) -> subprocess.CompletedProcess[str]:
    return completed(0, f"{path}\n")


def _encrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="encrypted",
        path="/mnt/encrypted",
        mount=MountConfig(
            device_uuid=_ENC_UUID,
            encryption=LuksEncryptionConfig(passphrase_id="encrypted"),
        ),
    )


def _unencrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="usb",
        path="/mnt/usb",
        mount=MountConfig(device_uuid=_USB_UUID),
    )


def _mount(
    vol: LocalVolume,
    run: FakeCommandRunner,
    passphrase: str = "secret",
) -> MountResult:
    assert vol.mount is not None
    return mount_volume(
        vol,
        vol.mount,
        {},
        lambda _: passphrase,
        run=run,
        sleep=lambda _: None,
    )


def _udisks_steps(run: FakeCommandRunner) -> list[str]:
    """Return the ``udisksctl`` subcommands that reached the host, in order."""
    return [cmd[1] for cmd in run.commands_starting_with("udisksctl")]


_LOOKUP_ERROR = "Error looking up object for device /dev/disk/by-uuid/" + _ENC_UUID


class TestRunUdisks:
    def test_retries_while_udisks_has_no_object_for_the_device(self) -> None:
        sleeps: list[float] = []
        run = FakeCommandRunner().on(
            "udisksctl",
            returns=[
                completed(1, stderr=_LOOKUP_ERROR),
                completed(1, stderr=_LOOKUP_ERROR),
                completed(0, "Unlocked"),
            ],
        )
        result = run_udisks(
            ["udisksctl", "unlock"],
            _encrypted_vol(),
            {},
            stdin="secret",
            sleep=sleeps.append,
            run=run,
        )
        assert result.returncode == 0
        attempts = [c for c in run.calls if c.cmd[0] == "udisksctl"]
        assert len(attempts) == 3
        assert all(c.stdin == "secret" for c in attempts)
        assert len(run.commands_starting_with("udevadm", "settle")) == 2
        assert len(sleeps) == 2

    def test_gives_up_after_bounded_attempts(self) -> None:
        sleeps: list[float] = []
        run = FakeCommandRunner().on(
            "udisksctl",
            returns=completed(1, stderr=_LOOKUP_ERROR),
        )
        result = run_udisks(
            ["udisksctl", "unlock"],
            _encrypted_vol(),
            {},
            sleep=sleeps.append,
            run=run,
        )
        attempts = len(run.commands_starting_with("udisksctl"))
        assert result.returncode == 1
        assert attempts == len(sleeps) + 1
        assert 1 < attempts <= 20
        assert result.stderr.startswith(_LOOKUP_ERROR)
        assert f"after {attempts} attempts over {sum(sleeps):g} s" in result.stderr

    def test_wrong_passphrase_is_not_retried(self) -> None:
        sleeps: list[float] = []
        run = FakeCommandRunner().on(
            "udisksctl",
            returns=completed(
                1,
                stderr="Error unlocking /dev/sda1: Failed to activate device",
            ),
        )
        run_udisks(
            ["udisksctl", "unlock"],
            _encrypted_vol(),
            {},
            sleep=sleeps.append,
            run=run,
        )
        assert len(run.commands_starting_with("udisksctl")) == 1
        assert sleeps == []


class TestMountVolume:
    def test_settles_udev_before_looking_for_the_device(self) -> None:
        """Regression: a check right after a lock found the by-uuid link missing."""
        run = FakeCommandRunner().on("test", "-e", returns=completed(1))
        _mount(_unencrypted_vol(), run)
        assert [cmd[0] for cmd in run.commands] == ["udevadm", "test"]

    def test_device_not_present(self) -> None:
        run = FakeCommandRunner().on("test", "-e", returns=completed(1))
        result = _mount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == MountFailureKind.DEVICE_NOT_PRESENT
        assert result.device_present is False
        assert result.failure == LifecycleFailure(device_uuid=_ENC_UUID)

    def test_unencrypted_mount(self) -> None:
        # findmnt: not mounted before the mount, mounted after it.
        run = FakeCommandRunner().on(
            "findmnt",
            returns=[_NOT_MOUNTED, _mounted_at("/mnt/usb")],
        )
        result = _mount(_unencrypted_vol(), run)
        assert result.success
        assert result.mounted is True
        assert result.luks_unlocked is None
        assert result.effective_path == "/mnt/usb"
        assert run.commands_starting_with("udisksctl") == [
            [
                "udisksctl",
                "mount",
                "-b",
                f"/dev/disk/by-uuid/{_USB_UUID}",
                "--no-user-interaction",
            ],
        ]

    def test_already_mounted_is_noop(self) -> None:
        run = FakeCommandRunner().on("findmnt", returns=_mounted_at("/mnt/usb"))
        result = _mount(_unencrypted_vol(), run)
        assert result.success
        assert result.mounted is True
        assert _udisks_steps(run) == []

    def test_encrypted_unlock_then_mount(self) -> None:
        # lsblk: locked first, unlocked once udisks unlocked it.
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=[_LOCKED, _UNLOCKED])
            .on("findmnt", returns=[_NOT_MOUNTED, _mounted_at("/mnt/encrypted")])
        )
        result = _mount(_encrypted_vol(), run, passphrase="secret")
        assert result.success
        assert result.luks_unlocked is True
        assert result.mounted is True
        assert result.cleartext_device == _CLEARTEXT
        assert result.effective_path == "/mnt/encrypted"
        # The unlock pipes the passphrase via stdin with NO trailing newline.
        unlock, mount = [c for c in run.calls if c.cmd[0] == "udisksctl"]
        assert unlock.cmd[:2] == ["udisksctl", "unlock"]
        assert unlock.stdin == "secret"
        assert mount.cmd[:4] == ["udisksctl", "mount", "-b", _CLEARTEXT]

    def test_unlock_reports_the_cleartext_device_itself(self) -> None:
        """The device udisks names on stdout is used without re-probing lsblk."""
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_LOCKED)
            .on(
                "udisksctl",
                "unlock",
                returns=completed(0, "Unlocked /dev/sdb1 as /dev/dm-0.\n"),
            )
            .on("findmnt", returns=[_NOT_MOUNTED, _mounted_at("/mnt/encrypted")])
        )
        result = _mount(_encrypted_vol(), run)
        assert result.success
        assert result.cleartext_device == "/dev/dm-0"
        assert len(run.commands_starting_with("lsblk")) == 1

    def test_encrypted_already_unlocked_skips_unlock(self) -> None:
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_UNLOCKED)
            .on("findmnt", returns=[_NOT_MOUNTED, _mounted_at("/mnt/encrypted")])
        )
        result = _mount(_encrypted_vol(), run)
        assert result.success
        assert _udisks_steps(run) == ["mount"]

    def test_unlock_failure(self) -> None:
        run = FakeCommandRunner().on(
            "udisksctl",
            "unlock",
            returns=completed(1, stderr="Error unlocking: device busy"),
        )
        result = _mount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == MountFailureKind.UNLOCK_FAILED
        assert result.luks_unlocked is False
        assert result.failure == LifecycleFailure(
            step=LifecycleStep.UNLOCK,
            exit_code=1,
            message="Error unlocking: device busy",
        )
        assert _udisks_steps(run) == ["unlock"]

    def test_unlocked_without_a_cleartext_device_is_an_unlock_failure(self) -> None:
        # udisks said nothing about the device, and lsblk still finds no
        # crypt child.
        run = FakeCommandRunner().on("lsblk", returns=_LOCKED)
        result = _mount(_encrypted_vol(), run)
        assert result.failure_reason == MountFailureKind.UNLOCK_FAILED
        assert result.failure == LifecycleFailure(step=LifecycleStep.UNLOCK)
        assert _udisks_steps(run) == ["unlock"]

    def test_unlock_polkit_denied_classified_as_not_authorized(self) -> None:
        """Polkit refusal under --no-user-interaction -> NOT_AUTHORIZED."""
        run = FakeCommandRunner().on(
            "udisksctl",
            "unlock",
            returns=completed(
                1,
                stderr="Error unlocking: Not Authorized to perform operation",
            ),
        )
        result = _mount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == MountFailureKind.NOT_AUTHORIZED
        assert result.failure is not None
        assert result.failure.step is LifecycleStep.UNLOCK

    def test_unlock_of_a_device_udisks_does_not_know_is_not_authorization(
        self,
    ) -> None:
        message = "Error looking up object for device /dev/disk/by-uuid/x"
        run = FakeCommandRunner().on(
            "udisksctl",
            "unlock",
            returns=completed(1, stderr=message),
        )
        result = _mount(_encrypted_vol(), run)
        assert result.failure_reason == MountFailureKind.UNLOCK_FAILED
        assert result.failure is not None
        assert (result.failure.step, result.failure.exit_code) == (
            LifecycleStep.UNLOCK,
            1,
        )
        assert (result.failure.message or "").startswith(
            f"{message} (still failing after ",
        )

    def test_mount_failure(self) -> None:
        run = FakeCommandRunner().on(
            "udisksctl",
            "mount",
            returns=completed(1, stderr="mount: unknown filesystem"),
        )
        result = _mount(_unencrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == MountFailureKind.MOUNT_FAILED
        assert result.mounted is False
        assert result.failure == LifecycleFailure(
            step=LifecycleStep.MOUNT,
            exit_code=1,
            message="mount: unknown filesystem",
        )

    @pytest.mark.parametrize(
        ("returncode", "stderr", "reason"),
        [
            (1, "Error mounting: Not Authorized", MountFailureKind.NOT_AUTHORIZED),
            (
                127,
                "udisksctl: command not found",
                MountFailureKind.UDISKS_NOT_AVAILABLE,
            ),
            # Verbatim dash output (`sh -c`, as on a Debian host) for a missing
            # binary, captured with exit code 127.
            (
                127,
                "sh: 1: udisksctl: not found",
                MountFailureKind.UDISKS_NOT_AVAILABLE,
            ),
            # Verbatim udisksctl 2.10.1 (Debian 13) output, exit code 1, with
            # the system D-Bus daemon stopped.
            (
                1,
                (
                    "Error connecting to the udisks daemon: Could not connect:"
                    " Connection refused"
                ),
                MountFailureKind.UDISKS_NOT_AVAILABLE,
            ),
        ],
        ids=["polkit-denied", "bash-missing", "dash-missing", "daemon-unreachable"],
    )
    def test_mount_failure_classification(
        self,
        returncode: int,
        stderr: str,
        reason: MountFailureKind,
    ) -> None:
        run = FakeCommandRunner().on(
            "udisksctl",
            "mount",
            returns=completed(returncode, stderr=stderr),
        )
        result = _mount(_unencrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == reason

    def test_udisks_unknown_device_is_a_mount_failure(self) -> None:
        """Udisks failing to find a device does not mean udisks is missing."""
        # Verbatim udisksctl 2.10.1 (Debian 13) output, exit code 1, for a
        # device udisks has no object for.
        stderr = f"Error looking up object for device /dev/disk/by-uuid/{_USB_UUID}"
        run = FakeCommandRunner().on(
            "udisksctl",
            "mount",
            returns=completed(1, stderr=stderr),
        )
        result = _mount(_unencrypted_vol(), run)
        assert result.failure_reason == MountFailureKind.MOUNT_FAILED

    def test_mount_volume_swallows_multiline_exception(self) -> None:
        multiline_msg = "first line\nsecond line\nthird line"
        run = FakeCommandRunner().on("test", "-e", returns=OSError(multiline_msg))
        result = _mount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == MountFailureKind.UNREACHABLE
        assert result.failure == LifecycleFailure(message="first line")

    def test_programming_error_propagates(self) -> None:
        """Only connection failures become UNREACHABLE; a bug must surface."""
        run = FakeCommandRunner().on("test", "-e", returns=RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            _mount(_encrypted_vol(), run)

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (
                paramiko.AuthenticationException("Authentication failed."),
                "Authentication failed.",
            ),
            (TimeoutError("timed out"), "timed out"),
            (ConnectionRefusedError("Connection refused"), "Connection refused"),
        ],
        ids=["auth-failure", "ssh-timeout", "connection-refused"],
    )
    def test_connection_failure_returns_unreachable(
        self,
        error: Exception,
        message: str,
    ) -> None:
        run = FakeCommandRunner().on("test", "-e", returns=error)
        result = _mount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == MountFailureKind.UNREACHABLE
        assert result.failure == LifecycleFailure(message=message)

    def test_missing_passphrase_on_plugged_in_drive(self) -> None:
        """
        A plugged-in drive whose passphrase cannot be retrieved fails loudly.

        Regression: the CredentialError used to be swallowed as UNREACHABLE,
        so the drive was silently skipped as "inactive".
        """
        vol = _encrypted_vol()
        assert vol.mount is not None

        def no_passphrase(pid: str) -> str:
            raise CredentialError(CredentialErrorKind.NOT_FOUND, passphrase_id=pid)

        run = FakeCommandRunner().on("lsblk", returns=_LOCKED)
        result = mount_volume(vol, vol.mount, {}, no_passphrase, run=run)
        assert not result.success
        assert result.failure_reason == MountFailureKind.PASSPHRASE_NOT_AVAILABLE
        assert result.device_present is True
        assert result.luks_unlocked is False
        assert _udisks_steps(run) == []

    def test_failed_lsblk_probe_is_not_locked(self) -> None:
        run = FakeCommandRunner().on("lsblk", returns=completed(1, stderr="boom"))
        result = _mount(_encrypted_vol(), run)
        assert result.failure_reason == MountFailureKind.PROBE_FAILED
        assert result.failure == LifecycleFailure(
            command="lsblk",
            exit_code=1,
            message="boom",
        )
        # No unlock attempted on a guess.
        assert _udisks_steps(run) == []

    def test_missing_local_tool_is_udisks_not_available(self) -> None:
        """Locally, FileNotFoundError is a missing tool, not an unreachable host."""
        run = FakeCommandRunner().on(
            "lsblk",
            returns=FileNotFoundError(2, "No such file", "lsblk"),
        )
        result = _mount(_encrypted_vol(), run)
        assert result.failure_reason == MountFailureKind.UDISKS_NOT_AVAILABLE
        assert result.failure == LifecycleFailure(command="lsblk")

    def test_missing_local_udevadm_fails_the_mount(self) -> None:
        """Settling is not skipped silently: without udevadm the race stays open."""
        run = FakeCommandRunner().on(
            "udevadm",
            returns=FileNotFoundError(2, "No such file", "udevadm"),
        )
        result = _mount(_encrypted_vol(), run)
        assert result.failure_reason == MountFailureKind.UDISKS_NOT_AVAILABLE
        assert result.failure == LifecycleFailure(command="udevadm")
        assert _udisks_steps(run) == []

    def test_remote_udevadm_not_found_is_a_probe_failure(self) -> None:
        run = FakeCommandRunner().on(
            "udevadm",
            returns=completed(127, stderr="udevadm: command not found"),
        )
        result = _mount(_encrypted_vol(), run)
        assert result.failure_reason == MountFailureKind.PROBE_FAILED
        assert _udisks_steps(run) == []


def _umount(
    vol: LocalVolume,
    run: FakeCommandRunner,
    sleep: Callable[[float], None] = lambda _: None,
) -> UmountResult:
    assert vol.mount is not None
    return umount_volume(vol, vol.mount, {}, run=run, sleep=sleep)


class TestUmountVolume:
    def test_umount_and_lock_luks(self) -> None:
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_UNLOCKED)
            .on("findmnt", returns=_mounted_at("/mnt/encrypted"))
        )
        result = _umount(_encrypted_vol(), run)
        assert result.success
        # unmount the cleartext device, then lock the container
        assert run.commands_starting_with("udisksctl") == [
            ["udisksctl", "unmount", "-b", _CLEARTEXT, "--no-user-interaction"],
            [
                "udisksctl",
                "lock",
                "-b",
                f"/dev/disk/by-uuid/{_ENC_UUID}",
                "--no-user-interaction",
            ],
        ]

    def test_not_mounted_still_locks_luks(self) -> None:
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_UNLOCKED)
            .on("findmnt", returns=_NOT_MOUNTED)
        )
        result = _umount(_encrypted_vol(), run)
        assert result.success
        assert _udisks_steps(run) == ["lock"]

    def test_locked_container_needs_nothing(self) -> None:
        run = FakeCommandRunner().on("lsblk", returns=_LOCKED)
        result = _umount(_encrypted_vol(), run)
        assert result.success
        assert _udisks_steps(run) == []

    def test_lock_failure_after_unmount_is_a_failure(self) -> None:
        """The drive must end locked: a failed lock is not a successful umount."""
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_UNLOCKED)
            .on("findmnt", returns=_mounted_at("/mnt/encrypted"))
            .on(
                "udisksctl",
                "lock",
                returns=completed(1, stderr="Error locking: device busy"),
            )
        )
        result = _umount(_encrypted_vol(), run)
        assert _udisks_steps(run) == ["unmount", "lock"]
        assert not result.success
        assert result.failure_reason == UmountFailureKind.LOCK_FAILED
        assert result.failure == LifecycleFailure(
            step=LifecycleStep.LOCK,
            exit_code=1,
            message="Error locking: device busy",
        )

    def test_retries_unmount_and_lock_while_udisks_catches_up(self) -> None:
        sleeps: list[float] = []
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_UNLOCKED)
            .on("findmnt", returns=_mounted_at("/mnt/encrypted"))
            .on(
                "udisksctl",
                "unmount",
                returns=[completed(1, stderr=_LOOKUP_ERROR), completed(0)],
            )
            .on(
                "udisksctl",
                "lock",
                returns=[completed(1, stderr=_LOOKUP_ERROR), completed(0)],
            )
        )
        result = _umount(_encrypted_vol(), run, sleeps.append)
        assert result.success
        assert _udisks_steps(run) == ["unmount", "unmount", "lock", "lock"]
        assert len(sleeps) == 2

    def test_umount_failure(self) -> None:
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_UNLOCKED)
            .on("findmnt", returns=_mounted_at("/mnt/encrypted"))
            .on("udisksctl", "unmount", returns=completed(1, stderr="target is busy"))
        )
        result = _umount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == UmountFailureKind.UNMOUNT_FAILED
        # Locking a mounted container cannot succeed: it is not attempted.
        assert _udisks_steps(run) == ["unmount"]

    @pytest.mark.parametrize(
        ("step", "stderr", "reason"),
        [
            (
                "unmount",
                "Error unmounting: Not authorized to perform operation",
                UmountFailureKind.NOT_AUTHORIZED,
            ),
            (
                "lock",
                "Error locking: Not authorized to perform operation",
                UmountFailureKind.NOT_AUTHORIZED,
            ),
            (
                "unmount",
                "bash: udisksctl: command not found",
                UmountFailureKind.UDISKS_NOT_AVAILABLE,
            ),
        ],
    )
    def test_unmount_and_lock_refusals_are_classified_as_for_mount(
        self,
        step: str,
        stderr: str,
        reason: UmountFailureKind,
    ) -> None:
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_UNLOCKED)
            .on("findmnt", returns=_mounted_at("/mnt/encrypted"))
            .on("udisksctl", step, returns=completed(1, stderr=stderr))
        )
        result = _umount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == reason

    def test_ssh_timeout_returns_failed(self) -> None:
        run = FakeCommandRunner().on("lsblk", returns=TimeoutError("timed out"))
        result = _umount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == UmountFailureKind.UNREACHABLE
        assert result.failure == LifecycleFailure(message="timed out")

    def test_missing_local_tool_is_not_unreachable(self) -> None:
        run = FakeCommandRunner().on(
            "findmnt",
            returns=FileNotFoundError(2, "No such file", "findmnt"),
        )
        result = _umount(_unencrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == UmountFailureKind.UDISKS_NOT_AVAILABLE
        assert result.failure == LifecycleFailure(command="findmnt")

    def test_failed_probe_is_reported_not_skipped(self) -> None:
        """A failed lsblk must not be read as "locked" and skip the lock."""
        run = FakeCommandRunner().on("lsblk", returns=completed(1, stderr="boom"))
        result = _umount(_encrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == UmountFailureKind.PROBE_FAILED
        assert result.failure == LifecycleFailure(
            command="lsblk",
            exit_code=1,
            message="boom",
        )
        assert _udisks_steps(run) == []

    def test_failed_findmnt_is_reported_not_skipped(self) -> None:
        """A failed findmnt must not be read as "not mounted" and skip unmount."""
        run = FakeCommandRunner().on(
            "findmnt",
            returns=completed(127, stderr="findmnt: command not found"),
        )
        result = _umount(_unencrypted_vol(), run)
        assert not result.success
        assert result.failure_reason == UmountFailureKind.PROBE_FAILED
        assert result.failure == LifecycleFailure(
            command="findmnt",
            exit_code=127,
            message="findmnt: command not found",
        )
        assert _udisks_steps(run) == []  # no unmount on a guess

    def test_unencrypted_umount_no_lock(self) -> None:
        run = FakeCommandRunner().on("findmnt", returns=_mounted_at("/mnt/usb"))
        result = _umount(_unencrypted_vol(), run)
        assert result.success
        # Only unmount, no lock for unencrypted.
        assert run.commands_starting_with("udisksctl") == [
            [
                "udisksctl",
                "unmount",
                "-b",
                f"/dev/disk/by-uuid/{_USB_UUID}",
                "--no-user-interaction",
            ],
        ]


def _config_with_mount() -> Config:
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


class TestMountVolumes:
    def test_only_mount_config_volumes(self) -> None:
        cfg = _config_with_mount()
        run = (
            FakeCommandRunner()
            .on("lsblk", returns=_UNLOCKED)
            .on("findmnt", returns=_mounted_at("/mnt/x"))
        )
        results = mount_volumes(cfg, {}, lambda _passphrase_id: "pass", run=run)
        # Only 2 volumes have mount config (enc, usb), not plain.
        assert [r.volume_slug for r in results] == ["encrypted", "usb"]
        assert all(r.success for r in results)
        assert {c.volume.slug for c in run.calls} == {"encrypted", "usb"}

    def test_filter_by_name(self) -> None:
        cfg = _config_with_mount()
        run = FakeCommandRunner().on("findmnt", returns=_mounted_at("/mnt/usb"))
        results = mount_volumes(
            cfg,
            {},
            lambda _passphrase_id: "pass",
            names=["usb"],
            run=run,
        )
        assert [r.volume_slug for r in results] == ["usb"]
        assert {c.volume.slug for c in run.calls} == {"usb"}

    def test_returns_list(self) -> None:
        cfg = _config_with_mount()
        run = FakeCommandRunner().on("test", "-e", returns=completed(1))
        results = mount_volumes(cfg, {}, lambda _passphrase_id: "pass", run=run)
        assert isinstance(results, list)
        assert all(not r.success for r in results)


class TestUmountVolumes:
    def test_reverse_order(self) -> None:
        cfg = _config_with_mount()
        slugs: list[str] = []
        umount_volumes(
            cfg,
            {},
            on_umount_start=slugs.append,
            run=FakeCommandRunner(),
        )
        # Should be reversed: usb first, then enc.
        assert slugs == ["usb", "enc"]

    def test_returns_list(self) -> None:
        cfg = _config_with_mount()
        results = umount_volumes(cfg, {}, run=FakeCommandRunner())
        assert isinstance(results, list)
        assert len(results) == 2


def _config_with_excluded_remote_mount() -> tuple[Config, ResolvedEndpoints]:
    """
    Build ``usb`` (local) and ``nas-drive`` (remote, only reachable from home).

    Resolved under ``--exclude-location home``.
    """
    cfg = Config(
        ssh_endpoints={
            "nas": SshEndpoint(slug="nas", host="nas.lan", location="home"),
        },
        volumes={
            "nas-drive": RemoteVolume(
                slug="nas-drive",
                ssh_endpoint="nas",
                path="/mnt/nas-drive",
                mount=MountConfig(device_uuid=_ENC_UUID),
            ),
            "usb": _unencrypted_vol(),
        },
    )
    resolved = resolve_all_endpoints(
        cfg,
        EndpointFilter(exclude_locations=["home"]),
        ssh_config=paramiko.SSHConfig(),
        lookup=lambda _host: {"192.168.1.10"},
    )
    return cfg, resolved


class TestLocationExcludedVolumes:
    """A volume excluded by location has no endpoint: nothing may reach it."""

    def test_mount_skips_excluded_volume(self) -> None:
        cfg, resolved = _config_with_excluded_remote_mount()
        run = FakeCommandRunner().on("findmnt", returns=_mounted_at("/mnt/usb"))
        results = mount_volumes(cfg, resolved, lambda _passphrase_id: "pass", run=run)
        assert [r.volume_slug for r in results] == ["usb"]
        assert {c.volume.slug for c in run.calls} == {"usb"}

    def test_umount_skips_excluded_volume(self) -> None:
        cfg, resolved = _config_with_excluded_remote_mount()
        run = FakeCommandRunner()
        results = umount_volumes(cfg, resolved, run=run)
        assert [r.volume_slug for r in results] == ["usb"]
        assert {c.volume.slug for c in run.calls} == {"usb"}

    def test_mount_count_leaves_out_excluded_volume(self) -> None:
        cfg, resolved = _config_with_excluded_remote_mount()
        assert mount_count(cfg, resolved) == 1

    @pytest.mark.parametrize("mounting", [True, False])
    def test_plan_skips_excluded_volume(self, *, mounting: bool) -> None:
        cfg, resolved = _config_with_excluded_remote_mount()
        probed: list[str] = []

        def probe(volume: Volume, *_: object) -> MountCapabilities:
            probed.append(volume.slug)
            return MountCapabilities(device_present=False)

        plans = plan_lifecycle(cfg, resolved, mounting=mounting, probe=probe)

        assert [p.volume_slug for p in plans] == ["usb"]
        assert probed == ["usb"]
