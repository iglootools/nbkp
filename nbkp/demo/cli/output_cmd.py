import contextlib
import tempfile
from io import StringIO
from pathlib import Path

import typer
import yaml
from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.text import Text

from ...cliwiring.runtime import Runtime, runtime_of
from ...config import ConfigError, load_config
from ...config.output import format_config_error, format_human_config
from ...ordering.output import (
    build_mermaid_graph,
    format_mermaid_ascii_graph,
    format_rich_tree_graph,
)
from ...preflight.output import format_human_check, format_human_troubleshoot
from ...remote.resolution import resolve_all_endpoints
from ...snapshots.output import format_human_prune_results
from ...sync.output import (
    format_human_results,
    format_run_preview,
)
from . import app, console as _console
lazy from ...config.testkit import config_show_config
lazy from ...preflight.testkit import (
    check_config,
    check_data,
    troubleshoot_config,
    troubleshoot_data,
)
lazy from ...sync.testkit.results import (
    dry_run_results,
    prune_dry_run_results,
    prune_results,
    run_results,
)


def _show(title: str, renderable: RenderableType, *, markup: bool = True) -> None:
    """Render *renderable* in a panel titled after the function that built it."""
    buf = StringIO()
    Console(file=buf, force_terminal=True, width=_console.width - 4).print(
        renderable, highlight=markup, markup=markup
    )
    _console.print(
        Panel(
            Text.from_ansi(buf.getvalue().rstrip("\n")),
            title=f"[bold]{title}[/bold]",
            border_style="cyan",
            padding=(0, 1),
        )
    )


@app.command("output")
def output_cmd(ctx: typer.Context) -> None:
    """Render all human output functions with fake data."""
    _show_config_show()
    _show_config_graph()
    _show_check()
    _show_run_preview()
    _show_results()
    _show_prune()
    _show_troubleshoot(runtime_of(ctx))
    _show_config_errors()


def _show_config_show() -> None:
    config = config_show_config()
    resolved = resolve_all_endpoints(config)
    _show(
        "format_human_config", format_human_config(config, resolved_endpoints=resolved)
    )


def _show_config_graph() -> None:
    config = config_show_config()
    _show("format_rich_tree_graph", format_rich_tree_graph(config))
    _show("format_mermaid_ascii_graph", format_mermaid_ascii_graph(config))
    _show(
        "build_mermaid_graph (mermaid syntax)",
        build_mermaid_graph(config),
        markup=False,
    )


def _show_check() -> None:
    config = check_config()
    preflight = check_data(config)
    _show(
        "format_human_check",
        format_human_check(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            config,
            resolved_endpoints=resolve_all_endpoints(config),
            wrap_in_panel=False,
        ),
    )


def _show_run_preview() -> None:
    config = check_config()
    preflight = check_data(config)
    _show(
        "format_run_preview",
        format_run_preview(
            preflight.sync_statuses,
            config,
            resolved_endpoints=resolve_all_endpoints(config),
            wrap_in_panel=False,
        ),
    )


def _show_results() -> None:
    config = config_show_config()
    resolved = resolve_all_endpoints(config)
    _show(
        "format_human_results (run)",
        format_human_results(run_results(config), False, config, resolved),
    )
    _show(
        "format_human_results (dry run)",
        format_human_results(dry_run_results(config), True, config, resolved),
    )


def _show_prune() -> None:
    config = config_show_config()
    _show(
        "format_human_prune_results (prune)",
        format_human_prune_results(prune_results(config), dry_run=False),
    )
    _show(
        "format_human_prune_results (dry run)",
        format_human_prune_results(prune_dry_run_results(config), dry_run=True),
    )


def _show_troubleshoot(runtime: Runtime) -> None:
    config = troubleshoot_config()
    preflight = troubleshoot_data(config)
    _show(
        "format_human_troubleshoot",
        format_human_troubleshoot(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            config,
            resolved_endpoints=resolve_all_endpoints(config),
            run=runtime.run,
        ),
    )


def _load_error(name: str, text: str | None) -> ConfigError:
    """The ConfigError ``load_config`` raises for a config file holding
    *text*, or for a missing one when *text* is ``None``.

    Raises when the file unexpectedly loads: the panel would otherwise
    render empty and the demo would silently stop showing that error.
    """
    path = Path(f"{name}.yaml")
    if text is not None:
        path.write_text(text, encoding="utf-8")
    try:
        load_config(path)
    except ConfigError as e:
        return e
    raise AssertionError(f"demo config unexpectedly loads: {text!r}")


def _show_config_errors() -> None:
    invalid_configs: list[tuple[str, str | None]] = [
        ("file not found", None),
        ("invalid YAML", "not_a_list:\n  - [invalid"),
        (
            "invalid volume type",
            yaml.safe_dump({"volumes": {"v": {"type": "ftp", "path": "/x"}}}),
        ),
        (
            "unknown server reference",
            yaml.safe_dump(
                {
                    "ssh-endpoints": {},
                    "volumes": {
                        "v": {"type": "remote", "ssh-endpoint": "missing", "path": "/x"}
                    },
                    "syncs": {},
                }
            ),
        ),
        (
            "missing required field",
            yaml.safe_dump({"volumes": {"v": {"type": "local"}}, "syncs": {}}),
        ),
    ]
    # Relative paths, from a scratch directory, keep the messages short.
    with (
        tempfile.TemporaryDirectory(prefix="nbkp-demo-") as config_dir,
        contextlib.chdir(config_dir),
    ):
        for title, text in invalid_configs:
            error = _load_error(title.lower().replace(" ", "-"), text)
            _show(f"format_config_error ({title})", format_config_error(error))
