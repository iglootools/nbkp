"""Shared base model, slug type and raw-input type guards for the config schema."""

from typing import Annotated, Any, TypeIs

from pydantic import BaseModel, ConfigDict, Field


def _to_kebab(name: str) -> str:
    return name.replace("_", "-")


# ``isinstance`` narrows a raw config value to ``dict[Unknown, Unknown]``, which
# strict pyright rejects wherever it is used. These narrow to ``Any`` items
# instead: the values are unvalidated input until pydantic checks them.
def is_raw_mapping(value: object) -> TypeIs[dict[Any, Any]]:
    """Return whether a raw, unvalidated config value is a ``dict``."""
    return isinstance(value, dict)


def is_raw_sequence(value: object) -> TypeIs[list[Any] | tuple[Any, ...]]:
    """Return whether a raw, unvalidated config value is a ``list`` or ``tuple``."""
    return isinstance(value, (list, tuple))


class ConfigModel(BaseModel):
    """
    The base of every config model.

    Fields are read from kebab-case keys (``ssh-endpoint``) or their Python
    names, instances are immutable, and unknown keys are rejected so that a
    typo in the config file is an error rather than a silently ignored key.
    """

    model_config = ConfigDict(
        alias_generator=_to_kebab,
        populate_by_name=True,
        frozen=True,
        extra="forbid",
    )


Slug = Annotated[
    str,
    Field(
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
    ),
]
