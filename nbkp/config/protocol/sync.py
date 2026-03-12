"""Sync configuration and rsync options."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from pydantic import ConfigDict, Field, field_validator

from .base import Slug, _BaseModel


class RsyncOptions(_BaseModel):
    """Rsync flag configuration for a sync operation."""

    model_config = ConfigDict(frozen=True)
    compress: bool = False
    checksum: bool = True
    default_options_override: Optional[List[str]] = None
    extra_options: List[str] = Field(default_factory=list)


_DIR_MERGE_OPTS = {"path", "exclude-self"}


def _validate_dir_merge_opts(opts: dict[str, Any]) -> None:
    """Raise ValueError for unknown keys in a dir-merge dict."""
    unknown = set(opts) - _DIR_MERGE_OPTS
    if unknown:
        raise ValueError(
            f"Unknown dir-merge option(s): {', '.join(sorted(unknown))}."
            f" Allowed: {', '.join(sorted(_DIR_MERGE_OPTS))}"
        )


class SyncConfig(_BaseModel):
    """Configuration for a single sync operation."""

    slug: Slug
    source: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    enabled: bool = True
    rsync_options: RsyncOptions = Field(default_factory=lambda: RsyncOptions())
    filters: List[str] = Field(default_factory=list)
    filter_file: Optional[str] = None

    @field_validator("filter_file", mode="before")
    @classmethod
    def normalize_filter_file(cls, v: Any) -> str | None:
        if not isinstance(v, str):
            return None
        return str(Path(v).expanduser())

    @field_validator("filters", mode="before")
    @classmethod
    def normalize_filters(cls, v: Any) -> list[str]:
        result: list[str] = []
        for item in v:
            match item:
                case str():
                    result.append(item)
                case {"include": str() as pattern}:
                    result.append(f"+ {pattern}")
                case {"exclude": str() as pattern}:
                    result.append(f"- {pattern}")
                case {"merge": str() as path}:
                    result.append(f"merge {Path(path).expanduser()}")
                case {"dir-merge": str() as path}:
                    result.append(f"dir-merge {path}")
                case {"dir-merge": dict() as opts} if "path" in opts:
                    _validate_dir_merge_opts(opts)
                    result.append(f"dir-merge {opts['path']}")
                    if opts.get("exclude-self"):
                        result.append(f"- {opts['path']}")
                case _:
                    raise ValueError(
                        f"Filter must be a string or a dict with"
                        f" 'include'/'exclude'/'merge'/'dir-merge'"
                        f" key, got: {item!r}"
                    )
        return result
