"""Interactive credential input for the ``prompt`` credential provider."""

import typer


def prompt_passphrase(passphrase_id: str) -> str:
    return typer.prompt(
        f"LUKS passphrase for {passphrase_id}",
        hide_input=True,
    )
