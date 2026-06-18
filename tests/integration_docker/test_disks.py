"""Integration tests: mount management detection and LUKS operations.

These tests run against the Docker container with a real LUKS-encrypted
file-backed volume. Tests that require dm-crypt (LUKS) are skipped
automatically when the kernel module is not available.

Docker-harness note (NEEDS REAL-DOCKER VALIDATION):
The mount lifecycle migrated from direct ``cryptsetup``/``mount`` to udisks2
(``udisksctl``).  These tests now drive ``udisksctl`` via the production
``mount_volume`` / ``umount_volume`` / ``detection`` functions.  For them to
pass against a real container, the image at
``nbkp/remote/testkit/dockerbuild/`` (frozen in this change) must be updated
to run ``udisksd`` + polkit and carry the ``50-nbkp.rules`` polkit grant plus
an fstab entry mapping ``/dev/mapper/luks-<uuid>`` to the mount path.  See the
``tests/_docker_fixtures`` module docstring for the full container checklist.
These files are made import-clean / pyright-clean and internally consistent
here; their actual execution requires that updated container.
"""

from __future__ import annotations

import pytest

from nbkp.config import (
    Config,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
)
from nbkp.config.epresolution import ResolvedEndpoints
from nbkp.disks.detection import (
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
    resolve_target_device,
)
from nbkp.disks.lifecycle import (
    MountFailureReason,
    mount_volume,
    mount_volumes,
    umount_volume,
    umount_volumes,
)
from nbkp.disks.observation import build_mount_observations
from nbkp.disks.udisks import build_lock_command, build_unlock_command
from nbkp.disks.context import managed_mount
from nbkp.remote.dispatch import run_on_volume

from tests._docker_fixtures import (
    LUKS_PASSPHRASE,
    resolved_endpoints_for,
    ssh_exec,
)


def _unlock_luks(
    volume: RemoteVolume,
    resolved: ResolvedEndpoints,
    luks_uuid: str,
) -> None:
    """Unlock the LUKS container via the production udisksctl builder."""
    run_on_volume(
        build_unlock_command(luks_uuid), volume, resolved, input=LUKS_PASSPHRASE
    )


def _lock_luks(
    volume: RemoteVolume,
    resolved: ResolvedEndpoints,
    luks_uuid: str,
) -> None:
    """Lock the LUKS container via the production udisksctl builder."""
    run_on_volume(build_lock_command(luks_uuid), volume, resolved)


def _cleanup(
    volume: RemoteVolume,
    resolved: ResolvedEndpoints,
    luks_uuid: str,
) -> None:
    """Idempotent cleanup: umount + lock via udisksctl (ignore errors)."""
    device = resolve_target_device(volume, volume.mount, resolved)  # type: ignore[arg-type]
    if device is not None:
        from nbkp.disks.udisks import build_unmount_command

        run_on_volume(build_unmount_command(device), volume, resolved)
    _lock_luks(volume, resolved, luks_uuid)


# ── Device detection ─────────────────────────────────────────────


class TestDetectDevicePresent:
    def test_present(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """UUID symlink exists in /dev/disk/by-uuid/."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        assert detect_device_present(remote_encrypted_volume, luks_uuid, resolved)

    def test_not_present(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
    ) -> None:
        """Fake UUID is not found."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        assert not detect_device_present(
            remote_encrypted_volume,
            "00000000-0000-0000-0000-000000000000",
            resolved,
        )


class TestDiscoverCleartextDevice:
    def test_none_when_locked(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """No cleartext device while the LUKS container is locked."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        assert (
            discover_cleartext_device(remote_encrypted_volume, luks_uuid, resolved)
            is None
        )

    def test_mapper_after_unlock(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """A ``/dev/mapper`` device appears after unlock."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        _unlock_luks(remote_encrypted_volume, resolved, luks_uuid)
        try:
            device = discover_cleartext_device(
                remote_encrypted_volume, luks_uuid, resolved
            )
            assert device is not None
            assert device.startswith("/dev/mapper/")
        finally:
            _lock_luks(remote_encrypted_volume, resolved, luks_uuid)

    def test_none_after_lock(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Cleartext device disappears after lock."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        _unlock_luks(remote_encrypted_volume, resolved, luks_uuid)
        _lock_luks(remote_encrypted_volume, resolved, luks_uuid)
        assert (
            discover_cleartext_device(remote_encrypted_volume, luks_uuid, resolved)
            is None
        )


# ── LUKS unlock / mount / umount / lock lifecycle ────────────────


class TestLuksLifecycle:
    def test_unlock_mount_umount_lock(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Full LUKS lifecycle via production mount_volume/umount_volume."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        result = mount_volume(
            remote_encrypted_volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
        )
        assert result.success, result.detail
        assert result.luks_unlocked is True
        assert result.mounted is True

        try:
            ssh_exec(
                docker_ssh_endpoint,
                f"echo 'luks-test-data' > {remote_encrypted_volume.path}/test-file.txt",
            )
            read_result = ssh_exec(
                docker_ssh_endpoint,
                f"cat {remote_encrypted_volume.path}/test-file.txt",
            )
            assert read_result.stdout.strip() == "luks-test-data"

            umount_result = umount_volume(
                remote_encrypted_volume, mount_config, resolved
            )
            assert umount_result.success, umount_result.detail

            ls_result = ssh_exec(
                docker_ssh_endpoint,
                f"ls {remote_encrypted_volume.path}/test-file.txt 2>/dev/null",
                check=False,
            )
            assert ls_result.returncode != 0
        finally:
            _cleanup(remote_encrypted_volume, resolved, luks_uuid)

    def test_data_persists_across_remount(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Data written to encrypted volume survives umount/remount."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        try:
            result = mount_volume(
                remote_encrypted_volume,
                mount_config,
                resolved,
                lambda _: LUKS_PASSPHRASE,
            )
            assert result.success, result.detail

            ssh_exec(
                docker_ssh_endpoint,
                f"echo 'persist-test' > {remote_encrypted_volume.path}/persist.txt",
            )
            umount_volume(remote_encrypted_volume, mount_config, resolved)

            result = mount_volume(
                remote_encrypted_volume,
                mount_config,
                resolved,
                lambda _: LUKS_PASSPHRASE,
            )
            assert result.success, result.detail

            read_result = ssh_exec(
                docker_ssh_endpoint,
                f"cat {remote_encrypted_volume.path}/persist.txt",
            )
            assert read_result.stdout.strip() == "persist-test"
        finally:
            _cleanup(remote_encrypted_volume, resolved, luks_uuid)


# ── Helpers for Config-level tests ──────────────────────────────


def _make_config(
    server: SshEndpoint,
    volume: RemoteVolume,
) -> tuple[Config, ResolvedEndpoints]:
    """Build a minimal Config + ResolvedEndpoints for a single volume."""
    config = Config(
        ssh_endpoints={"test-server": server},
        volumes={volume.slug: volume},
    )
    resolved = resolved_endpoints_for(server, volume)
    return config, resolved


# ── Mount detection via findmnt ─────────────────────────────────


class TestFindMountpoint:
    def test_none_when_umounted(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """find_mountpoint returns None when device is not mounted."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        # While locked there is no cleartext device, so target resolves to None.
        device = resolve_target_device(
            remote_encrypted_volume,
            remote_encrypted_volume.mount,
            resolved,  # type: ignore[arg-type]
        )
        assert (
            device is None
            or find_mountpoint(remote_encrypted_volume, device, resolved) is None
        )

    def test_target_after_mount(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """find_mountpoint returns the target after a successful mount."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        result = mount_volume(
            remote_encrypted_volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
        )
        assert result.success, result.detail
        try:
            device = resolve_target_device(
                remote_encrypted_volume, mount_config, resolved
            )
            assert device is not None
            assert (
                find_mountpoint(remote_encrypted_volume, device, resolved) is not None
            )
        finally:
            _cleanup(remote_encrypted_volume, resolved, luks_uuid)


# ── Mount idempotency ───────────────────────────────────────────


class TestMountVolumeIdempotency:
    def test_mount_already_mounted_succeeds(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Mounting an already-mounted volume succeeds (skip logic)."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        result1 = mount_volume(
            remote_encrypted_volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
        )
        assert result1.success, result1.detail
        try:
            result2 = mount_volume(
                remote_encrypted_volume,
                mount_config,
                resolved,
                lambda _: LUKS_PASSPHRASE,
            )
            assert result2.success, result2.detail
        finally:
            _cleanup(remote_encrypted_volume, resolved, luks_uuid)

    def test_mount_device_not_present_returns_structured_failure(
        self,
        docker_ssh_endpoint: SshEndpoint,
        luks_uuid: str,
    ) -> None:
        """mount_volume with a fake UUID returns DEVICE_NOT_PRESENT."""
        volume = RemoteVolume(
            slug="test-missing-device",
            ssh_endpoint="test-server",
            path="/srv/btrfs-encrypted-backups",
            mount=MountConfig(
                device_uuid="00000000-0000-0000-0000-000000000000",
                encryption=LuksEncryptionConfig(passphrase_id="test-luks"),
            ),
        )
        resolved = resolved_endpoints_for(docker_ssh_endpoint, volume)
        mount_config = volume.mount
        assert mount_config is not None

        result = mount_volume(
            volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
        )
        assert not result.success
        assert result.failure_reason == MountFailureReason.DEVICE_NOT_PRESENT


class TestUmountVolumeIdempotency:
    def test_umount_already_umounted_succeeds(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
    ) -> None:
        """Umounting an already-umounted volume succeeds."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        result = umount_volume(remote_encrypted_volume, mount_config, resolved)
        assert result.success


# ── Mount observations ──────────────────────────────────────────


class TestMountObservations:
    def test_observations_after_successful_mount(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Observations from a successful mount reflect actual state."""
        config, resolved = _make_config(docker_ssh_endpoint, remote_encrypted_volume)
        mount_results = mount_volumes(
            config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
        )
        try:
            assert len(mount_results) == 1
            assert mount_results[0].success

            observations = build_mount_observations(mount_results)
            slug = remote_encrypted_volume.slug
            assert slug in observations
            obs = observations[slug]
            assert obs.device_present is True
            assert obs.luks_unlocked is True
            assert obs.mounted is True
        finally:
            umount_volumes(config, resolved)

    def test_observations_after_device_not_present(
        self,
        docker_ssh_endpoint: SshEndpoint,
        luks_uuid: str,
    ) -> None:
        """Observations from a failed mount capture device_present=False."""
        volume = RemoteVolume(
            slug="test-missing",
            ssh_endpoint="test-server",
            path="/srv/btrfs-encrypted-backups",
            mount=MountConfig(
                device_uuid="00000000-0000-0000-0000-000000000000",
                encryption=LuksEncryptionConfig(passphrase_id="test-luks"),
            ),
        )
        config, resolved = _make_config(docker_ssh_endpoint, volume)
        mount_results = mount_volumes(
            config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
        )
        assert len(mount_results) == 1
        assert not mount_results[0].success

        observations = build_mount_observations(mount_results)
        slug = volume.slug
        assert slug in observations
        obs = observations[slug]
        assert obs.device_present is False


# ── managed_mount orchestration ─────────────────────────────────


class TestManagedMount:
    def test_mounts_and_umounts(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """managed_mount mounts on entry and umounts on exit."""
        config, resolved = _make_config(docker_ssh_endpoint, remote_encrypted_volume)
        with managed_mount(config, resolved, lambda _: LUKS_PASSPHRASE) as (
            _resolved_config,
            _obs,
        ):
            ssh_exec(
                docker_ssh_endpoint,
                f"echo 'managed-test' > {remote_encrypted_volume.path}/managed.txt",
            )
            read = ssh_exec(
                docker_ssh_endpoint,
                f"cat {remote_encrypted_volume.path}/managed.txt",
            )
            assert read.stdout.strip() == "managed-test"

        ls = ssh_exec(
            docker_ssh_endpoint,
            f"ls {remote_encrypted_volume.path}/managed.txt 2>/dev/null",
            check=False,
        )
        assert ls.returncode != 0

    def test_yields_observations(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """managed_mount yields observations with correct state."""
        config, resolved = _make_config(docker_ssh_endpoint, remote_encrypted_volume)
        with managed_mount(config, resolved, lambda _: LUKS_PASSPHRASE) as (
            _resolved_config,
            observations,
        ):
            slug = remote_encrypted_volume.slug
            assert slug in observations
            obs = observations[slug]
            assert obs.device_present is True
            assert obs.luks_unlocked is True
            assert obs.mounted is True

    def test_callbacks_invoked(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Mount/umount callbacks are called with correct arguments."""
        config, resolved = _make_config(docker_ssh_endpoint, remote_encrypted_volume)
        mount_starts: list[str] = []
        mount_ends: list[tuple[str, bool]] = []
        umount_starts: list[str] = []
        umount_ends: list[tuple[str, bool]] = []

        with managed_mount(
            config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            on_mount_start=lambda slug: mount_starts.append(slug),
            on_mount_end=lambda slug, r: mount_ends.append((slug, r.success)),
            on_umount_start=lambda slug: umount_starts.append(slug),
            on_umount_end=lambda slug, r: umount_ends.append((slug, r.success)),
        ):
            pass

        slug = remote_encrypted_volume.slug
        assert mount_starts == [slug]
        assert mount_ends == [(slug, True)]
        assert umount_starts == [slug]
        assert umount_ends == [(slug, True)]

    def test_umounts_on_exception(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Volume is umounted even when an exception occurs inside the context."""
        config, resolved = _make_config(docker_ssh_endpoint, remote_encrypted_volume)
        with pytest.raises(RuntimeError, match="deliberate"):
            with managed_mount(config, resolved, lambda _: LUKS_PASSPHRASE):
                raise RuntimeError("deliberate")

        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None
        device = resolve_target_device(remote_encrypted_volume, mount_config, resolved)
        assert (
            device is None
            or find_mountpoint(remote_encrypted_volume, device, resolved) is None
        )


# ── Unencrypted mount ───────────────────────────────────────────


class TestUnencryptedMount:
    def test_mount_umount_unencrypted(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_unencrypted_volume: RemoteVolume,
    ) -> None:
        """Full mount/umount lifecycle for a genuinely unencrypted device.

        ``device_uuid`` is the filesystem UUID; nbkp mounts it directly via
        ``udisksctl mount`` with no unlock step (the MountConfig has no
        encryption block).
        """
        volume = remote_unencrypted_volume
        resolved = resolved_endpoints_for(docker_ssh_endpoint, volume)
        mount_config = volume.mount
        assert mount_config is not None
        assert mount_config.encryption is None

        try:
            result = mount_volume(volume, mount_config, resolved, lambda _: "")
            assert result.success, result.detail

            ssh_exec(
                docker_ssh_endpoint,
                f"echo 'unencrypted-test' > {volume.path}/unencrypted.txt",
            )
            read = ssh_exec(
                docker_ssh_endpoint,
                f"cat {volume.path}/unencrypted.txt",
            )
            assert read.stdout.strip() == "unencrypted-test"

            umount_result = umount_volume(volume, mount_config, resolved)
            assert umount_result.success, umount_result.detail
        finally:
            device = resolve_target_device(volume, mount_config, resolved)
            if device is not None:
                from nbkp.disks.udisks import build_unmount_command

                run_on_volume(build_unmount_command(device), volume, resolved)
