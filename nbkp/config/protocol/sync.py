"""The config models of a sync and its rsync options and filter rules."""

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from nbkp.config.protocol.base import ConfigModel, Slug, is_raw_mapping, is_raw_sequence
from nbkp.config.protocol.errors import ConfigValidationErrorKind, config_error


class RsyncOptions(ConfigModel):
    """The rsync flags a sync adds to, or uses instead of, nbkp's defaults."""

    compress: bool = Field(default=False, description="Enable rsync `--compress`")
    checksum: bool = Field(default=False, description="Enable rsync `--checksum`")
    default_options_override: list[str] | None = Field(
        default=None,
        description="Replace default rsync flags entirely",
    )
    extra_options: list[str] = Field(
        default_factory=list,
        description="Additional flags appended after defaults",
    )


_DIR_MERGE_OPTS = {"path", "exclude-self"}


def _validate_dir_merge_opts(opts: dict[str, Any]) -> None:
    unknown = set(opts) - _DIR_MERGE_OPTS
    if unknown:
        raise config_error(
            ConfigValidationErrorKind.UNKNOWN_DIR_MERGE_OPTION,
            f"Unknown dir-merge option(s): {', '.join(sorted(unknown))}."
            f" Allowed: {', '.join(sorted(_DIR_MERGE_OPTS))}",
        )


def _filter_rules(item: object) -> list[str]:
    """
    Translate one ``filters`` entry into its rsync filter rule(s).

    Most entries map to a single rule; ``dir-merge`` with ``exclude-self``
    maps to two (the merge plus an exclude of the filter file itself).
    """
    match item:
        case str():
            return [item]
        case _ if is_raw_mapping(item) and len(item) != 1:
            # Mapping patterns ignore extra keys, so without this guard
            # ``{include: a, exclude: b}`` would keep the include and drop
            # the exclude.
            raise _invalid_filter(item)
        case {"include": str() as pattern}:
            return [f"+ {pattern}"]
        case {"exclude": str() as pattern}:
            return [f"- {pattern}"]
        case {"merge": str() as path}:
            return [f"merge {Path(path).expanduser()}"]
        case {"dir-merge": str() as path}:
            return [f"dir-merge {path}"]
        case {"dir-merge": object() as opts} if is_raw_mapping(opts) and "path" in opts:
            _validate_dir_merge_opts(opts)
            return [
                f"dir-merge {opts['path']}",
                *([f"- {opts['path']}"] if opts.get("exclude-self") else []),
            ]
        case _:
            raise _invalid_filter(item)


def _invalid_filter(item: object) -> PydanticCustomError:
    return config_error(
        ConfigValidationErrorKind.INVALID_FILTER,
        "Filter must be a string or a dict with exactly one"
        " 'include'/'exclude'/'merge'/'dir-merge'"
        f" key, got: {item!r}",
    )


class SyncConfig(ConfigModel):
    """
    One sync: rsync from a source sync endpoint to a destination sync endpoint.

    ``filters`` holds rsync filter rules once validated: the config's
    ``include``/``exclude``/``merge``/``dir-merge`` entries are translated into
    rule strings.
    """

    slug: Slug
    source: str = Field(..., min_length=1, description="Source sync endpoint slug")
    destination: str = Field(
        ...,
        min_length=1,
        description="Destination sync endpoint slug",
    )
    enabled: bool = Field(default=True, description="Whether this sync is active")
    rsync_options: RsyncOptions = Field(
        default_factory=RsyncOptions,
        description="Rsync flag configuration",
    )
    filters: list[str] = Field(
        default_factory=list,
        description="Rsync filter rules (see below)",
    )
    filter_file: str | None = Field(
        default=None,
        description="Path to external rsync filter file",
    )

    @field_validator("filter_file", mode="before")
    @classmethod
    def normalize_filter_file(cls, v: object) -> object:
        """Return the filter file path with ``~`` expanded."""
        # Non-strings pass through so pydantic rejects them rather than
        # silently dropping the filter file.
        match v:
            case str():
                return str(Path(v).expanduser())
            case _:
                return v

    @field_validator("filters", mode="before")
    @classmethod
    def normalize_filters(cls, v: object) -> object:
        """
        Translate the raw ``filters`` entries into rsync filter rule strings.

        Raises a ``filters-not-a-list`` error for a mapping or a single string,
        whose entries would otherwise be misread, and an ``invalid-filter`` or
        ``unknown-dir-merge-option`` error for an entry it cannot translate.
        """
        match v:
            case _ if is_raw_sequence(v):
                return [rule for item in v for rule in _filter_rules(item)]
            case dict():
                raise config_error(
                    ConfigValidationErrorKind.FILTERS_NOT_A_LIST,
                    "filters must be a list, not a mapping. Add '- ' before each"
                    f" filter rule. Got: {v!r}",
                )
            case str():
                # Iterating a string would yield one filter rule per character.
                raise config_error(
                    ConfigValidationErrorKind.FILTERS_NOT_A_LIST,
                    "filters must be a list, not a single string. Write it as"
                    f" a one-item list: filters: [{v!r}]",
                )
            case _:
                # Let pydantic report the type mismatch (e.g. null, a number).
                return v


def sync_predecessors(
    syncs: dict[str, SyncConfig],
) -> dict[str, set[str]]:
    """
    Direct upstream syncs for each sync slug.

    A sync B depends on sync A when A's destination endpoint
    slug matches B's source endpoint slug.
    """
    return {
        sync_slug: {
            writer
            for writer, upstream in syncs.items()
            if upstream.destination == sync.source and writer != sync_slug
        }
        for sync_slug, sync in syncs.items()
    }
