"""Tests for nbkp.scriptgen."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    DestinationSyncEndpoint,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    RsyncOptions,
    SshEndpoint,
    SshConnectionOptions,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.scriptgen import ScriptOptions, generate_script

_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)
_OPTIONS = ScriptOptions(config_path="/etc/nbkp/config.yaml")


def _local_to_local_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    sync = SyncConfig(
        slug="my-sync",
        source=SyncEndpoint(volume="src", subdir="photos"),
        destination=DestinationSyncEndpoint(volume="dst", subdir="backup"),
    )
    return Config(
        volumes={"src": src, "dst": dst},
        syncs={"my-sync": sync},
    )


def _local_to_remote_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/data")
    server = SshEndpoint(
        slug="nas",
        host="nas.example.com",
        port=5022,
        user="backup",
        key="~/.ssh/nas_ed25519",
    )
    dst = RemoteVolume(
        slug="nas-vol",
        ssh_endpoint="nas",
        path="/volume1/backups",
    )
    sync = SyncConfig(
        slug="photos-to-nas",
        source=SyncEndpoint(volume="src", subdir="photos"),
        destination=DestinationSyncEndpoint(
            volume="nas-vol", subdir="photos-backup"
        ),
    )
    return Config(
        ssh_endpoints={"nas": server},
        volumes={"src": src, "nas-vol": dst},
        syncs={"photos-to-nas": sync},
    )


def _remote_to_local_config() -> Config:
    server = SshEndpoint(
        slug="remote",
        host="remote.example.com",
        user="admin",
    )
    src = RemoteVolume(
        slug="remote-vol",
        ssh_endpoint="remote",
        path="/data",
    )
    dst = LocalVolume(slug="local", path="/mnt/backup")
    sync = SyncConfig(
        slug="pull-data",
        source=SyncEndpoint(volume="remote-vol"),
        destination=DestinationSyncEndpoint(volume="local"),
    )
    return Config(
        ssh_endpoints={"remote": server},
        volumes={"remote-vol": src, "local": dst},
        syncs={"pull-data": sync},
    )


def _btrfs_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    sync = SyncConfig(
        slug="btrfs-sync",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(
            volume="dst",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=5),
        ),
    )
    return Config(
        volumes={"src": src, "dst": dst},
        syncs={"btrfs-sync": sync},
    )


def _btrfs_no_prune_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    sync = SyncConfig(
        slug="btrfs-sync",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(
            volume="dst",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        ),
    )
    return Config(
        volumes={"src": src, "dst": dst},
        syncs={"btrfs-sync": sync},
    )


def _disabled_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    sync = SyncConfig(
        slug="disabled-sync",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(volume="dst"),
        enabled=False,
    )
    return Config(
        volumes={"src": src, "dst": dst},
        syncs={"disabled-sync": sync},
    )


def _filters_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    sync = SyncConfig(
        slug="filtered-sync",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(volume="dst"),
        filters=["+ *.jpg", "- *.tmp", "H .git"],
        filter_file="~/.config/nbkp/filters.rules",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        syncs={"filtered-sync": sync},
    )


def _proxy_jump_config() -> Config:
    bastion = SshEndpoint(
        slug="bastion",
        host="bastion.example.com",
        user="admin",
    )
    nas = SshEndpoint(
        slug="nas",
        host="nas.internal",
        port=5022,
        user="backup",
        proxy_jump="bastion",
    )
    src = LocalVolume(slug="src", path="/mnt/data")
    dst = RemoteVolume(
        slug="nas-vol",
        ssh_endpoint="nas",
        path="/volume1",
    )
    sync = SyncConfig(
        slug="proxy-sync",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(volume="nas-vol"),
    )
    return Config(
        ssh_endpoints={"bastion": bastion, "nas": nas},
        volumes={"src": src, "nas-vol": dst},
        syncs={"proxy-sync": sync},
    )


def _proxy_jumps_config() -> Config:
    bastion1 = SshEndpoint(
        slug="bastion1",
        host="bastion1.example.com",
        user="admin",
    )
    bastion2 = SshEndpoint(
        slug="bastion2",
        host="bastion2.example.com",
        port=2222,
    )
    nas = SshEndpoint(
        slug="nas",
        host="nas.internal",
        port=5022,
        user="backup",
        proxy_jumps=["bastion1", "bastion2"],
    )
    src = LocalVolume(slug="src", path="/mnt/data")
    dst = RemoteVolume(
        slug="nas-vol",
        ssh_endpoint="nas",
        path="/volume1",
    )
    sync = SyncConfig(
        slug="proxy-jumps-sync",
        source=SyncEndpoint(volume="src"),
        destination=DestinationSyncEndpoint(volume="nas-vol"),
    )
    return Config(
        ssh_endpoints={
            "bastion1": bastion1,
            "bastion2": bastion2,
            "nas": nas,
        },
        volumes={"src": src, "nas-vol": dst},
        syncs={"proxy-jumps-sync": sync},
    )


class TestHeader:
    def test_shebang(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert script.startswith("#!/bin/bash\n")

    def test_strict_mode(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "set -euo pipefail" in script
        assert "IFS=$'\\n\\t'" in script

    def test_config_path_in_header(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "# Config: /etc/nbkp/config.yaml" in script

    def test_timestamp_in_header(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "2026-02-21T12:00:00Z" in script

    def test_preserved_dropped_comments(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "# Preserved from nbkp run:" in script
        assert "# Dropped from nbkp run:" in script
        assert "rsync command variants" in script
        assert "Paramiko-only SSH options" in script

    def test_arg_parsing(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "NBKP_DRY_RUN=false" in script
        assert 'NBKP_PROGRESS=""' in script
        assert "-n|--dry-run" in script
        assert "NBKP_FAILURES=0" in script

    def test_nbkp_log_helper(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "nbkp_log()" in script

    def test_nbkp_log_dry_run_tag(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "[nbkp] [dry-run]" in script
        assert '"[nbkp] $*"' in script


class TestVolumeChecks:
    def test_local_volume_check(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "test -f /mnt/src/.nbkp-vol" in script
        assert "test -f /mnt/dst/.nbkp-vol" in script

    def test_remote_volume_check(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        # Remote volume check uses ssh
        assert "ssh" in script
        assert "/volume1/backups/.nbkp-vol" in script


class TestLocalToLocal:
    def test_rsync_command(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "rsync" in script
        assert "/mnt/src/photos/" in script
        assert "/mnt/dst/backup/" in script

    def test_function_name(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "sync_my_sync()" in script

    def test_sync_invocation(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert ("sync_my_sync" " || { NBKP_FAILED[sync_") in script


class TestLocalToRemote:
    def test_rsync_with_ssh(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        assert "rsync" in script
        assert "/mnt/data/photos/" in script
        assert "backup@nas.example.com:/volume1/backups" in script
        assert "-e" in script
        assert "-p" in script
        assert "5022" in script


class TestRemoteToLocal:
    def test_rsync_with_remote_source(self) -> None:
        config = _remote_to_local_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        assert "admin@remote.example.com:/data/" in script
        assert "/mnt/backup/" in script


class TestDisabledSync:
    def test_commented_out(self) -> None:
        config = _disabled_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "# : disabled" in script
        assert "# sync_disabled_sync()" in script

    def test_disabled_invocation_commented(self) -> None:
        config = _disabled_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "# sync_disabled_sync  # disabled" in script


class TestBtrfs:
    def test_no_link_dest(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "RSYNC_LINK_DEST" not in script

    def test_snapshot_creation(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "btrfs" in script
        assert "subvolume" in script
        assert "snapshot" in script
        assert "/mnt/dst/tmp" in script
        assert "NBKP_TS=$(date -u" in script

    def test_snapshot_guarded_by_dry_run(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert 'if [ "$NBKP_DRY_RUN" = false ]' in script

    def test_prune_with_max_snapshots(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "Prune old snapshots" in script
        assert "max: 5" in script
        assert "NBKP_EXCESS" in script
        assert "btrfs property set" in script
        assert "btrfs subvolume delete" in script
        # Latest-protection: prune block must read the latest symlink target
        assert "readlink" in script
        assert "NBKP_LATEST" in script

    def test_no_prune_without_max(self) -> None:
        config = _btrfs_no_prune_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "Prune old snapshots" not in script

    def test_tmp_and_snapshots_dir_checks(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "tmp/ directory not found" in script
        assert "snapshots/ directory not found" in script

    def test_symlink_update_after_snapshot(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "ln -sfn" in script
        assert "snapshots/$NBKP_TS" in script


class TestPreflightChecks:
    def test_source_sentinel(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert ".nbkp-src" in script
        assert "source sentinel" in script

    def test_destination_sentinel(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert ".nbkp-dst" in script
        assert "destination sentinel" in script

    def test_rsync_availability(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "command -v rsync" in script
        assert "rsync not found" in script

    def test_btrfs_availability(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "command -v btrfs" in script
        assert "btrfs not found" in script

    def test_remote_preflight_uses_ssh(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        # Remote destination checks should use ssh
        assert "ssh" in script
        assert ".nbkp-dst" in script


class TestDryRunAndProgress:
    def test_dry_run_flag_injected(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "RSYNC_DRY_RUN_FLAG" in script
        assert '${RSYNC_DRY_RUN_FLAG:+"$RSYNC_DRY_RUN_FLAG"}' in script

    def test_progress_flags_injected(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "RSYNC_PROGRESS_FLAGS" in script
        assert "$RSYNC_PROGRESS_FLAGS" in script

    def test_progress_modes(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "overall" in script
        assert "per-file" in script
        assert "--info=progress2" in script
        assert "--progress" in script


class TestFilters:
    def test_filter_args_present(self) -> None:
        config = _filters_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "--filter=+ *.jpg" in script
        assert "--filter=- *.tmp" in script
        assert "--filter=H .git" in script

    def test_filter_file_present(self) -> None:
        config = _filters_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "--filter=merge ~/.config/nbkp/filters.rules" in script


class TestProxyJump:
    def test_proxy_jump_in_ssh(self) -> None:
        config = _proxy_jump_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        assert "ProxyCommand=" in script
        assert "admin@bastion.example.com" in script

    def test_proxy_jumps_in_ssh(self) -> None:
        config = _proxy_jumps_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        assert "ProxyCommand=" in script
        assert "admin@bastion1.example.com" in script
        assert "bastion2.example.com" in script

    def test_proxy_jumps_valid_syntax(self) -> None:
        config = _proxy_jumps_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


class TestSshConnectionOptions:
    def test_connection_options_in_script(self) -> None:
        server = SshEndpoint(
            slug="nas",
            host="nas.example.com",
            user="backup",
            connection_options=SshConnectionOptions(
                compress=True,
                server_alive_interval=60,
                strict_host_key_checking=False,
                forward_agent=True,
            ),
        )
        src = LocalVolume(slug="src", path="/mnt/data")
        dst = RemoteVolume(
            slug="nas-vol",
            ssh_endpoint="nas",
            path="/backup",
        )
        sync = SyncConfig(
            slug="ssh-opts-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="nas-vol"),
        )
        config = Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "nas-vol": dst},
            syncs={"ssh-opts-sync": sync},
        )
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        assert "Compression=yes" in script
        assert "ServerAliveInterval=60" in script
        assert "StrictHostKeyChecking=no" in script
        assert "ForwardAgent=yes" in script


class TestShellValidity:
    def test_local_to_local_valid_syntax(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_local_to_remote_valid_syntax(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_remote_to_local_valid_syntax(self) -> None:
        config = _remote_to_local_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_btrfs_valid_syntax(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_disabled_valid_syntax(self) -> None:
        config = _disabled_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_filters_valid_syntax(self) -> None:
        config = _filters_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_proxy_jump_valid_syntax(self) -> None:
        config = _proxy_jump_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


class TestEdgeCases:
    def test_empty_config(self) -> None:
        config = Config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "#!/bin/bash" in script
        assert "All syncs completed successfully" in script
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_all_disabled(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        s1 = SyncConfig(
            slug="s-one",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
            enabled=False,
        )
        s2 = SyncConfig(
            slug="s-two",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
            enabled=False,
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            syncs={"s-one": s1, "s-two": s2},
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "# : disabled" in script
        assert "# sync_s_one" in script
        assert "# sync_s_two" in script
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_paths_with_spaces(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/my data")
        dst = LocalVolume(slug="dst", path="/mnt/my backup")
        sync = SyncConfig(
            slug="space-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            syncs={"space-sync": sync},
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        # Paths with spaces should be properly quoted
        assert "'/mnt/my data/'" in script
        assert "'/mnt/my backup/'" in script
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_no_config_path(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(config_path=None)
        script = generate_script(config, options, now=_NOW)
        assert "# Config: <stdin>" in script

    def test_custom_rsync_options(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="custom-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
            rsync_options=RsyncOptions(
                default_options_override=["-a", "--delete"],
                extra_options=["--bwlimit=1000", "--progress"],
            ),
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            syncs={"custom-sync": sync},
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "--bwlimit=1000" in script
        assert "--progress" in script

    def test_mixed_enabled_disabled(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        enabled = SyncConfig(
            slug="active-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
        )
        disabled = SyncConfig(
            slug="off-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(volume="dst"),
            enabled=False,
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            syncs={
                "active-sync": enabled,
                "off-sync": disabled,
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "sync_active_sync()" in script
        assert "# : disabled — off-sync" in script
        assert ("sync_active_sync" " || { NBKP_FAILED[sync_") in script
        assert "# sync_off_sync  # disabled" in script
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0


class TestSummary:
    def test_failure_count_and_exit(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert 'nbkp_log "$NBKP_FAILURES sync(s) failed"' in script
        assert "exit 1" in script
        assert "All syncs completed successfully" in script


class TestRemoteBtrfs:
    def test_remote_btrfs_snapshot(self) -> None:
        server = SshEndpoint(
            slug="nas",
            host="nas.example.com",
            user="backup",
        )
        src = LocalVolume(slug="src", path="/mnt/data")
        dst = RemoteVolume(
            slug="nas-vol",
            ssh_endpoint="nas",
            path="/volume1",
        )
        sync = SyncConfig(
            slug="remote-btrfs",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(
                volume="nas-vol",
                btrfs_snapshots=BtrfsSnapshotConfig(
                    enabled=True, max_snapshots=3
                ),
            ),
        )
        config = Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "nas-vol": dst},
            syncs={"remote-btrfs": sync},
        )
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config, _OPTIONS, now=_NOW, resolved_endpoints=resolved
        )
        # Btrfs commands should be wrapped in SSH
        assert "btrfs subvolume snapshot" in script
        assert "btrfs property set" in script
        assert "btrfs subvolume delete" in script
        assert "nas.example.com" in script
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


class TestHardLink:
    def _hl_config(self) -> Config:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="hl-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(
                volume="dst",
                hard_link_snapshots=HardLinkSnapshotConfig(
                    enabled=True, max_snapshots=5
                ),
            ),
        )
        return Config(
            volumes={"src": src, "dst": dst},
            syncs={"hl-sync": sync},
        )

    def _hl_no_prune_config(self) -> Config:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="hl-sync",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(
                volume="dst",
                hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
            ),
        )
        return Config(
            volumes={"src": src, "dst": dst},
            syncs={"hl-sync": sync},
        )

    def _hl_remote_config(self) -> Config:
        server = SshEndpoint(
            slug="nas",
            host="nas.example.com",
            user="backup",
        )
        src = LocalVolume(slug="src", path="/mnt/data")
        dst = RemoteVolume(
            slug="nas-vol",
            ssh_endpoint="nas",
            path="/volume1",
        )
        sync = SyncConfig(
            slug="hl-remote",
            source=SyncEndpoint(volume="src"),
            destination=DestinationSyncEndpoint(
                volume="nas-vol",
                hard_link_snapshots=HardLinkSnapshotConfig(
                    enabled=True, max_snapshots=3
                ),
            ),
        )
        return Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "nas-vol": dst},
            syncs={"hl-remote": sync},
        )

    def test_orphan_cleanup(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "readlink" in script
        assert "latest" in script

    def test_link_dest_resolution(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "RSYNC_LINK_DEST" in script
        assert "--link-dest" in script

    def test_mkdir_snapshot_dir(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "mkdir -p" in script
        assert "NBKP_TS" in script
        assert "/mnt/dst/snapshots/" in script

    def test_rsync_to_snapshot(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "/mnt/dst/snapshots/$NBKP_TS/" in script

    def test_symlink_update(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "ln -sfn" in script
        assert "snapshots/$NBKP_TS" in script

    def test_symlink_guarded_by_dry_run(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert 'if [ "$NBKP_DRY_RUN" = false ]' in script

    def test_prune_with_max_snapshots(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "Prune old snapshots" in script
        assert "max: 5" in script
        assert "rm -rf" in script

    def test_no_prune_without_max(self) -> None:
        config = self._hl_no_prune_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "Prune old snapshots" not in script

    def test_no_btrfs_commands(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "btrfs subvolume" not in script
        assert "btrfs property" not in script

    def test_snapshots_dir_preflight_check(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "snapshots/ directory not found" in script

    def test_valid_syntax(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_remote_valid_syntax(self) -> None:
        config = self._hl_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_remote_orphan_cleanup_uses_ssh(self) -> None:
        config = self._hl_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        assert "nas.example.com" in script
        assert "readlink" in script


class TestRelativePaths:
    """Tests for --relative-src / --relative-dst path handling."""

    def test_relative_dst_local_to_local(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        # Source stays absolute
        assert "/mnt/src/photos/" in script
        # Dest uses NBKP_SCRIPT_DIR-relative path
        assert "${NBKP_SCRIPT_DIR}" in script
        assert "/mnt/dst/backup/" not in script

    def test_relative_src_local_to_local(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/src/backup.sh",
            relative_src=True,
        )
        script = generate_script(config, options, now=_NOW)
        # Source uses NBKP_SCRIPT_DIR-relative path
        assert "${NBKP_SCRIPT_DIR}" in script
        # Dest stays absolute
        assert "/mnt/dst/backup/" in script

    def test_relative_both(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/backup.sh",
            relative_src=True,
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        assert "${NBKP_SCRIPT_DIR}" in script
        # Both absolute paths replaced
        assert "/mnt/src/photos/" not in script
        assert "/mnt/dst/backup/" not in script

    def test_relative_src_local_to_remote(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/data/backup.sh",
            relative_src=True,
        )
        script = generate_script(
            config, options, now=_NOW, resolved_endpoints=resolved
        )
        # Local source relativized
        assert "${NBKP_SCRIPT_DIR}" in script
        # Remote dest stays absolute
        assert "nas.example.com" in script

    def test_relative_dst_remote_to_local(self) -> None:
        config = _remote_to_local_config()
        resolved = resolve_all_endpoints(config)
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/backup/backup.sh",
            relative_dst=True,
        )
        script = generate_script(
            config, options, now=_NOW, resolved_endpoints=resolved
        )
        # Local dest relativized
        assert "${NBKP_SCRIPT_DIR}" in script
        # Remote source stays absolute
        assert "admin@remote.example.com" in script

    def test_script_dir_in_header(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        assert "NBKP_SCRIPT_DIR=" in script
        assert "BASH_SOURCE[0]" in script

    def test_no_script_dir_when_absolute(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "NBKP_SCRIPT_DIR" not in script

    def test_relative_shell_valid(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_relative_both_shell_valid(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/backup.sh",
            relative_src=True,
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_relative_btrfs(self) -> None:
        config = _btrfs_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        assert "${NBKP_SCRIPT_DIR}" in script
        # Btrfs snapshot uses relative dest paths
        assert "btrfs" in script
        assert "subvolume" in script
        assert "snapshot" in script
        assert "${NBKP_SCRIPT_DIR}" in script
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"

    def test_relative_volume_checks(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        # Dst volume check should use relative path
        assert "${NBKP_SCRIPT_DIR}" in script
        # Src volume check stays absolute
        assert "test -f /mnt/src/.nbkp-vol" in script

    def test_relative_preflight_checks(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        # Source preflight stays absolute
        assert "/mnt/src/photos/.nbkp-src" in script
        # Dest preflight uses relative
        assert ".nbkp-dst" in script


class TestDependencyGuard:
    def test_dependent_sync_has_guard(self) -> None:
        """A chain sync should have a dependency guard."""
        src = LocalVolume(slug="src", path="/src")
        mid = LocalVolume(slug="mid", path="/mid")
        dst = LocalVolume(slug="dst", path="/dst")
        config = Config(
            volumes={"src": src, "mid": mid, "dst": dst},
            syncs={
                "step-1": SyncConfig(
                    slug="step-1",
                    source=SyncEndpoint(volume="src"),
                    destination=DestinationSyncEndpoint(volume="mid"),
                ),
                "step-2": SyncConfig(
                    slug="step-2",
                    source=SyncEndpoint(volume="mid"),
                    destination=DestinationSyncEndpoint(volume="dst"),
                ),
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        # step-1 has no guard (no predecessors)
        assert "NBKP_FAILED[sync_step_1]" in script
        # step-2 has a dependency guard checking step-1
        assert "NBKP_FAILED[sync_step_1]+set" in script
        assert "CANCELLED step-2" in script

    def test_independent_syncs_no_guard(self) -> None:
        """Independent syncs should not have guards."""
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "CANCELLED" not in script

    def test_dependency_guard_valid_syntax(self) -> None:
        """Generated script with guards must pass bash -n."""
        src = LocalVolume(slug="src", path="/src")
        mid = LocalVolume(slug="mid", path="/mid")
        dst = LocalVolume(slug="dst", path="/dst")
        config = Config(
            volumes={"src": src, "mid": mid, "dst": dst},
            syncs={
                "step-1": SyncConfig(
                    slug="step-1",
                    source=SyncEndpoint(volume="src"),
                    destination=DestinationSyncEndpoint(volume="mid"),
                ),
                "step-2": SyncConfig(
                    slug="step-2",
                    source=SyncEndpoint(volume="mid"),
                    destination=DestinationSyncEndpoint(volume="dst"),
                ),
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"
