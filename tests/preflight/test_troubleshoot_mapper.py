"""Tests for the cleartext-device name troubleshoot prints in fstab fixes.

``luks-<uuid>`` is only udisks's default name for an unlocked LUKS container: a
LUKS2 header label or an ``/etc/crypttab`` entry renames the mapper.  An fstab
line built from the container UUID therefore never matches on such a host, so
the fix prefers the device udisks actually created.

Discovery is a live probe, done while *gathering* facts
(:func:`gather_remediation_facts`); rendering only reads the gathered
:class:`RemediationFacts`, so the rendering tests below need no patching.
"""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from nbkp.config import Config, LocalVolume, LuksEncryptionConfig, MountConfig
from nbkp.disks import DeviceProbeError
from nbkp.preflight.output import remediation as rem, troubleshoot as ts
from nbkp.preflight.status.ssh import SshEndpointDiagnostics, SshEndpointStatus
from nbkp.preflight.status.volume import VolumeDiagnostics, VolumeError, VolumeStatus

_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"
_LABELLED = "/dev/mapper/seagate8tb-luks"


def _volume() -> LocalVolume:
    return LocalVolume(
        slug="seagate8tb",
        path="/mnt/seagate8tb",
        mount=MountConfig(
            device_uuid=_UUID,
            encryption=LuksEncryptionConfig(passphrase_id="seagate8tb"),
        ),
    )


def _mount(vol: LocalVolume) -> MountConfig:
    assert vol.mount is not None
    return vol.mount


def _issues(vol: LocalVolume, error: VolumeError) -> list[ts.TroubleshootIssue]:
    status = VolumeStatus(
        slug=vol.slug,
        config=vol,
        ssh_endpoint_status=SshEndpointStatus(
            slug="localhost", diagnostics=SshEndpointDiagnostics(), errors=[]
        ),
        diagnostics=VolumeDiagnostics(),
        errors=[error],
    )
    return ts.collect_issues({}, {vol.slug: status}, {})


def _ctx(vol: LocalVolume) -> rem.TroubleshootContext:
    return rem.TroubleshootContext(config=Config(volumes={vol.slug: vol}))


class TestCleartextDeviceRendering:
    def test_prefers_the_discovered_mapper(self) -> None:
        """A LUKS2-labelled container unlocks as /dev/mapper/<label>."""
        vol = _volume()
        facts = rem.RemediationFacts(cleartext_devices={vol.slug: _LABELLED})
        device, discovered = rem._cleartext_device(vol, _mount(vol), facts)
        assert device == _LABELLED
        assert discovered is True

    def test_falls_back_to_the_default_name_when_locked(self) -> None:
        """With no unlocked device to inspect, the derived name is all there is."""
        vol = _volume()
        device, discovered = rem._cleartext_device(
            vol, _mount(vol), rem.RemediationFacts()
        )
        assert device == f"/dev/mapper/luks-{_UUID}"
        assert discovered is False

    def test_does_not_derive_a_name_that_contradicts_discovery(self) -> None:
        """Regression: the UUID-derived name must not win over the real one.

        This is the bug the preference fixes — an fstab line naming
        /dev/mapper/luks-<uuid> on a host whose mapper is /dev/mapper/<label>
        silently never matches, so udisks mounts at /run/media instead.
        """
        vol = _volume()
        facts = rem.RemediationFacts(cleartext_devices={vol.slug: _LABELLED})
        device, _ = rem._cleartext_device(vol, _mount(vol), facts)
        assert _UUID not in device

    def test_fstab_fix_renders_the_gathered_device(self) -> None:
        vol = _volume()
        ctx = rem.TroubleshootContext(
            config=Config(volumes={vol.slug: vol}),
            facts=rem.RemediationFacts(cleartext_devices={vol.slug: _LABELLED}),
        )
        buf = StringIO()
        ts.print_issues(
            Console(file=buf, width=200, color_system=None),
            _issues(vol, VolumeError.FSTAB_MOUNTPOINT_MISMATCH),
            ctx,
        )
        out = buf.getvalue()
        assert f"{_LABELLED}  /mnt/seagate8tb" in out
        assert "The container is locked" not in out


class TestCleartextDeviceGathering:
    def test_gathers_the_discovered_mapper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ts, "discover_cleartext_device", lambda *_: _LABELLED)
        vol = _volume()
        facts = ts.gather_remediation_facts(
            _issues(vol, VolumeError.FSTAB_MOUNTPOINT_MISMATCH), _ctx(vol)
        )
        assert facts.cleartext_devices == {vol.slug: _LABELLED}

    def test_locked_container_is_not_discovered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ts, "discover_cleartext_device", lambda *_: None)
        vol = _volume()
        facts = ts.gather_remediation_facts(
            _issues(vol, VolumeError.MOUNT_FAILED), _ctx(vol)
        )
        assert facts.cleartext_devices == {}

    def test_only_probes_for_fixes_that_print_a_device(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _unexpected(*_: object) -> str | None:
            raise AssertionError("discovery should not run")

        monkeypatch.setattr(ts, "discover_cleartext_device", _unexpected)
        vol = _volume()
        facts = ts.gather_remediation_facts(
            _issues(vol, VolumeError.UNLOCK_FAILED), _ctx(vol)
        )
        assert facts.cleartext_devices == {}

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
        facts = ts.gather_remediation_facts(
            _issues(vol, VolumeError.FSTAB_MOUNTPOINT_MISMATCH), _ctx(vol)
        )
        assert facts.cleartext_devices == {}

    def test_survives_a_failed_lsblk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail(*_: object) -> str | None:
            raise DeviceProbeError("lsblk", 1, "permission denied")

        monkeypatch.setattr(ts, "discover_cleartext_device", _fail)
        vol = _volume()
        facts = ts.gather_remediation_facts(
            _issues(vol, VolumeError.FSTAB_MOUNTPOINT_MISMATCH), _ctx(vol)
        )
        assert facts.cleartext_devices == {}


class TestPolkitRuleGathering:
    def test_gathers_the_rule_for_the_volume_user(self) -> None:
        vol = _volume()
        ctx = rem.TroubleshootContext(
            config=Config(volumes={vol.slug: vol}), local_user="alice"
        )
        facts = ts.gather_remediation_facts(
            _issues(vol, VolumeError.POLKIT_RULES_MISSING), ctx
        )
        assert set(facts.polkit_rules) == {"alice"}
        assert 'subject.user == "alice"' in facts.polkit_rules["alice"].content

    def test_no_rule_without_a_polkit_issue(self) -> None:
        vol = _volume()
        facts = ts.gather_remediation_facts(
            _issues(vol, VolumeError.UNLOCK_FAILED), _ctx(vol)
        )
        assert facts.polkit_rules == {}
