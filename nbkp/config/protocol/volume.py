"""Volume models: local and remote filesystem volumes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Union

from pydantic import ConfigDict, Field, field_validator

from .base import Slug, _BaseModel


class LocalVolume(_BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["local"] = "local"
    """A local filesystem volume."""
    slug: Slug
    path: str = Field(..., min_length=1)

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: Any) -> str:
        if not isinstance(v, str):
            return v  # type: ignore[no-any-return, return-value]
        return str(Path(v).expanduser())


class RemoteVolume(_BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["remote"] = "remote"
    """A remote volume accessible via SSH."""
    slug: Slug
    ssh_endpoint: str = Field(..., min_length=1)
    ssh_endpoints: Optional[List[str]] = None
    path: str = Field(..., min_length=1)

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: Any) -> str:
        if not isinstance(v, str):
            return v  # type: ignore[no-any-return, return-value]
        stripped = v.rstrip("/")
        return stripped if stripped else "/"


Volume = Annotated[Union[LocalVolume, RemoteVolume], Field(discriminator="type")]
