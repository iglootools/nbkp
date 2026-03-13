"""Tests for Config.orphan_* detection methods."""

from __future__ import annotations

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)


def _minimal_config(
    ssh_endpoints: dict[str, SshEndpoint] | None = None,
    volumes: dict[str, LocalVolume | RemoteVolume] | None = None,
    sync_endpoints: dict[str, SyncEndpoint] | None = None,
    syncs: dict[str, SyncConfig] | None = None,
) -> Config:
    return Config(
        ssh_endpoints=ssh_endpoints or {},
        volumes=volumes or {},
        sync_endpoints=sync_endpoints or {},
        syncs=syncs or {},
    )


class TestOrphanSshEndpoints:
    def test_all_used_by_volume(self) -> None:
        cfg = _minimal_config(
            ssh_endpoints={
                "ep": SshEndpoint(slug="ep", host="h"),
            },
            volumes={
                "v": RemoteVolume(slug="v", ssh_endpoint="ep", path="/data"),
            },
        )
        assert cfg.orphan_ssh_endpoints() == []

    def test_used_via_ssh_endpoints_list(self) -> None:
        cfg = _minimal_config(
            ssh_endpoints={
                "primary": SshEndpoint(slug="primary", host="h1"),
                "fallback": SshEndpoint(slug="fallback", host="h2"),
            },
            volumes={
                "v": RemoteVolume(
                    slug="v",
                    ssh_endpoint="primary",
                    ssh_endpoints=["primary", "fallback"],
                    path="/data",
                ),
            },
        )
        assert cfg.orphan_ssh_endpoints() == []

    def test_used_as_proxy_jump(self) -> None:
        cfg = _minimal_config(
            ssh_endpoints={
                "bastion": SshEndpoint(slug="bastion", host="jump"),
                "target": SshEndpoint(slug="target", host="h", proxy_jump="bastion"),
            },
            volumes={
                "v": RemoteVolume(slug="v", ssh_endpoint="target", path="/data"),
            },
        )
        assert cfg.orphan_ssh_endpoints() == []

    def test_orphan_detected(self) -> None:
        cfg = _minimal_config(
            ssh_endpoints={
                "used": SshEndpoint(slug="used", host="h1"),
                "orphan": SshEndpoint(slug="orphan", host="h2"),
            },
            volumes={
                "v": RemoteVolume(slug="v", ssh_endpoint="used", path="/data"),
            },
        )
        assert cfg.orphan_ssh_endpoints() == ["orphan"]

    def test_no_volumes_all_orphan(self) -> None:
        cfg = _minimal_config(
            ssh_endpoints={
                "a": SshEndpoint(slug="a", host="h1"),
                "b": SshEndpoint(slug="b", host="h2"),
            },
        )
        assert cfg.orphan_ssh_endpoints() == ["a", "b"]

    def test_empty(self) -> None:
        cfg = _minimal_config()
        assert cfg.orphan_ssh_endpoints() == []


class TestOrphanVolumes:
    def test_all_used(self) -> None:
        cfg = _minimal_config(
            volumes={"v": LocalVolume(slug="v", path="/a")},
            sync_endpoints={"ep": SyncEndpoint(slug="ep", volume="v")},
        )
        assert cfg.orphan_volumes() == []

    def test_orphan_detected(self) -> None:
        cfg = _minimal_config(
            volumes={
                "used": LocalVolume(slug="used", path="/a"),
                "orphan": LocalVolume(slug="orphan", path="/b"),
            },
            sync_endpoints={"ep": SyncEndpoint(slug="ep", volume="used")},
        )
        assert cfg.orphan_volumes() == ["orphan"]

    def test_empty(self) -> None:
        cfg = _minimal_config()
        assert cfg.orphan_volumes() == []


class TestOrphanSyncEndpoints:
    def test_all_used(self) -> None:
        cfg = _minimal_config(
            volumes={
                "v1": LocalVolume(slug="v1", path="/a"),
                "v2": LocalVolume(slug="v2", path="/b"),
            },
            sync_endpoints={
                "src": SyncEndpoint(slug="src", volume="v1"),
                "dst": SyncEndpoint(slug="dst", volume="v2"),
            },
            syncs={
                "s": SyncConfig(slug="s", source="src", destination="dst"),
            },
        )
        assert cfg.orphan_sync_endpoints() == []

    def test_orphan_detected(self) -> None:
        cfg = _minimal_config(
            volumes={
                "v1": LocalVolume(slug="v1", path="/a"),
                "v2": LocalVolume(slug="v2", path="/b"),
                "v3": LocalVolume(slug="v3", path="/c"),
            },
            sync_endpoints={
                "src": SyncEndpoint(slug="src", volume="v1"),
                "dst": SyncEndpoint(slug="dst", volume="v2"),
                "orphan": SyncEndpoint(slug="orphan", volume="v3"),
            },
            syncs={
                "s": SyncConfig(slug="s", source="src", destination="dst"),
            },
        )
        assert cfg.orphan_sync_endpoints() == ["orphan"]

    def test_empty(self) -> None:
        cfg = _minimal_config()
        assert cfg.orphan_sync_endpoints() == []
