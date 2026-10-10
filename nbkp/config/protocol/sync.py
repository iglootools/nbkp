"""Sync configuration and rsync options."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from .base import Slug, _BaseModel
from .errors import ConfigValidationCode, config_error


class RsyncOptions(_BaseModel):
    """Rsync flag configuration for a sync operation."""

    model_config = ConfigDict(frozen=True)
    compress: bool = Field(default=False, description="Enable rsync `--compress`")
    checksum: bool = Field(default=False, description="Enable rsync `--checksum`")
    default_options_override: list[str] | None = Field(
        default=None, description="Replace default rsync flags entirely"
    )
    extra_options: list[str] = Field(
        default_factory=list,
        description="Additional flags appended after defaults",
    )


_DIR_MERGE_OPTS = {"path", "exclude-self"}


def _validate_dir_merge_opts(opts: dict[str, Any]) -> None:
    """Reject unknown keys in a dir-merge dict."""
    unknown = set(opts) - _DIR_MERGE_OPTS
    if unknown:
        raise config_error(
            ConfigValidationCode.UNKNOWN_DIR_MERGE_OPTION,
            f"Unknown dir-merge option(s): {', '.join(sorted(unknown))}."
            f" Allowed: {', '.join(sorted(_DIR_MERGE_OPTS))}",
        )


def _filter_rules(item: Any) -> list[str]:
    """Translate one ``filters`` entry into its rsync filter rule(s).

    Most entries map to a single rule; ``dir-merge`` with ``exclude-self``
    maps to two (the merge plus an exclude of the filter file itself).
    """
    match item:
        case str():
            return [item]
        case {"include": str() as pattern}:
            return [f"+ {pattern}"]
        case {"exclude": str() as pattern}:
            return [f"- {pattern}"]
        case {"merge": str() as path}:
            return [f"merge {Path(path).expanduser()}"]
        case {"dir-merge": str() as path}:
            return [f"dir-merge {path}"]
        case {"dir-merge": dict() as opts} if "path" in opts:
            _validate_dir_merge_opts(opts)
            return [
                f"dir-merge {opts['path']}",
                *([f"- {opts['path']}"] if opts.get("exclude-self") else []),
            ]
        case _:
            raise config_error(
                ConfigValidationCode.INVALID_FILTER,
                "Filter must be a string or a dict with"
                " 'include'/'exclude'/'merge'/'dir-merge'"
                f" key, got: {item!r}",
            )


class SyncConfig(_BaseModel):
    """Configuration for a single sync operation."""

    slug: Slug
    source: str = Field(..., min_length=1, description="Source sync endpoint slug")
    destination: str = Field(
        ..., min_length=1, description="Destination sync endpoint slug"
    )
    enabled: bool = Field(default=True, description="Whether this sync is active")
    rsync_options: RsyncOptions = Field(
        default_factory=lambda: RsyncOptions(),
        description="Rsync flag configuration",
    )
    filters: list[str] = Field(
        default_factory=list, description="Rsync filter rules (see below)"
    )
    filter_file: str | None = Field(
        default=None, description="Path to external rsync filter file"
    )

    @field_validator("filter_file", mode="before")
    @classmethod
    def normalize_filter_file(cls, v: Any) -> Any:
        # Non-strings pass through so pydantic rejects them rather than
        # silently dropping the filter file.
        match v:
            case str():
                return str(Path(v).expanduser())
            case _:
                return v

    @field_validator("filters", mode="before")
    @classmethod
    def normalize_filters(cls, v: Any) -> Any:
        match v:
            case list() | tuple():
                return [rule for item in v for rule in _filter_rules(item)]
            case dict():
                raise config_error(
                    ConfigValidationCode.FILTERS_NOT_A_LIST,
                    "filters must be a list, not a mapping. Add '- ' before each"
                    f" filter rule. Got: {v!r}",
                )
            case str():
                # Iterating a string would yield one filter rule per character.
                raise config_error(
                    ConfigValidationCode.FILTERS_NOT_A_LIST,
                    "filters must be a list, not a single string. Write it as"
                    f" a one-item list: filters: [{v!r}]",
                )
            case _:
                # Let pydantic report the type mismatch (e.g. null, a number).
                return v
