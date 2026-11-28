"""
Rendering of credentials for the user.

The sentence that says what to do about a missing passphrase, and
``credentials keyring-status``'s table and JSON.
"""

import shlex
from typing import Any

from rich.table import Table
from rich.text import Text

from nbkp.config import Config
from nbkp.credentials import (
    CredentialError,
    CredentialErrorKind,
    PassphrasePrefetch,
    collect_passphrase_ids,
    is_prefetchable,
)
from nbkp.policy import Severity
from nbkp.theme import severity_style, severity_symbol


def credential_error_message(e: CredentialError) -> str:
    """Render why a passphrase could not be retrieved, and how to fix it."""
    command = e.command or []
    match e.kind:
        case CredentialErrorKind.NOT_INSTALLED:
            return (
                "keyring package not installed."
                " Install with: uv tool install 'nbkp[keyring]'"
                " (quotes required — the shell would glob the extra otherwise),"
                " or switch to another credential-provider."
            )
        case CredentialErrorKind.BACKEND_UNAVAILABLE:
            return (
                f"Keyring backend unavailable for id '{e.passphrase_id}':"
                f" {e.detail}. Unlock the keyring, configure a backend,"
                " or switch to another credential-provider."
            )
        case CredentialErrorKind.NOT_FOUND:
            return (
                f"No passphrase found in keyring for id '{e.passphrase_id}'."
                f" Store it with: keyring set nbkp {shlex.quote(e.passphrase_id or '')}"
                " (the keyring command is not part of nbkp's install:"
                " uv tool install keyring)"
            )
        case CredentialErrorKind.ENV_UNSET:
            return (
                f"Environment variable '{e.env_var}' not set."
                f" Export it with: export {e.env_var}=..."
            )
        case CredentialErrorKind.COMMAND_MISSING:
            return (
                "credential-command is required when credential-provider is 'command'"
            )
        case CredentialErrorKind.COMMAND_NOT_RUNNABLE:
            return f"Credential command could not be started ({command[0]}): {e.detail}"
        case CredentialErrorKind.COMMAND_FAILED:
            return (
                f"Credential command failed (exit {e.exit_code}):"
                f" {shlex.join(command)}\nstderr: {e.detail}"
            )
        case CredentialErrorKind.EMPTY:
            return (
                f"Credential provider '{e.provider}' returned an empty"
                f" passphrase for id '{e.passphrase_id}'."
            )


def keyring_status_json(
    cfg: Config,
    results: list[PassphrasePrefetch],
) -> dict[str, Any]:
    """
    Build ``credentials keyring-status -o json``.

    Every configured passphrase id with its volumes, and, when the provider can be
    checked ahead of a run, whether it was available (and why not).
    """
    checked = is_prefetchable(cfg.credential_provider)
    by_id = {r.passphrase_id: r for r in results}
    return {
        "provider": cfg.credential_provider.value,
        "checked": checked,
        "passphrases": {
            pid: {
                "volumes": sorted(volumes),
                **(_result_json(by_id[pid]) if pid in by_id else {}),
            }
            for pid, volumes in sorted(collect_passphrase_ids(cfg).items())
        },
    }


def _result_json(result: PassphrasePrefetch) -> dict[str, Any]:
    error = result.error
    return {
        "available": result.success,
        **(
            {"error": credential_error_message(error), "reason": error.kind.value}
            if error is not None
            else {}
        ),
    }


def build_keyring_status_table(
    cfg: Config,
    results: list[PassphrasePrefetch],
) -> Table:
    """Return the table of each passphrase id: its volumes, provider and status."""
    table = Table(title="Credential Status:")
    table.add_column("Passphrase ID")
    table.add_column("Volumes")
    table.add_column("Provider")
    table.add_column("Status")
    for result in results:
        # Passphrase ids, slugs and error details are free-form: Text keeps
        # a bracket in them from being read as a style tag.
        table.add_row(
            Text(result.passphrase_id),
            Text(", ".join(result.volumes)),
            cfg.credential_provider.value,
            _status_text(result),
        )
    return table


def _status_text(result: PassphrasePrefetch) -> Text:
    severity, label = _status(result)
    return Text(f"{severity_symbol(severity)} {label}", style=severity_style(severity))


def _status(result: PassphrasePrefetch) -> tuple[Severity, str]:
    match result:
        case PassphrasePrefetch(success=True):
            return Severity.OK, "available"
        case PassphrasePrefetch(error=CredentialError() as error):
            return Severity.ERROR, f"missing ({credential_error_message(error)})"
        case _:
            return Severity.ERROR, "missing"
