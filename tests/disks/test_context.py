"""Tests for nbkp.disks.context (mount lifecycle context manager)."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.credentials import CredentialError, CredentialErrorReason
from nbkp.disks.context import managed_mount

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
        events: list[str] = []

        def passphrase_fn(passphrase_id: str) -> str:
            events.append(f"passphrase:{passphrase_id}")
            return "secret"

        def detect(_vol: object, uuid: str, _re: object) -> bool:
            events.append(f"probe:{uuid}")
            return uuid == _ONLINE_UUID

        with (
            patch("nbkp.disks.lifecycle.detect_device_present", side_effect=detect),
            patch(
                "nbkp.disks.lifecycle.discover_cleartext_device",
                return_value="/dev/mapper/luks-online",
            ),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/online"),
            patch("nbkp.disks.lifecycle.run_on_volume"),
            managed_mount(_config(), {}, passphrase_fn, umount=False),
        ):
            pass

        # Both ids retrieved, in id order, before the first device is probed.
        # The already-unlocked online volume needs no passphrase of its own,
        # so every retrieval here comes from the prefetch pass.
        assert events[:2] == ["passphrase:offline-luks", "passphrase:online-luks"]
        assert [e for e in events if e.startswith("probe:")] == [
            f"probe:{_ONLINE_UUID}",
            f"probe:{_OFFLINE_UUID}",
        ]

    def test_prefetch_ignores_the_names_filter(self) -> None:
        calls: list[str] = []

        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=False),
            patch("nbkp.disks.lifecycle.run_on_volume"),
            managed_mount(
                _config(), {}, _recorder(calls), names=["online"], umount=False
            ),
        ):
            pass

        assert calls == ["offline-luks", "online-luks"]

    def test_no_prefetch_when_mounting_is_disabled(self) -> None:
        calls: list[str] = []

        with managed_mount(_config(), {}, _recorder(calls), mount=False):
            pass

        assert calls == []

    def test_prefetch_failure_does_not_abort_the_mount_phase(self) -> None:
        def passphrase_fn(pid: str) -> str:
            raise CredentialError(
                f"No passphrase found in keyring for id '{pid}'",
                reason=CredentialErrorReason.NOT_FOUND,
            )

        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=False),
            patch("nbkp.disks.lifecycle.run_on_volume"),
            managed_mount(_config(), {}, passphrase_fn, umount=False) as (
                resolved_config,
                observations,
            ),
        ):
            assert resolved_config is not None
            assert set(observations) == {"online", "offline"}


class TestUmountOnInterruption:
    """Regression: an interruption during mounting left drives unlocked."""

    def _interrupted(self, interrupt: BaseException) -> list[str]:
        umounted: list[str] = []

        def on_mount_end(slug: str, _result: object) -> None:
            raise interrupt

        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch(
                "nbkp.disks.lifecycle.discover_cleartext_device",
                return_value="/dev/mapper/luks-online",
            ),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/online"),
            patch("nbkp.disks.lifecycle.run_on_volume"),
            patch(
                "nbkp.disks.context.umount_volumes",
                side_effect=lambda *_a, **_k: umounted.append("umount") or [],
            ),
            pytest.raises(type(interrupt)),
            managed_mount(_config(), {}, _recorder([]), on_mount_end=on_mount_end),
        ):
            pass
        return umounted

    def test_ctrl_c_during_mount_still_umounts(self) -> None:
        assert self._interrupted(KeyboardInterrupt()) == ["umount"]

    def test_callback_exception_during_mount_still_umounts(self) -> None:
        assert self._interrupted(RuntimeError("display crashed")) == ["umount"]

    def test_umount_false_is_respected_even_on_interruption(self) -> None:
        umounted: list[str] = []
        with (
            patch(
                "nbkp.disks.context.prefetch_passphrases",
                side_effect=KeyboardInterrupt(),
            ),
            patch(
                "nbkp.disks.context.umount_volumes",
                side_effect=lambda *_a, **_k: umounted.append("umount") or [],
            ),
            pytest.raises(KeyboardInterrupt),
            managed_mount(_config(), {}, _recorder([]), umount=False),
        ):
            pass
        assert umounted == []
