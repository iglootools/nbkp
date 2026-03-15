"""Integration tests: mount management detection and LUKS operations.

These tests run against the Docker container with a real LUKS-encrypted
file-backed volume. Tests that require dm-crypt (LUKS) are skipped
automatically when the kernel module is not available.

The Docker container runs sshd as PID 1 (not systemd), so tests use
``DirectMountStrategy`` command builders and the production lifecycle
functions (``mount_volume`` / ``umount_volume``) rather than the
systemd-based operations.
"""

from __future__ import annotations

import pytest

from nbkp.config import (
    Config,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
)
from nbkp.mount.detection import (
    detect_device_present,
    detect_luks_attached,
    detect_systemd_cryptsetup_path,
    resolve_mount_strategy,
)
from nbkp.mount.lifecycle import (
    MountFailureReason,
    mount_volume,
    mount_volumes,
    umount_volume,
    umount_volumes,
)
from nbkp.mount.observation import build_mount_observations
from nbkp.mount.strategy import DirectMountStrategy, SystemdMountStrategy
from nbkp.orchestration import managed_mount
from nbkp.remote.dispatch import run_on_volume

from tests._docker_fixtures import (
    LUKS_MAPPER_NAME,
    LUKS_PASSPHRASE,
    direct_strategy_for,
    resolved_endpoints_for,
    ssh_exec,
)


def _attach_luks(
    volume: RemoteVolume,
    strategy: DirectMountStrategy,
    resolved: ResolvedEndpoints,
    luks_uuid: str,
) -> None:
    """Attach LUKS via production command builder + run_on_volume."""
    cmd = strategy.build_attach_luks_command(LUKS_MAPPER_NAME, luks_uuid)
    run_on_volume(cmd, volume, resolved, input=LUKS_PASSPHRASE)


def _close_luks(
    volume: RemoteVolume,
    strategy: DirectMountStrategy,
    resolved: ResolvedEndpoints,
) -> None:
    """Close LUKS via production command builder + run_on_volume."""
    cmd = strategy.build_close_luks_command(LUKS_MAPPER_NAME)
    run_on_volume(cmd, volume, resolved)


def _cleanup(
    volume: RemoteVolume,
    strategy: DirectMountStrategy,
    resolved: ResolvedEndpoints,
) -> None:
    """Idempotent cleanup: umount + lock (ignore errors)."""
    umount_cmd = strategy.build_umount_command()
    run_on_volume(umount_cmd, volume, resolved)
    lock_cmd = strategy.build_close_luks_command(LUKS_MAPPER_NAME)
    run_on_volume(lock_cmd, volume, resolved)


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


class TestDetectLuksAttached:
    def test_not_attached_when_closed(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
    ) -> None:
        """Mapper does not exist when LUKS is closed."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        assert not detect_luks_attached(
            remote_encrypted_volume, LUKS_MAPPER_NAME, resolved
        )

    def test_attached_after_open(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Mapper exists after LUKS is opened."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        _attach_luks(remote_encrypted_volume, strategy, resolved, luks_uuid)
        try:
            assert detect_luks_attached(
                remote_encrypted_volume, LUKS_MAPPER_NAME, resolved
            )
        finally:
            _close_luks(remote_encrypted_volume, strategy, resolved)

    def test_not_attached_after_close(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Mapper disappears after LUKS is closed."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        _attach_luks(remote_encrypted_volume, strategy, resolved, luks_uuid)
        _close_luks(remote_encrypted_volume, strategy, resolved)
        assert not detect_luks_attached(
            remote_encrypted_volume, LUKS_MAPPER_NAME, resolved
        )


# ── systemd-cryptsetup binary detection ─────────────────────────


class TestDetectSystemdCryptsetupPath:
    def test_finds_binary_or_returns_none(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
    ) -> None:
        """systemd-cryptsetup detection returns a path or None.

        Whether the binary exists depends on Docker image packages.
        This test verifies the function runs without error.
        """
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        result = detect_systemd_cryptsetup_path(remote_encrypted_volume, resolved)
        if result is not None:
            assert result.endswith("systemd-cryptsetup")


# ── LUKS open/close lifecycle ────────────────────────────────────


class TestLuksLifecycle:
    def test_open_mount_umount_close(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Full LUKS lifecycle via production mount_volume/umount_volume."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        # Mount (handles unlock + mount)
        result = mount_volume(
            remote_encrypted_volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            strategy,
        )
        assert result.success, result.detail

        try:
            # Write a file
            ssh_exec(
                docker_ssh_endpoint,
                f"echo 'luks-test-data' > {remote_encrypted_volume.path}/test-file.txt",
            )

            # Read back
            read_result = ssh_exec(
                docker_ssh_endpoint,
                f"cat {remote_encrypted_volume.path}/test-file.txt",
            )
            assert read_result.stdout.strip() == "luks-test-data"

            # Umount (handles umount + lock)
            umount_result = umount_volume(
                remote_encrypted_volume, mount_config, resolved, strategy
            )
            assert umount_result.success, umount_result.detail

            # Verify file is gone (mount point empty)
            ls_result = ssh_exec(
                docker_ssh_endpoint,
                f"ls {remote_encrypted_volume.path}/test-file.txt 2>/dev/null",
                check=False,
            )
            assert ls_result.returncode != 0

        finally:
            _cleanup(remote_encrypted_volume, strategy, resolved)

    def test_data_persists_across_remount(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Data written to encrypted volume survives umount/remount."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        try:
            # First mount: write data
            result = mount_volume(
                remote_encrypted_volume,
                mount_config,
                resolved,
                lambda _: LUKS_PASSPHRASE,
                strategy,
            )
            assert result.success, result.detail

            ssh_exec(
                docker_ssh_endpoint,
                f"echo 'persist-test' > {remote_encrypted_volume.path}/persist.txt",
            )
            umount_volume(remote_encrypted_volume, mount_config, resolved, strategy)

            # Second mount: verify data
            result = mount_volume(
                remote_encrypted_volume,
                mount_config,
                resolved,
                lambda _: LUKS_PASSPHRASE,
                strategy,
            )
            assert result.success, result.detail

            read_result = ssh_exec(
                docker_ssh_endpoint,
                f"cat {remote_encrypted_volume.path}/persist.txt",
            )
            assert read_result.stdout.strip() == "persist-test"
        finally:
            _cleanup(remote_encrypted_volume, strategy, resolved)

    def test_idempotent_open(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Opening an already-open LUKS device does not fail."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        _attach_luks(remote_encrypted_volume, strategy, resolved, luks_uuid)
        try:
            # Second open should not raise (cryptsetup returns 0 or
            # non-zero for "already active" — we accept either)
            ssh_exec(
                docker_ssh_endpoint,
                f"echo -n '{LUKS_PASSPHRASE}' | sudo cryptsetup open"
                f" --type luks /dev/disk/by-uuid/{luks_uuid}"
                f" {LUKS_MAPPER_NAME} - 2>&1 || true",
                check=False,
            )
            # The device should still be unlocked regardless
            check = ssh_exec(
                docker_ssh_endpoint,
                f"test -b /dev/mapper/{LUKS_MAPPER_NAME}",
                check=False,
            )
            assert check.returncode == 0
        finally:
            _cleanup(remote_encrypted_volume, strategy, resolved)


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


# ── Strategy resolution ─────────────────────────────────────────


class TestResolveMountStrategy:
    def test_auto_resolves_to_systemd_when_systemctl_present(
        self,
        docker_ssh_endpoint: SshEndpoint,
        luks_uuid: str,
    ) -> None:
        """Docker container has systemctl, so auto should resolve to systemd."""
        volume = RemoteVolume(
            slug="test-auto",
            ssh_endpoint="test-server",
            path="/srv/btrfs-encrypted-backups",
            mount=MountConfig(
                strategy="auto",
                device_uuid=luks_uuid,
                encryption=LuksEncryptionConfig(
                    mapper_name=LUKS_MAPPER_NAME,
                    passphrase_id="test-luks",
                ),
            ),
        )
        config, resolved = _make_config(docker_ssh_endpoint, volume)
        strategies = resolve_mount_strategy(config, resolved, names=None)
        assert volume.slug in strategies
        assert isinstance(strategies[volume.slug], SystemdMountStrategy)

    def test_direct_resolves_to_direct(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
    ) -> None:
        """Explicit strategy=direct produces DirectMountStrategy."""
        config, resolved = _make_config(docker_ssh_endpoint, remote_encrypted_volume)
        strategies = resolve_mount_strategy(config, resolved, names=None)
        assert remote_encrypted_volume.slug in strategies
        assert isinstance(strategies[remote_encrypted_volume.slug], DirectMountStrategy)


# ── Mount detection via DirectMountStrategy ─────────────────────


class TestDetectMounted:
    def test_not_mounted_when_umounted(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
    ) -> None:
        """detect_mounted returns False when volume is not mounted."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        assert not strategy.detect_mounted(remote_encrypted_volume, resolved)

    def test_mounted_after_mount(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """detect_mounted returns True after mount_volume succeeds."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        result = mount_volume(
            remote_encrypted_volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            strategy,
        )
        assert result.success, result.detail
        try:
            assert strategy.detect_mounted(remote_encrypted_volume, resolved)
        finally:
            _cleanup(remote_encrypted_volume, strategy, resolved)

    def test_not_mounted_after_umount(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """detect_mounted returns False after umount_volume."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        result = mount_volume(
            remote_encrypted_volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            strategy,
        )
        assert result.success, result.detail
        try:
            umount_volume(remote_encrypted_volume, mount_config, resolved, strategy)
            assert not strategy.detect_mounted(remote_encrypted_volume, resolved)
        finally:
            _cleanup(remote_encrypted_volume, strategy, resolved)


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
        strategy = direct_strategy_for(remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        result1 = mount_volume(
            remote_encrypted_volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            strategy,
        )
        assert result1.success, result1.detail
        try:
            result2 = mount_volume(
                remote_encrypted_volume,
                mount_config,
                resolved,
                lambda _: LUKS_PASSPHRASE,
                strategy,
            )
            assert result2.success, result2.detail
        finally:
            _cleanup(remote_encrypted_volume, strategy, resolved)

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
                strategy="direct",
                device_uuid="00000000-0000-0000-0000-000000000000",
                encryption=LuksEncryptionConfig(
                    mapper_name="nonexistent-mapper",
                    passphrase_id="test-luks",
                ),
            ),
        )
        resolved = resolved_endpoints_for(docker_ssh_endpoint, volume)
        strategy = DirectMountStrategy(volume_path=volume.path)
        mount_config = volume.mount
        assert mount_config is not None

        result = mount_volume(
            volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            strategy,
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
        strategy = direct_strategy_for(remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        result = umount_volume(
            remote_encrypted_volume, mount_config, resolved, strategy
        )
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
        strategies = resolve_mount_strategy(config, resolved, names=None)
        mount_results = mount_volumes(
            config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            mount_strategy=strategies,
        )
        try:
            assert len(mount_results) == 1
            assert mount_results[0].success

            observations = build_mount_observations(mount_results, strategies, config)
            slug = remote_encrypted_volume.slug
            assert slug in observations
            obs = observations[slug]
            assert obs.resolved_backend == "direct"
            assert obs.device_present is True
            assert obs.luks_attached is True
            assert obs.mounted is True
        finally:
            umount_volumes(config, resolved, mount_strategy=strategies)

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
                strategy="direct",
                device_uuid="00000000-0000-0000-0000-000000000000",
                encryption=LuksEncryptionConfig(
                    mapper_name="nonexistent-mapper",
                    passphrase_id="test-luks",
                ),
            ),
        )
        config, resolved = _make_config(docker_ssh_endpoint, volume)
        strategies = resolve_mount_strategy(config, resolved, names=None)
        mount_results = mount_volumes(
            config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            mount_strategy=strategies,
        )
        assert len(mount_results) == 1
        assert not mount_results[0].success

        observations = build_mount_observations(mount_results, strategies, config)
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
            _strategy,
            _obs,
        ):
            # Volume is mounted — write and read a file
            ssh_exec(
                docker_ssh_endpoint,
                f"echo 'managed-test' > {remote_encrypted_volume.path}/managed.txt",
            )
            read = ssh_exec(
                docker_ssh_endpoint,
                f"cat {remote_encrypted_volume.path}/managed.txt",
            )
            assert read.stdout.strip() == "managed-test"

        # After context exit, volume is umounted
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
            _strategy,
            observations,
        ):
            slug = remote_encrypted_volume.slug
            assert slug in observations
            obs = observations[slug]
            assert obs.resolved_backend == "direct"
            assert obs.device_present is True
            assert obs.luks_attached is True
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

        # Volume should be umounted after exception
        strategy = direct_strategy_for(remote_encrypted_volume)
        vol_resolved = resolved_endpoints_for(
            docker_ssh_endpoint, remote_encrypted_volume
        )
        assert not strategy.detect_mounted(remote_encrypted_volume, vol_resolved)


# ── Unencrypted mount ───────────────────────────────────────────


class TestUnencryptedMount:
    def test_mount_umount_unencrypted(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume_unencrypted: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Full mount/umount lifecycle without LUKS encryption.

        The volume references the same device (already a LUKS container
        in Docker) but the MountConfig has no encryption block, so nbkp
        skips the attach/close steps.  Since the underlying device is
        LUKS and needs to be unlocked first, we pre-open LUKS manually
        so that ``mount`` can succeed.
        """
        volume = remote_encrypted_volume_unencrypted
        resolved = resolved_endpoints_for(docker_ssh_endpoint, volume)
        strategy = DirectMountStrategy(volume_path=volume.path)
        mount_config = volume.mount
        assert mount_config is not None
        assert mount_config.encryption is None

        # Pre-open LUKS so `mount` can access the device
        ssh_exec(
            docker_ssh_endpoint,
            f"echo -n '{LUKS_PASSPHRASE}' | sudo cryptsetup open"
            f" --type luks /dev/disk/by-uuid/{luks_uuid}"
            f" {LUKS_MAPPER_NAME} - 2>/dev/null || true",
        )
        try:
            result = mount_volume(
                volume, mount_config, resolved, lambda _: "", strategy
            )
            assert result.success, result.detail

            # Write + read
            ssh_exec(
                docker_ssh_endpoint,
                f"echo 'unencrypted-test' > {volume.path}/unencrypted.txt",
            )
            read = ssh_exec(
                docker_ssh_endpoint,
                f"cat {volume.path}/unencrypted.txt",
            )
            assert read.stdout.strip() == "unencrypted-test"

            # Umount
            umount_result = umount_volume(volume, mount_config, resolved, strategy)
            assert umount_result.success, umount_result.detail

            # Verify inaccessible
            ls = ssh_exec(
                docker_ssh_endpoint,
                f"ls {volume.path}/unencrypted.txt 2>/dev/null",
                check=False,
            )
            assert ls.returncode != 0
        finally:
            # Cleanup: umount + close LUKS
            ssh_exec(
                docker_ssh_endpoint,
                f"sudo umount {volume.path} 2>/dev/null || true",
            )
            ssh_exec(
                docker_ssh_endpoint,
                f"sudo cryptsetup close {LUKS_MAPPER_NAME} 2>/dev/null || true",
            )
