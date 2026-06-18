"""Tests for the cleartext-device name troubleshoot prints in fstab fixes.

``luks-<uuid>`` is only udisks's default name for an unlocked LUKS container: a
LUKS2 header label or an ``/etc/crypttab`` entry renames the mapper.  An fstab
line built from the container UUID therefore never matches on such a host, so
the fix prefers the device udisks actually created.
"""

from __future__ import annotations

import pytest

from nbkp.config import LocalVolume, LuksEncryptionConfig, MountConfig
from nbkp.preflight.output import troubleshoot as ts

_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"


def _volume() -> LocalVolume:
    return LocalVolume(
        slug="seagate8tb",
        path="/mnt/seagate8tb",
        mount=MountConfig(
            device_uuid=_UUID,
            encryption=LuksEncryptionConfig(passphrase_id="seagate8tb"),
        ),
    )


class TestCleartextDevice:
    def test_prefers_the_discovered_mapper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A LUKS2-labelled container unlocks as /dev/mapper/<label>."""
        monkeypatch.setattr(
            ts, "discover_cleartext_device", lambda *_: "/dev/mapper/seagate8tb-luks"
        )
        vol = _volume()
        assert vol.mount is not None
        device, discovered = ts._cleartext_device(vol, vol.mount, {})
        assert device == "/dev/mapper/seagate8tb-luks"
        assert discovered is True

    def test_falls_back_to_the_default_name_when_locked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no unlocked device to inspect, the derived name is all there is."""
        monkeypatch.setattr(ts, "discover_cleartext_device", lambda *_: None)
        vol = _volume()
        assert vol.mount is not None
        device, discovered = ts._cleartext_device(vol, vol.mount, {})
        assert device == f"/dev/mapper/luks-{_UUID}"
        assert discovered is False

    def test_does_not_derive_a_name_that_contradicts_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: the UUID-derived name must not win over the real one.

        This is the bug the preference fixes — an fstab line naming
        /dev/mapper/luks-<uuid> on a host whose mapper is /dev/mapper/<label>
        silently never matches, so udisks mounts at /run/media instead.
        """
        monkeypatch.setattr(
            ts, "discover_cleartext_device", lambda *_: "/dev/mapper/seagate8tb-luks"
        )
        vol = _volume()
        assert vol.mount is not None
        device, _ = ts._cleartext_device(vol, vol.mount, {})
        assert _UUID not in device

    def test_survives_a_missing_lsblk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Troubleshoot output must never raise — it is the error path itself.

        `nbkp demo output` renders these fixes on machines with no udisks and no
        lsblk at all, so a discovery that explodes has to degrade to the derived
        name instead of propagating.
        """

        def _boom(*_: object) -> str | None:
            raise FileNotFoundError(2, "No such file or directory", "lsblk")

        monkeypatch.setattr(ts, "discover_cleartext_device", _boom)
        vol = _volume()
        assert vol.mount is not None
        device, discovered = ts._cleartext_device(vol, vol.mount, {})
        assert device == f"/dev/mapper/luks-{_UUID}"
        assert discovered is False
