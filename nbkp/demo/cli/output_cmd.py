from io import StringIO

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ...cliwiring.runtime import Runtime, runtime_of
from ...config import (
    Config,
    ConfigError,
    ConfigErrorKind,
)
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


def _capture_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    con = Console(
        file=buf,
        force_terminal=True,
        width=_console.width - 4,
    )
    return con, buf


def _print_panel(title: str, buf: StringIO) -> None:
    content = Text.from_ansi(buf.getvalue().rstrip("\n"))
    _console.print(
        Panel(
            content,
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
    console, buf = _capture_console()
    config = config_show_config()
    re = resolve_all_endpoints(config)
    console.print(format_human_config(config, resolved_endpoints=re))
    _print_panel("format_human_config", buf)


def _show_config_graph() -> None:
    config = config_show_config()

    console, buf = _capture_console()
    console.print(format_rich_tree_graph(config))
    _print_panel("format_rich_tree_graph", buf)

    console, buf = _capture_console()
    console.print(format_mermaid_ascii_graph(config))
    _print_panel("format_mermaid_ascii_graph", buf)

    console, buf = _capture_console()
    mermaid_src = build_mermaid_graph(config)
    console.print(mermaid_src, highlight=False, markup=False)
    _print_panel("build_mermaid_graph (mermaid syntax)", buf)


def _show_check() -> None:
    console, buf = _capture_console()
    config = check_config()
    re = resolve_all_endpoints(config)
    preflight = check_data(config)
    console.print(
        format_human_check(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            config,
            resolved_endpoints=re,
            wrap_in_panel=False,
        )
    )
    _print_panel("format_human_check", buf)


def _show_run_preview() -> None:
    console, buf = _capture_console()
    config = check_config()
    re = resolve_all_endpoints(config)
    preflight = check_data(config)
    console.print(
        format_run_preview(
            preflight.sync_statuses,
            config,
            resolved_endpoints=re,
            wrap_in_panel=False,
        )
    )
    _print_panel("format_run_preview", buf)


def _show_results() -> None:
    config = config_show_config()
    re = resolve_all_endpoints(config)
    console, buf = _capture_console()
    console.print(format_human_results(run_results(config), False, config, re))
    _print_panel("format_human_results (run)", buf)

    console, buf = _capture_console()
    console.print(format_human_results(dry_run_results(config), True, config, re))
    _print_panel("format_human_results (dry run)", buf)


def _show_prune() -> None:
    config = config_show_config()
    console, buf = _capture_console()
    console.print(format_human_prune_results(prune_results(config), dry_run=False))
    _print_panel("format_human_prune_results (prune)", buf)

    console, buf = _capture_console()
    console.print(
        format_human_prune_results(prune_dry_run_results(config), dry_run=True)
    )
    _print_panel("format_human_prune_results (dry run)", buf)


def _show_troubleshoot(runtime: Runtime) -> None:
    console, buf = _capture_console()
    config = troubleshoot_config()
    re = resolve_all_endpoints(config)
    preflight = troubleshoot_data(config)
    console.print(
        format_human_troubleshoot(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            config,
            resolved_endpoints=re,
            run=runtime.run,
        )
    )
    _print_panel("format_human_troubleshoot", buf)


def _show_error_panel(title: str, error: ConfigError) -> None:
    console, buf = _capture_console()
    console.print(format_config_error(error))
    _print_panel(f"format_config_error ({title})", buf)


def _validation_error(data: dict[str, object]) -> ConfigError:
    """The ConfigError *data* produces; it must be an invalid config.

    Raises when *data* unexpectedly validates — the panel would otherwise
    render empty and the demo would silently stop showing that error.
    """
    try:
        Config.model_validate(data)
    except ValidationError as ve:
        err = ConfigError(str(ve), reason=ConfigErrorKind.VALIDATION)
        err.__cause__ = ve
        return err
    raise AssertionError(f"demo config unexpectedly validates: {data!r}")


def _yaml_error() -> ConfigError:
    try:
        yaml.safe_load("not_a_list:\n  - [invalid")
    except yaml.YAMLError as ye:
        err = ConfigError(
            f"Invalid YAML in /etc/nbkp/config.yaml: {ye}",
            reason=ConfigErrorKind.INVALID_YAML,
        )
        err.__cause__ = ye
        return err
    raise AssertionError("demo YAML unexpectedly parses")


def _show_config_errors() -> None:
    _show_error_panel(
        "file not found",
        ConfigError(
            "Config file not found: /etc/nbkp/config.yaml",
            reason=ConfigErrorKind.FILE_NOT_FOUND,
        ),
    )
    _show_error_panel("invalid YAML", _yaml_error())
    _show_error_panel(
        "invalid volume type",
        _validation_error({"volumes": {"v": {"type": "ftp", "path": "/x"}}}),
    )
    _show_error_panel(
        "unknown server reference",
        _validation_error(
            {
                "ssh-endpoints": {},
                "volumes": {
                    "v": {"type": "remote", "ssh-endpoint": "missing", "path": "/x"},
                },
                "syncs": {},
            }
        ),
    )
    _show_error_panel(
        "missing required field",
        _validation_error({"volumes": {"v": {"type": "local"}}, "syncs": {}}),
    )
