"""Demo CLI output command: render all human output functions with fake data."""

from __future__ import annotations

from io import StringIO

import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ..config import (
    Config,
    ConfigError,
    ConfigErrorReason,
)
from ..ordering.output import (
    build_mermaid_graph,
    print_mermaid_ascii_graph,
    print_rich_tree_graph,
)
from ..config.output import print_config_error, print_human_config
from ..preflight.output import print_human_check, print_human_troubleshoot
from ..remote.resolution import resolve_all_endpoints
from ..sync.output import (
    print_human_prune_results,
    print_human_results,
    print_run_preview,
)
from ..config.testkit import config_show_config
from ..preflight.testkit import (
    check_config,
    check_data,
    troubleshoot_config,
    troubleshoot_data,
)
from ..sync.testkit.runner import (
    dry_run_results,
    prune_dry_run_results,
    prune_results,
    run_results,
)
from .app import app, console as _console


def _capture_console() -> tuple[Console, StringIO]:
    """Create a Console that captures output to a StringIO buffer."""
    buf = StringIO()
    con = Console(
        file=buf,
        force_terminal=True,
        width=_console.width - 4,
    )
    return con, buf


def _print_panel(title: str, buf: StringIO) -> None:
    """Wrap captured console output in a titled panel."""
    content = Text.from_ansi(buf.getvalue().rstrip("\n"))
    _console.print(
        Panel(
            content,
            title=f"[bold]{title}[/bold]",
            border_style="cyan",
            padding=(0, 1),
        )
    )


@app.command()
def output() -> None:
    """Render all human output functions with fake data."""
    _show_config_show()
    _show_config_graph()
    _show_check()
    _show_run_preview()
    _show_results()
    _show_prune()
    _show_troubleshoot()
    _show_config_errors()


def _show_config_show() -> None:
    console, buf = _capture_console()
    config = config_show_config()
    re = resolve_all_endpoints(config)
    print_human_config(config, console=console, resolved_endpoints=re)
    _print_panel("print_human_config", buf)


def _show_config_graph() -> None:
    config = config_show_config()

    console, buf = _capture_console()
    print_rich_tree_graph(config, console=console)
    _print_panel("print_rich_tree_graph", buf)

    console, buf = _capture_console()
    print_mermaid_ascii_graph(config, console=console)
    _print_panel("print_mermaid_ascii_graph", buf)

    console, buf = _capture_console()
    mermaid_src = build_mermaid_graph(config)
    console.print(mermaid_src, highlight=False)
    _print_panel("build_mermaid_graph (mermaid syntax)", buf)


def _show_check() -> None:
    console, buf = _capture_console()
    config = check_config()
    re = resolve_all_endpoints(config)
    vol_statuses, sync_statuses = check_data(config)
    print_human_check(
        vol_statuses,
        sync_statuses,
        config,
        console=console,
        resolved_endpoints=re,
        wrap_in_panel=False,
    )
    _print_panel("print_human_check", buf)


def _show_run_preview() -> None:
    console, buf = _capture_console()
    config = check_config()
    re = resolve_all_endpoints(config)
    _vol_statuses, sync_statuses = check_data(config)
    print_run_preview(
        sync_statuses,
        config,
        console=console,
        resolved_endpoints=re,
        wrap_in_panel=False,
    )
    _print_panel("print_run_preview", buf)


def _show_results() -> None:
    config = config_show_config()
    re = resolve_all_endpoints(config)
    console, buf = _capture_console()
    print_human_results(run_results(config), False, config, re, console=console)
    _print_panel("print_human_results (run)", buf)

    console, buf = _capture_console()
    print_human_results(dry_run_results(config), True, config, re, console=console)
    _print_panel("print_human_results (dry run)", buf)


def _show_prune() -> None:
    config = config_show_config()
    console, buf = _capture_console()
    print_human_prune_results(prune_results(config), dry_run=False, console=console)
    _print_panel("print_human_prune_results (prune)", buf)

    console, buf = _capture_console()
    print_human_prune_results(
        prune_dry_run_results(config),
        dry_run=True,
        console=console,
    )
    _print_panel("print_human_prune_results (dry run)", buf)


def _show_troubleshoot() -> None:
    console, buf = _capture_console()
    config = troubleshoot_config()
    re = resolve_all_endpoints(config)
    vol_statuses, sync_statuses = troubleshoot_data(config)
    print_human_troubleshoot(
        vol_statuses,
        sync_statuses,
        config,
        console=console,
        resolved_endpoints=re,
    )
    _print_panel("print_human_troubleshoot", buf)


def _show_config_errors() -> None:
    console, buf = _capture_console()
    print_config_error(
        ConfigError(
            "Config file not found: /etc/nbkp/config.yaml",
            reason=ConfigErrorReason.FILE_NOT_FOUND,
        ),
        console=console,
    )
    _print_panel("print_config_error (file not found)", buf)

    console, buf = _capture_console()
    try:
        yaml.safe_load("not_a_list:\n  - [invalid")
    except yaml.YAMLError as ye:
        err = ConfigError(
            f"Invalid YAML in /etc/nbkp/config.yaml: {ye}",
            reason=ConfigErrorReason.INVALID_YAML,
        )
        err.__cause__ = ye
        print_config_error(err, console=console)
    _print_panel("print_config_error (invalid YAML)", buf)

    console, buf = _capture_console()
    try:
        Config.model_validate({"volumes": {"v": {"type": "ftp", "path": "/x"}}})
    except ValidationError as ve:
        err = ConfigError(str(ve), reason=ConfigErrorReason.VALIDATION)
        err.__cause__ = ve
        print_config_error(err, console=console)
    _print_panel("print_config_error (invalid volume type)", buf)

    console, buf = _capture_console()
    try:
        Config.model_validate(
            {
                "ssh-endpoints": {},
                "volumes": {
                    "v": {
                        "type": "remote",
                        "ssh-endpoint": "missing",
                        "path": "/x",
                    },
                },
                "syncs": {},
            }
        )
    except ValidationError as ve:
        err = ConfigError(str(ve), reason=ConfigErrorReason.VALIDATION)
        err.__cause__ = ve
        print_config_error(err, console=console)
    _print_panel("print_config_error (unknown server reference)", buf)

    console, buf = _capture_console()
    try:
        Config.model_validate({"volumes": {"v": {"type": "local"}}, "syncs": {}})
    except ValidationError as ve:
        err = ConfigError(str(ve), reason=ConfigErrorReason.VALIDATION)
        err.__cause__ = ve
        print_config_error(err, console=console)
    _print_panel("print_config_error (missing required field)", buf)
