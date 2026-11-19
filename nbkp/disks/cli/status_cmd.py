import typer

from ...clidisplay import OutputFormat
from ...cliwiring.config import load_config_or_exit, resolve_endpoints
from ...cliwiring.options import (
    CONFIG_OPTION,
    EXCLUDE_LOCATION_OPTION,
    LOCATION_OPTION,
    NETWORK_OPTION,
    OUTPUT_OPTION,
)
from ...cliwiring.runtime import runtime_of
from . import app
from .helpers import probe_and_show_status, require_known_names
from .options import NAME_OPTION


@app.command("status")
def status_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    name: NAME_OPTION = None,
    location: LOCATION_OPTION = None,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
) -> None:
    """Show mount status for volumes with mount config."""
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_names(cfg, name, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)
    probe_and_show_status(cfg, resolved, output, name, run=runtime.run)
