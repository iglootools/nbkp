"""Generate a standalone bash script from nbkp config.

Compiles a Config into a self-contained shell script that performs
the same sync operations as ``nbkp run``, with all paths and
options baked in.  The generated script accepts ``--dry-run``,
``--progress`` and ``--strictness`` flags at runtime.
"""

from __future__ import annotations

import importlib.resources
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, Template

from ..config import Config, LocalVolume, RemoteVolume
from ..config.epresolution import ResolvedEndpoints
from ..ordering.graph import sort_syncs, sync_predecessors
from .preflight import build_preflight_block
from .quoting import SCRIPT_DIR, comment_out, has_shell_var
from .spec import build_sync_spec
from .steps import Step, build_steps

# ── Public API ────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class ScriptOptions:
    """Options for script generation.

    ``platform`` (a ``sys.platform`` value) selects the snapshot timestamp
    format; it is supplied by the CLI so generation stays deterministic.
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
    """Generate a standalone bash script from config.

    *now* is the generation time written to the header.
    """
    re = resolved_endpoints or {}
    vol_paths = _build_vol_paths(config, options)
    ctx = _build_script_context(config, options, vol_paths, now, re)
    return _load_template().render(ctx) + "\n"


# ── Context ──────────────────────────────────────────────────


@dataclass(frozen=True)
class _SyncContext:
    slug: str
    fn_name: str
    enabled: bool
    preflight: str
    steps: tuple[Step, ...]
    predecessors: tuple[str, ...]


def _load_template() -> Template:
    """Load the Jinja2 template with shell-friendly delimiters."""
    tpl_text = (
        importlib.resources.files("nbkp.sh.templates")
        .joinpath("backup.sh.j2")
        .read_text(encoding="utf-8")
    )
    env = Environment(
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
    """Compute volume slug -> effective path."""
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
    """Resolve effective path for a single volume.

    The generated script is static, so a volume must have a declared ``path``
    (mount management — including discovery-model volumes with no ``path`` —
    is excluded from ``sh``).
    """
    if vol.path is None:
        msg = (
            f"volume '{slug}': 'path' is required for shell script generation"
            " (mount management is not supported in generated scripts)"
        )
        raise ValueError(msg)
    match vol:
        case RemoteVolume():
            return vol.path
        case LocalVolume():
            should_relativize = (slug in src_slugs and options.relative_src) or (
                slug in dst_slugs and options.relative_dst
            )
            if should_relativize and options.output_file:
                output_dir = os.path.dirname(options.output_file)
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
    predecessors: set[str],
) -> _SyncContext:
    """Pre-render the preflight and steps of one sync.

    Disabled syncs are rendered too: the template emits them commented out.
    """
    spec = build_sync_spec(slug, config, vol_paths, resolved_endpoints, platform)
    return _SyncContext(
        slug=slug,
        fn_name=_slug_to_fn(slug),
        enabled=spec.sync.enabled,
        preflight=build_preflight_block(spec),
        steps=build_steps(spec),
        predecessors=tuple(_slug_to_fn(p) for p in sorted(predecessors)),
    )


def _build_script_context(
    config: Config,
    options: ScriptOptions,
    vol_paths: dict[str, str],
    now: datetime,
    resolved_endpoints: ResolvedEndpoints,
) -> dict[str, object]:
    """Build the full template context dict."""
    pred_map = sync_predecessors(config.syncs)
    syncs = [
        _build_sync_context(
            slug,
            config,
            vol_paths,
            resolved_endpoints,
            options.platform,
            pred_map.get(slug, set()),
        )
        for slug in sort_syncs(config.syncs)
    ]
    return {
        "timestamp": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "config_line": f"# Config: {options.config_path or '<stdin>'}",
        "has_script_dir": any(has_shell_var(p) for p in vol_paths.values()),
        "portable": options.portable,
        "syncs": syncs,
        "enabled_syncs": [s for s in syncs if s.enabled],
    }
