import subprocess
from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent, indent

import pytest

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    MountConfig,
    RemoteVolume,
    RsyncOptions,
    SshConnectionOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.remote.endpoints import EndpointFilter
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.sh import ScriptOptions, VolumeWithoutPathError, generate_script

_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=UTC)
_OPTIONS = ScriptOptions(platform="linux", config_path="/etc/nbkp/config.yaml")


def _bash_n(script: str) -> None:
    result = subprocess.run(
        ["bash", "-n"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


def _local_to_local_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="photos"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir="backup"),
        },
        syncs={
            "my-sync": SyncConfig(
                slug="my-sync",
                source="ep-src",
                destination="ep-dst",
            ),
        },
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
    return Config(
        ssh_endpoints={"nas": server},
        volumes={"src": src, "nas-vol": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="photos"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="nas-vol",
                subdir="photos-backup",
            ),
        },
        syncs={
            "photos-to-nas": SyncConfig(
                slug="photos-to-nas",
                source="ep-src",
                destination="ep-dst",
            ),
        },
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
    return Config(
        ssh_endpoints={"remote": server},
        volumes={"remote-vol": src, "local": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="remote-vol"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="local"),
        },
        syncs={
            "pull-data": SyncConfig(
                slug="pull-data",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )


def _btrfs_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=5),
            ),
        },
        syncs={
            "btrfs-sync": SyncConfig(
                slug="btrfs-sync",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )


def _btrfs_no_prune_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={
            "btrfs-sync": SyncConfig(
                slug="btrfs-sync",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )


def _btrfs_chain_config() -> Config:
    """A->B chain where B's source has btrfs snapshots."""
    src = LocalVolume(slug="src", path="/mnt/src")
    mid = LocalVolume(slug="mid", path="/mnt/mid")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    return Config(
        volumes={"src": src, "mid": mid, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-mid": SyncEndpoint(
                slug="ep-mid",
                volume="mid",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=5),
            ),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=5),
            ),
        },
        syncs={
            "step-1": SyncConfig(
                slug="step-1",
                source="ep-src",
                destination="ep-mid",
            ),
            "step-2": SyncConfig(
                slug="step-2",
                source="ep-mid",
                destination="ep-dst",
            ),
        },
    )


def _disabled_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
        },
        syncs={
            "disabled-sync": SyncConfig(
                slug="disabled-sync",
                source="ep-src",
                destination="ep-dst",
                enabled=False,
            ),
        },
    )


def _filters_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
        },
        syncs={
            "filtered-sync": SyncConfig(
                slug="filtered-sync",
                source="ep-src",
                destination="ep-dst",
                filters=["+ *.jpg", "- *.tmp", "H .git"],
                filter_file="~/.config/nbkp/filters.rules",
            ),
        },
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
    return Config(
        ssh_endpoints={"bastion": bastion, "nas": nas},
        volumes={"src": src, "nas-vol": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="nas-vol"),
        },
        syncs={
            "proxy-sync": SyncConfig(
                slug="proxy-sync",
                source="ep-src",
                destination="ep-dst",
            ),
        },
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
    return Config(
        ssh_endpoints={
            "bastion1": bastion1,
            "bastion2": bastion2,
            "nas": nas,
        },
        volumes={"src": src, "nas-vol": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="nas-vol"),
        },
        syncs={
            "proxy-jumps-sync": SyncConfig(
                slug="proxy-jumps-sync",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )


class TestHeader:
    def test_script_starts_with_bash_shebang(self) -> None:
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
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
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
        assert 'nbkp_run_sync my-sync sync_my_sync "$NBKP_PF_sync_my_sync"\n' in script


class TestLocalToRemote:
    def test_rsync_with_ssh(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
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
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        assert "admin@remote.example.com:/data/" in script
        assert "/mnt/backup/" in script


def _location_excluded_script(*, enabled: bool = True) -> str:
    """Script for a pull from a volume whose only endpoint is at excluded ``home``."""
    config = _remote_to_local_config()
    server = config.ssh_endpoints["remote"].model_copy(update={"location": "home"})
    sync = config.syncs["pull-data"].model_copy(update={"enabled": enabled})
    config = config.model_copy(
        update={"ssh_endpoints": {"remote": server}, "syncs": {"pull-data": sync}},
    )
    resolved = resolve_all_endpoints(
        config,
        EndpointFilter(exclude_locations=["home"]),
    )
    return generate_script(config, _OPTIONS, now=_NOW, resolved_endpoints=resolved)


def _run_script(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-s", "--", *args],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )


class TestLocationExcluded:
    def test_sync_is_rendered_inactive(self) -> None:
        script = _location_excluded_script()
        assert "# : location-excluded — pull-data: volume remote-vol:" in script
        assert "remote.example.com" not in script
        assert "sync_pull_data()" not in script
        assert (
            'nbkp_run_sync pull-data sync_pull_data "$NBKP_PF_sync_pull_data"\n'
        ) in script
        _bash_n(script)

    def test_sync_is_skipped_as_inactive(self) -> None:
        result = _run_script(_location_excluded_script(), "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "INACTIVE: volume remote-vol: all SSH endpoints" in result.stderr
        assert "SKIPPED pull-data: inactive" in result.stderr

    def test_ignore_none_fails_on_it(self) -> None:
        result = _run_script(
            _location_excluded_script(),
            "--dry-run",
            "--strictness",
            "ignore-none",
        )
        assert result.returncode == 1

    def test_disabled_sync_is_not_rendered(self) -> None:
        script = _location_excluded_script(enabled=False)
        assert (
            "# : disabled — pull-data (not rendered: volume remote-vol: all SSH"
            in script
        )
        assert "remote.example.com" not in script
        _bash_n(script)


class TestArguments:
    """The generated script's own options and exit codes match ``nbkp run``'s."""

    @pytest.mark.parametrize("flag", ["-h", "--help"])
    def test_help_flag_prints_usage(self, flag: str) -> None:
        result = _run_script(generate_script(Config(), _OPTIONS, now=_NOW), flag)
        assert result.returncode == 0, result.stderr
        assert "Usage: " in result.stdout
        assert "--strictness LEVEL" in result.stdout

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["--nope"], "unknown option: --nope"),
            (["--strictness", "strict"], "unknown strictness: strict"),
            (["--progress=bars"], "unknown progress mode: bars"),
            (["--progress"], "option --progress requires a value"),
            (["-n", "-S"], "option -S requires a value"),
        ],
    )
    def test_usage_error_exits_2(self, args: list[str], message: str) -> None:
        result = _run_script(generate_script(Config(), _OPTIONS, now=_NOW), *args)
        assert result.returncode == 2
        assert f"Error: {message}" in result.stderr
        assert "--help" in result.stderr


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


_SYMLINK_STEP = dedent("""\
    # Update latest symlink (skip if dry-run)
    if [ "$NBKP_DRY_RUN" = false ]; then
        ln -sfn "snapshots/${NBKP_TS}" /mnt/dst/latest
    fi
""")
"""The whole step: ``snapshots/${NBKP_TS}`` and the dry-run guard also
appear in other steps, so matching them alone could not fail."""


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
        assert "/mnt/dst/staging" in script
        assert "NBKP_TS=$(date -u" in script

    def test_snapshot_guarded_by_dry_run(self) -> None:
        """The whole step: the guard also appears in other steps."""
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        step = dedent("""\
            # Btrfs snapshot (skip if dry-run)
            if [ "$NBKP_DRY_RUN" = false ]; then
                NBKP_TS=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
                NBKP_LATEST_LINK=$(readlink /mnt/dst/latest 2>/dev/null || true)
                if [ -n "$NBKP_LATEST_LINK" ] && [ "$NBKP_LATEST_LINK" != "/dev/null" ]; then
                    NBKP_LATEST_NAME="${NBKP_LATEST_LINK##*/}"
                    if ! nbkp_snapshot_after "$NBKP_TS" "$NBKP_LATEST_NAME"; then
                        nbkp_log "ERROR: new snapshot $NBKP_TS would not be after the latest snapshot $NBKP_LATEST_NAME; is the clock behind?"
                        return 1
                    fi
                fi
                btrfs subvolume snapshot -r /mnt/dst/staging "/mnt/dst/snapshots/${NBKP_TS}"
            fi
        """)  # noqa: E501 -- the generated script, line for line
        assert indent(step, "    ") in script

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
        assert "staging/ directory not found" in script
        assert "snapshots/ directory not found" in script

    def test_symlink_update_after_snapshot_guarded_by_dry_run(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert indent(_SYMLINK_STEP, "    ") in script
        assert script.index("# Btrfs snapshot") < script.index(
            "# Update latest symlink",
        )


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

    def test_source_latest_symlink_check(self) -> None:
        """Source latest check uses test -L (symlink), not -d."""
        config = _btrfs_chain_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "test -L" in script
        assert "latest symlink not found" in script

    def test_remote_preflight_uses_ssh(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
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
        assert '${RSYNC_PROGRESS_FLAGS[@]+"${RSYNC_PROGRESS_FLAGS[@]}"}' in script

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
        expanded = str(Path("~/.config/nbkp/filters.rules").expanduser())
        assert f"--filter=merge {expanded}" in script


class TestProxyJump:
    def test_proxy_jump_in_ssh(self) -> None:
        config = _proxy_jump_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
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

    def test_bastion_own_proxy_jump_in_ssh(self) -> None:
        base = _proxy_jump_config()
        config = base.model_copy(
            update={
                "ssh_endpoints": {
                    **base.ssh_endpoints,
                    "outer": SshEndpoint(slug="outer", host="outer.example.com"),
                    "bastion": base.ssh_endpoints["bastion"].model_copy(
                        update={"proxy_jump": "outer"},
                    ),
                },
            },
        )
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        assert "admin@bastion.example.com" in script
        assert "outer.example.com" in script
        _bash_n(script)

    def test_proxy_jumps_valid_syntax(self) -> None:
        config = _proxy_jumps_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        _bash_n(script)


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
        config = Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "nas-vol": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="nas-vol"),
            },
            syncs={
                "ssh-opts-sync": SyncConfig(
                    slug="ssh-opts-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        assert "Compression=yes" in script
        assert "ServerAliveInterval=60" in script
        assert "StrictHostKeyChecking=no" in script
        assert "ForwardAgent=yes" in script


class TestShellValidity:
    def test_local_to_local_valid_syntax(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        _bash_n(script)

    def test_local_to_remote_valid_syntax(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        _bash_n(script)

    def test_remote_to_local_valid_syntax(self) -> None:
        config = _remote_to_local_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        _bash_n(script)

    def test_btrfs_valid_syntax(self) -> None:
        config = _btrfs_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        _bash_n(script)

    def test_disabled_valid_syntax(self) -> None:
        config = _disabled_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        _bash_n(script)

    def test_filters_valid_syntax(self) -> None:
        config = _filters_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        _bash_n(script)

    def test_proxy_jump_valid_syntax(self) -> None:
        config = _proxy_jump_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        _bash_n(script)


def mount_managed_config() -> Config:
    """Build a sync onto a mount-managed volume whose mountpoint is discovered."""
    config = _local_to_local_config()
    dst = LocalVolume(
        slug="dst",
        mount=MountConfig(device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6"),
    )
    return config.model_copy(update={"volumes": {**config.volumes, "dst": dst}})


class TestEdgeCases:
    def test_volume_without_path_is_refused(self) -> None:
        with pytest.raises(
            VolumeWithoutPathError,
            check=lambda e: e.volume_slug == "dst",
        ):
            generate_script(mount_managed_config(), _OPTIONS, now=_NOW)

    def test_unused_volume_without_path_is_ignored(self) -> None:
        config = _local_to_local_config()
        spare = LocalVolume(
            slug="spare",
            mount=MountConfig(device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6"),
        )
        config = config.model_copy(
            update={"volumes": {**config.volumes, "spare": spare}},
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "sync_my_sync()" in script
        _bash_n(script)

    def test_disabled_sync_on_volume_without_path_is_not_rendered(self) -> None:
        config = mount_managed_config()
        sync = config.syncs["my-sync"].model_copy(update={"enabled": False})
        config = config.model_copy(update={"syncs": {"my-sync": sync}})
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "# : disabled — my-sync (not rendered: volume dst has no 'path')" in (
            script
        )
        assert "# sync_my_sync  # disabled" in script
        _bash_n(script)

    def test_empty_config(self) -> None:
        config = Config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "#!/bin/bash" in script
        assert "All syncs completed successfully" in script
        _bash_n(script)

    def test_all_disabled(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst1": SyncEndpoint(slug="ep-dst1", volume="dst", subdir="one"),
                "ep-dst2": SyncEndpoint(slug="ep-dst2", volume="dst", subdir="two"),
            },
            syncs={
                "s-one": SyncConfig(
                    slug="s-one",
                    source="ep-src",
                    destination="ep-dst1",
                    enabled=False,
                ),
                "s-two": SyncConfig(
                    slug="s-two",
                    source="ep-src",
                    destination="ep-dst2",
                    enabled=False,
                ),
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "# : disabled" in script
        assert "# sync_s_one" in script
        assert "# sync_s_two" in script
        _bash_n(script)

    def test_paths_with_spaces(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/my data")
        dst = LocalVolume(slug="dst", path="/mnt/my backup")
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "space-sync": SyncConfig(
                    slug="space-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "'/mnt/my data/'" in script
        assert "'/mnt/my backup/'" in script
        _bash_n(script)

    def test_no_config_path(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(platform="linux", config_path=None)
        script = generate_script(config, options, now=_NOW)
        assert "# Config: <stdin>" in script

    def test_custom_rsync_options(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "custom-sync": SyncConfig(
                    slug="custom-sync",
                    source="ep-src",
                    destination="ep-dst",
                    rsync_options=RsyncOptions(
                        default_options_override=["-a", "--delete"],
                        extra_options=["--bwlimit=1000", "--progress"],
                    ),
                ),
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "--bwlimit=1000" in script
        assert "--progress" in script

    def test_mixed_enabled_disabled(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst1": SyncEndpoint(slug="ep-dst1", volume="dst", subdir="on"),
                "ep-dst2": SyncEndpoint(slug="ep-dst2", volume="dst", subdir="off"),
            },
            syncs={
                "active-sync": SyncConfig(
                    slug="active-sync",
                    source="ep-src",
                    destination="ep-dst1",
                ),
                "off-sync": SyncConfig(
                    slug="off-sync",
                    source="ep-src",
                    destination="ep-dst2",
                    enabled=False,
                ),
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "sync_active_sync()" in script
        assert "# : disabled — off-sync" in script
        assert (
            'nbkp_run_sync active-sync sync_active_sync "$NBKP_PF_sync_active_sync"\n'
        ) in script
        assert "sync_off_sync_preflight &&" not in script
        assert "# sync_off_sync  # disabled" in script
        _bash_n(script)


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
        config = Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "nas-vol": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="nas-vol",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=3),
                ),
            },
            syncs={
                "remote-btrfs": SyncConfig(
                    slug="remote-btrfs",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        # Btrfs commands should be wrapped in SSH
        assert "btrfs subvolume snapshot" in script
        assert "btrfs property set" in script
        assert "btrfs subvolume delete" in script
        assert "nas.example.com" in script
        _bash_n(script)


class TestHardLink:
    def _hl_config(self) -> Config:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        return Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="dst",
                    hard_link_snapshots=HardLinkSnapshotConfig(
                        enabled=True,
                        max_snapshots=5,
                    ),
                ),
            },
            syncs={
                "hl-sync": SyncConfig(
                    slug="hl-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )

    def _hl_no_prune_config(self) -> Config:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        return Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="dst",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={
                "hl-sync": SyncConfig(
                    slug="hl-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
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
        return Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "nas-vol": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="nas-vol",
                    hard_link_snapshots=HardLinkSnapshotConfig(
                        enabled=True,
                        max_snapshots=3,
                    ),
                ),
            },
            syncs={
                "hl-remote": SyncConfig(
                    slug="hl-remote",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )

    def test_orphan_cleanup(self) -> None:
        """The whole step: other steps also emit readlink/latest/"/dev/null"."""
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        step = dedent("""\
            # Cleanup orphaned snapshots
            if [ "$NBKP_DRY_RUN" = false ]; then
                NBKP_LATEST_LINK=$(readlink /mnt/dst/latest 2>/dev/null || true)
                if [ -n "$NBKP_LATEST_LINK" ]; then
                    NBKP_LATEST_NAME="${NBKP_LATEST_LINK##*/}"
                    for snap in $(ls /mnt/dst/snapshots 2>/dev/null | nbkp_snapshot_names); do
                        if [ "$NBKP_LATEST_LINK" = "/dev/null" ] \\
                            || nbkp_snapshot_after "$snap" "$NBKP_LATEST_NAME"; then
                            nbkp_log "Removing orphaned snapshot: $snap"
                            rm -rf "/mnt/dst/snapshots/${snap}" || nbkp_log "WARN: failed to remove orphaned snapshot: $snap"
                        fi
                    done
                fi
            fi
        """)  # noqa: E501 -- the generated script, line for line
        assert indent(step, "    ") in script

    def test_rejects_filesystems_without_hard_links(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        checks = dedent("""\
            NBKP_DST_FS_TYPE="$(stat -f -c %T /mnt/dst 2>/dev/null || true)"
            [ -n "$NBKP_DST_FS_TYPE" ] || { nbkp_log 'ERROR: could not determine the filesystem type of destination volume /mnt/dst'; return 1; }
            ! nbkp_matches "$NBKP_DST_FS_TYPE" 'vfat|msdos|exfat' || { nbkp_log 'ERROR: destination volume /mnt/dst does not support hard links'; return 1; }
        """)  # noqa: E501 -- the generated script, line for line
        assert indent(checks, "    ") in script

    def test_reads_the_filesystem_type_with_df_and_mount_on_macos(self) -> None:
        config = self._hl_config()
        options = ScriptOptions(platform="darwin", config_path=None)
        script = generate_script(config, options, now=_NOW)
        assert 'NBKP_DST_FS_TYPE="$(nbkp_macos_fs_type /mnt/dst)"' in script
        assert "stat -f -c %T" not in script

    def test_link_dest_resolution(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "RSYNC_LINK_DEST" in script
        assert "--link-dest" in script

    def test_mkdir_snapshot_dir(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "mkdir -p /mnt/dst/snapshots\n" in script
        # Plain mkdir: an existing snapshot dir must fail the sync.
        assert 'mkdir "/mnt/dst/snapshots/${NBKP_TS}"' in script

    def test_rsync_to_snapshot(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "/mnt/dst/snapshots/${NBKP_TS}/" in script

    def test_symlink_update_guarded_by_dry_run(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert indent(_SYMLINK_STEP, "    ") in script

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

    def test_devnull_guard_in_prune(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert '= "/dev/null"' in script
        assert 'NBKP_LATEST_NAME=""' in script

    def test_valid_syntax(self) -> None:
        config = self._hl_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        _bash_n(script)

    def test_remote_valid_syntax(self) -> None:
        config = self._hl_remote_config()
        resolved = resolve_all_endpoints(config)
        script = generate_script(
            config,
            _OPTIONS,
            now=_NOW,
            resolved_endpoints=resolved,
        )
        _bash_n(script)

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
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        assert "/mnt/src/photos/" in script
        assert "${NBKP_SCRIPT_DIR}" in script
        assert "/mnt/dst/backup/" not in script

    def test_relative_src_local_to_local(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/src/backup.sh",
            relative_src=True,
        )
        script = generate_script(config, options, now=_NOW)
        assert "${NBKP_SCRIPT_DIR}" in script
        assert "/mnt/dst/backup/" in script

    def test_relative_both(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/backup.sh",
            relative_src=True,
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        assert "${NBKP_SCRIPT_DIR}" in script
        assert "/mnt/src/photos/" not in script
        assert "/mnt/dst/backup/" not in script

    def test_relative_src_local_to_remote(self) -> None:
        config = _local_to_remote_config()
        resolved = resolve_all_endpoints(config)
        options = ScriptOptions(
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/data/backup.sh",
            relative_src=True,
        )
        script = generate_script(config, options, now=_NOW, resolved_endpoints=resolved)
        assert "${NBKP_SCRIPT_DIR}" in script
        # Remote dest stays absolute
        assert "nas.example.com" in script

    def test_relative_dst_remote_to_local(self) -> None:
        config = _remote_to_local_config()
        resolved = resolve_all_endpoints(config)
        options = ScriptOptions(
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/backup/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW, resolved_endpoints=resolved)
        assert "${NBKP_SCRIPT_DIR}" in script
        # Remote source stays absolute
        assert "admin@remote.example.com" in script

    def test_script_dir_in_header(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            platform="linux",
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
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        _bash_n(script)

    def test_relative_both_shell_valid(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/backup.sh",
            relative_src=True,
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        _bash_n(script)

    def test_relative_btrfs(self) -> None:
        config = _btrfs_config()
        options = ScriptOptions(
            platform="linux",
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
        _bash_n(script)

    def test_relative_volume_checks(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        assert "${NBKP_SCRIPT_DIR}" in script
        assert "test -f /mnt/src/.nbkp-vol" in script

    def test_relative_preflight_checks(self) -> None:
        config = _local_to_local_config()
        options = ScriptOptions(
            platform="linux",
            config_path="/etc/nbkp/config.yaml",
            output_file="/mnt/dst/backup.sh",
            relative_dst=True,
        )
        script = generate_script(config, options, now=_NOW)
        assert "/mnt/src/photos/.nbkp-src" in script
        assert ".nbkp-dst" in script


class TestDependencyGuard:
    def test_downstream_sync_has_guard(self) -> None:
        src = LocalVolume(slug="src", path="/src")
        mid = LocalVolume(slug="mid", path="/mid")
        dst = LocalVolume(slug="dst", path="/dst")
        config = Config(
            volumes={"src": src, "mid": mid, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-mid": SyncEndpoint(slug="ep-mid", volume="mid"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "step-1": SyncConfig(
                    slug="step-1",
                    source="ep-src",
                    destination="ep-mid",
                ),
                "step-2": SyncConfig(
                    slug="step-2",
                    source="ep-mid",
                    destination="ep-dst",
                ),
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        # step-1 has no upstream syncs
        assert 'nbkp_run_sync step-1 sync_step_1 "$NBKP_PF_sync_step_1"\n' in script
        # step-2 lists upstream step-1, so it is cancelled when step-1 fails
        assert (
            'nbkp_run_sync step-2 sync_step_2 "$NBKP_PF_sync_step_2" sync_step_1\n'
        ) in script

    def test_independent_syncs_no_guard(self) -> None:
        config = _local_to_local_config()
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert 'nbkp_run_sync my-sync sync_my_sync "$NBKP_PF_sync_my_sync"\n' in script

    def test_dependency_guard_valid_syntax(self) -> None:
        src = LocalVolume(slug="src", path="/src")
        mid = LocalVolume(slug="mid", path="/mid")
        dst = LocalVolume(slug="dst", path="/dst")
        config = Config(
            volumes={"src": src, "mid": mid, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-mid": SyncEndpoint(slug="ep-mid", volume="mid"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "step-1": SyncConfig(
                    slug="step-1",
                    source="ep-src",
                    destination="ep-mid",
                ),
                "step-2": SyncConfig(
                    slug="step-2",
                    source="ep-mid",
                    destination="ep-dst",
                ),
            },
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        _bash_n(script)


class TestNoPortable:
    """Tests for --no-portable (bash 4+ with declare -A)."""

    def _chain_config(self) -> Config:
        src = LocalVolume(slug="src", path="/src")
        mid = LocalVolume(slug="mid", path="/mid")
        dst = LocalVolume(slug="dst", path="/dst")
        return Config(
            volumes={"src": src, "mid": mid, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-mid": SyncEndpoint(slug="ep-mid", volume="mid"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "step-1": SyncConfig(
                    slug="step-1",
                    source="ep-src",
                    destination="ep-mid",
                ),
                "step-2": SyncConfig(
                    slug="step-2",
                    source="ep-mid",
                    destination="ep-dst",
                ),
            },
        )

    def test_uses_declare_a(self) -> None:
        opts = ScriptOptions(platform="linux", config_path="test.yaml", portable=False)
        script = generate_script(self._chain_config(), opts, now=_NOW)
        assert "declare -A NBKP_FAILED=()" in script
        assert 'NBKP_FAILED=" "' not in script

    def test_associative_array_guard(self) -> None:
        opts = ScriptOptions(platform="linux", config_path="test.yaml", portable=False)
        script = generate_script(self._chain_config(), opts, now=_NOW)
        assert "NBKP_FAILED[$1]=1" in script
        assert '${NBKP_FAILED[$1]+set}" = "set"' in script

    def test_valid_bash_syntax(self) -> None:
        opts = ScriptOptions(platform="linux", config_path="test.yaml", portable=False)
        script = generate_script(self._chain_config(), opts, now=_NOW)
        _bash_n(script)

    def test_simple_sync_uses_associative_array(
        self,
    ) -> None:
        config = _local_to_local_config()
        opts = ScriptOptions(platform="linux", config_path="test.yaml", portable=False)
        script = generate_script(config, opts, now=_NOW)
        assert "declare -A NBKP_FAILED=()" in script
        assert "NBKP_FAILED[$1]=1" in script


def _remote_snapshot_config(mode: str) -> Config:
    """Local source -> remote destination whose path contains a space."""
    btrfs = BtrfsSnapshotConfig(enabled=mode == "btrfs", max_snapshots=3)
    hard_link = HardLinkSnapshotConfig(enabled=mode == "hard-link", max_snapshots=3)
    return Config(
        ssh_endpoints={
            "nas": SshEndpoint(slug="nas", host="nas.example.com", user="backup"),
        },
        volumes={
            "src": LocalVolume(slug="src", path="/mnt/data"),
            "nas-vol": RemoteVolume(
                slug="nas-vol",
                ssh_endpoint="nas",
                path="/srv/back ups",
            ),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="nas-vol",
                subdir="hl",
                btrfs_snapshots=btrfs,
                hard_link_snapshots=hard_link,
            ),
        },
        syncs={
            "remote-snap": SyncConfig(
                slug="remote-snap",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )


def _remote_script(mode: str) -> str:
    config = _remote_snapshot_config(mode)
    resolved = resolve_all_endpoints(config)
    return generate_script(config, _OPTIONS, now=_NOW, resolved_endpoints=resolved)


class TestBtrfsPruneFailedDelete:
    """
    A failed btrfs delete makes the snapshot read-only again and fails the block.

    ``nbkp_run_sync`` turns the failed prune block into a warning.
    """

    _STUB = dedent("""\
        btrfs() {
            echo "btrfs $*"
            case "$1 $2 $5" in
                "subvolume delete "*) return "$DELETE_RC" ;;
                "property set true") return "$RESTORE_RC" ;;
            esac
        }
        nbkp_log() { echo "$*"; }
        snap=2026-01-01T00:00:00.000Z
        """)

    def _run_delete(self, *, delete_rc: int, restore_rc: int) -> list[str]:
        script = generate_script(_btrfs_config(), _OPTIONS, now=_NOW)
        [delete_line] = [
            line.strip()
            for line in script.splitlines()
            if "btrfs subvolume delete" in line
        ]
        block = (
            f"{self._STUB}set +e\n( set -e\n{delete_line}\necho deleted )\necho rc=$?\n"
        )
        result = subprocess.run(
            ["bash", "-c", block],
            env={"DELETE_RC": str(delete_rc), "RESTORE_RC": str(restore_rc)},
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.splitlines()

    def test_successful_delete_leaves_the_flag_alone(self) -> None:
        lines = self._run_delete(delete_rc=0, restore_rc=0)
        assert [line for line in lines if "ro true" in line] == []
        assert lines[-2:] == ["deleted", "rc=0"]

    def test_failed_delete_makes_the_snapshot_read_only_again(self) -> None:
        lines = self._run_delete(delete_rc=1, restore_rc=0)
        path = "/mnt/dst/snapshots/2026-01-01T00:00:00.000Z"
        assert lines == [
            f"btrfs subvolume delete {path}",
            f"btrfs property set {path} ro true",
            "rc=1",
        ]

    def test_failed_restore_is_logged(self) -> None:
        lines = self._run_delete(delete_rc=1, restore_rc=1)
        assert lines[-2:] == [
            "WARN: snapshot 2026-01-01T00:00:00.000Z is left writable",
            "rc=1",
        ]


class TestBtrfsLatestReadOnlyWarning:
    """Preflight warns, without failing, when ``latest``'s snapshot is writable."""

    def _run(self, *, latest: str, ro: str) -> list[str]:
        script = generate_script(_btrfs_config(), _OPTIONS, now=_NOW)
        [line] = [
            line.strip() for line in script.splitlines() if "not read-only" in line
        ]
        stub = dedent("""\
            btrfs() { echo "$RO"; }
            nbkp_log() { echo "$*"; }
            """)
        result = subprocess.run(
            ["bash", "-c", f"{stub}set -e\n{line}\necho passed\n"],
            env={"RO": ro, "NBKP_DST_LATEST": latest},
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.splitlines()

    def test_writable_snapshot_is_a_warning(self) -> None:
        warning = "WARN: the snapshot latest points to is not read-only"
        assert self._run(latest="snapshots/S", ro="ro=false") == [
            f"{warning} (/mnt/dst/snapshots/S)",
            "passed",
        ]

    @pytest.mark.parametrize(
        ("latest", "ro"),
        [("snapshots/S", "ro=true"), ("snapshots/S", ""), ("/dev/null", "ro=false")],
    )
    def test_no_warning_otherwise(self, latest: str, ro: str) -> None:
        assert self._run(latest=latest, ro=ro) == ["passed"]


class TestRemoteCommandQuoting:
    """Remote paths are quoted for the remote shell; only $NBKP_TS/$snap expand."""

    def test_hard_link_remote_commands(self) -> None:
        script = _remote_script("hard-link")
        assert "\"mkdir '/srv/back ups/hl/snapshots/${NBKP_TS}'\"" in script
        assert "\"rm -rf '/srv/back ups/hl/snapshots/${snap}'\"" in script
        assert "\"ln -sfn 'snapshots/${NBKP_TS}' '/srv/back ups/hl/latest'\"" in script
        _bash_n(script)

    def test_btrfs_remote_prune_expands_snap_locally(self) -> None:
        script = _remote_script("btrfs")
        assert (
            "\"btrfs property set '/srv/back ups/hl/snapshots/${snap}' ro false\""
            in script
        )
        assert (
            "\"btrfs subvolume delete '/srv/back ups/hl/snapshots/${snap}'\"" in script
        )
        assert (
            "\"btrfs subvolume snapshot -r '/srv/back ups/hl/staging'"
            " '/srv/back ups/hl/snapshots/${NBKP_TS}'\"" in script
        )
        assert "\\$snap" not in script
        _bash_n(script)

    def test_snapshot_names_validated_before_use(self) -> None:
        script = _remote_script("hard-link")
        assert "| nbkp_snapshot_names)" in script

    def test_log_messages_quote_paths(self) -> None:
        src = LocalVolume(slug="src", path='/mnt/a "b" $c')
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s": SyncConfig(slug="s", source="ep-src", destination="ep-dst")},
        )
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert (
            'nbkp_log \'INACTIVE: source sentinel /mnt/a "b" $c/.nbkp-src not found'
            in script
        )
        assert "test -f '/mnt/a \"b\" $c/.nbkp-src'" in script
        _bash_n(script)


class TestPreflightParity:
    def test_volume_sentinels_gate_sync(self) -> None:
        script = generate_script(_local_to_local_config(), _OPTIONS, now=_NOW)
        assert (
            "test -f /mnt/src/.nbkp-vol || { nbkp_log 'INACTIVE: source volume"
            in script
        )
        assert (
            "/mnt/dst/.nbkp-vol not found (or host unreachable)'; return 2; }" in script
        )

    def test_rsync_version_checked(self) -> None:
        script = generate_script(_local_to_local_config(), _OPTIONS, now=_NOW)
        assert (
            'nbkp_rsync_version_ok "$(rsync --version 2>/dev/null || true)"' in script
        )

    def test_btrfs_destination_checks(self) -> None:
        script = generate_script(_btrfs_config(), _OPTIONS, now=_NOW)
        assert "user_subvol_rm_allowed" in script
        assert "is not a btrfs subvolume" in script
        assert "not on btrfs filesystem" in script
        assert "destination latest symlink not found" in script

    def test_source_devnull_with_upstream_is_pending_in_dry_run(self) -> None:
        script = generate_script(_btrfs_chain_config(), _OPTIONS, now=_NOW)
        assert "source snapshot not yet available (dry-run" in script
        assert "with no upstream sync" not in script

    def test_source_devnull_without_upstream_is_broken(self) -> None:
        config = _btrfs_chain_config()
        config = config.model_copy(update={"syncs": {"step-2": config.syncs["step-2"]}})
        script = generate_script(config, _OPTIONS, now=_NOW)
        assert "source latest -> /dev/null with no upstream sync" in script

    def test_preflight_runs_before_syncs_with_strictness(self) -> None:
        script = generate_script(_local_to_local_config(), _OPTIONS, now=_NOW)
        preflight = script.index("sync_my_sync_preflight && NBKP_PF_sync_my_sync=0")
        enforce = script.index('nbkp_enforce_strictness "$NBKP_PF_sync_my_sync"')
        run = script.index("nbkp_run_sync my-sync")
        assert preflight < enforce < run
        assert "NBKP_STRICTNESS=ignore-inactive" in script
        assert "--strictness|-S)" in script


class TestSyncSteps:
    def test_btrfs_has_no_link_dest_step(self) -> None:
        script = generate_script(_btrfs_config(), _OPTIONS, now=_NOW)
        assert "Link-dest resolution" not in script

    def test_rsync_failure_returns_before_snapshot(self) -> None:
        script = generate_script(_btrfs_config(), _OPTIONS, now=_NOW)
        rsync_check = script.index('if [ "$NBKP_RSYNC_RC" -ne 0 ]; then')
        snapshot = script.index("# Btrfs snapshot")
        assert rsync_check < snapshot
        assert "|| NBKP_RSYNC_RC=$?" in script

    def test_vanished_files_fail_by_default(self) -> None:
        script = generate_script(_btrfs_config(), _OPTIONS, now=_NOW)
        assert '"$NBKP_RSYNC_RC" -eq 24' not in script

    def test_hard_link_failure_removes_new_snapshot(self) -> None:
        script = _remote_script("hard-link")
        assert "\"rm -rf '/srv/back ups/hl/snapshots/${NBKP_TS}'\" || true" in script

    def test_sync_runs_in_errexit_subshell(self) -> None:
        script = generate_script(_local_to_local_config(), _OPTIONS, now=_NOW)
        assert '( set -e; "$fn" )' in script

    def test_prune_skips_latest_before_counting(self) -> None:
        script = generate_script(_btrfs_config(), _OPTIONS, now=_NOW)
        limit = script.index('[ "$NBKP_PRUNED" -lt "$NBKP_EXCESS" ] || break')
        skip = script.index('[ "$snap" != "$NBKP_LATEST_NAME" ] || continue')
        increment = script.index("NBKP_PRUNED=$((NBKP_PRUNED + 1))")
        assert limit < skip < increment

    def test_darwin_local_timestamps_avoid_colons(self) -> None:
        options = ScriptOptions(platform="darwin", config_path="c.yaml")
        script = generate_script(_btrfs_config(), options, now=_NOW)
        assert "NBKP_TS=$(date -u +%Y-%m-%dT%H-%M-%S.000Z)" in script


def test_config_path_cannot_inject_shell_lines() -> None:
    options = ScriptOptions(platform="linux", config_path="/tmp/x\nrm -rf ~\n.yaml")  # noqa: S108 -- data only, never written
    script = generate_script(_local_to_local_config(), options, now=_NOW)
    assert "# Config: /tmp/x\\nrm -rf ~\\n.yaml\n" in script
    assert "\nrm -rf ~" not in script
