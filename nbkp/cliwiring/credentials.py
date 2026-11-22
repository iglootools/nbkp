"""Interactive credential input for the ``prompt`` credential provider."""

import typer


def prompt_passphrase(passphrase_id: str) -> str:
    """Ask the user for the LUKS passphrase *passphrase_id* and return it, unechoed."""
    return typer.prompt(
        f"LUKS passphrase for {passphrase_id}",
        hide_input=True,
    )
