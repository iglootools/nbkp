"""Tests for nbkp.disks.plan: the dry-run plan of the mount lifecycle."""

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    Volume,
)
from nbkp.disks.models import MountCapabilities
from nbkp.disks.plan import MountAction, MountProbeFn, VolumePlan, plan_lifecycle
from nbkp.remote.endpoints import ResolvedEndpoints

_UNLOCK, _MOUNT = MountAction.UNLOCK, MountAction.MOUNT
_UNMOUNT, _LOCK = MountAction.UNMOUNT, MountAction.LOCK


def _volume(slug: str, *, encrypted: bool = True) -> LocalVolume:
    encryption = LuksEncryptionConfig(passphrase_id=slug) if encrypted else None
    return LocalVolume(
        slug=slug,
        path=f"/mnt/{slug}",
        mount=MountConfig(
            device_uuid=f"00000000-0000-0000-0000-00000000000{len(slug)}",
            encryption=encryption,
        ),
    )


def _config(*volumes: LocalVolume) -> Config:
    return Config(volumes={v.slug: v for v in volumes})


def _probe(states: dict[str, MountCapabilities | Exception]) -> MountProbeFn:
    def probe(
        volume: Volume,
        _mount: MountConfig,
        _resolved: ResolvedEndpoints,
    ) -> MountCapabilities:
        match states[volume.slug]:
            case Exception() as error:
                raise error
            case caps:
                return caps

    return probe


def _plan(
    config: Config,
    states: dict[str, MountCapabilities | Exception],
    *,
    mounting: bool,
    names: list[str] | None = None,
) -> list[VolumePlan]:
    return plan_lifecycle(
        config,
        {},
        mounting=mounting,
        names=names,
        probe=_probe(states),
    )


_LOCKED = MountCapabilities(device_present=True, luks_unlocked=False, mounted=False)
_UNLOCKED_ONLY = MountCapabilities(
    device_present=True,
    luks_unlocked=True,
    mounted=False,
)
_MOUNTED = MountCapabilities(
    device_present=True,
    luks_unlocked=True,
    mounted=True,
    effective_path="/mnt/enc",
)
_ABSENT = MountCapabilities(device_present=False)


class TestMountPlan:
    def test_locked_drive_is_unlocked_then_mounted(self) -> None:
        [plan] = _plan(_config(_volume("enc")), {"enc": _LOCKED}, mounting=True)
        assert plan == VolumePlan("enc", (_UNLOCK, _MOUNT))

    def test_unlocked_but_not_mounted_is_only_mounted(self) -> None:
        [plan] = _plan(_config(_volume("enc")), {"enc": _UNLOCKED_ONLY}, mounting=True)
        assert plan == VolumePlan("enc", (_MOUNT,))

    def test_unencrypted_drive_is_only_mounted(self) -> None:
        state = MountCapabilities(device_present=True, mounted=False)
        config = _config(_volume("plain", encrypted=False))
        [plan] = _plan(config, {"plain": state}, mounting=True)
        assert plan == VolumePlan("plain", (_MOUNT,))

    def test_already_mounted_needs_nothing(self) -> None:
        [plan] = _plan(_config(_volume("enc")), {"enc": _MOUNTED}, mounting=True)
        assert plan == VolumePlan("enc", (), "already mounted at /mnt/enc")

    def test_absent_drive_needs_nothing(self) -> None:
        [plan] = _plan(_config(_volume("enc")), {"enc": _ABSENT}, mounting=True)
        assert plan == VolumePlan("enc", (), "device not plugged in")


class TestUmountPlan:
    def test_mounted_drive_is_unmounted_then_locked(self) -> None:
        [plan] = _plan(_config(_volume("enc")), {"enc": _MOUNTED}, mounting=False)
        assert plan == VolumePlan("enc", (_UNMOUNT, _LOCK))

    def test_unlocked_but_not_mounted_is_only_locked(self) -> None:
        config = _config(_volume("enc"))
        [plan] = _plan(config, {"enc": _UNLOCKED_ONLY}, mounting=False)
        assert plan == VolumePlan("enc", (_LOCK,))

    def test_unencrypted_mounted_drive_is_only_unmounted(self) -> None:
        state = MountCapabilities(device_present=True, mounted=True)
        config = _config(_volume("plain", encrypted=False))
        [plan] = _plan(config, {"plain": state}, mounting=False)
        assert plan == VolumePlan("plain", (_UNMOUNT,))

    def test_locked_drive_needs_nothing(self) -> None:
        [plan] = _plan(_config(_volume("enc")), {"enc": _LOCKED}, mounting=False)
        assert plan == VolumePlan("enc", (), "not mounted")


class TestPlanLifecycle:
    def test_probe_failure_is_reported_not_raised(self) -> None:
        states: dict[str, MountCapabilities | Exception] = {
            "enc": OSError("No route to host"),
        }

        [plan] = _plan(_config(_volume("enc")), states, mounting=True)

        assert plan.probe_failed is True
        assert plan.actions == ()
        assert plan.detail is not None
        assert plan.detail.startswith("unreachable: ")
        assert "No route to host" in plan.detail

    def test_unknown_mount_state_is_a_probe_failure_when_unmounting(self) -> None:
        state = MountCapabilities(device_present=True)

        [plan] = _plan(_config(_volume("enc")), {"enc": state}, mounting=False)

        assert plan == VolumePlan("enc", (), "device state unknown", probe_failed=True)

    def test_unknown_lock_state_is_a_probe_failure_when_mounting(self) -> None:
        state = MountCapabilities(device_present=True, mounted=False)

        [plan] = _plan(_config(_volume("enc")), {"enc": state}, mounting=True)

        assert plan == VolumePlan("enc", (), "device state unknown", probe_failed=True)

    def test_missing_local_tool_is_reported_as_not_installed(self) -> None:
        states: dict[str, MountCapabilities | Exception] = {
            "enc": FileNotFoundError(2, "No such file or directory", "lsblk"),
        }

        [plan] = _plan(_config(_volume("enc")), states, mounting=False)

        assert plan == VolumePlan("enc", (), "lsblk not installed", probe_failed=True)

    def test_umount_is_planned_in_reverse_order(self) -> None:
        config = _config(_volume("first"), _volume("second"))
        states: dict[str, MountCapabilities | Exception] = {
            "first": _MOUNTED,
            "second": _MOUNTED,
        }

        mounting = _plan(config, states, mounting=True)
        umounting = _plan(config, states, mounting=False)

        assert [p.volume_slug for p in mounting] == ["first", "second"]
        assert [p.volume_slug for p in umounting] == ["second", "first"]

    def test_names_filter_selects_volumes(self) -> None:
        config = _config(_volume("first"), _volume("second"))
        # Probing an unselected volume would raise KeyError.
        states: dict[str, MountCapabilities | Exception] = {"second": _LOCKED}

        plans = _plan(config, states, mounting=True, names=["second"])

        assert [p.volume_slug for p in plans] == ["second"]

    def test_volumes_without_mount_config_are_ignored(self) -> None:
        config = Config(
            volumes={
                "enc": _volume("enc"),
                "fixed": LocalVolume(slug="fixed", path="/srv/data"),
            },
        )

        plans = _plan(config, {"enc": _LOCKED}, mounting=True)

        assert [p.volume_slug for p in plans] == ["enc"]
