"""Tests for nbkp.config.resolution."""

from __future__ import annotations

from nbkp.config import (
    Config,
    RemoteVolume,
    SshEndpoint,
)
from nbkp.config.epresolution import EndpointFilter
from nbkp.remote.resolution import (
    resolve_all_endpoints,
    resolve_endpoint_for_volume,
)


class TestResolveEndpoint:
    def test_location_filter_with_locations(
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

    def test_exclude_location(
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

    def test_exclude_and_include_location(
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

    def test_exclude_all_falls_back(
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
