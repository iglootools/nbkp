"""Integration tests: volume and sync checks."""

from __future__ import annotations

from pathlib import Path

from nbkp.preflight import (
    DestinationEndpointError,
    check_sync,
    check_volume,
)
from nbkp.preflight.snapshot_checks import (
    check_btrfs_filesystem,
    check_btrfs_subvolume,
)
from nbkp.preflight.volume_checks import observe_ssh_endpoint, observe_volume
from nbkp.preflight.endpoint_checks import (
    observe_source_endpoint,
    observe_destination_endpoint,
)
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.remote.testkit.docker import (
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_PATH,
    REMOTE_BTRFS_ENCRYPTED_PATH,
)
from nbkp.sync.testkit.seed import create_seed_sentinels
from nbkp.disks.lifecycle import mount_volume, umount_volume

from tests._docker_fixtures import (
    create_sentinels,
    ssh_exec,
    resolved_endpoints_for,
    direct_strategy_for,
    LUKS_PASSPHRASE,
)


class TestLocalVolumeCheck:
    def test_local_volume_active(self, tmp_path: Path) -> None:
        vol_path = tmp_path / "vol"
        vol_path.mkdir()
        (vol_path / ".nbkp-vol").touch()

        vol = LocalVolume(slug="local", path=str(vol_path))
        status = check_volume(vol)
        assert status.active is True

    def test_local_volume_inactive(self, tmp_path: Path) -> None:
        vol_path = tmp_path / "vol"
        vol_path.mkdir()
        # No .nbkp-vol sentinel

        vol = LocalVolume(slug="local", path=str(vol_path))
        status = check_volume(vol)
        assert status.active is False


class TestRemoteVolumeCheck:
    def test_remote_volume_active(
        self,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        create_sentinels(docker_ssh_endpoint, REMOTE_BACKUP_PATH, [".nbkp-vol"])
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"test-remote": docker_remote_volume},
        )
        resolved = resolve_all_endpoints(config)
        status = check_volume(docker_remote_volume, resolved)
        assert status.active is True

    def test_remote_volume_inactive(
        self,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        # No sentinel created
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"test-remote": docker_remote_volume},
        )
        resolved = resolve_all_endpoints(config)
        status = check_volume(docker_remote_volume, resolved)
        assert status.active is False


class TestSyncCheck:
    def test_sync_status_active(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        src_path = tmp_path / "src"
        src_vol = LocalVolume(slug="src", path=str(src_path))
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"src": src_vol, "dst": docker_remote_volume},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "test-sync": SyncConfig(
                    slug="test-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        sync = config.syncs["test-sync"]

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)

        status = check_sync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert status.active is True
        assert status.errors == []


class TestBtrfsFilesystemCheck:
    def test_btrfs_path_detected(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"btrfs": remote_btrfs_volume},
        )
        resolved = resolve_all_endpoints(config)
        assert check_btrfs_filesystem(remote_btrfs_volume, resolved) is True

    def test_non_btrfs_path_detected(
        self,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"data": docker_remote_volume},
        )
        resolved = resolve_all_endpoints(config)
        assert check_btrfs_filesystem(docker_remote_volume, resolved) is False


class TestBtrfsSubvolumeCheck:
    def test_subvolume_detected(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        ssh_exec(
            docker_ssh_endpoint,
            f"btrfs subvolume create {REMOTE_BTRFS_PATH}/test-subvol",
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"btrfs": remote_btrfs_volume},
        )
        resolved = resolve_all_endpoints(config)
        assert (
            check_btrfs_subvolume(
                remote_btrfs_volume,
                "test-subvol",
                resolved,
            )
            is True
        )

        # Cleanup
        ssh_exec(
            docker_ssh_endpoint,
            f"btrfs subvolume delete {REMOTE_BTRFS_PATH}/test-subvol",
        )

    def test_regular_dir_not_subvolume(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        ssh_exec(
            docker_ssh_endpoint,
            f"mkdir -p {REMOTE_BTRFS_PATH}/regular-dir",
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"btrfs": remote_btrfs_volume},
        )
        resolved = resolve_all_endpoints(config)
        assert (
            check_btrfs_subvolume(
                remote_btrfs_volume,
                "regular-dir",
                resolved,
            )
            is False
        )

        # Cleanup
        ssh_exec(
            docker_ssh_endpoint,
            f"rm -rf {REMOTE_BTRFS_PATH}/regular-dir",
        )


class TestSyncCheckBtrfs:
    def test_sync_inactive_when_not_subvolume(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        # Create a regular directory with staging/ (not a subvolume)
        ssh_exec(
            docker_ssh_endpoint,
            f"mkdir -p {REMOTE_BTRFS_PATH}/not-a-subvol/staging",
        )
        create_sentinels(
            docker_ssh_endpoint,
            REMOTE_BTRFS_PATH,
            [".nbkp-vol"],
        )
        create_sentinels(
            docker_ssh_endpoint,
            f"{REMOTE_BTRFS_PATH}/not-a-subvol",
            [".nbkp-dst"],
        )

        src_path = tmp_path / "src"
        src_path.mkdir()
        (src_path / ".nbkp-vol").touch()
        (src_path / ".nbkp-src").touch()

        src_vol = LocalVolume(slug="src", path=str(src_path))
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={
                "src": src_vol,
                "dst": remote_btrfs_volume,
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="dst",
                    subdir="not-a-subvol",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={
                "test-sync": SyncConfig(
                    slug="test-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        sync = config.syncs["test-sync"]

        resolved = resolve_all_endpoints(config)

        status = check_sync(
            sync,
            config,
            resolved_endpoints=resolved,
        )
        assert status.active is False
        assert (
            DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME
            in status.destination_endpoint_status.errors
        )

        # Cleanup
        ssh_exec(
            docker_ssh_endpoint,
            f"rm -rf {REMOTE_BTRFS_PATH}/not-a-subvol",
        )


class TestObserveRemoteVolume:
    def test_observe_active_volume_capabilities(
        self,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        create_sentinels(docker_ssh_endpoint, REMOTE_BACKUP_PATH, [".nbkp-vol"])
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"test-remote": docker_remote_volume},
        )
        resolved = resolve_all_endpoints(config)

        ssh_diag = observe_ssh_endpoint(docker_remote_volume, resolved)
        assert ssh_diag.host_tools is not None
        assert ssh_diag.host_tools.has_rsync is True
        assert ssh_diag.host_tools.rsync_version_ok is True
        assert ssh_diag.host_tools.has_stat is True

        diag = observe_volume(
            docker_remote_volume,
            host_tools=ssh_diag.host_tools,
            mount_tools=ssh_diag.mount_tools,
            resolved_endpoints=resolved,
        )

        assert diag.capabilities is not None
        assert diag.capabilities.sentinel_exists is True
        assert diag.capabilities.is_btrfs_filesystem is False
        assert diag.capabilities.hardlink_supported is True
        assert diag.capabilities.mount is None

    def test_observe_btrfs_volume_capabilities(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_btrfs_volume: RemoteVolume,
    ) -> None:
        create_sentinels(docker_ssh_endpoint, REMOTE_BTRFS_PATH, [".nbkp-vol"])
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"test-btrfs": remote_btrfs_volume},
        )
        resolved = resolve_all_endpoints(config)

        ssh_diag = observe_ssh_endpoint(remote_btrfs_volume, resolved)
        assert ssh_diag.host_tools is not None

        diag = observe_volume(
            remote_btrfs_volume,
            host_tools=ssh_diag.host_tools,
            mount_tools=ssh_diag.mount_tools,
            resolved_endpoints=resolved,
        )

        assert diag.capabilities is not None
        assert diag.capabilities.is_btrfs_filesystem is True
        assert diag.capabilities.btrfs_user_subvol_rm is True

    def test_observe_missing_sentinel_gives_safe_defaults(
        self,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        # No sentinel created
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"test-remote": docker_remote_volume},
        )
        resolved = resolve_all_endpoints(config)

        ssh_diag = observe_ssh_endpoint(docker_remote_volume, resolved)
        assert ssh_diag.host_tools is not None

        diag = observe_volume(
            docker_remote_volume,
            host_tools=ssh_diag.host_tools,
            mount_tools=ssh_diag.mount_tools,
            resolved_endpoints=resolved,
        )

        assert diag.capabilities is not None
        assert diag.capabilities.sentinel_exists is False


class TestObserveEndpoints:
    def test_observe_source_endpoint_with_snapshots(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        hl = HardLinkSnapshotConfig(enabled=True)
        src_vol = LocalVolume(slug="src", path=str(tmp_path / "src"))
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"src": src_vol, "dst": docker_remote_volume},
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="src",
                    hard_link_snapshots=hl,
                ),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="dst",
                    hard_link_snapshots=hl,
                ),
            },
            syncs={
                "test-sync": SyncConfig(
                    slug="test-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)
        resolved = resolve_all_endpoints(config)

        src_endpoint = config.sync_endpoints["ep-src"]
        # Observe SSH endpoint + volume capabilities (needed by endpoint checks)
        ssh_diag = observe_ssh_endpoint(src_vol)
        assert ssh_diag.host_tools is not None
        vol_diag = observe_volume(
            src_vol, host_tools=ssh_diag.host_tools, mount_tools=ssh_diag.mount_tools
        )
        assert vol_diag.capabilities is not None

        diag = observe_source_endpoint(
            src_endpoint,
            src_vol,
            vol_diag.capabilities,
            resolved,
            host_tools=ssh_diag.host_tools,
        )

        assert diag.sentinel_exists is True
        assert diag.snapshot_dirs is not None
        assert diag.snapshot_dirs.exists is True
        assert diag.latest is not None
        assert diag.latest.exists is True
        assert diag.latest.raw_target == "/dev/null"

    def test_observe_destination_endpoint_with_snapshots(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        hl = HardLinkSnapshotConfig(enabled=True)
        src_vol = LocalVolume(slug="src", path=str(tmp_path / "src"))
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"src": src_vol, "dst": docker_remote_volume},
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="src",
                    hard_link_snapshots=hl,
                ),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="dst",
                    hard_link_snapshots=hl,
                ),
            },
            syncs={
                "test-sync": SyncConfig(
                    slug="test-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)
        resolved = resolve_all_endpoints(config)

        dst_endpoint = config.sync_endpoints["ep-dst"]
        dst_vol = config.volumes[dst_endpoint.volume]
        # Observe SSH endpoint + volume capabilities (needed by endpoint checks)
        ssh_diag = observe_ssh_endpoint(dst_vol, resolved)
        assert ssh_diag.host_tools is not None
        vol_diag = observe_volume(
            dst_vol,
            host_tools=ssh_diag.host_tools,
            mount_tools=ssh_diag.mount_tools,
            resolved_endpoints=resolved,
        )
        assert vol_diag.capabilities is not None

        diag = observe_destination_endpoint(
            dst_endpoint,
            dst_vol,
            vol_diag.capabilities,
            resolved,
            host_tools=ssh_diag.host_tools,
        )

        assert diag.sentinel_exists is True
        assert diag.endpoint_writable is True
        assert diag.snapshot_dirs is not None
        assert diag.snapshot_dirs.exists is True
        assert diag.snapshot_dirs.writable is True
        assert diag.latest is not None
        assert diag.latest.exists is True


class TestMountCapabilities:
    def test_direct_mount_capabilities_encrypted(
        self,
        docker_ssh_endpoint: SshEndpoint,
        remote_encrypted_volume: RemoteVolume,
        luks_uuid: str,
    ) -> None:
        """Mount capabilities are probed for an encrypted volume after mounting."""
        resolved = resolved_endpoints_for(docker_ssh_endpoint, remote_encrypted_volume)
        strategy = direct_strategy_for(remote_encrypted_volume)
        mount_config = remote_encrypted_volume.mount
        assert mount_config is not None

        # Mount the volume so sentinel can be created on the btrfs filesystem
        result = mount_volume(
            remote_encrypted_volume,
            mount_config,
            resolved,
            lambda _: LUKS_PASSPHRASE,
            strategy,
        )
        assert result.success, result.detail

        try:
            # Create sentinel on the mounted encrypted volume
            create_sentinels(
                docker_ssh_endpoint,
                REMOTE_BTRFS_ENCRYPTED_PATH,
                [".nbkp-vol"],
            )

            config = Config(
                ssh_endpoints={"test-server": docker_ssh_endpoint},
                volumes={"test-encrypted": remote_encrypted_volume},
            )
            resolved = resolve_all_endpoints(config)

            ssh_diag = observe_ssh_endpoint(
                remote_encrypted_volume, resolved, probe_mount_tools=True
            )
            assert ssh_diag.host_tools is not None
            assert ssh_diag.mount_tools is not None
            assert ssh_diag.mount_tools.has_sudo is True
            assert ssh_diag.mount_tools.has_mount_cmd is True
            assert ssh_diag.mount_tools.has_umount_cmd is True
            assert ssh_diag.mount_tools.has_mountpoint is True
            assert ssh_diag.mount_tools.has_cryptsetup is True

            diag = observe_volume(
                remote_encrypted_volume,
                host_tools=ssh_diag.host_tools,
                mount_tools=ssh_diag.mount_tools,
                resolved_endpoints=resolved,
            )

            assert diag.capabilities is not None
            caps = diag.capabilities
            assert caps.mount is not None
            assert caps.mount.resolved_backend == "direct"
            assert caps.mount.device_present is True
            assert caps.mount.luks_attached is True
            assert caps.mount.mounted is True
        finally:
            umount_volume(remote_encrypted_volume, mount_config, resolved, strategy)

    def test_direct_mount_capabilities_device_not_present(
        self,
        docker_ssh_endpoint: SshEndpoint,
        luks_uuid: str,
    ) -> None:
        """Mount capabilities report device_present=False for a fake UUID."""
        volume = RemoteVolume(
            slug="test-fake-device",
            ssh_endpoint="test-server",
            path=REMOTE_BTRFS_ENCRYPTED_PATH,
            mount=MountConfig(
                strategy="direct",
                device_uuid="00000000-0000-0000-0000-000000000000",
                encryption=LuksEncryptionConfig(
                    mapper_name="nonexistent-mapper",
                    passphrase_id="test-luks",
                ),
            ),
        )
        config = Config(
            ssh_endpoints={"test-server": docker_ssh_endpoint},
            volumes={"test-fake-device": volume},
        )
        resolved = resolve_all_endpoints(config)

        ssh_diag = observe_ssh_endpoint(volume, resolved, probe_mount_tools=True)
        assert ssh_diag.host_tools is not None

        diag = observe_volume(
            volume,
            host_tools=ssh_diag.host_tools,
            mount_tools=ssh_diag.mount_tools,
            resolved_endpoints=resolved,
        )

        # Volume is not mounted, so sentinel won't exist — but mount
        # capabilities are still probed via _sentinel_only_capabilities.
        assert diag.capabilities is not None
        assert diag.capabilities.mount is not None
        assert diag.capabilities.mount.device_present is False
