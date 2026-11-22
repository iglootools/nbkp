"""Endpoint resolution types: filter, resolved endpoint, and network classification.

The data side of the endpoint selection performed in :mod:`.resolution`:
the operator's hints (:class:`EndpointFilter`) go in, the SSH endpoint
chosen for each remote volume (:class:`ResolvedEndpoint`) comes out.
"""

from enum import StrEnum

from pydantic import Field

from nbkp.config.protocol import ConfigModel, RemoteVolume, SshEndpoint, Volume


class NetworkType(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"


class EndpointFilter(ConfigModel):
    """Not serialized."""

    locations: list[str] = Field(default_factory=list)
    exclude_locations: list[str] = Field(default_factory=list)
    network: NetworkType | None = None


class ResolvedEndpoint(ConfigModel):
    server: SshEndpoint
    proxy_chain: list[SshEndpoint] = Field(default_factory=list[SshEndpoint])


type ResolvedEndpoints = dict[str, ResolvedEndpoint]


def location_excluded(volume: Volume, resolved: ResolvedEndpoints) -> bool:
    """Whether endpoint resolution skipped *volume* for its location.

    Such a remote volume has no resolved endpoint, so nothing may try to
    reach it.
    """
    return isinstance(volume, RemoteVolume) and volume.slug not in resolved
