"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)

pytest_plugins = ["tests._docker_fixtures"]


def config_to_yaml(config: Config) -> str:
    """Convert a Config to a YAML string."""
    return yaml.safe_dump(
        config.model_dump(by_alias=True),
        default_flow_style=False,
        sort_keys=False,
    )


def _sample_config() -> Config:
    """Build the full sample Config."""
    return Config(
        ssh_endpoints={
            "nas-server": SshEndpoint(
                slug="nas-server",
                host="nas.example.com",
                port=5022,
                user="backup",
                key="~/.ssh/key",
            ),
        },
        volumes={
            "local-data": LocalVolume(slug="local-data", path="/mnt/data"),
            "nas": RemoteVolume(
                slug="nas",
                ssh_endpoint="nas-server",
                path="/volume1/backups",
            ),
        },
        sync_endpoints={
            "local-photos": SyncEndpoint(
                slug="local-photos",
                volume="local-data",
                subdir="photos",
            ),
            "nas-photos": SyncEndpoint(
                slug="nas-photos",
                volume="nas",
                subdir="photos-backup",
            ),
        },
        syncs={
            "photos-to-nas": SyncConfig(
                slug="photos-to-nas",
                source="local-photos",
                destination="nas-photos",
                enabled=True,
                filters=["+ *.jpg", "- *.tmp"],
                filter_file=("~/.config/nbkp/filters/photos.rules"),
            ),
        },
    )


def _sample_minimal_config() -> Config:
    """Build the minimal sample Config."""
    return Config(
        volumes={
            "src": LocalVolume(slug="src", path="/src"),
            "dst": LocalVolume(slug="dst", path="/dst"),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
        },
        syncs={
            "s1": SyncConfig(
                slug="s1",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )


@pytest.fixture()
def sample_config_file(tmp_path: Path) -> Path:
    """Write sample YAML config to a temp file."""
    p = tmp_path / "config.yaml"
    p.write_text(config_to_yaml(_sample_config()))
    return p


@pytest.fixture()
def sample_minimal_config_file(tmp_path: Path) -> Path:
    """Write minimal YAML config to a temp file."""
    p = tmp_path / "config.yaml"
    p.write_text(config_to_yaml(_sample_minimal_config()))
    return p


@pytest.fixture()
def local_volume() -> LocalVolume:
    return LocalVolume(slug="local-data", path="/mnt/data")


@pytest.fixture()
def ssh_endpoint() -> SshEndpoint:
    return SshEndpoint(
        slug="nas-server",
        host="nas.example.com",
        port=5022,
        user="backup",
        key="~/.ssh/key",
    )


@pytest.fixture()
def ssh_endpoint_minimal() -> SshEndpoint:
    return SshEndpoint(
        slug="nas2-server",
        host="nas2.example.com",
    )


@pytest.fixture()
def remote_volume() -> RemoteVolume:
    return RemoteVolume(
        slug="nas",
        ssh_endpoint="nas-server",
        path="/volume1/backups",
    )


@pytest.fixture()
def remote_volume_minimal() -> RemoteVolume:
    return RemoteVolume(
        slug="nas2",
        ssh_endpoint="nas2-server",
        path="/backups",
    )


@pytest.fixture()
def sample_config() -> Config:
    return _sample_config()


@pytest.fixture()
def sample_minimal_config() -> Config:
    return _sample_minimal_config()
