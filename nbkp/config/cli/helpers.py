"""Config and endpoint resolution helpers for CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer

from .. import Config, ConfigError, load_config
from ..epresolution import (
    EndpointFilter,
    NetworkType,
    ResolvedEndpoints,
)
from ..output import print_config_error
from ...remote.resolution import resolve_all_endpoints


def load_config_or_exit(
    config_path: str | Path | None,
) -> Config:
    """Load config or exit with code 2 on error."""
    try:
        return load_config(config_path)
    except ConfigError as e:
        print_config_error(e)
        raise typer.Exit(2)


def build_endpoint_filter(
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    network: NetworkType | None,
) -> EndpointFilter | None:
    """Build an EndpointFilter from CLI options."""
    locs = locations or []
    excl = exclude_locations or []
    return (
        EndpointFilter(locations=locs, exclude_locations=excl, network=network)
        if locs or excl or network is not None
        else None
    )


def _validate_locations(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
) -> None:
    """Exit with an error if any location value is not defined in the config."""
    known = set(cfg.known_locations())
    if not known:
        all_values = [*(locations or []), *(exclude_locations or [])]
        if all_values:
            typer.echo(
                "Error: no locations are defined in the configuration."
                " --location and --exclude-location cannot be used.",
                err=True,
            )
            raise typer.Exit(2)
        return
    for label, values in [
        ("--location", locations),
        ("--exclude-location", exclude_locations),
    ]:
        for v in values or []:
            if v not in known:
                typer.echo(
                    f"Error: unknown location '{v}' passed to {label}."
                    f" Known locations: {', '.join(sorted(known))}",
                    err=True,
                )
                raise typer.Exit(2)


def resolve_endpoints(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    network: NetworkType | None,
) -> ResolvedEndpoints:
    """Build filter and resolve all endpoints once."""
    _validate_locations(cfg, locations, exclude_locations)
    ef = build_endpoint_filter(locations, exclude_locations, network)
    return resolve_all_endpoints(cfg, ef)
