"""Endpoint resolution types: filter, resolved endpoint, and network classification.

The data side of the endpoint selection performed in :mod:`.resolution`:
the operator's hints (:class:`EndpointFilter`) go in, the SSH endpoint
chosen for each remote volume (:class:`ResolvedEndpoint`) comes out.
"""

from __future__ import annotations

from enum import Enum

from pydantic import ConfigDict, Field

from ..config.protocol import SshEndpoint, _BaseModel


class NetworkType(str, Enum):
    """Network type for endpoint filtering."""

    PRIVATE = "private"
    PUBLIC = "public"


class EndpointFilter(_BaseModel):
    """Endpoint selection filter (not serialized)."""

    model_config = ConfigDict(frozen=True)
    locations: list[str] = Field(default_factory=list)
    exclude_locations: list[str] = Field(default_factory=list)
    network: NetworkType | None = None


class ResolvedEndpoint(_BaseModel):
    """Pre-resolved SSH endpoint with proxy chain."""

    model_config = ConfigDict(frozen=True)
    server: SshEndpoint
    proxy_chain: list[SshEndpoint] = Field(default_factory=list)


ResolvedEndpoints = dict[str, ResolvedEndpoint]
