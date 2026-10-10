"""Config loading and endpoint resolution shared by every command."""

from __future__ import annotations

from pathlib import Path

import typer

from ..clihelpers import OutputFormat, echo_json
from ..config import Config, ConfigError, load_config
from ..config.epresolution import (
    EndpointFilter,
    NetworkType,
    ResolvedEndpoints,
)
from ..config.output import config_error_json, print_config_error
from ..remote.resolution import resolve_all_endpoints


def load_config_or_exit(
    config_path: str | Path | None,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> Config:
    """Load config or exit with code 2 on error.

    The error is rendered in the command's output format: a Rich panel on
    stderr, or a JSON ``{"error": ...}`` object on stdout for ``-o json`` so
    scripted callers can still parse the output.
    """
    try:
        return load_config(config_path)
    except ConfigError as e:
        match output_format:
            case OutputFormat.JSON:
                echo_json(config_error_json(e))
            case OutputFormat.HUMAN:
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
    unknown = [
        (label, value)
        for label, values in [
            ("--location", locations),
            ("--exclude-location", exclude_locations),
        ]
        for value in values or []
        if value not in known
    ]
    if unknown:
        label, value = unknown[0]
        message = (
            f"Error: unknown location '{value}' passed to {label}."
            f" Known locations: {', '.join(sorted(known))}"
            if known
            else "Error: no locations are defined in the configuration."
            " --location and --exclude-location cannot be used."
        )
        typer.echo(message, err=True)
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
