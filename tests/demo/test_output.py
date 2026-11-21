"""Regression tests for the `nbkp demo output` command and its fake data.

The demo builders (``config_show_config``, ``check_config``,
``troubleshoot_config``, …) are the source of truth for manual QA via
``nbkp demo output``.  ``tests/config/test_testkit.py`` checks that each builds
a valid ``Config`` by round-tripping it through ``write_config``/``load_config``;
this module checks that the full ``output`` command renders every section
without raising.
"""

from typer.testing import CliRunner

from nbkp.demo.cli import app
from tests.fakes import FakeCommandRunner, fake_runtime


def test_demo_output_renders_all_sections() -> None:
    """`nbkp demo output` renders every human-output function without error."""
    # Gathering troubleshoot facts discovers the unlocked LUKS mapper with lsblk;
    # no real host commands in unit tests (see test_troubleshoot_mapper.py).
    run = FakeCommandRunner()
    result = CliRunner().invoke(app, ["output"], obj=fake_runtime(run=run))
    assert result.exit_code == 0, result.output
    assert run.commands_starting_with("lsblk")
    # Sanity: the command renders panels named after the output functions.
    assert "format_human_config" in result.output
    assert "format_human_troubleshoot" in result.output
    # The config errors come from load_config itself, one per kind shown.
    for kind in ("file-not-found", "invalid-yaml", "validation"):
        assert f"Config error [{kind}]" in result.output
