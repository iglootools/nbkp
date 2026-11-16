"""Config loading and endpoint resolution shared by every command."""

from pathlib import Path

import typer

from ..clidisplay import OutputFormat, echo_json
from ..config import Config, ConfigError
from ..config.output import config_error_json, print_config_error
from ..remote.endpoints import (
    EndpointFilter,
    NetworkType,
    ResolvedEndpoints,
)
from ..remote.resolution import resolve_all_endpoints
from .runtime import Runtime


def load_config_or_exit(
    runtime: Runtime,
    config_path: str | Path | None,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> Config:
    """Load config, searched for in *runtime*'s environment when no path is
    given, or exit with code 2 on error.

    The error is rendered in the command's output format: a Rich panel on
    stderr, or a JSON ``{"error": ...}`` object on stdout for ``-o json`` so
    scripted callers can still parse the output.
    """
    try:
        return runtime.load_config(config_path)
    except ConfigError as e:
        match output_format:
            case OutputFormat.JSON:
                echo_json(config_error_json(e))
            case OutputFormat.HUMAN:
                print_config_error(e)
        raise typer.Exit(2)


def require_known_syncs(
    cfg: Config,
    names: list[str] | None,
    output_format: OutputFormat,
) -> None:
    """Exit 2 when a ``--sync`` value names no sync.

    Without this, a typo (in a timer entry, say) filters every sync out and
    the command reports success having done nothing.
    """
    unknown = [name for name in names or [] if name not in cfg.syncs]
    if unknown:
        known = ", ".join(sorted(cfg.syncs)) or "(none)"
        message = (
            f"Error: unknown sync name(s): {', '.join(unknown)}. Known syncs: {known}"
        )
        match output_format:
            case OutputFormat.JSON:
                echo_json(
                    {
                        "error": {
                            "reason": "unknown-sync",
                            "message": message,
                            "names": unknown,
                        }
                    }
                )
            case OutputFormat.HUMAN:
                typer.echo(message, err=True)
        raise typer.Exit(2)


def build_endpoint_filter(
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    network: NetworkType | None,
) -> EndpointFilter | None:
    """``None`` when no option narrows the endpoint selection."""
    locs = locations or []
    excl = exclude_locations or []
    return (
        EndpointFilter(locations=locs, exclude_locations=excl, network=network)
        if locs or excl or network is not None
        else None
    )


UNKNOWN_LOCATION_REASON = "unknown-location"
"""``reason`` of the JSON error for a location the config does not define."""


def _validate_locations(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    output_format: OutputFormat,
) -> None:
    """Exit with code 2 if any location value is not defined in the config.

    Like a config error, it is rendered in the command's output format: text
    on stderr, or a JSON ``{"error": ...}`` object on stdout for ``-o json``.
    """
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
            f"unknown location '{value}' passed to {label}."
            f" Known locations: {', '.join(sorted(known))}"
            if known
            else "no locations are defined in the configuration."
            " --location and --exclude-location cannot be used."
        )
        match output_format:
            case OutputFormat.JSON:
                echo_json(
                    {"error": {"reason": UNKNOWN_LOCATION_REASON, "message": message}}
                )
            case OutputFormat.HUMAN:
                typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(2)


def resolve_endpoints(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    network: NetworkType | None,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> ResolvedEndpoints:
    _validate_locations(cfg, locations, exclude_locations, output_format)
    ef = build_endpoint_filter(locations, exclude_locations, network)
    return resolve_all_endpoints(cfg, ef)
