"""Typer CLI entry point.

The root app and all domain sub-apps are wired together in app.py.
"""

from .app import app

__all__ = ["app", "main"]


def main() -> None:
    """Main CLI entry point."""
    app()
