"""Preflight-specific CLI helpers."""

from __future__ import annotations

from ...clihelpers import OutputFormat
from ...clihelpers.invocation import Invocation
from ...commands.preflight import check_all_with_progress
from ...config import Config
from ...disks import MountObservation
from ...policy import Strictness
from ...preflight import PreflightResult
from ...preflight.output import print_human_check
from ...remote.endpoints import ResolvedEndpoints
from ..strictness import has_fatal_errors


def check_and_display(
    cfg: Config,
    output_format: OutputFormat,
    strictness: Strictness,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
    invocation: Invocation | None = None,
) -> tuple[PreflightResult, bool]:
    """Compute statuses, display human output, and check for errors.

    Returns the preflight result and whether there are fatal errors.
    When *only_syncs* is given, only those syncs (and the volumes
    they reference) are checked.
    """
    preflight = check_all_with_progress(
        cfg,
        use_progress=output_format is OutputFormat.HUMAN,
        only_syncs=only_syncs,
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
        mount_observations=mount_observations,
        strictness=strictness,
    )

    if output_format is OutputFormat.HUMAN:
        print_human_check(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            cfg,
            resolved_endpoints=resolved_endpoints,
            strictness=strictness,
            invocation=invocation,
        )

    return preflight, has_fatal_errors(preflight.sync_statuses, strictness=strictness)
