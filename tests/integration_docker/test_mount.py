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

from nbkp.config import (
    RemoteVolume,
    ResolvedEndpoints,
    SshEndpoint,
)
from nbkp.mount.detection import (
    detect_device_present,
    detect_device_unlocked,
    detect_systemd_cryptsetup_path,
)
from nbkp.mount.lifecycle import mount_volume, umount_volume
from nbkp.mount.strategy import DirectMountStrategy
from nbkp.remote.dispatch import run_on_volume

from tests._docker_fixtures import (
    LUKS_MAPPER_NAME,
    LUKS_PASSPHRASE,
    direct_strategy_for,
    resolved_endpoints_for,
    ssh_exec,
)


def _unlock(
    volume: RemoteVolume,
    strategy: DirectMountStrategy,
    resolved: ResolvedEndpoints,
    luks_uuid: str,
) -> None:
    """Unlock LUKS via production command builder + run_on_volume."""
    cmd = strategy.build_unlock_command(LUKS_MAPPER_NAME, luks_uuid)
    run_on_volume(cmd, volume, resolved, input=LUKS_PASSPHRASE)


def _lock(
    volume: RemoteVolume,
    strategy: DirectMountStrategy,
    resolved: ResolvedEndpoints,
) -> None:
    """Lock LUKS via production command builder + run_on_volume."""
    cmd = strategy.build_lock_command(LUKS_MAPPER_NAME)
    run_on_volume(cmd, volume, resolved)


def _cleanup(
    volume: RemoteVolume,
    strategy: DirectMountStrategy,
    resolved: ResolvedEndpoints,
) -> None:
    """Idempotent cleanup: umount + lock (ignore errors)."""
    umount_cmd = strategy.build_umount_command()
    run_on_volume(umount_cmd, volume, resolved)
    lock_cmd = strategy.build_lock_command(LUKS_MAPPER_NAME)
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


class TestDetectDeviceUnlocked:
    def test_not_unlocked_when_closed(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
    ) -> None:
        """Mapper does not exist when LUKS is closed."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        assert not detect_device_unlocked(
            remote_encrypted_volume, LUKS_MAPPER_NAME, resolved
        )

    def test_unlocked_after_open(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Mapper exists after LUKS is opened."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        _unlock(remote_encrypted_volume, strategy, resolved, luks_uuid)
        try:
            assert detect_device_unlocked(
                remote_encrypted_volume, LUKS_MAPPER_NAME, resolved
            )
        finally:
            _lock(remote_encrypted_volume, strategy, resolved)

    def test_not_unlocked_after_close(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Mapper disappears after LUKS is closed."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        _unlock(remote_encrypted_volume, strategy, resolved, luks_uuid)
        _lock(remote_encrypted_volume, strategy, resolved)
        assert not detect_device_unlocked(
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
        _unlock(remote_encrypted_volume, strategy, resolved, luks_uuid)
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
