"""Endpoint resolution types: filter, resolved endpoint, and network classification.

These data structs do not truly belong to config, but since config is a universal dependency,
not worth creating another top-level module at this point.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import ConfigDict, Field

from .protocol import SshEndpoint, _BaseModel


class NetworkType(str, Enum):
    """Network type for endpoint filtering."""

    PRIVATE = "private"
    PUBLIC = "public"


class EndpointFilter(_BaseModel):
    """Endpoint selection filter (not serialized)."""

    model_config = ConfigDict(frozen=True)
    locations: List[str] = Field(default_factory=list)
    exclude_locations: List[str] = Field(default_factory=list)
    network: Optional[NetworkType] = None


class ResolvedEndpoint(_BaseModel):
    """Pre-resolved SSH endpoint with proxy chain."""

    model_config = ConfigDict(frozen=True)
    server: SshEndpoint
    proxy_chain: list[SshEndpoint] = Field(default_factory=list)


ResolvedEndpoints = dict[str, ResolvedEndpoint]
