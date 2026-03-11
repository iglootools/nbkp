"""Base model and common types for the configuration protocol."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


def _to_kebab(name: str) -> str:
    return name.replace("_", "-")


class _BaseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_kebab,
        populate_by_name=True,
    )


Slug = Annotated[
    str,
    Field(
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
    ),
]
