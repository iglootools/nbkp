"""Typer CLI entry point.

The root app and all domain sub-apps are wired together in app.py.
"""

from nbkp.cli.app import app

__all__ = ["app", "main"]


def main() -> None:
    app()
