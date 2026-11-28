import os
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any, TypedDict

import pytest
import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    ConfigError,
    ConfigErrorKind,
    ConfigValidationErrorKind,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    RsyncOptions,
    SshConnectionOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    find_config_file,
    load_config,
)
from nbkp.config.loader import ConfigDirFn
from nbkp.config.testkit import config_to_yaml
from nbkp.remote.resolution import resolve_proxy_chain


def _validation_errors(path: Path) -> list[ErrorDetails]:
    """Load ``path``, expecting a validation failure, and return its errors."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(path))
    assert excinfo.value.kind == ConfigErrorKind.VALIDATION
    cause = excinfo.value.__cause__
    assert isinstance(cause, ValidationError)
    return cause.errors()


def _error_types(error: ValidationError) -> set[str]:
    """Return the pydantic error ``type`` codes carried by a ValidationError."""
    return {err["type"] for err in error.errors()}


def _validation_error_types(path: Path) -> set[str]:
    """Return the pydantic error ``type`` codes raised when loading ``path``."""
    return {err["type"] for err in _validation_errors(path)}


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED_EXAMPLES = sorted(
    [
        *(_PROJECT_ROOT / "docs" / "config-examples").glob("*.yaml"),
        _PROJECT_ROOT / "marketing" / "r-backup-intro" / "config.yaml",
    ],
)


class _SearchArgs(TypedDict):
    home: Path
    user_dir: ConfigDirFn
    site_dir: ConfigDirFn


def _search_in(tmp_path: Path) -> _SearchArgs:
    """
    Return search arguments that keep the lookup under *tmp_path*.

    A home there, and platform config directories there, none of which exists yet.
    """
    return {
        "home": tmp_path / "home",
        "user_dir": lambda app: str(tmp_path / "user-config" / app),
        "site_dir": lambda app: str(tmp_path / "site-config" / app),
    }


def _place_config(directory: Path) -> Path:
    path = directory / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(config_to_yaml(Config()))
    return path


class TestFindConfigFile:
    def test_explicit_path(self, sample_config_file: Path) -> None:
        result = find_config_file(str(sample_config_file))
        assert result == sample_config_file

    def test_explicit_path_missing(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            find_config_file("/nonexistent/config.yaml")
        assert excinfo.value.kind == ConfigErrorKind.FILE_NOT_FOUND

    def test_xdg_config(self, tmp_path: Path) -> None:
        xdg = tmp_path / "xdg"
        cfg = _place_config(xdg / "nbkp")
        _place_config(tmp_path / "user-config" / "nbkp")
        result = find_config_file(
            environ={"XDG_CONFIG_HOME": str(xdg)},
            **_search_in(tmp_path),
        )
        assert result == cfg

    def test_home_config_without_xdg(self, tmp_path: Path) -> None:
        cfg = _place_config(tmp_path / "home" / ".config" / "nbkp")
        assert find_config_file(environ={}, **_search_in(tmp_path)) == cfg

    @pytest.mark.parametrize("xdg", ["", "relative"])
    def test_empty_or_relative_xdg_ignored(
        self,
        tmp_path: Path,
        chdir: Callable[[Path], None],
        xdg: str,
    ) -> None:
        workdir = tmp_path / "workdir"
        _place_config(workdir / (xdg or ".") / "nbkp")
        cfg = _place_config(tmp_path / "home" / ".config" / "nbkp")
        chdir(workdir)
        result = find_config_file(
            environ={"XDG_CONFIG_HOME": xdg},
            **_search_in(tmp_path),
        )
        assert result == cfg

    def test_platform_user_dir_before_site_dir(self, tmp_path: Path) -> None:
        user = _place_config(tmp_path / "user-config" / "nbkp")
        _place_config(tmp_path / "site-config" / "nbkp")
        assert find_config_file(environ={}, **_search_in(tmp_path)) == user

    def test_platform_site_dir(self, tmp_path: Path) -> None:
        site = _place_config(tmp_path / "site-config" / "nbkp")
        assert find_config_file(environ={}, **_search_in(tmp_path)) == site

    def test_no_config_found(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as excinfo:
            find_config_file(environ={}, **_search_in(tmp_path))
        assert excinfo.value.kind == ConfigErrorKind.NO_CONFIG_FOUND
        assert excinfo.value.searched[0] == (
            tmp_path / "home" / ".config" / "nbkp" / "config.yaml"
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root traverses any directory")
    def test_untraversable_explicit_path_is_unreadable(self, tmp_path: Path) -> None:
        cfg = _place_config(tmp_path / "locked")
        cfg.parent.chmod(0)
        try:
            with pytest.raises(ConfigError) as excinfo:
                find_config_file(cfg)
        finally:
            cfg.parent.chmod(0o700)
        assert excinfo.value.kind == ConfigErrorKind.UNREADABLE
        assert excinfo.value.path == cfg

    @pytest.mark.skipif(os.geteuid() == 0, reason="root traverses any directory")
    def test_untraversable_candidate_stops_search(self, tmp_path: Path) -> None:
        xdg = tmp_path / "xdg"
        cfg = _place_config(xdg / "nbkp")
        _place_config(tmp_path / "site-config" / "nbkp")
        cfg.parent.chmod(0)
        try:
            with pytest.raises(ConfigError) as excinfo:
                find_config_file(
                    environ={"XDG_CONFIG_HOME": str(xdg)},
                    **_search_in(tmp_path),
                )
        finally:
            cfg.parent.chmod(0o700)
        assert excinfo.value.kind == ConfigErrorKind.UNREADABLE
        assert excinfo.value.path == cfg


class TestLoadConfig:
    def test_full_config(self, sample_config: Config, sample_config_file: Path) -> None:
        cfg = load_config(str(sample_config_file))
        assert cfg == sample_config

    def test_minimal_config(
        self,
        sample_minimal_config: Config,
        sample_minimal_config_file: Path,
    ) -> None:
        cfg = load_config(str(sample_minimal_config_file))
        assert cfg == sample_minimal_config

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("not_a_list:\n  - [invalid")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.kind == ConfigErrorKind.INVALID_YAML

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
    def test_non_utf8_file(self, tmp_path: Path) -> None:
        p = tmp_path / "latin1.yaml"
        p.write_bytes("# caf\u00e9\nvolumes: {}\n".encode("latin-1"))
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.kind == ConfigErrorKind.INVALID_YAML

    def test_unreadable_file(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("syncs: {}\n")
        p.chmod(0)
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.kind == ConfigErrorKind.UNREADABLE

    def test_duplicate_sync_key_is_invalid_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(
            dedent("""\
                syncs:
                  photos: {source: a, destination: b}
                  photos: {source: a, destination: c}
            """),
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.kind == ConfigErrorKind.INVALID_YAML
        assert "duplicate key 'photos'" in str(excinfo.value.detail)
        assert "line 3" in str(excinfo.value.detail)

    def test_duplicate_top_level_section_is_invalid_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("syncs: {}\nsyncs: {}\n")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.kind == ConfigErrorKind.INVALID_YAML
        assert "duplicate key 'syncs'" in str(excinfo.value.detail)

    def test_merge_key_may_be_overridden(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(
            dedent("""\
                volumes:
                  a: &vol {type: local, path: /a}
                  b:
                    <<: *vol
                    path: /b
            """),
        )
        cfg = load_config(str(p))
        assert cfg.volumes["b"].path == "/b"

    def test_not_a_mapping(self, tmp_path: Path) -> None:
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.kind == ConfigErrorKind.NOT_A_MAPPING

    def test_empty_file_names_config_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(p))
        assert excinfo.value.kind == ConfigErrorKind.NOT_A_MAPPING
        assert Path(str(excinfo.value.path)) == p

    def test_invalid_volume_type(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_type.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v:
                type: ftp
                path: /x
            syncs: {}
        """),
        )
        assert "union_tag_invalid" in _validation_error_types(p)

    def test_missing_local_path(self, tmp_path: Path) -> None:
        p = tmp_path / "no_path.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v:
                type: local
            syncs: {}
        """),
        )
        # ``path`` is Optional with a model validator enforcing "path
        # required iff no mount", so a missing path surfaces as a model-level
        # error rather than a field "missing" error.
        assert any(
            err["loc"] == ("volumes", "v", "local")
            and err["type"] == ConfigValidationErrorKind.PATH_REQUIRED
            for err in _validation_errors(p)
        )

    def test_missing_remote_host(self, tmp_path: Path) -> None:
        p = tmp_path / "no_host.yaml"
        p.write_text(
            dedent("""\
            ssh-endpoints:
              s:
                port: 22
            volumes:
              v:
                type: remote
                ssh-endpoint: s
                path: /x
            syncs: {}
        """),
        )
        assert any(
            "host" in err["loc"] and err["type"] == "missing"
            for err in _validation_errors(p)
        )

    def test_unknown_ssh_endpoint_reference(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_server_ref.yaml"
        p.write_text(
            dedent("""\
            ssh-endpoints: {}
            volumes:
              v:
                type: remote
                ssh-endpoint: missing
                path: /x
            syncs: {}
        """),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)

    def test_unknown_volume_reference(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_ref.yaml"
        p.write_text(
            dedent("""\
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
        """),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)

    def test_missing_source_volume(self, tmp_path: Path) -> None:
        p = tmp_path / "no_src_vol.yaml"
        p.write_text(
            dedent("""\
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
        """),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)

    def test_sync_missing_source(self, tmp_path: Path) -> None:
        p = tmp_path / "no_src.yaml"
        p.write_text(
            dedent("""\
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
        """),
        )
        assert any(
            err["loc"] == ("syncs", "s", "source") and err["type"] == "missing"
            for err in _validation_errors(p)
        )

    def test_filter_normalization(self, tmp_path: Path) -> None:
        p = tmp_path / "filters.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v-src:
                type: local
                path: /x
              v-dst:
                type: local
                path: /y
            sync-endpoints:
              ep-src:
                volume: v-src
              ep-dst:
                volume: v-dst
            syncs:
              s:
                source: ep-src
                destination: ep-dst
                filters:
                  - include: "*.jpg"
                  - exclude: "*.tmp"
                  - "H .git"
        """),
        )
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        assert sync.filters == ["+ *.jpg", "- *.tmp", "H .git"]

    def test_filter_merge_normalization(self, tmp_path: Path) -> None:
        p = tmp_path / "merge-filters.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v-src:
                type: local
                path: /x
              v-dst:
                type: local
                path: /y
            sync-endpoints:
              ep-src:
                volume: v-src
              ep-dst:
                volume: v-dst
            syncs:
              s:
                source: ep-src
                destination: ep-dst
                filters:
                  - merge: /etc/nbkp/rules.txt
                  - dir-merge: .rsync-filter
        """),
        )
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        assert sync.filters == [
            "merge /etc/nbkp/rules.txt",
            "dir-merge .rsync-filter",
        ]

    def test_filter_dir_merge_exclude_self(self, tmp_path: Path) -> None:
        p = tmp_path / "dir-merge-exclude.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v-src:
                type: local
                path: /x
              v-dst:
                type: local
                path: /y
            sync-endpoints:
              ep-src:
                volume: v-src
              ep-dst:
                volume: v-dst
            syncs:
              s:
                source: ep-src
                destination: ep-dst
                filters:
                  - dir-merge:
                      path: .rsync-filter
                      exclude-self: true
        """),
        )
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        assert sync.filters == [
            "dir-merge .rsync-filter",
            "- .rsync-filter",
        ]

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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
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
                },
            ),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)

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
                },
            ),
        )
        assert ConfigValidationErrorKind.CIRCULAR_PROXY_JUMP in _validation_error_types(
            p,
        )

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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
        cfg = load_config(str(p))
        chain = resolve_proxy_chain(cfg, cfg.ssh_endpoints["target"])
        assert len(chain) == 1
        assert chain[0].host == "bastion.example.com"

    def test_proxy_jump_and_proxy_jumps_mutual_exclusivity(
        self,
        tmp_path: Path,
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
                },
            ),
        )
        assert ConfigValidationErrorKind.MUTUALLY_EXCLUSIVE in _validation_error_types(
            p,
        )

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
                },
            ),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)

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
                },
            ),
        )
        assert ConfigValidationErrorKind.CIRCULAR_PROXY_JUMP in _validation_error_types(
            p,
        )

    def test_extends_proxy_jumps_overrides_parent_proxy_jump(
        self,
        tmp_path: Path,
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
                },
            ),
        )
        cfg = load_config(str(p))
        chain = resolve_proxy_chain(cfg, cfg.ssh_endpoints["child"])
        assert len(chain) == 2
        assert chain[0].host == "bastion2.example.com"
        assert chain[1].host == "bastion3.example.com"

    def test_extends_merges_connection_options(self, tmp_path: Path) -> None:
        p = tmp_path / "extends_options.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "base": {
                            "host": "nas.lan",
                            "connection-options": {
                                "known-hosts-file": "/etc/nbkp/kh",
                                "strict-host-key-checking": False,
                                "connect-timeout": 30,
                            },
                        },
                        "nas-wan": {
                            "extends": "base",
                            "host": "nas.example.com",
                            "connection-options": {
                                "compress": True,
                                "connect-timeout": 60,
                            },
                        },
                    },
                },
            ),
        )
        options = load_config(p).ssh_endpoints["nas-wan"].connection_options
        assert options == SshConnectionOptions(
            known_hosts_file="/etc/nbkp/kh",
            strict_host_key_checking=False,
            connect_timeout=60,
            compress=True,
        )

    def test_extends_proxy_jump_overrides_parent_proxy_jumps(
        self,
        tmp_path: Path,
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
                },
            ),
        )
        cfg = load_config(str(p))
        chain = resolve_proxy_chain(cfg, cfg.ssh_endpoints["child"])
        assert len(chain) == 1
        assert chain[0].host == "bastion3.example.com"

    def test_proxy_jump_chain_property(self) -> None:
        ep_single = SshEndpoint(
            slug="single",
            host="host.example.com",
            proxy_jump="bastion",
        )
        assert ep_single.proxy_jump_chain == ["bastion"]

        ep_list = SshEndpoint(
            slug="multi",
            host="host.example.com",
            proxy_jumps=["bastion1", "bastion2"],
        )
        assert ep_list.proxy_jump_chain == [
            "bastion1",
            "bastion2",
        ]

        ep_none = SshEndpoint(
            slug="no-proxy",
            host="host.example.com",
        )
        assert ep_none.proxy_jump_chain == []

    def test_invalid_filter_entry(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_filter.yaml"
        p.write_text(
            dedent("""\
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
        """),
        )
        assert ConfigValidationErrorKind.INVALID_FILTER in _validation_error_types(p)

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
                        enabled=True,
                        max_snapshots=10,
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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        dst_ep = cfg.destination_endpoint(sync)
        assert dst_ep.hard_link_snapshots.enabled is True
        assert dst_ep.hard_link_snapshots.max_snapshots is None

    def test_mutual_exclusivity_btrfs_and_hardlink(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyncEndpoint(
                slug="ep",
                volume="v",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
            )
        assert _error_types(excinfo.value) == {
            ConfigValidationErrorKind.MUTUALLY_EXCLUSIVE,
        }

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

    def test_max_snapshots_follows_enabled_mode(self) -> None:
        btrfs = SyncEndpoint(
            slug="ep",
            volume="v",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=3),
            hard_link_snapshots=HardLinkSnapshotConfig(max_snapshots=7),
        )
        hard_link = SyncEndpoint(
            slug="ep",
            volume="v",
            btrfs_snapshots=BtrfsSnapshotConfig(max_snapshots=3),
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True, max_snapshots=7),
        )
        disabled = SyncEndpoint(
            slug="ep",
            volume="v",
            btrfs_snapshots=BtrfsSnapshotConfig(max_snapshots=3),
        )
        assert (btrfs.max_snapshots, hard_link.max_snapshots) == (3, 7)
        assert disabled.max_snapshots is None

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
                },
            ),
        )
        assert ConfigValidationErrorKind.MUTUALLY_EXCLUSIVE in _validation_error_types(
            p,
        )

    def test_location_list_property(self) -> None:
        ep_single = SshEndpoint(
            slug="single",
            host="host.example.com",
            location="home",
        )
        assert ep_single.location_list == ["home"]

        ep_list = SshEndpoint(
            slug="multi",
            host="host.example.com",
            locations=["home", "travel"],
        )
        assert ep_list.location_list == ["home", "travel"]

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
                },
            ),
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
                },
            ),
        )
        cfg = load_config(str(p))
        assert cfg.ssh_endpoints["child"].location_list == [
            "office",
        ]

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
        p.write_text(config_to_yaml(config))
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
        p.write_text(config_to_yaml(config))
        cfg = load_config(str(p))
        sync = cfg.syncs["s"]
        src_ep = cfg.source_endpoint(sync)
        assert src_ep.hard_link_snapshots.enabled is True
        assert src_ep.snapshot_mode == "hard-link"
        assert src_ep.btrfs_snapshots.enabled is False

    def test_unknown_source_endpoint_reference(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_src_ep.yaml"
        p.write_text(
            dedent("""\
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
        """),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)

    def test_unknown_destination_endpoint_reference(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_dst_ep.yaml"
        p.write_text(
            dedent("""\
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
        """),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)

    def test_sync_onto_its_own_source_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            Config(
                volumes={"v": LocalVolume(slug="v", path="/x")},
                sync_endpoints={"ep": SyncEndpoint(slug="ep", volume="v")},
                syncs={"s": SyncConfig(slug="s", source="ep", destination="ep")},
            )
        assert _error_types(excinfo.value) == {ConfigValidationErrorKind.SELF_SYNC}

    def test_duplicate_destination_endpoint(self, tmp_path: Path) -> None:
        p = tmp_path / "dup_dst.yaml"
        p.write_text(
            dedent("""\
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
        """),
        )
        assert ConfigValidationErrorKind.SHARED_DESTINATION in _validation_error_types(
            p,
        )

    def test_cyclic_sync_dependency(self, tmp_path: Path) -> None:
        p = tmp_path / "cycle.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-x:
                volume: v
                subdir: x
              ep-y:
                volume: v
                subdir: y
            syncs:
              a:
                source: ep-x
                destination: ep-y
              b:
                source: ep-y
                destination: ep-x
        """),
        )
        assert ConfigValidationErrorKind.CYCLIC_DEPENDENCY in _validation_error_types(p)

    def test_duplicate_volume_subdir_in_endpoints(self, tmp_path: Path) -> None:
        p = tmp_path / "dup_loc.yaml"
        p.write_text(
            dedent("""\
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
        """),
        )
        assert (
            ConfigValidationErrorKind.DUPLICATE_ENDPOINT_LOCATION
            in _validation_error_types(p)
        )

    def test_duplicate_location_spelled_differently(self, tmp_path: Path) -> None:
        p = tmp_path / "dup_loc_spelling.yaml"
        p.write_text(
            dedent("""\
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
                subdir: ./data
            syncs: {}
        """),
        )
        assert (
            ConfigValidationErrorKind.DUPLICATE_ENDPOINT_LOCATION
            in _validation_error_types(p)
        )

    def test_nested_endpoint_subdir(self, tmp_path: Path) -> None:
        p = tmp_path / "nested.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-parent:
                volume: v
                subdir: backups
              ep-child:
                volume: v
                subdir: backups/photos
            syncs: {}
        """),
        )
        [error] = _validation_errors(p)
        assert error["type"] == ConfigValidationErrorKind.NESTED_ENDPOINT
        # The message names the child first: it is the one to move or drop.
        assert error["msg"].startswith(
            "Sync endpoint 'ep-child' is nested inside 'ep-parent'",
        )

    def test_nested_endpoint_root_contains_subdir(self, tmp_path: Path) -> None:
        p = tmp_path / "root_nested.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-root:
                volume: v
              ep-sub:
                volume: v
                subdir: data
            syncs: {}
        """),
        )
        assert ConfigValidationErrorKind.NESTED_ENDPOINT in _validation_error_types(p)

    def test_nested_endpoint_deeply_nested(self, tmp_path: Path) -> None:
        p = tmp_path / "deep_nested.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep-shallow:
                volume: v
                subdir: a
              ep-deep:
                volume: v
                subdir: a/b/c
            syncs: {}
        """),
        )
        assert ConfigValidationErrorKind.NESTED_ENDPOINT in _validation_error_types(p)

    def test_non_overlapping_prefix_endpoints_allowed(self, tmp_path: Path) -> None:
        config = Config(
            volumes={"v": LocalVolume(slug="v", path="/x")},
            sync_endpoints={
                "ep1": SyncEndpoint(slug="ep1", volume="v", subdir="backup"),
                "ep2": SyncEndpoint(slug="ep2", volume="v", subdir="backup2"),
            },
        )
        p = tmp_path / "prefix_ok.yaml"
        p.write_text(config_to_yaml(config))
        cfg = load_config(str(p))
        assert len(cfg.sync_endpoints) == 2

    def test_nested_paths_on_different_volumes_allowed(self, tmp_path: Path) -> None:
        config = Config(
            volumes={
                "v1": LocalVolume(slug="v1", path="/x"),
                "v2": LocalVolume(slug="v2", path="/y"),
            },
            sync_endpoints={
                "ep1": SyncEndpoint(slug="ep1", volume="v1", subdir="data"),
                "ep2": SyncEndpoint(slug="ep2", volume="v2", subdir="data/sub"),
            },
        )
        p = tmp_path / "diff_vol.yaml"
        p.write_text(config_to_yaml(config))
        cfg = load_config(str(p))
        assert len(cfg.sync_endpoints) == 2

    def test_shared_proxy_hop_is_not_circular(self, tmp_path: Path) -> None:
        # A diamond (target -> [b1, b2], b1 -> b2) reaches b2 twice without
        # a cycle, so it must not be reported as a circular chain.
        config = Config(
            ssh_endpoints={
                "b2": SshEndpoint(slug="b2", host="b2.example.com"),
                "b1": SshEndpoint(slug="b1", host="b1.example.com", proxy_jump="b2"),
                "target": SshEndpoint(
                    slug="target",
                    host="target.internal",
                    proxy_jumps=["b1", "b2"],
                ),
            },
        )
        p = tmp_path / "diamond.yaml"
        p.write_text(config_to_yaml(config))
        assert load_config(str(p)).ssh_endpoints["target"].proxy_jump_chain == [
            "b1",
            "b2",
        ]

    def test_extends_circular(self, tmp_path: Path) -> None:
        p = tmp_path / "circular_extends.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "a": {"host": "a.example.com", "extends": "b"},
                        "b": {"host": "b.example.com", "extends": "a"},
                    },
                },
            ),
        )
        [error] = _validation_errors(p)
        assert error["type"] == ConfigValidationErrorKind.CIRCULAR_EXTENDS
        assert error["msg"] == "Circular extends chain: a -> b -> a"

    def test_extends_does_not_inherit_parent_slug(self, tmp_path: Path) -> None:
        p = tmp_path / "extends_slug.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "base": {"slug": "base", "host": "h.example.com"},
                        "a": {"extends": "base"},
                    },
                },
            ),
        )
        endpoint = load_config(str(p)).ssh_endpoints["a"]
        assert (endpoint.slug, endpoint.host) == ("a", "h.example.com")

    @pytest.mark.parametrize("extends", [["base"], {"x": 1}])
    def test_extends_not_a_string(self, tmp_path: Path, extends: object) -> None:
        p = tmp_path / "extends_list.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "base": {"host": "h.example.com"},
                        "a": {"host": "a.example.com", "extends": extends},
                    },
                },
            ),
        )
        [error] = _validation_errors(p)
        assert (error["loc"], error["type"]) == (
            ("ssh-endpoints", "a", "extends"),
            "string_type",
        )

    def test_extends_unknown_endpoint(self, tmp_path: Path) -> None:
        p = tmp_path / "unknown_extends.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "ssh-endpoints": {
                        "a": {"host": "a.example.com", "extends": "missing"},
                    },
                },
            ),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)

    def test_sync_endpoint_unknown_volume(self, tmp_path: Path) -> None:
        p = tmp_path / "ep_bad_vol.yaml"
        p.write_text(
            dedent("""\
            volumes:
              v:
                type: local
                path: /x
            sync-endpoints:
              ep:
                volume: nonexistent
            syncs: {}
        """),
        )
        assert ConfigValidationErrorKind.UNKNOWN_REFERENCE in _validation_error_types(p)


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

    def test_subdir_dot_and_empty_segments_dropped(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v", subdir="./backups//./docs/.")
        assert ep.subdir == "backups/docs"

    def test_subdir_dot_only_becomes_none(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v", subdir="./")
        assert ep.subdir is None

    @pytest.mark.parametrize("subdir", ["..", "../etc", "photos/../../etc", "a/.."])
    def test_subdir_parent_segment_rejected(self, subdir: str) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyncEndpoint(slug="ep", volume="v", subdir=subdir)
        assert _error_types(excinfo.value) == {ConfigValidationErrorKind.INVALID_SUBDIR}

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
            Path("~/.config/nbkp/filters.rules").expanduser(),
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

    def test_merge_filter_tilde_expansion(self) -> None:
        sync = SyncConfig.model_validate(
            {
                "slug": "s",
                "source": "src",
                "destination": "dst",
                "filters": [{"merge": "~/rules.txt"}],
            },
        )
        assert sync.filters == [f"merge {Path('~/rules.txt').expanduser()}"]

    def test_merge_filter_no_tilde(self) -> None:
        sync = SyncConfig.model_validate(
            {
                "slug": "s",
                "source": "src",
                "destination": "dst",
                "filters": [{"merge": "/etc/rules.txt"}],
            },
        )
        assert sync.filters == ["merge /etc/rules.txt"]

    def test_dir_merge_no_tilde_expansion(self) -> None:
        sync = SyncConfig.model_validate(
            {
                "slug": "s",
                "source": "src",
                "destination": "dst",
                "filters": [{"dir-merge": ".rsync-filter"}],
            },
        )
        assert sync.filters == ["dir-merge .rsync-filter"]

    def test_dir_merge_unknown_option_raises(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyncConfig.model_validate(
                {
                    "slug": "s",
                    "source": "src",
                    "destination": "dst",
                    "filters": [{"dir-merge": {"path": ".rsync-filter", "bad": True}}],
                },
            )
        assert _error_types(excinfo.value) == {
            ConfigValidationErrorKind.UNKNOWN_DIR_MERGE_OPTION,
        }

    def test_ssh_key_tilde_expansion(self) -> None:
        ep = SshEndpoint(slug="s", host="example.com", key="~/.ssh/id_rsa")
        assert ep.key == str(Path("~/.ssh/id_rsa").expanduser())

    def test_ssh_key_no_tilde(self) -> None:
        ep = SshEndpoint(slug="s", host="example.com", key="/etc/ssh/key")
        assert ep.key == "/etc/ssh/key"

    def test_ssh_key_none(self) -> None:
        ep = SshEndpoint(slug="s", host="example.com")
        assert ep.key is None

    def test_known_hosts_file_tilde_expansion(self) -> None:
        """Paramiko's load_host_keys does not expand ``~`` the way ssh does."""
        opts = SshConnectionOptions(known_hosts_file="~/.ssh/nbkp_known_hosts")
        assert opts.known_hosts_file == str(
            Path("~/.ssh/nbkp_known_hosts").expanduser(),
        )

    def test_known_hosts_file_dev_null_unchanged(self) -> None:
        opts = SshConnectionOptions(known_hosts_file="/dev/null")
        assert opts.known_hosts_file == "/dev/null"


class TestNoSilentCoercion:
    """Wrongly-typed values are rejected, not silently replaced by a default."""

    def test_non_string_subdir_rejected(self) -> None:
        # An unquoted ``subdir: 2024`` used to collapse to the volume root.
        with pytest.raises(ValidationError) as excinfo:
            SyncEndpoint.model_validate({"slug": "ep", "volume": "v", "subdir": 2024})
        assert _error_types(excinfo.value) == {"string_type"}

    def test_non_string_filter_file_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyncConfig.model_validate(
                {"slug": "s", "source": "a", "destination": "b", "filter-file": 42},
            )
        assert _error_types(excinfo.value) == {"string_type"}

    def test_non_string_ssh_key_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SshEndpoint.model_validate(
                {"slug": "s", "host": "example.com", "key": ["~/.ssh/id"]},
            )
        assert _error_types(excinfo.value) == {"string_type"}

    def test_filters_as_single_string_rejected(self) -> None:
        # A bare string used to be iterated into one rule per character.
        with pytest.raises(ValidationError) as excinfo:
            SyncConfig.model_validate(
                {"slug": "s", "source": "a", "destination": "b", "filters": "- *.tmp"},
            )
        assert _error_types(excinfo.value) == {
            ConfigValidationErrorKind.FILTERS_NOT_A_LIST,
        }

    def test_filters_as_mapping_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyncConfig.model_validate(
                {
                    "slug": "s",
                    "source": "a",
                    "destination": "b",
                    "filters": {"exclude": "*.tmp"},
                },
            )
        assert _error_types(excinfo.value) == {
            ConfigValidationErrorKind.FILTERS_NOT_A_LIST,
        }

    def test_filters_null_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyncConfig.model_validate(
                {"slug": "s", "source": "a", "destination": "b", "filters": None},
            )
        assert _error_types(excinfo.value) == {"list_type"}

    def test_invalid_filter_entry_code(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyncConfig.model_validate(
                {
                    "slug": "s",
                    "source": "a",
                    "destination": "b",
                    "filters": [{"badkey": "value"}],
                },
            )
        assert _error_types(excinfo.value) == {ConfigValidationErrorKind.INVALID_FILTER}

    @pytest.mark.parametrize(
        "item",
        [
            {"include": "*.jpg", "exclude": "*"},
            {"merge": "~/.rsync-filter", "exclude": "*"},
            {"dir-merge": ".rsync-filter", "include": "*"},
        ],
    )
    def test_filter_mapping_with_several_keys_rejected(
        self,
        item: dict[str, str],
    ) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyncConfig.model_validate(
                {"slug": "s", "source": "a", "destination": "b", "filters": [item]},
            )
        assert _error_types(excinfo.value) == {ConfigValidationErrorKind.INVALID_FILTER}


class TestUnknownKeys:
    """A misspelt key is rejected where it sits, not dropped for the defaults."""

    @staticmethod
    def _raw_config(sample_config: Config) -> dict[str, Any]:
        return sample_config.model_dump(by_alias=True, mode="json")

    def test_unknown_top_level_key_rejected(
        self,
        sample_config: Config,
        tmp_path: Path,
    ) -> None:
        raw = {**self._raw_config(sample_config), "credential-providr": "command"}
        p = tmp_path / "typo.yaml"
        p.write_text(yaml.safe_dump(raw))
        errors = _validation_errors(p)
        assert [(e["type"], e["loc"]) for e in errors] == [
            ("extra_forbidden", ("credential-providr",)),
        ]

    def test_unknown_nested_key_rejected(
        self,
        sample_config: Config,
        tmp_path: Path,
    ) -> None:
        raw = self._raw_config(sample_config)
        slug, endpoint = next(iter(raw["sync-endpoints"].items()))
        endpoint["hard-link-snapshots"] = {"enabled": True, "max-snapshot": 5}
        p = tmp_path / "typo.yaml"
        p.write_text(yaml.safe_dump(raw))
        errors = _validation_errors(p)
        assert [(e["type"], e["loc"]) for e in errors] == [
            (
                "extra_forbidden",
                ("sync-endpoints", slug, "hard-link-snapshots", "max-snapshot"),
            ),
        ]


class TestSlugs:
    def test_slug_differing_from_key_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "slug.yaml"
        p.write_text(
            dedent("""\
            volumes:
              a:
                type: local
                slug: x
                path: /mnt/a
              b:
                type: local
                slug: x
                path: /mnt/b
        """),
        )
        errors = _validation_errors(p)
        assert [(e["type"], e["loc"]) for e in errors] == [
            (ConfigValidationErrorKind.SLUG_MISMATCH, ("volumes",)),
        ]

    def test_slug_equal_to_key_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "slug.yaml"
        p.write_text(
            dedent("""\
            volumes:
              a:
                type: local
                slug: a
                path: /mnt/a
        """),
        )
        assert load_config(p).volumes["a"].slug == "a"


class TestShippedExamples:
    def test_examples_found(self) -> None:
        assert len(_SHIPPED_EXAMPLES) >= 2
        assert all(path.is_file() for path in _SHIPPED_EXAMPLES)

    @pytest.mark.parametrize(
        "path",
        _SHIPPED_EXAMPLES,
        ids=lambda p: str(p.relative_to(_PROJECT_ROOT)),
    )
    def test_example_loads(self, path: Path) -> None:
        load_config(path)


class TestFrozenModels:
    def test_config_is_immutable(self, sample_config: Config) -> None:
        with pytest.raises(ValidationError) as excinfo:
            sample_config.credential_command = ["x"]
        assert _error_types(excinfo.value) == {"frozen_instance"}

    def test_sync_endpoint_is_immutable(self) -> None:
        ep = SyncEndpoint(slug="ep", volume="v")
        with pytest.raises(ValidationError):
            ep.subdir = "x"
