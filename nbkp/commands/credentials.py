"""Interactive credential input for the ``prompt`` credential provider."""

from __future__ import annotations

import typer


def prompt_passphrase(passphrase_id: str) -> str:
    """Ask the operator for the LUKS passphrase of *passphrase_id* (hidden input)."""
    return typer.prompt(
        f"LUKS passphrase for {passphrase_id}",
        hide_input=True,
    )
