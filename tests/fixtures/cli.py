"""Shared CLI test fixtures: the runner, and a Rich panel flattener."""

import re

from typer.testing import CliRunner

runner = CliRunner()


def strip_panel(text: str) -> str:
    """Strip Rich panel border characters and normalize whitespace."""
    text = re.sub(r"[╭╮╰╯│─]", "", text)
    return re.sub(r"\s+", " ", text).strip()
