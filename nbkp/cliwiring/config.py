"""Config loading and endpoint resolution shared by every command."""

from collections.abc import Collection, Sequence
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console

from nbkp.cliwiring.output_format import OutputFormat, echo_json
from nbkp.cliwiring.runtime import Runtime
from nbkp.config import Config, ConfigError, ConfigErrorKind
from nbkp.config.output import config_error_json, format_config_error
from nbkp.remote.endpoints import EndpointFilter, NetworkType, ResolvedEndpoints
from nbkp.remote.resolution import (
    SshConfigError,
    SshConfigErrorKind,
    resolve_all_endpoints,
)


def load_config_or_exit(
    runtime: Runtime,
    config_path: str | Path | None,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> Config:
    """
    Load the config, or exit with code 2 on error.

    When no path is given, the config is searched for in *runtime*'s environment.

    The error is rendered in the command's output format: a Rich panel on
    stderr, or a JSON ``{"error": ...}`` object on stdout for ``-o json`` so
    scripted callers can still parse the output.
    """
    try:
        return runtime.load_config(config_path)
    except ConfigError as e:
        _exit_on_config_error(e, output_format)


def _exit_on_config_error(e: ConfigError, output_format: OutputFormat) -> NoReturn:
    match output_format:
        case OutputFormat.JSON:
            echo_json(config_error_json(e))
        case OutputFormat.HUMAN:
            Console(stderr=True).print(format_config_error(e))
    raise typer.Exit(2)


def require_known(
    kind: str,
    unknown: Sequence[str],
    known: Collection[str],
    output_format: OutputFormat,
) -> None:
    """
    Exit 2 when an option names a *kind* of thing the config does not define.

    The kind is ``sync``, ``volume`` or ``location``.

    Without this, a typo (in a timer entry, say) filters everything out and
    the command reports success having done nothing. The error is rendered in
    the command's output format: text on stderr, or a JSON ``{"error": ...}``
    object (reason ``unknown-<kind>``) on stdout for ``-o json``.
    """
    if unknown:
        message = (
            f"unknown {kind} name(s): {', '.join(unknown)}."
            f" Known {kind}s: {', '.join(sorted(known)) or '(none)'}"
        )
        match output_format:
            case OutputFormat.JSON:
                echo_json(
                    {
                        "error": {
                            "reason": f"unknown-{kind}",
                            "message": message,
                            "names": list(unknown),
                        },
                    },
                )
            case OutputFormat.HUMAN:
                typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(2)


def require_known_syncs(
    cfg: Config,
    names: list[str] | None,
    output_format: OutputFormat,
) -> None:
    """Exit 2 when a ``--sync`` value names no sync."""
    unknown = [name for name in names or [] if name not in cfg.syncs]
    require_known("sync", unknown, cfg.syncs, output_format)


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


def _require_known_locations(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    output_format: OutputFormat,
) -> None:
    """
    Exit 2 when a location option names a location the config does not define.

    The option is ``--location`` or ``--exclude-location``.
    """
    known = cfg.known_locations()
    values: dict[str, None] = dict.fromkeys(
        [*(locations or []), *(exclude_locations or [])],
    )
    unknown = [value for value in values if value not in known]
    require_known("location", unknown, known, output_format)


def resolve_endpoints(
    cfg: Config,
    locations: list[str] | None,
    exclude_locations: list[str] | None,
    network: NetworkType | None,
    output_format: OutputFormat = OutputFormat.HUMAN,
    *,
    ssh_config_path: Path,
) -> ResolvedEndpoints:
    """
    Resolve the endpoints, or exit 2 on an SSH config value nbkp cannot use.

    *ssh_config_path* is the OpenSSH client config to read (the runtime's
    :meth:`~.runtime.Runtime.ssh_config_path`). Exit with code 2, like a config
    error, naming that file, when it gives a host a value nbkp cannot use.
    """
    _require_known_locations(cfg, locations, exclude_locations, output_format)
    ef = build_endpoint_filter(locations, exclude_locations, network)
    try:
        return resolve_all_endpoints(cfg, ef, ssh_config_path=ssh_config_path)
    except SshConfigError as e:
        _exit_on_config_error(_ssh_config_error(e, ssh_config_path), output_format)


def _ssh_config_error(e: SshConfigError, path: Path) -> ConfigError:
    match e.kind:
        case SshConfigErrorKind.INVALID_PORT:
            detail = f"Port {e.value!r} for host '{e.host}' is not a number"
    return ConfigError(ConfigErrorKind.INVALID_SSH_CONFIG, path=path, detail=detail)
