"""Volume models: local and remote filesystem volumes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Union

from pydantic import ConfigDict, Field, field_validator

from .base import Slug, _BaseModel


class LocalVolume(_BaseModel):
    """A local filesystem volume."""

    model_config = ConfigDict(frozen=True)
    type: Literal["local"] = "local"
    slug: Slug
    path: str = Field(
        ...,
        min_length=1,
        description=(
            "Absolute path to the volume."
            " `~` is expanded to the user's home directory."
            " Trailing slashes are stripped."
        ),
    )

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: Any) -> str:
        if not isinstance(v, str):
            return v  # type: ignore[no-any-return, return-value]
        return str(Path(v).expanduser())


class RemoteVolume(_BaseModel):
    """A remote volume accessible via SSH."""

    model_config = ConfigDict(frozen=True)
    type: Literal["remote"] = "remote"
    slug: Slug
    ssh_endpoint: str = Field(
        ..., min_length=1, description="Primary SSH endpoint slug"
    )
    ssh_endpoints: Optional[List[str]] = Field(
        default=None, description="Candidate endpoints for auto-selection"
    )
    path: str = Field(
        ...,
        min_length=1,
        description=(
            "Absolute path on the remote host."
            " Trailing slashes are stripped."
            " `~` is not expanded"
            " (it refers to the remote user's home"
            " and is resolved by SSH/rsync)."
        ),
    )

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: Any) -> str:
        if not isinstance(v, str):
            return v  # type: ignore[no-any-return, return-value]
        stripped = v.rstrip("/")
        return stripped if stripped else "/"


Volume = Annotated[Union[LocalVolume, RemoteVolume], Field(discriminator="type")]
