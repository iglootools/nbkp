"""The ``nbkp config show`` command."""

import typer
from rich.console import Console

from nbkp.cliwiring.config import load_config_or_exit, resolve_endpoints
from nbkp.cliwiring.options import CONFIG_OPTION, OUTPUT_OPTION
from nbkp.cliwiring.output_format import OutputFormat, echo_json
from nbkp.cliwiring.runtime import runtime_of
from nbkp.config.output import format_human_config


def show_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
) -> None:
    """
    Load, validate, and render the config as tables or JSON.

    Useful for verifying that inheritance, filters, and cross-references resolve
    correctly.
    """
    cfg = load_config_or_exit(runtime_of(ctx), config, output)
    match output:
        case OutputFormat.JSON:
            echo_json(cfg.model_dump(by_alias=True, mode="json"))
        case OutputFormat.HUMAN:
            resolved = resolve_endpoints(cfg, None, None, None)
            Console().print(format_human_config(cfg, resolved_endpoints=resolved))
