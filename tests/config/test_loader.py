"""Tests for nbkp.configloader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    ConfigError,
    ConfigErrorReason,
    EndpointFilter,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    RsyncOptions,
    SshEndpoint,
    SshConnectionOptions,
    SyncConfig,
    SyncEndpoint,
    find_config_file,
    load_config,
    resolve_endpoint_for_volume,
    resolve_proxy_chain,
)


def _config_to_yaml(config: Config) -> str:
    return yaml.safe_dump(
        config.model_dump(by_alias=True),
        default_flow_style=False,
        sort_keys=False,
    )


class TestFindConfigFile:
    def test_explicit_path(self, sample_config_file: Path) -> None:
        result = find_config_file(str(sample_config_file))
        assert result == sample_config_file

    def test_explicit_path_missing(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            find_config_file("/nonexistent/config.yaml")
        assert excinfo.value.reason == ConfigErrorReason.FILE_NOT_FOUND

    def test_xdg_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        xdg = tmp_path / "xdg"
        cfg = xdg / "nbkp" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("volumes: {}\nsyncs: {}\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        result = find_config_file()
        assert result == cfg

    def test_no_config_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
        with pytest.raises(ConfigError) as excinfo:
            find_config_file()
        assert excinfo.value.reason == ConfigErrorReason.NO_CONFIG_FOUND


class TestLoadConfig:
    def test_full_config(self, sample_config_file: Path) -> None:
        cfg = load_config(str(sample_config_file))
        assert "nas-server" in cfg.ssh_endpoints
        server = cfg.ssh_endpoints["nas-server"]
        assert server.slug == "nas-server"
        assert server.host == "nas.example.com"
        assert server.port == 5022
        assert server.user == "backup"
        assert server.key == str(Path("~/.ssh/key").expanduser())
        assert server.connection_options.connect_timeout == 10
        assert "local-data" in cfg.volumes
        assert "nas" in cfg.volumes
        assert "photos-to-nas" in cfg.syncs
        local = cfg.volumes["local-data"]
        assert isinstance(local, LocalVolume)
        assert local.path == "/mnt/data"
        remote = cfg.volumes["nas"]
        assert isinstance(remote, RemoteVolume)
        assert remote.ssh_endpoint == "nas-server"
        sync = cfg.syncs["photos-to-nas"]
        assert sync.source == "local-photos"
        assert sync.destination == "nas-photos"
        src_ep = cfg.source_endpoint(sync)
        dst_ep = cfg.destination_endpoint(sync)
        assert src_ep.volume == "local-data"
        assert src_ep.subdir == "photos"
        assert dst_ep.volume == "nas"
        assert dst_ep.subdir == "photos-backup"
        assert sync.enabled is True
        assert dst_ep.btrfs_snapshots.enabled is False
        assert sync.rsync_options.default_options_override is None
        assert sync.rsync_options.extra_options == []
        assert sync.rsync_options.checksum is True
        assert sync.rsync_options.compress is False
        assert sync.filters == ["+ *.jpg", "- *.tmp"]
        assert sync.filter_file == str(
            Path("~/.config/nbkp/filters/photos.rules").expanduser()
        )

    def test_minimal_config(self, sample_minimal_config_file: Path) -> None:
        cfg = load_config(str(sample_minimal_config_file))
        sync = cfg.syncs["s1"]
        assert sync.enabled is True
        dst_ep = cfg.destination_endpoint(sync)
        src_ep = cfg.source_endpoint(sync)
        assert dst_ep.btrfs_snapshots.enabled is False
        assert src_ep.subdir is None
        assert sync.rsync_options.default_options_override is None
        assert sync.rsync_options.extra_options == []
        assert sync.filters == []
        assert sync.filter_file is None

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("not_a_list:\n  - [invalid")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.INVALID_YAML

    def test_not_a_mapping(self, tmp_path: Path) -> None:
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.NOT_A_MAPPING

    def test_invalid_volume_type(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_type.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: ftp
                path: /x
            syncs: {}
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "does not match any of the expected tags" in str(cause)

    def test_missing_local_path(self, tmp_path: Path) -> None:
        p = tmp_path / "no_path.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
            syncs: {}
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        errors = cause.errors()
        assert any(
            err["loc"] == ("volumes", "v", "local", "path") and err["type"] == "missing"
            for err in errors
        )

    def test_missing_remote_host(self, tmp_path: Path) -> None:
        p = tmp_path / "no_host.yaml"
        p.write_text(dedent("""\
            ssh-endpoints:
              s:
                port: 22
            volumes:
              v:
                type: remote
                ssh-endpoint: s
                path: /x
            syncs: {}
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        errors = cause.errors()
        assert any(
            "host" in str(err["loc"]) and err["type"] == "missing" for err in errors
        )

    def test_unknown_ssh_endpoint_reference(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_server_ref.yaml"
        p.write_text(dedent("""\
            ssh-endpoints: {}
            volumes:
              v:
                type: remote
                ssh-endpoint: missing
                path: /x
            syncs: {}
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "unknown ssh-endpoint 'missing'" in str(cause)

    def test_unknown_volume_reference(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_ref.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-src:
                volume: v
              ep-dst:
                volume: missing
            syncs:
              s:
                source: ep-src
                destination: ep-dst
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "unknown volume" in str(cause)

    def test_missing_source_volume(self, tmp_path: Path) -> None:
        p = tmp_path / "no_src_vol.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-src:
                volume: missing
              ep-dst:
                volume: v
            syncs:
              s:
                source: ep-src
                destination: ep-dst
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "unknown volume" in str(cause)

    def test_sync_missing_source(self, tmp_path: Path) -> None:
        p = tmp_path / "no_src.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-dst:
                volume: v
            syncs:
              s:
                destination: ep-dst
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        errors = cause.errors()
        assert any(
            err["loc"] == ("syncs", "s", "source") and err["type"] == "missing"
            for err in errors
        )

    def test_filter_normalization(self, tmp_path: Path) -> None:
        p = tmp_path / "filters.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-src:
                volume: v
              ep-dst:
                volume: v
                subdir: dst
            syncs:
              s:
                source: ep-src
                destination: ep-dst
                filters:
                  - include: "*.jpg"
                  - exclude: "*.tmp"
                  - "H .git"
        """))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        assert sync.filters == ["+ *.jpg", "- *.tmp", "H .git"]

    def test_rsync_options_override(self, tmp_path: Path) -> None:
        config = Config(
            volumes={
                "v": LocalVolume(slug="v", path="/x"),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="v", subdir="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="v", subdir="dst"),
            },
            syncs={
                "s": SyncConfig(
                    slug="s",
                    source="ep-src",
                    destination="ep-dst",
                    rsync_options=RsyncOptions(
                        default_options_override=["-a", "--delete"],
                    ),
                ),
            },
        )
        p = tmp_path / "opts.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        assert sync.rsync_options.default_options_override == [
            "-a",
            "--delete",
        ]
        assert sync.rsync_options.extra_options == []

    def test_rsync_extra_options(self, tmp_path: Path) -> None:
        config = Config(
            volumes={
                "v": LocalVolume(slug="v", path="/x"),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="v", subdir="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="v", subdir="dst"),
            },
            syncs={
                "s": SyncConfig(
                    slug="s",
                    source="ep-src",
                    destination="ep-dst",
                    rsync_options=RsyncOptions(
                        extra_options=[
                            "--bwlimit=1000",
                            "--progress",
                        ],
                    ),
                ),
            },
        )
        p = tmp_path / "extra.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        assert sync.rsync_options.default_options_override is None
        assert sync.rsync_options.extra_options == [
            "--bwlimit=1000",
            "--progress",
        ]

    def test_connection_options(self, tmp_path: Path) -> None:
        config = Config(
            ssh_endpoints={
                "slow": SshEndpoint(
                    slug="slow",
                    host="slow.example.com",
                    connection_options=SshConnectionOptions(
                        connect_timeout=30,
                        strict_host_key_checking=False,
                        known_hosts_file="/dev/null",
                    ),
                ),
            },
        )
        p = tmp_path / "ssh_opts.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        opts = cfg.ssh_endpoints["slow"].connection_options
        assert opts.connect_timeout == 30
        assert opts.strict_host_key_checking is False
        assert opts.known_hosts_file == "/dev/null"

    def test_connection_options_server_alive_interval(self, tmp_path: Path) -> None:
        config = Config(
            ssh_endpoints={
                "keepalive": SshEndpoint(
                    slug="keepalive",
                    host="host.example.com",
                    connection_options=SshConnectionOptions(
                        server_alive_interval=60,
                    ),
                ),
            },
        )
        p = tmp_path / "keepalive.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        opts = cfg.ssh_endpoints["keepalive"].connection_options
        assert opts.server_alive_interval == 60

    def test_connection_options_channel_timeout(self, tmp_path: Path) -> None:
        config = Config(
            ssh_endpoints={
                "ch-timeout": SshEndpoint(
                    slug="ch-timeout",
                    host="host.example.com",
                    connection_options=SshConnectionOptions(
                        channel_timeout=30.0,
                    ),
                ),
            },
        )
        p = tmp_path / "ch_timeout.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        opts = cfg.ssh_endpoints["ch-timeout"].connection_options
        assert opts.channel_timeout == 30.0

    def test_connection_options_disabled_algorithms(self, tmp_path: Path) -> None:
        config = Config(
            ssh_endpoints={
                "restricted": SshEndpoint(
                    slug="restricted",
                    host="host.example.com",
                    connection_options=SshConnectionOptions(
                        disabled_algorithms={
                            "ciphers": ["aes128-cbc"],
                        },
                    ),
                ),
            },
        )
        p = tmp_path / "disabled_algs.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        opts = cfg.ssh_endpoints["restricted"].connection_options
        assert opts.disabled_algorithms == {
            "ciphers": ["aes128-cbc"],
        }

    def test_proxy_jump_valid(self, tmp_path: Path) -> None:
        config = Config(
            ssh_endpoints={
                "bastion": SshEndpoint(
                    slug="bastion",
                    host="bastion.example.com",
                ),
                "target": SshEndpoint(
                    slug="target",
                    host="target.internal",
                    proxy_jump="bastion",
                ),
            },
        )
        p = tmp_path / "proxy.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        assert cfg.ssh_endpoints["target"].proxy_jump == "bastion"
        chain = resolve_proxy_chain(cfg, cfg.ssh_endpoints["target"])
        assert len(chain) == 1
        assert chain[0].host == "bastion.example.com"

    def test_proxy_jump_unknown_server(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_proxy.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "target": {
                            "host": "target.internal",
                            "proxy-jump": "nonexistent",
                        },
                    },
                }
            )
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "unknown proxy-jump server" in str(cause)

    def test_proxy_jump_circular(self, tmp_path: Path) -> None:
        p = tmp_path / "circular.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "a": {
                            "host": "a.example.com",
                            "proxy-jump": "b",
                        },
                        "b": {
                            "host": "b.example.com",
                            "proxy-jump": "a",
                        },
                    },
                }
            )
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "Circular proxy-jump chain" in str(cause)

    def test_proxy_jumps_valid(self, tmp_path: Path) -> None:
        config = Config(
            ssh_endpoints={
                "bastion1": SshEndpoint(
                    slug="bastion1",
                    host="bastion1.example.com",
                ),
                "bastion2": SshEndpoint(
                    slug="bastion2",
                    host="bastion2.example.com",
                ),
                "target": SshEndpoint(
                    slug="target",
                    host="target.internal",
                    proxy_jumps=["bastion1", "bastion2"],
                ),
            },
        )
        p = tmp_path / "proxy_jumps.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        chain = resolve_proxy_chain(cfg, cfg.ssh_endpoints["target"])
        assert len(chain) == 2
        assert chain[0].host == "bastion1.example.com"
        assert chain[1].host == "bastion2.example.com"

    def test_proxy_jumps_single_element(self, tmp_path: Path) -> None:
        config = Config(
            ssh_endpoints={
                "bastion": SshEndpoint(
                    slug="bastion",
                    host="bastion.example.com",
                ),
                "target": SshEndpoint(
                    slug="target",
                    host="target.internal",
                    proxy_jumps=["bastion"],
                ),
            },
        )
        p = tmp_path / "proxy_jumps_single.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        chain = resolve_proxy_chain(cfg, cfg.ssh_endpoints["target"])
        assert len(chain) == 1
        assert chain[0].host == "bastion.example.com"

    def test_proxy_jump_and_proxy_jumps_mutual_exclusivity(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "exclusive.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "bastion": {
                            "host": "bastion.example.com",
                        },
                        "target": {
                            "host": "target.internal",
                            "proxy-jump": "bastion",
                            "proxy-jumps": ["bastion"],
                        },
                    },
                }
            )
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "mutually exclusive" in str(cause)

    def test_proxy_jumps_unknown_server(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_proxy_jumps.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "target": {
                            "host": "target.internal",
                            "proxy-jumps": ["nonexistent"],
                        },
                    },
                }
            )
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "unknown proxy-jump server" in str(cause)

    def test_proxy_jumps_circular(self, tmp_path: Path) -> None:
        p = tmp_path / "circular_jumps.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "a": {
                            "host": "a.example.com",
                            "proxy-jumps": ["b"],
                        },
                        "b": {
                            "host": "b.example.com",
                            "proxy-jumps": ["a"],
                        },
                    },
                }
            )
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "Circular proxy-jump chain" in str(cause)

    def test_extends_proxy_jumps_overrides_parent_proxy_jump(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "extends_jumps.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "bastion1": {
                            "host": "bastion1.example.com",
                        },
                        "bastion2": {
                            "host": "bastion2.example.com",
                        },
                        "bastion3": {
                            "host": "bastion3.example.com",
                        },
                        "parent": {
                            "host": "parent.internal",
                            "proxy-jump": "bastion1",
                        },
                        "child": {
                            "host": "child.internal",
                            "extends": "parent",
                            "proxy-jumps": [
                                "bastion2",
                                "bastion3",
                            ],
                        },
                    },
                }
            )
        )
        cfg = load_config(str(p))
        chain = resolve_proxy_chain(cfg, cfg.ssh_endpoints["child"])
        assert len(chain) == 2
        assert chain[0].host == "bastion2.example.com"
        assert chain[1].host == "bastion3.example.com"

    def test_extends_proxy_jump_overrides_parent_proxy_jumps(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "extends_jump.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "bastion1": {
                            "host": "bastion1.example.com",
                        },
                        "bastion2": {
                            "host": "bastion2.example.com",
                        },
                        "bastion3": {
                            "host": "bastion3.example.com",
                        },
                        "parent": {
                            "host": "parent.internal",
                            "proxy-jumps": [
                                "bastion1",
                                "bastion2",
                            ],
                        },
                        "child": {
                            "host": "child.internal",
                            "extends": "parent",
                            "proxy-jump": "bastion3",
                        },
                    },
                }
            )
        )
        cfg = load_config(str(p))
        chain = resolve_proxy_chain(cfg, cfg.ssh_endpoints["child"])
        assert len(chain) == 1
        assert chain[0].host == "bastion3.example.com"

    def test_proxy_jump_chain_property(self) -> None:
        # Single proxy-jump
        ep_single = SshEndpoint(
            slug="single",
            host="host.example.com",
            proxy_jump="bastion",
        )
        assert ep_single.proxy_jump_chain == ["bastion"]

        # List proxy-jumps
        ep_list = SshEndpoint(
            slug="multi",
            host="host.example.com",
            proxy_jumps=["bastion1", "bastion2"],
        )
        assert ep_list.proxy_jump_chain == [
            "bastion1",
            "bastion2",
        ]

        # No proxy
        ep_none = SshEndpoint(
            slug="no-proxy",
            host="host.example.com",
        )
        assert ep_none.proxy_jump_chain == []

    def test_invalid_filter_entry(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_filter.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-src:
                volume: v
              ep-dst:
                volume: v
                subdir: dst
            syncs:
              s:
                source: ep-src
                destination: ep-dst
                filters:
                  - badkey: value
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "include" in str(cause) or "exclude" in str(cause)

    def test_hard_link_snapshots(self, tmp_path: Path) -> None:
        config = Config(
            volumes={
                "v": LocalVolume(slug="v", path="/x"),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="v", subdir="src"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="v",
                    subdir="dst",
                    hard_link_snapshots=HardLinkSnapshotConfig(
                        enabled=True, max_snapshots=10
                    ),
                ),
            },
            syncs={
                "s": SyncConfig(
                    slug="s",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        p = tmp_path / "hl.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        dst_ep = cfg.destination_endpoint(sync)
        assert dst_ep.hard_link_snapshots.enabled is True
        assert dst_ep.hard_link_snapshots.max_snapshots == 10
        assert dst_ep.btrfs_snapshots.enabled is False
        assert dst_ep.snapshot_mode == "hard-link"

    def test_hard_link_snapshots_no_max(self, tmp_path: Path) -> None:
        config = Config(
            volumes={
                "v": LocalVolume(slug="v", path="/x"),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="v", subdir="src"),
                "ep-dst": SyncEndpoint(
                    slug="ep-dst",
                    volume="v",
                    subdir="dst",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={
                "s": SyncConfig(
                    slug="s",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        p = tmp_path / "hl_no_max.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        dst_ep = cfg.destination_endpoint(sync)
        assert dst_ep.hard_link_snapshots.enabled is True
        assert dst_ep.hard_link_snapshots.max_snapshots is None

    def test_mutual_exclusivity_btrfs_and_hardlink(self, tmp_path: Path) -> None:
        with pytest.raises(Exception, match="mutually exclusive"):
            SyncEndpoint(
                slug="ep",
                volume="v",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
            )

    def test_snapshot_mode_none(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v")
        assert ep.snapshot_mode == "none"

    def test_snapshot_mode_btrfs(self) -> None:
        ep = SyncEndpoint(
            slug="ep",
            volume="v",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        )
        assert ep.snapshot_mode == "btrfs"

    def test_snapshot_mode_hard_link(self) -> None:
        ep = SyncEndpoint(
            slug="ep",
            volume="v",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        )
        assert ep.snapshot_mode == "hard-link"

    def test_location_and_locations_mutual_exclusivity(self, tmp_path: Path) -> None:
        p = tmp_path / "exclusive_loc.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "server": {
                            "host": "server.example.com",
                            "location": "home",
                            "locations": ["home", "travel"],
                        },
                    },
                }
            )
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "mutually exclusive" in str(cause)

    def test_location_list_property(self) -> None:
        # Single location
        ep_single = SshEndpoint(
            slug="single",
            host="host.example.com",
            location="home",
        )
        assert ep_single.location_list == ["home"]

        # List locations
        ep_list = SshEndpoint(
            slug="multi",
            host="host.example.com",
            locations=["home", "travel"],
        )
        assert ep_list.location_list == ["home", "travel"]

        # No location
        ep_none = SshEndpoint(
            slug="no-loc",
            host="host.example.com",
        )
        assert ep_none.location_list == []

    def test_extends_locations_overrides_parent_location(self, tmp_path: Path) -> None:
        p = tmp_path / "extends_locs.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "parent": {
                            "host": "parent.internal",
                            "location": "home",
                        },
                        "child": {
                            "host": "child.internal",
                            "extends": "parent",
                            "locations": ["home", "travel"],
                        },
                    },
                }
            )
        )
        cfg = load_config(str(p))
        assert cfg.ssh_endpoints["child"].location_list == [
            "home",
            "travel",
        ]

    def test_extends_location_overrides_parent_locations(self, tmp_path: Path) -> None:
        p = tmp_path / "extends_loc.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "parent": {
                            "host": "parent.internal",
                            "locations": [
                                "home",
                                "travel",
                            ],
                        },
                        "child": {
                            "host": "child.internal",
                            "extends": "parent",
                            "location": "office",
                        },
                    },
                }
            )
        )
        cfg = load_config(str(p))
        assert cfg.ssh_endpoints["child"].location_list == [
            "office",
        ]

    def test_resolve_endpoint_location_filter_with_locations(
        self,
    ) -> None:
        config = Config(
            ssh_endpoints={
                "home-server": SshEndpoint(
                    slug="home-server",
                    host="192.168.1.10",
                    location="home",
                ),
                "multi-server": SshEndpoint(
                    slug="multi-server",
                    host="10.0.0.5",
                    locations=["home", "travel"],
                ),
                "office-server": SshEndpoint(
                    slug="office-server",
                    host="10.1.0.5",
                    location="office",
                ),
            },
            volumes={
                "remote": RemoteVolume(
                    slug="remote",
                    ssh_endpoint="home-server",
                    ssh_endpoints=[
                        "home-server",
                        "multi-server",
                        "office-server",
                    ],
                    path="/data",
                ),
            },
            syncs={},
        )
        # Filter for "travel" should match multi-server
        ef = EndpointFilter(locations=["travel"])
        result = resolve_endpoint_for_volume(
            config,
            config.volumes["remote"],
            ef,  # type: ignore[arg-type]
        )
        assert result.slug == "multi-server"

    def test_resolve_endpoint_exclude_location(
        self,
    ) -> None:
        config = Config(
            ssh_endpoints={
                "home-server": SshEndpoint(
                    slug="home-server",
                    host="192.168.1.10",
                    location="home",
                ),
                "travel-server": SshEndpoint(
                    slug="travel-server",
                    host="10.0.0.5",
                    location="travel",
                ),
                "office-server": SshEndpoint(
                    slug="office-server",
                    host="10.1.0.5",
                    location="office",
                ),
            },
            volumes={
                "remote": RemoteVolume(
                    slug="remote",
                    ssh_endpoint="home-server",
                    ssh_endpoints=[
                        "home-server",
                        "travel-server",
                        "office-server",
                    ],
                    path="/data",
                ),
            },
            syncs={},
        )
        # Exclude "home" — should pick travel-server (first non-excluded)
        ef = EndpointFilter(exclude_locations=["home"])
        result = resolve_endpoint_for_volume(
            config,
            config.volumes["remote"],
            ef,  # type: ignore[arg-type]
        )
        assert result.slug == "travel-server"

    def test_resolve_endpoint_exclude_and_include_location(
        self,
    ) -> None:
        config = Config(
            ssh_endpoints={
                "home-server": SshEndpoint(
                    slug="home-server",
                    host="192.168.1.10",
                    location="home",
                ),
                "travel-server": SshEndpoint(
                    slug="travel-server",
                    host="10.0.0.5",
                    location="travel",
                ),
                "office-server": SshEndpoint(
                    slug="office-server",
                    host="10.1.0.5",
                    location="office",
                ),
            },
            volumes={
                "remote": RemoteVolume(
                    slug="remote",
                    ssh_endpoint="home-server",
                    ssh_endpoints=[
                        "home-server",
                        "travel-server",
                        "office-server",
                    ],
                    path="/data",
                ),
            },
            syncs={},
        )
        # Exclude "home", include "office" — should pick office-server
        ef = EndpointFilter(
            locations=["office"],
            exclude_locations=["home"],
        )
        result = resolve_endpoint_for_volume(
            config,
            config.volumes["remote"],
            ef,  # type: ignore[arg-type]
        )
        assert result.slug == "office-server"

    def test_resolve_endpoint_exclude_all_falls_back(
        self,
    ) -> None:
        config = Config(
            ssh_endpoints={
                "home-server": SshEndpoint(
                    slug="home-server",
                    host="192.168.1.10",
                    location="home",
                ),
            },
            volumes={
                "remote": RemoteVolume(
                    slug="remote",
                    ssh_endpoint="home-server",
                    ssh_endpoints=["home-server"],
                    path="/data",
                ),
            },
            syncs={},
        )
        # Excluding all candidates falls back (keeps original list)
        ef = EndpointFilter(exclude_locations=["home"])
        result = resolve_endpoint_for_volume(
            config,
            config.volumes["remote"],
            ef,  # type: ignore[arg-type]
        )
        assert result.slug == "home-server"

    def test_resolve_all_endpoints_skips_excluded_volumes(
        self,
    ) -> None:
        from nbkp.config.resolution import resolve_all_endpoints

        config = Config(
            ssh_endpoints={
                "home-server": SshEndpoint(
                    slug="home-server",
                    host="192.168.1.10",
                    location="home",
                ),
                "travel-server": SshEndpoint(
                    slug="travel-server",
                    host="10.0.0.5",
                    location="travel",
                ),
            },
            volumes={
                "home-vol": RemoteVolume(
                    slug="home-vol",
                    ssh_endpoint="home-server",
                    path="/data",
                ),
                "travel-vol": RemoteVolume(
                    slug="travel-vol",
                    ssh_endpoint="travel-server",
                    path="/backup",
                ),
            },
            syncs={},
        )
        ef = EndpointFilter(exclude_locations=["home"])
        result = resolve_all_endpoints(config, ef)
        # home-vol should be excluded (not resolved)
        assert "home-vol" not in result
        assert "travel-vol" in result

    def test_source_btrfs_snapshots(self, tmp_path: Path) -> None:
        config = Config(
            volumes={
                "v": LocalVolume(slug="v", path="/x"),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="v",
                    subdir="src",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="v", subdir="dst"),
            },
            syncs={
                "s": SyncConfig(
                    slug="s",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        p = tmp_path / "src_btrfs.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        src_ep = cfg.source_endpoint(sync)
        dst_ep = cfg.destination_endpoint(sync)
        assert src_ep.btrfs_snapshots.enabled is True
        assert src_ep.snapshot_mode == "btrfs"
        assert dst_ep.btrfs_snapshots.enabled is False

    def test_source_hard_link_snapshots(self, tmp_path: Path) -> None:
        config = Config(
            volumes={
                "v": LocalVolume(slug="v", path="/x"),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(
                    slug="ep-src",
                    volume="v",
                    subdir="src",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="v", subdir="dst"),
            },
            syncs={
                "s": SyncConfig(
                    slug="s",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        p = tmp_path / "src_hl.yaml"
        p.write_text(_config_to_yaml(config))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        src_ep = cfg.source_endpoint(sync)
        assert src_ep.hard_link_snapshots.enabled is True
        assert src_ep.snapshot_mode == "hard-link"
        assert src_ep.btrfs_snapshots.enabled is False

    def test_unknown_source_endpoint_reference(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_src_ep.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-dst:
                volume: v
            syncs:
              s:
                source: missing
                destination: ep-dst
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "unknown source endpoint" in str(cause)

    def test_unknown_destination_endpoint_reference(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_dst_ep.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-src:
                volume: v
            syncs:
              s:
                source: ep-src
                destination: missing
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "unknown destination endpoint" in str(cause)

    def test_duplicate_destination_endpoint(self, tmp_path: Path) -> None:
        p = tmp_path / "dup_dst.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-src1:
                volume: v
                subdir: src1
              ep-src2:
                volume: v
                subdir: src2
              ep-dst:
                volume: v
                subdir: dst
            syncs:
              s1:
                source: ep-src1
                destination: ep-dst
              s2:
                source: ep-src2
                destination: ep-dst
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "share destination endpoint" in str(cause)

    def test_duplicate_volume_subdir_in_endpoints(self, tmp_path: Path) -> None:
        p = tmp_path / "dup_loc.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep1:
                volume: v
                subdir: data
              ep2:
                volume: v
                subdir: data
            syncs: {}
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "both target volume" in str(cause)

    def test_sync_endpoint_unknown_volume(self, tmp_path: Path) -> None:
        p = tmp_path / "ep_bad_vol.yaml"
        p.write_text(dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep:
                volume: nonexistent
            syncs: {}
        """))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.reason == ConfigErrorReason.VALIDATION
        cause = excinfo.value.__cause__
        assert cause is not None
        assert "unknown volume" in str(cause)


class TestPathNormalization:
    """Trailing-slash stripping and tilde expansion."""

    def test_local_volume_trailing_slash(self) -> None:
        vol = LocalVolume(slug="v", path="/mnt/data/")
        assert vol.path == "/mnt/data"

    def test_local_volume_tilde_expansion(self) -> None:
        vol = LocalVolume(slug="v", path="~/data")
        assert vol.path == str(Path("~/data").expanduser())

    def test_local_volume_tilde_only(self) -> None:
        vol = LocalVolume(slug="v", path="~")
        assert vol.path == str(Path("~").expanduser())

    def test_local_volume_tilde_trailing_slash(self) -> None:
        vol = LocalVolume(slug="v", path="~/data/")
        assert vol.path == str(Path("~/data").expanduser())

    def test_local_volume_no_trailing_slash(self) -> None:
        vol = LocalVolume(slug="v", path="/mnt/data")
        assert vol.path == "/mnt/data"

    def test_remote_volume_trailing_slash(self) -> None:
        vol = RemoteVolume(slug="v", ssh_endpoint="ep", path="/volume1/backups/")
        assert vol.path == "/volume1/backups"

    def test_remote_volume_no_trailing_slash(self) -> None:
        vol = RemoteVolume(slug="v", ssh_endpoint="ep", path="/volume1/backups")
        assert vol.path == "/volume1/backups"

    def test_remote_volume_root_slash(self) -> None:
        vol = RemoteVolume(slug="v", ssh_endpoint="ep", path="/")
        assert vol.path == "/"

    def test_remote_volume_no_tilde_expansion(self) -> None:
        vol = RemoteVolume(slug="v", ssh_endpoint="ep", path="~/backups")
        assert vol.path == "~/backups"

    def test_subdir_trailing_slash(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v", subdir="photos/")
        assert ep.subdir == "photos"

    def test_subdir_leading_slash(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v", subdir="/photos")
        assert ep.subdir == "photos"

    def test_subdir_both_slashes(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v", subdir="/photos/")
        assert ep.subdir == "photos"

    def test_subdir_nested_trailing_slash(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v", subdir="backups/docs/")
        assert ep.subdir == "backups/docs"

    def test_subdir_slash_only_becomes_none(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v", subdir="/")
        assert ep.subdir is None

    def test_subdir_none_stays_none(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v")
        assert ep.subdir is None

    def test_filter_file_tilde_expansion(self) -> None:
        sync = SyncConfig(
            slug="s",
            source="src",
            destination="dst",
            filter_file="~/.config/nbkp/filters.rules",
        )
        assert sync.filter_file == str(
            Path("~/.config/nbkp/filters.rules").expanduser()
        )

    def test_filter_file_no_tilde(self) -> None:
        sync = SyncConfig(
            slug="s",
            source="src",
            destination="dst",
            filter_file="/etc/nbkp/filters.rules",
        )
        assert sync.filter_file == "/etc/nbkp/filters.rules"

    def test_filter_file_none(self) -> None:
        sync = SyncConfig(
            slug="s",
            source="src",
            destination="dst",
        )
        assert sync.filter_file is None

    def test_ssh_key_tilde_expansion(self) -> None:
        ep = SshEndpoint(slug="s", host="example.com", key="~/.ssh/id_rsa")
        assert ep.key == str(Path("~/.ssh/id_rsa").expanduser())

    def test_ssh_key_no_tilde(self) -> None:
        ep = SshEndpoint(slug="s", host="example.com", key="/etc/ssh/key")
        assert ep.key == "/etc/ssh/key"

    def test_ssh_key_none(self) -> None:
        ep = SshEndpoint(slug="s", host="example.com")
        assert ep.key is None
