"""Credential retrieval for LUKS passphrases."""

from __future__ import annotations

import os
import subprocess
from typing import Callable

import typer
from pydantic import SecretStr

from ..config import CredentialProvider


class CredentialError(Exception):
    """Raised when a passphrase cannot be retrieved."""


def _from_keyring(passphrase_id: str) -> str:
    try:
        import keyring  # type: ignore[import-untyped]
    except ImportError:
        raise CredentialError(
            "keyring package not installed."
            " Install with: pip install nbkp[keyring]"
            " Or switch to another credential-provider."
        ) from None

    password = keyring.get_password("nbkp", passphrase_id)
    if password is None:
        raise CredentialError(
            f"No passphrase found in keyring for id '{passphrase_id}'."
            f" Store it with: keyring set nbkp {passphrase_id}"
        )
    return password


def _from_prompt(passphrase_id: str) -> str:
    return typer.prompt(
        f"LUKS passphrase for {passphrase_id}",
        hide_input=True,
    )


def _from_env(passphrase_id: str) -> str:
    env_var = f"NBKP_PASSPHRASE_{passphrase_id.upper().replace('-', '_')}"
    value = os.environ.get(env_var)
    if value is None:
        raise CredentialError(
            f"Environment variable '{env_var}' not set."
            f" Export it with: export {env_var}=..."
        )
    return value


def _from_command(passphrase_id: str, command_template: list[str] | None) -> str:
    if command_template is None:
        raise CredentialError(
            "credential-command is required when credential-provider is 'command'"
        )
    command = [arg.replace("{id}", passphrase_id) for arg in command_template]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        cmd_str = " ".join(command)
        raise CredentialError(
            f"Credential command failed (exit {result.returncode}):"
            f" {cmd_str}\nstderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def retrieve_passphrase(
    passphrase_id: str,
    provider: CredentialProvider,
    command_template: list[str] | None = None,
) -> str:
    """Retrieve a LUKS passphrase using the configured provider."""
    match provider:
        case CredentialProvider.KEYRING:
            return _from_keyring(passphrase_id)
        case CredentialProvider.PROMPT:
            return _from_prompt(passphrase_id)
        case CredentialProvider.ENV:
            return _from_env(passphrase_id)
        case CredentialProvider.COMMAND:
            return _from_command(passphrase_id, command_template)


class PassphraseCache:
    """In-memory cache for passphrases during a single run.

    Values are stored as ``SecretStr`` to prevent accidental logging.
    """

    def __init__(self) -> None:
        self._cache: dict[str, SecretStr] = {}

    def get_or_retrieve(
        self,
        passphrase_id: str,
        retrieve_fn: Callable[[str], str],
    ) -> str:
        """Return cached passphrase or retrieve and cache it."""
        if passphrase_id not in self._cache:
            self._cache[passphrase_id] = SecretStr(retrieve_fn(passphrase_id))
        return self._cache[passphrase_id].get_secret_value()

    def clear(self) -> None:
        """Drop all cached passphrases."""
        self._cache.clear()


def build_passphrase_fn(
    provider: CredentialProvider,
    command_template: list[str] | None,
) -> tuple[Callable[[str], str], PassphraseCache]:
    """Build a passphrase retrieval function backed by a per-run cache."""
    cache = PassphraseCache()

    def passphrase_fn(passphrase_id: str) -> str:
        return cache.get_or_retrieve(
            passphrase_id,
            lambda pid: retrieve_passphrase(pid, provider, command_template),
        )

    return passphrase_fn, cache
