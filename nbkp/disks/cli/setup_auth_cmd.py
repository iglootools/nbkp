from typing import Annotated

import typer

from ...clidisplay import OutputFormat, echo_json
from ...cliwiring.config import load_config_or_exit
from ...cliwiring.options import (
    CONFIG_OPTION,
    OUTPUT_OPTION,
)
from ...cliwiring.runtime import runtime_of
from ..auth import AuthRuleBlock, generate_auth_rules
from . import app


@app.command("setup-auth")
def setup_auth_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    user: Annotated[
        str | None,
        typer.Option(
            "--user",
            "-u",
            help=(
                "System user the rule authorizes: the user nbkp runs as on the"
                " host (the SSH user for remote volumes)."
                " Defaults to the current user."
            ),
        ),
    ] = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
) -> None:
    """Generate the polkit rule for udisks-based mount management."""
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    rules = generate_auth_rules(cfg, user if user is not None else runtime.local_user())
    blocks = list(rules.blocks())
    match output:
        case OutputFormat.JSON:
            echo_json({"rules": [_block_json(block) for block in blocks]})
        case OutputFormat.HUMAN:
            _print_blocks(blocks)


def _block_json(block: AuthRuleBlock) -> dict[str, str]:
    return {"name": block.name, "path": block.path, "content": block.content}


def _print_blocks(blocks: list[AuthRuleBlock]) -> None:
    if not blocks:
        typer.echo("No volumes with mount config found.", err=True)
    for block in blocks:
        typer.echo(f"# {block.name}")
        typer.echo(f"# {block.install_hint}")
        typer.echo()
        typer.echo(block.content)
