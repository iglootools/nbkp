"""Regression tests for the `nbkp demo output` command and its fake data.

The demo builders (``config_show_config``, ``check_config``,
``troubleshoot_config``, …) are the source of truth for manual QA via
``nbkp demo output``.  They are easy to break silently: a builder can produce a
``Config`` that no longer validates (e.g. after a new cross-reference rule
lands) and nothing in the rest of the suite would notice, because no automated
test exercises them.  These tests close that gap — they assert every builder
produces a valid ``Config`` and that the full ``output`` command renders every
section without raising.
"""

import pytest
from typer.testing import CliRunner

from nbkp.config import Config
from nbkp.config.testkit import config_show_config
from nbkp.demo.cli import app
from nbkp.preflight.testkit import check_config, troubleshoot_config
from tests.fakes import FakeCommandRunner, fake_runtime


@pytest.mark.parametrize(
    "builder",
    [config_show_config, check_config, troubleshoot_config],
    ids=lambda fn: fn.__name__,
)
def test_demo_config_builder_validates(builder) -> None:
    """Each demo config builder must produce a config that passes validation.

    Constructing a ``Config`` runs the cross-reference validators (unique
    destinations, non-overlapping endpoint paths, …), so a returned instance
    proves the fake data is internally consistent.
    """
    config = builder()
    assert isinstance(config, Config)


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
