"""Credential retrieval for LUKS passphrases."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import typer
from pydantic import SecretStr

from ..config import Config, CredentialProvider


class CredentialError(Exception):
    """Raised when a passphrase cannot be retrieved."""


def _from_keyring(passphrase_id: str) -> str:
    try:
        import keyring  # type: ignore[import-untyped]
    except ImportError:
        raise CredentialError(
            "keyring package not installed."
            " Install with: uv tool install 'nbkp[keyring]'"
            " (quotes required — the shell would glob the extra otherwise),"
            " or switch to another credential-provider."
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
    result = subprocess.run(command, capture_output=True, text=True, check=False)
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


def collect_passphrase_ids(config: Config) -> dict[str, list[str]]:
    """Map each configured passphrase-id to the volume slugs that use it.

    Covers *every* encrypted mount-managed volume in the config, whether or
    not its drive is currently plugged in.
    """
    result: dict[str, list[str]] = {}
    for vol in config.volumes.values():
        mount = vol.mount
        if mount is not None and mount.encryption is not None:
            result.setdefault(mount.encryption.passphrase_id, []).append(vol.slug)
    return result


# Providers worth retrieving eagerly.  ``prompt`` is deliberately excluded:
# prefetching it would ask the operator to type the passphrase of every
# encrypted volume, including drives that are not plugged in — the opposite
# of the unattended-run goal that prefetching serves.
_PREFETCHABLE_PROVIDERS: frozenset[CredentialProvider] = frozenset(
    {
        CredentialProvider.KEYRING,
        CredentialProvider.ENV,
        CredentialProvider.COMMAND,
    }
)


@dataclass(frozen=True)
class PassphrasePrefetch:
    """Outcome of eagerly retrieving one configured passphrase."""

    passphrase_id: str
    volumes: tuple[str, ...]
    success: bool
    detail: str | None = None


def prefetch_passphrases(
    config: Config,
    passphrase_fn: Callable[[str], str],
    *,
    on_prefetch_start: Callable[[str], None] | None = None,
    on_prefetch_end: Callable[[str, PassphrasePrefetch], None] | None = None,
) -> list[PassphrasePrefetch]:
    """Warm the passphrase cache with *every* configured passphrase-id.

    Called once before the mount phase so that all credential-store access
    happens up front, in a single burst.  OS secret stores (macOS Keychain,
    Linux SecretService) gate access per *item* and per *binary*, so a run
    that only reads the passphrases of the drives currently plugged in leaves
    the remaining items un-approved — and the next run with a different drive
    attached blocks on an interactive approval dialog.  Reading them all,
    including the ones this run has no use for, means one approval pass covers
    every drive and subsequent runs are unattended.

    Retrieval is **best-effort**: a passphrase that cannot be retrieved is
    reported and skipped rather than aborting.  A missing passphrase only
    matters for a drive that is actually present, and that case surfaces at
    unlock time with the same error.  Failures are not cached, so the unlock
    path retries retrieval.

    Returns one :class:`PassphrasePrefetch` per configured passphrase-id, in
    id order.  Returns an empty list when the provider is not prefetchable
    (see ``_PREFETCHABLE_PROVIDERS``) or no encrypted volume is configured.
    """
    if config.credential_provider not in _PREFETCHABLE_PROVIDERS:
        return []

    passphrase_ids = collect_passphrase_ids(config)
    results: list[PassphrasePrefetch] = []
    for pid in sorted(passphrase_ids):
        if on_prefetch_start is not None:
            on_prefetch_start(pid)
        volumes = tuple(sorted(passphrase_ids[pid]))
        try:
            passphrase_fn(pid)
            result = PassphrasePrefetch(pid, volumes, success=True)
        except CredentialError as e:
            result = PassphrasePrefetch(pid, volumes, success=False, detail=str(e))
        results.append(result)
        if on_prefetch_end is not None:
            on_prefetch_end(pid, result)
    return results


def prefetch_count(config: Config) -> int:
    """Number of passphrase-ids :func:`prefetch_passphrases` would retrieve."""
    if config.credential_provider not in _PREFETCHABLE_PROVIDERS:
        return 0
    return len(collect_passphrase_ids(config))
