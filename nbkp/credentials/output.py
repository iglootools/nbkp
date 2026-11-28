"""Rendering of credential errors: the sentence that tells the user what to do."""

import shlex

from nbkp.credentials import CredentialError, CredentialErrorKind


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
