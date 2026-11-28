"""The ``credentials keyring-status`` command."""

import typer
from rich.console import Console

from nbkp.cliwiring.config import load_config_or_exit
from nbkp.cliwiring.options import CONFIG_OPTION, OUTPUT_OPTION
from nbkp.cliwiring.output_format import OutputFormat, echo_json
from nbkp.cliwiring.runtime import runtime_of
from nbkp.config import Config
from nbkp.credentials import (
    PassphrasePrefetch,
    collect_passphrase_ids,
    is_prefetchable,
    prefetch_passphrases,
    retrieve_passphrase,
)
from nbkp.credentials.output import build_keyring_status_table, keyring_status_json


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
        Console().print(build_keyring_status_table(cfg, results))
