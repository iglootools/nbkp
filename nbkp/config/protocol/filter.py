"""Endpoint filtering: network type and endpoint filter."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import ConfigDict, Field

from .base import _BaseModel


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
