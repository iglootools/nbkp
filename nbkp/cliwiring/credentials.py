"""Interactive credential input for the ``prompt`` credential provider."""

import typer


def prompt_passphrase(passphrase_id: str) -> str:
    """Ask the operator for the LUKS passphrase of *passphrase_id* (hidden input)."""
    return typer.prompt(
        f"LUKS passphrase for {passphrase_id}",
        hide_input=True,
    )
