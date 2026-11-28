"""The ``nbkp disks status`` command."""

import typer

from nbkp.cliwiring.config import load_config_or_exit, resolve_endpoints
from nbkp.cliwiring.disks import probe_and_show_status, require_known_names
from nbkp.cliwiring.options import (
    CONFIG_OPTION,
    EXCLUDE_LOCATION_OPTION,
    LOCATION_OPTION,
    NETWORK_OPTION,
    OUTPUT_OPTION,
)
from nbkp.cliwiring.output_format import OutputFormat
from nbkp.cliwiring.runtime import runtime_of
from nbkp.disks.cli.options import NAME_OPTION


def status_cmd(
    ctx: typer.Context,
    config: CONFIG_OPTION = None,
    output: OUTPUT_OPTION = OutputFormat.HUMAN,
    name: NAME_OPTION = None,
    location: LOCATION_OPTION = None,
    *,
    exclude_location: EXCLUDE_LOCATION_OPTION = None,
    network: NETWORK_OPTION = None,
) -> None:
    """Show mount status for volumes with mount config."""
    runtime = runtime_of(ctx)
    cfg = load_config_or_exit(runtime, config, output)
    require_known_names(cfg, name, output)
    resolved = resolve_endpoints(cfg, location, exclude_location, network, output)
    probe_and_show_status(cfg, resolved, output, name, run=runtime.run)
