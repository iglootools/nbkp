"""Tests for nbkp.disks.context (mount lifecycle context manager)."""

from collections.abc import Callable

import pytest

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.credentials import CredentialError, CredentialErrorKind
from nbkp.disks.context import managed_mount, mount_phase
from tests.fakes import FakeCommandRunner, completed

_ONLINE_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"
_OFFLINE_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _config() -> Config:
    """Two encrypted volumes; only ``online``'s drive is plugged in."""
    return Config(
        volumes={
            "online": LocalVolume(
                slug="online",
                path="/mnt/online",
                mount=MountConfig(
                    device_uuid=_ONLINE_UUID,
                    encryption=LuksEncryptionConfig(passphrase_id="online-luks"),
                ),
            ),
            "offline": LocalVolume(
                slug="offline",
                path="/mnt/offline",
                mount=MountConfig(
                    device_uuid=_OFFLINE_UUID,
                    encryption=LuksEncryptionConfig(passphrase_id="offline-luks"),
                ),
            ),
        }
    )


def _online_drive() -> FakeCommandRunner:
    """Only ``online``'s drive is plugged in; it is unlocked and mounted."""
    return (
        FakeCommandRunner()
        .on("test", "-e", f"/dev/disk/by-uuid/{_ONLINE_UUID}", returns=completed(0))
        .on("test", "-e", returns=completed(1))
        .on(
            "lsblk",
            "-rno",
            "NAME,TYPE",
            f"/dev/disk/by-uuid/{_ONLINE_UUID}",
            returns=completed(0, "sdb part\nluks-online crypt\n"),
        )
        .on("lsblk", returns=completed(32))
        .on("findmnt", returns=completed(0, "/mnt/online\n"))
    )


def _udisks_steps(run: FakeCommandRunner) -> list[str]:
    return [cmd[1] for cmd in run.commands_starting_with("udisksctl")]


def _recorder(calls: list[str]) -> Callable[[str], str]:
    """A ``passphrase_fn`` that records the ids it is asked for."""

    def passphrase_fn(passphrase_id: str) -> str:
        calls.append(passphrase_id)
        return "secret"

    return passphrase_fn


class TestPassphrasePrefetch:
    def test_retrieves_offline_volume_passphrase_before_mounting(self) -> None:
        """Every configured passphrase is read up front, plugged in or not.

        The point of prefetching: an OS secret store gates access per item,
        so reading only the online drive's passphrase would leave the offline
        one un-approved and block the next run.
        """
        run = _online_drive()
        # Each retrieval records how many host commands had run by then.
        retrievals: list[tuple[str, int]] = []

        def passphrase_fn(passphrase_id: str) -> str:
            retrievals.append((passphrase_id, len(run.calls)))
            return "secret"

        with managed_mount(_config(), {}, passphrase_fn, umount=False, run=run):
            pass

        # Both ids retrieved, in id order, before the first device is probed.
        # The already-unlocked online volume needs no passphrase of its own,
        # so every retrieval here comes from the prefetch pass.
        assert retrievals == [("offline-luks", 0), ("online-luks", 0)]
        assert [cmd[2] for cmd in run.commands_starting_with("test", "-e")] == [
            f"/dev/disk/by-uuid/{_ONLINE_UUID}",
            f"/dev/disk/by-uuid/{_OFFLINE_UUID}",
        ]

    def test_prefetch_ignores_the_names_filter(self) -> None:
        calls: list[str] = []
        run = FakeCommandRunner().on("test", "-e", returns=completed(1))

        with managed_mount(
            _config(), {}, _recorder(calls), names=["online"], umount=False, run=run
        ):
            pass

        assert calls == ["offline-luks", "online-luks"]

    def test_no_prefetch_when_mounting_is_disabled(self) -> None:
        calls: list[str] = []
        run = FakeCommandRunner()

        with managed_mount(_config(), {}, _recorder(calls), mount=False, run=run):
            pass

        assert calls == []
        assert run.commands == []

    def test_prefetch_failure_does_not_abort_the_mount_phase(self) -> None:
        def passphrase_fn(pid: str) -> str:
            raise CredentialError(
                f"No passphrase found in keyring for id '{pid}'",
                reason=CredentialErrorKind.NOT_FOUND,
            )

        run = FakeCommandRunner().on("test", "-e", returns=completed(1))
        with managed_mount(_config(), {}, passphrase_fn, umount=False, run=run) as (
            resolved_config,
            observations,
        ):
            assert resolved_config is not None
            assert set(observations) == {"online", "offline"}


class TestMountPhase:
    def test_prefetches_every_passphrase_then_mounts_only_named_volumes(
        self,
    ) -> None:
        calls: list[str] = []
        run = FakeCommandRunner().on("test", "-e", returns=completed(1))

        results = mount_phase(
            _config(), {}, passphrase_fn=_recorder(calls), names=["online"], run=run
        )

        assert calls == ["offline-luks", "online-luks"]
        assert [r.volume_slug for r in results] == ["online"]
        assert run.commands_starting_with("udisksctl") == []


class TestUmountOnInterruption:
    """Regression: an interruption during mounting left drives unlocked."""

    def _interrupted(self, interrupt: BaseException) -> list[str]:
        """The udisks steps run when *interrupt* stops the mount phase."""
        run = _online_drive()

        def on_mount_end(slug: str, _result: object) -> None:
            raise interrupt

        with (
            pytest.raises(type(interrupt)),
            managed_mount(
                _config(), {}, _recorder([]), on_mount_end=on_mount_end, run=run
            ),
        ):
            pass
        return _udisks_steps(run)

    def test_ctrl_c_during_mount_still_umounts(self) -> None:
        # The online drive was already mounted: nothing to mount, then the
        # umount phase unmounts and locks it.
        assert self._interrupted(KeyboardInterrupt()) == ["unmount", "lock"]

    def test_callback_exception_during_mount_still_umounts(self) -> None:
        assert self._interrupted(RuntimeError("display crashed")) == [
            "unmount",
            "lock",
        ]

    def test_umount_false_is_respected_even_on_interruption(self) -> None:
        run = _online_drive()

        def interrupt_prefetch(_passphrase_id: str) -> None:
            raise KeyboardInterrupt

        with (
            pytest.raises(KeyboardInterrupt),
            managed_mount(
                _config(),
                {},
                _recorder([]),
                umount=False,
                on_prefetch_start=interrupt_prefetch,
                run=run,
            ),
        ):
            pass
        assert run.commands == []
