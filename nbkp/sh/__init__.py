"""
Generate a standalone bash script from nbkp config.

Compiles a Config into a self-contained shell script that performs
the same sync operations as ``nbkp run``, with all paths and
options baked in.  The generated script accepts ``--dry-run``,
``--progress`` and ``--strictness`` flags at runtime.
"""

import importlib.resources
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

lazy from jinja2 import Environment, Template

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SyncConfig,
    sync_predecessors,
)
from nbkp.ordering.graph import sort_syncs
from nbkp.remote.endpoints import ResolvedEndpoints, location_excluded
from nbkp.sh.commands import INACTIVE
from nbkp.sh.preflight import build_preflight_block
from nbkp.sh.quoting import (
    SCRIPT_DIR,
    comment_out,
    comment_text,
    has_shell_var,
    quote,
)
from nbkp.sh.spec import build_sync_spec
from nbkp.sh.steps import Step, build_steps


class VolumeWithoutPathError(ValueError):
    """
    A volume the script would use has no ``path``.

    The script is static, so it cannot discover a mount-managed volume's
    mountpoint at run time (mount management is left out of ``sh``).
    """

    def __init__(self, volume_slug: str) -> None:
        """Build the error for the volume named *volume_slug*."""
        self.volume_slug = volume_slug
        super().__init__(
            f"volume '{volume_slug}': 'path' is required for shell script generation",
        )


@dataclass(frozen=True, kw_only=True)
class ScriptOptions:
    """
    Options for generating a script.

    ``platform`` (a ``sys.platform`` value) selects the snapshot timestamp format; it is
    supplied by the CLI so generation stays deterministic.
    """

    platform: str
    config_path: str | Path | None = None
    output_file: str | Path | None = None
    relative_src: bool = False
    relative_dst: bool = False
    portable: bool = True


def generate_script(
    config: Config,
    options: ScriptOptions,
    *,
    now: datetime,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> str:
    """
    *now* is the generation time written to the header.

    Raises :class:`VolumeWithoutPathError` when a volume has no ``path``.
    """
    re = resolved_endpoints or {}
    vol_paths = _build_vol_paths(config, options)
    ctx = _build_script_context(config, options, vol_paths, now, re)
    return _load_template().render(ctx) + "\n"


@dataclass(frozen=True)
class _SyncContext:
    slug: str
    fn_name: str  # noqa: DC06 -- read by templates/backup.sh.j2
    enabled: bool
    preflight: str
    steps: tuple[Step, ...]
    predecessors: tuple[str, ...]
    # Set when endpoint resolution left out a volume of the sync for its
    # location: the sync cannot run from there, so only a preflight that
    # reports it inactive is rendered.
    excluded_reason: str | None = None  # noqa: DC06 -- read by templates/backup.sh.j2


def _load_template() -> Template:
    """Load the Jinja2 template with shell-friendly delimiters."""
    tpl_text = (
        importlib.resources.files("nbkp.sh.templates")
        .joinpath("backup.sh.j2")
        .read_text(encoding="utf-8")
    )
    env = Environment(  # noqa: S701 -- a shell script, not HTML; values are shell-quoted
        variable_start_string="${{",
        variable_end_string="}}",
        block_start_string="<%",
        block_end_string="%>",
        comment_start_string="<#",
        comment_end_string="#>",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["comment_out"] = comment_out
    return env.from_string(tpl_text)


def _build_vol_paths(config: Config, options: ScriptOptions) -> dict[str, str]:
    src_slugs = {config.source_endpoint(s).volume for s in config.syncs.values()}
    dst_slugs = {config.destination_endpoint(s).volume for s in config.syncs.values()}
    return {
        slug: _resolve_vol_path(slug, vol, src_slugs, dst_slugs, options)
        for slug, vol in config.volumes.items()
    }


def _resolve_vol_path(
    slug: str,
    vol: LocalVolume | RemoteVolume,
    src_slugs: set[str],
    dst_slugs: set[str],
    options: ScriptOptions,
) -> str:
    """Raise :class:`VolumeWithoutPathError` for a volume with no ``path``."""
    if vol.path is None:
        raise VolumeWithoutPathError(slug)
    match vol:
        case RemoteVolume():
            return vol.path
        case LocalVolume():
            should_relativize = (slug in src_slugs and options.relative_src) or (
                slug in dst_slugs and options.relative_dst
            )
            if should_relativize and options.output_file:
                output_dir = Path(options.output_file).parent
                return f"{SCRIPT_DIR}/{os.path.relpath(vol.path, output_dir)}"
            else:
                return vol.path


def _slug_to_fn(slug: str) -> str:
    return f"sync_{slug.replace('-', '_')}"


def _build_sync_context(
    slug: str,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
    platform: str,
    *,
    predecessors: set[str],
) -> _SyncContext:
    """
    Disabled syncs are rendered too: the template emits them commented out.

    A sync with a location-excluded volume is inactive, as ``nbkp run``
    reports it: its preflight returns ``INACTIVE`` and no sync body is
    rendered, since no endpoint was resolved to reach the volume.
    """
    sync = config.syncs[slug]
    excluded = _excluded_volumes(sync, config, resolved_endpoints)
    if excluded:
        reason = (
            f"volume {', '.join(excluded)}: all SSH endpoints at an excluded"
            " location (applied when the script was generated)"
        )
        return _SyncContext(
            slug=slug,
            fn_name=_slug_to_fn(slug),
            enabled=sync.enabled,
            preflight=f"nbkp_log {quote(f'INACTIVE: {reason}')}\nreturn {INACTIVE}",
            steps=(),
            predecessors=tuple(_slug_to_fn(p) for p in sorted(predecessors)),
            excluded_reason=comment_text(reason),
        )
    spec = build_sync_spec(slug, config, vol_paths, resolved_endpoints, platform)
    return _SyncContext(
        slug=slug,
        fn_name=_slug_to_fn(slug),
        enabled=spec.sync.enabled,
        preflight=build_preflight_block(spec),
        steps=build_steps(spec),
        predecessors=tuple(_slug_to_fn(p) for p in sorted(predecessors)),
    )


def _excluded_volumes(
    sync: SyncConfig,
    config: Config,
    resolved_endpoints: ResolvedEndpoints,
) -> list[str]:
    """Slugs of the sync's volumes that endpoint resolution left out (deduplicated)."""
    slugs = dict.fromkeys(
        [config.source_endpoint(sync).volume, config.destination_endpoint(sync).volume],
    )
    return [
        v for v in slugs if location_excluded(config.volumes[v], resolved_endpoints)
    ]


def _build_script_context(
    config: Config,
    options: ScriptOptions,
    vol_paths: dict[str, str],
    now: datetime,
    resolved_endpoints: ResolvedEndpoints,
) -> dict[str, object]:
    pred_map = sync_predecessors(config.syncs)
    syncs = [
        _build_sync_context(
            slug,
            config,
            vol_paths,
            resolved_endpoints,
            options.platform,
            predecessors=pred_map.get(slug, set()),
        )
        for slug in sort_syncs(config.syncs)
    ]
    return {
        "timestamp": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "config_line": (
            f"# Config: {comment_text(str(options.config_path or '<stdin>'))}"
        ),
        "has_script_dir": any(has_shell_var(p) for p in vol_paths.values()),
        "portable": options.portable,
        "syncs": syncs,
        "enabled_syncs": [s for s in syncs if s.enabled],
    }
