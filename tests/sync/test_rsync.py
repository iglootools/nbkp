"""Tests for nbkp.rsync."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    RsyncOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.sync.rsync import ProgressMode, build_rsync_command, run_rsync

_SSH_KEY = str(Path("~/.ssh/key").expanduser())


class TestBuildRsyncCommandLocalToLocal:
    def test_basic(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="photos"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir="backup"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "/mnt/src/photos/",
            "/mnt/dst/backup/",
        ]

    def test_dry_run(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config, dry_run=True)
        assert "--dry-run" in cmd

    def test_link_dest(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(
            sync,
            config,
            link_dest="../../snapshots/20240101T000000Z",
        )
        assert "--link-dest=../../snapshots/20240101T000000Z" in cmd


class TestBuildRsyncCommandLocalToRemote:
    def test_basic(self) -> None:
        nas_server = SshEndpoint(
            slug="nas-server",
            host="nas.local",
            port=5022,
            user="backup",
            key="~/.ssh/key",
        )
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = RemoteVolume(
            slug="dst",
            ssh_endpoint="nas-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"nas-server": nas_server},
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="photos"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir="photos"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "-e",
            f"ssh -o ConnectTimeout=10 -o BatchMode=yes -p 5022 -i {_SSH_KEY}",
            "/mnt/src/photos/",
            "backup@nas.local:/backup/photos/",
        ]


class TestBuildRsyncCommandRemoteToLocal:
    def test_basic(self) -> None:
        server = SshEndpoint(
            slug="server",
            host="server.local",
            user="admin",
        )
        src = RemoteVolume(
            slug="src",
            ssh_endpoint="server",
            path="/data",
        )
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"server": server},
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir="backup"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "-e",
            "ssh -o ConnectTimeout=10 -o BatchMode=yes",
            "admin@server.local:/data/",
            "/mnt/dst/backup/",
        ]


class TestBuildRsyncCommandRemoteToRemoteSameServer:
    """When both volumes resolve to the same SSH endpoint,
    rsync should use local paths (no inner SSH)."""

    def _simple_config(
        self,
        server: SshEndpoint | None = None,
        extra_endpoints: dict[str, SshEndpoint] | None = None,
        src_path: str = "/data/src",
        dst_path: str = "/data/dst",
        src_subdir: str | None = None,
        dst_subdir: str | None = None,
        **sync_kwargs: object,
    ) -> tuple[SyncConfig, Config]:
        srv = server or SshEndpoint(
            slug="nas",
            host="nas.local",
            port=5022,
            user="backup",
            key="~/.ssh/key",
        )
        endpoints: dict[str, SshEndpoint] = {"nas": srv}
        if extra_endpoints:
            endpoints.update(extra_endpoints)
        src = RemoteVolume(
            slug="src",
            ssh_endpoint="nas",
            path=src_path,
        )
        dst = RemoteVolume(
            slug="dst",
            ssh_endpoint="nas",
            path=dst_path,
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            **sync_kwargs,  # type: ignore[arg-type]
        )
        config = Config(
            ssh_endpoints=endpoints,
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir=src_subdir),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir=dst_subdir),
            },
            syncs={"s1": sync},
        )
        return sync, config

    def test_basic(self) -> None:
        sync, config = self._simple_config(src_subdir="photos", dst_subdir="backup")
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)

        assert cmd[0] == "ssh"
        assert "backup@nas.local" in cmd

        inner = cmd[-1]
        assert inner.startswith("rsync")
        # No SSH transport — both paths are local
        assert "-e 'ssh" not in inner
        assert "backup@nas.local:" not in inner
        assert "/data/src/photos/" in inner
        assert "/data/dst/backup/" in inner

    def test_dry_run(self) -> None:
        sync, config = self._simple_config()
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(
            sync, config, dry_run=True, resolved_endpoints=resolved
        )
        inner = cmd[-1]
        assert "--dry-run" in inner

    def test_with_filters(self) -> None:
        sync, config = self._simple_config(
            filters=["+ *.jpg", "- *.tmp"],
            filter_file="/etc/nbkp/filters.rules",
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        inner = cmd[-1]
        assert "'--filter=+ *.jpg'" in inner
        assert "'--filter=- *.tmp'" in inner
        assert "'--filter=merge /etc/nbkp/filters.rules'" in inner

    def test_with_link_dest(self) -> None:
        sync, config = self._simple_config()
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(
            sync,
            config,
            link_dest="../../snapshots/20240101T000000Z",
            resolved_endpoints=resolved,
        )
        inner = cmd[-1]
        assert "--link-dest=../../snapshots/20240101T000000Z" in inner

    def test_custom_dest_suffix(self) -> None:
        sync, config = self._simple_config()
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(
            sync,
            config,
            resolved_endpoints=resolved,
            dest_suffix="snapshots/T1",
        )
        inner = cmd[-1]
        assert "/data/dst/snapshots/T1/" in inner

    def test_with_proxy_chain(self) -> None:
        bastion = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            user="admin",
        )
        server = SshEndpoint(
            slug="nas",
            host="nas.internal",
            user="backup",
            proxy_jump="bastion",
        )
        sync, config = self._simple_config(
            server=server,
            extra_endpoints={"bastion": bastion},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)

        assert cmd[0] == "ssh"
        assert any("ProxyCommand=" in arg for arg in cmd)
        assert "backup@nas.internal" in cmd

        inner = cmd[-1]
        assert "-e 'ssh" not in inner
        assert "/data/src/" in inner
        assert "/data/dst/" in inner

    def test_progress(self) -> None:
        sync, config = self._simple_config()
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(
            sync,
            config,
            progress=ProgressMode.PER_FILE,
            resolved_endpoints=resolved,
        )
        inner = cmd[-1]
        assert "-v" in inner
        assert "--progress" in inner


class TestBuildRsyncCommandFilters:
    def test_inline_filters(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            filters=["+ *.jpg", "- *.tmp"],
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "--filter=+ *.jpg",
            "--filter=- *.tmp",
            "/mnt/src/",
            "/mnt/dst/",
        ]

    def test_filter_file(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            filter_file="/etc/nbkp/filters.rules",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "--filter=merge /etc/nbkp/filters.rules",
            "/mnt/src/",
            "/mnt/dst/",
        ]

    def test_filters_and_filter_file(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            filters=["+ *.jpg"],
            filter_file="/etc/nbkp/filters.rules",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "--filter=+ *.jpg",
            "--filter=merge /etc/nbkp/filters.rules",
            "/mnt/src/",
            "/mnt/dst/",
        ]

    def test_dir_merge_filter(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            filters=[
                "dir-merge .rsync-filter",
                "- .rsync-filter",
                "merge /etc/nbkp/rules.txt",
            ],
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "--filter=dir-merge .rsync-filter",
            "--filter=- .rsync-filter",
            "--filter=merge /etc/nbkp/rules.txt",
            "/mnt/src/",
            "/mnt/dst/",
        ]

    def test_no_filters(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        user_filters = [
            a
            for a in cmd
            if a.startswith("--filter=")
            and not a.startswith("--filter=P")
            and not a.startswith("--filter=H")
        ]
        assert user_filters == []


class TestBuildRsyncCommandOptions:
    def _simple_config(self) -> tuple[SyncConfig, Config]:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        return sync, config

    def test_override_default_options(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            rsync_options=RsyncOptions(
                default_options_override=["-a"],
                checksum=False,
            ),
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "/mnt/src/",
            "/mnt/dst/",
        ]

    def test_extra_options(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            rsync_options=RsyncOptions(
                extra_options=["--bwlimit=1000"],
                checksum=False,
            ),
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--bwlimit=1000",
            "/mnt/src/",
            "/mnt/dst/",
        ]

    def test_override_and_extra(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            rsync_options=RsyncOptions(
                default_options_override=["-a", "--delete"],
                extra_options=["--bwlimit=1000"],
                checksum=False,
            ),
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--bwlimit=1000",
            "/mnt/src/",
            "/mnt/dst/",
        ]

    def test_checksum_default(self) -> None:
        sync, config = self._simple_config()
        cmd = build_rsync_command(sync, config)
        assert "--checksum" in cmd

    def test_checksum_disabled(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            rsync_options=RsyncOptions(checksum=False),
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        cmd = build_rsync_command(sync, config)
        assert "--checksum" not in cmd

    def test_compress_enabled(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            rsync_options=RsyncOptions(compress=True),
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        cmd = build_rsync_command(sync, config)
        assert "--compress" in cmd
        assert "--checksum" in cmd

    def test_compress_default(self) -> None:
        sync, config = self._simple_config()
        cmd = build_rsync_command(sync, config)
        assert "--compress" not in cmd


class TestBuildRsyncCommandProxyJump:
    def test_local_to_remote_with_proxy(self) -> None:
        bastion = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            port=2222,
            user="admin",
        )
        nas_server = SshEndpoint(
            slug="nas-server",
            host="nas.local",
            port=5022,
            user="backup",
            key="~/.ssh/key",
            proxy_jump="bastion",
        )
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = RemoteVolume(
            slug="dst",
            ssh_endpoint="nas-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={
                "bastion": bastion,
                "nas-server": nas_server,
            },
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        import shlex

        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -p 2222"
            " -W %h:%p admin@bastion.example.com"
        )
        quoted = shlex.quote(f"ProxyCommand={proxy_cmd}")
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "-e",
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            f" -p 5022 -i {_SSH_KEY}"
            f" -o {quoted}",
            "/mnt/src/",
            "backup@nas.local:/backup/",
        ]

    def test_remote_to_local_with_proxy(self) -> None:
        bastion = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            user="admin",
        )
        server = SshEndpoint(
            slug="server",
            host="server.internal",
            user="backup",
            proxy_jump="bastion",
        )
        src = RemoteVolume(
            slug="src",
            ssh_endpoint="server",
            path="/data",
        )
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={
                "bastion": bastion,
                "server": server,
            },
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        import shlex

        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -W %h:%p admin@bastion.example.com"
        )
        quoted = shlex.quote(f"ProxyCommand={proxy_cmd}")
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "-e",
            f"ssh -o ConnectTimeout=10 -o BatchMode=yes -o {quoted}",
            "backup@server.internal:/data/",
            "/mnt/dst/",
        ]


class TestBuildRsyncCommandMultiHopProxy:
    def test_local_to_remote_with_multi_hop_proxy(self) -> None:
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
        nas_server = SshEndpoint(
            slug="nas-server",
            host="nas.local",
            port=5022,
            user="backup",
            key="~/.ssh/key",
            proxy_jumps=["bastion1", "bastion2"],
        )
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = RemoteVolume(
            slug="dst",
            ssh_endpoint="nas-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={
                "bastion1": bastion1,
                "bastion2": bastion2,
                "nas-server": nas_server,
            },
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        import shlex

        inner = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -W %%h:%%p admin@bastion1.example.com"
        )
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            f" -o ProxyCommand={inner}"
            " -p 2222"
            " -W %h:%p bastion2.example.com"
        )
        quoted = shlex.quote(f"ProxyCommand={proxy_cmd}")
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "-e",
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            f" -p 5022 -i {_SSH_KEY}"
            f" -o {quoted}",
            "/mnt/src/",
            "backup@nas.local:/backup/",
        ]


class TestBuildRsyncCommandSpacesInPaths:
    def test_local_to_local_spaces(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/my src")
        dst = LocalVolume(slug="dst", path="/mnt/my dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="my photos"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir="my backup"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd == [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            "--partial-dir=.rsync-partial",
            "--safe-links",
            "--filter=H .nbkp-*",
            "--filter=P .nbkp-*",
            "--checksum",
            "/mnt/my src/my photos/",
            "/mnt/my dst/my backup/",
        ]


class TestBuildRsyncCommandProgress:
    def _simple_config(self) -> tuple[SyncConfig, Config]:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        return sync, config

    def test_no_progress(self) -> None:
        sync, config = self._simple_config()
        cmd = build_rsync_command(sync, config)
        assert "-v" not in cmd
        assert "--progress" not in cmd
        assert "--info=progress2" not in cmd
        assert "--stats" not in cmd
        assert "--human-readable" not in cmd

    def test_progress_none(self) -> None:
        sync, config = self._simple_config()
        cmd = build_rsync_command(sync, config, progress=ProgressMode.NONE)
        assert "-v" not in cmd
        assert "--progress" not in cmd
        assert "--info=progress2" not in cmd

    def test_progress_overall(self) -> None:
        sync, config = self._simple_config()
        cmd = build_rsync_command(sync, config, progress=ProgressMode.OVERALL)
        assert "--info=progress2" in cmd
        assert "--stats" in cmd
        assert "--human-readable" in cmd
        assert "-v" not in cmd
        assert "--progress" not in cmd

    def test_progress_per_file(self) -> None:
        sync, config = self._simple_config()
        cmd = build_rsync_command(sync, config, progress=ProgressMode.PER_FILE)
        assert "-v" in cmd
        assert "--progress" in cmd
        assert "--human-readable" in cmd
        assert "--info=progress2" not in cmd
        assert "--stats" not in cmd

    def test_progress_full(self) -> None:
        sync, config = self._simple_config()
        cmd = build_rsync_command(sync, config, progress=ProgressMode.FULL)
        assert "-v" in cmd
        assert "--progress" in cmd
        assert "--info=progress2" in cmd
        assert "--stats" in cmd
        assert "--human-readable" in cmd


class TestSourceSnapshotPath:
    """When source has snapshots, rsync should read from latest/."""

    def test_local_to_local_btrfs_source(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="src",
                    subdir="photos",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir="backup"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd[-2] == "/mnt/src/photos/latest/"
        assert cmd[-1] == "/mnt/dst/backup/"

    def test_local_to_local_hard_link_source(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="src",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd[-2] == "/mnt/src/latest/"
        assert cmd[-1] == "/mnt/dst/"

    def test_local_to_remote_btrfs_source(self) -> None:
        server = SshEndpoint(slug="nas", host="nas.local", user="backup")
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = RemoteVolume(slug="dst", ssh_endpoint="nas", path="/backup")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="src",
                    subdir="data",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        assert cmd[-2] == "/mnt/src/data/latest/"
        assert cmd[-1] == "backup@nas.local:/backup/"

    def test_remote_to_local_hard_link_source(self) -> None:
        server = SshEndpoint(slug="srv", host="srv.local", user="admin")
        src = RemoteVolume(slug="src", ssh_endpoint="srv", path="/data")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"srv": server},
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="src",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        assert cmd[-2] == "admin@srv.local:/data/latest/"
        assert cmd[-1] == "/mnt/dst/"

    def test_no_snapshots_source_unchanged(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="photos"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd[-2] == "/mnt/src/photos/"
        assert cmd[-1] == "/mnt/dst/"

    def test_remote_to_remote_same_server_btrfs_source(self) -> None:
        server = SshEndpoint(
            slug="nas",
            host="nas.local",
            user="backup",
        )
        src = RemoteVolume(slug="src", ssh_endpoint="nas", path="/data/src")
        dst = RemoteVolume(slug="dst", ssh_endpoint="nas", path="/data/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="src",
                    subdir="photos",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(sync, config, resolved_endpoints=resolved)
        inner = cmd[-1]
        assert "/data/src/photos/latest/" in inner
        assert "/data/dst/" in inner


class TestDestSuffix:
    def test_default_bare(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config)
        assert cmd[-1] == "/mnt/dst/"

    def test_default_btrfs(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(sync, config, dest_suffix="latest")
        assert cmd[-1] == "/mnt/dst/latest/"

    def test_custom_suffix(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        cmd = build_rsync_command(
            sync, config, dest_suffix="snapshots/2026-02-21T12:00:00.000Z"
        )
        assert cmd[-1] == "/mnt/dst/snapshots/2026-02-21T12:00:00.000Z/"

    def test_local_to_remote(self) -> None:
        server = SshEndpoint(slug="nas", host="nas.local", user="backup")
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = RemoteVolume(slug="dst", ssh_endpoint="nas", path="/backup")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"nas": server},
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(
            sync,
            config,
            resolved_endpoints=resolved,
            dest_suffix="snapshots/T1",
        )
        assert cmd[-1] == "backup@nas.local:/backup/snapshots/T1/"

    def test_remote_to_local(self) -> None:
        server = SshEndpoint(slug="srv", host="srv.local", user="admin")
        src = RemoteVolume(slug="src", ssh_endpoint="srv", path="/data")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            ssh_endpoints={"srv": server},
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )
        resolved = resolve_all_endpoints(config)

        cmd = build_rsync_command(
            sync,
            config,
            resolved_endpoints=resolved,
            dest_suffix="snapshots/T1",
        )
        assert cmd[-1] == "/mnt/dst/snapshots/T1/"


class TestRunRsync:
    @patch("nbkp.sync.rsync.subprocess.run")
    def test_run_rsync(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
        src = LocalVolume(slug="src", path="/src")
        dst = LocalVolume(slug="dst", path="/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        result = run_rsync(sync, config)
        assert result.returncode == 0
        mock_run.assert_called_once()

    @patch("nbkp.sync.rsync.subprocess.Popen")
    def test_run_rsync_streams_output(self, mock_popen: MagicMock) -> None:
        src = LocalVolume(slug="src", path="/src")
        dst = LocalVolume(slug="dst", path="/dst")
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        streamed = "sending incremental file list\r\nfile.txt\n"
        proc = MagicMock()
        proc.stdout = io.StringIO(streamed)
        proc.poll.side_effect = lambda: (
            0 if proc.stdout.tell() == len(streamed) else None
        )
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        chunks: list[str] = []
        result = run_rsync(sync, config, on_output=chunks.append)

        assert result.returncode == 0
        assert result.stdout == streamed
        assert "".join(chunks) == streamed
        mock_popen.assert_called_once()
