"""The ``credentials keyring-status`` command."""

from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from nbkp.cliwiring.config import load_config_or_exit
from nbkp.cliwiring.options import CONFIG_OPTION, OUTPUT_OPTION
from nbkp.cliwiring.output_format import OutputFormat, echo_json
from nbkp.cliwiring.runtime import runtime_of
from nbkp.config import Config
from nbkp.credentials import (
    CredentialError,
    PassphrasePrefetch,
    collect_passphrase_ids,
    is_prefetchable,
    prefetch_passphrases,
    retrieve_passphrase,
)
from nbkp.credentials.output import credential_error_message
from nbkp.policy import Severity
from nbkp.theme import severity_style, severity_symbol


def keyring_status_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
) -> None:
    """
    Check whether LUKS passphrases are available in the credential store.

    Exits 1 when a passphrase the provider was asked for is missing, so a
    setup script can check the store before scheduling a run.
    """
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    # Same sweep as the one run/mount perform before touching any device, so
    # this command triggers exactly the approval prompts a run would — and,
    # like it, never prompts interactively for the ``prompt`` provider.
    results = prefetch_passphrases(
        cfg,
        lambda pid: retrieve_passphrase(
            pid,
            cfg.credential_provider,
            cfg.credential_command,
            runtime.environ,
            run=runtime.run_credential_command,
        ),
    )
    match output:
        case OutputFormat.JSON:
            echo_json(keyring_status_json(cfg, results))
        case OutputFormat.HUMAN:
            _print_human_status(cfg, results)
    if any(not result.success for result in results):
        raise typer.Exit(1)


def keyring_status_json(
    cfg: Config,
    results: list[PassphrasePrefetch],
) -> dict[str, Any]:
    """
    Build ``credentials keyring-status -o json``.

    Every configured passphrase id with its volumes, and, when the provider can be
    checked ahead of a run, whether it was available (and why not).
    """
    checked = is_prefetchable(cfg.credential_provider)
    by_id = {r.passphrase_id: r for r in results}
    return {
        "provider": cfg.credential_provider.value,
        "checked": checked,
        "passphrases": {
            pid: {
                "volumes": sorted(volumes),
                **(_result_json(by_id[pid]) if pid in by_id else {}),
            }
            for pid, volumes in sorted(collect_passphrase_ids(cfg).items())
        },
    }


def _result_json(result: PassphrasePrefetch) -> dict[str, Any]:
    error = result.error
    return {
        "available": result.success,
        **(
            {"error": credential_error_message(error), "reason": error.kind.value}
            if error is not None
            else {}
        ),
    }


def _print_human_status(cfg: Config, results: list[PassphrasePrefetch]) -> None:
    if not collect_passphrase_ids(cfg):
        typer.echo("No encrypted volumes found.", err=True)
    elif not is_prefetchable(cfg.credential_provider):
        typer.echo(
            f"Credential provider '{cfg.credential_provider.value}' asks for each"
            " passphrase interactively at unlock time; there is nothing to check"
            " ahead of a run.",
            err=True,
        )
    else:
        table = Table(title="Credential Status:")
        table.add_column("Passphrase ID")
        table.add_column("Volumes")
        table.add_column("Provider")
        table.add_column("Status")
        for result in results:
            # Passphrase ids, slugs and error details are free-form: Text keeps
            # a bracket in them from being read as a style tag.
            table.add_row(
                Text(result.passphrase_id),
                Text(", ".join(result.volumes)),
                cfg.credential_provider.value,
                _status_text(result),
            )
        Console().print(table)


def _status_text(result: PassphrasePrefetch) -> Text:
    severity, label = _status(result)
    return Text(f"{severity_symbol(severity)} {label}", style=severity_style(severity))


def _status(result: PassphrasePrefetch) -> tuple[Severity, str]:
    match result:
        case PassphrasePrefetch(success=True):
            return Severity.OK, "available"
        case PassphrasePrefetch(error=CredentialError() as error):
            return Severity.ERROR, f"missing ({credential_error_message(error)})"
        case _:
            return Severity.ERROR, "missing"
