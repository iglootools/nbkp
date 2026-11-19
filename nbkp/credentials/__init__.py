"""Credential retrieval for LUKS passphrases."""

import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import SecretStr

from ..config import Config, CredentialProvider


class CredentialErrorKind(StrEnum):
    NOT_INSTALLED = "not-installed"
    NOT_FOUND = "not-found"
    BACKEND_UNAVAILABLE = "backend-unavailable"
    ENV_UNSET = "env-unset"
    COMMAND_MISSING = "command-missing"
    COMMAND_FAILED = "command-failed"
    COMMAND_NOT_RUNNABLE = "command-not-runnable"


class CredentialError(Exception):
    def __init__(self, message: str, reason: CredentialErrorKind) -> None:
        super().__init__(message)
        self.reason = reason


def _from_keyring(passphrase_id: str) -> str:
    try:
        import keyring  # type: ignore[import-untyped]
        from keyring.errors import KeyringError  # type: ignore[import-untyped]
    except ImportError:
        raise CredentialError(
            "keyring package not installed."
            " Install with: uv tool install 'nbkp[keyring]'"
            " (quotes required — the shell would glob the extra otherwise),"
            " or switch to another credential-provider.",
            reason=CredentialErrorKind.NOT_INSTALLED,
        ) from None

    try:
        password = keyring.get_password("nbkp", passphrase_id)
    except KeyringError as e:
        # No usable backend (headless host, timer session) or a locked
        # store: a retrieval failure like any other, so prefetch reports it
        # as a warning and unlock as PASSPHRASE_NOT_AVAILABLE.
        raise CredentialError(
            f"Keyring backend unavailable for id '{passphrase_id}': {e}."
            " Unlock the keyring, configure a backend,"
            " or switch to another credential-provider.",
            reason=CredentialErrorKind.BACKEND_UNAVAILABLE,
        ) from e
    if password is None:
        raise CredentialError(
            f"No passphrase found in keyring for id '{passphrase_id}'."
            f" Store it with: keyring set nbkp {passphrase_id}",
            reason=CredentialErrorKind.NOT_FOUND,
        )
    return password


type PromptFn = Callable[[str], str]
"""Asks the operator for the passphrase of a passphrase-id (``prompt`` provider).

Supplied by the CLI layer, so this package stays free of terminal I/O.
"""


def _from_prompt(passphrase_id: str, prompt: PromptFn | None) -> str:
    if prompt is None:
        # A wiring bug, not a user error: every caller that can retrieve a
        # ``prompt``-provider passphrase must pass the CLI prompt.
        raise ValueError(
            "credential-provider 'prompt' needs a prompt function; none was supplied"
        )
    return prompt(passphrase_id)


def passphrase_env_var(passphrase_id: str) -> str:
    """Environment variable the ``env`` provider reads for ``passphrase_id``."""
    return f"NBKP_PASSPHRASE_{passphrase_id.upper().replace('-', '_')}"


def _from_env(passphrase_id: str, environ: Mapping[str, str]) -> str:
    env_var = passphrase_env_var(passphrase_id)
    value = environ.get(env_var)
    if value is None:
        raise CredentialError(
            f"Environment variable '{env_var}' not set."
            f" Export it with: export {env_var}=...",
            reason=CredentialErrorKind.ENV_UNSET,
        )
    return value


type CommandFn = Callable[[list[str]], subprocess.CompletedProcess[str]]
"""Runs the ``command`` provider's command, capturing its output as text."""


def run_captured(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _from_command(
    passphrase_id: str, command_template: list[str] | None, run: CommandFn
) -> str:
    if command_template is None:
        raise CredentialError(
            "credential-command is required when credential-provider is 'command'",
            reason=CredentialErrorKind.COMMAND_MISSING,
        )
    command = [arg.replace("{id}", passphrase_id) for arg in command_template]
    try:
        result = run(command)
    except OSError as e:
        # Left uncaught, this would crash prefetch, or reach unlock as an
        # OSError and be mislabelled an unreachable host.
        raise CredentialError(
            f"Credential command could not be started ({command[0]}): {e}",
            reason=CredentialErrorKind.COMMAND_NOT_RUNNABLE,
        ) from e
    if result.returncode != 0:
        raise CredentialError(
            f"Credential command failed (exit {result.returncode}):"
            f" {shlex.join(command)}\nstderr: {result.stderr.strip()}",
            reason=CredentialErrorKind.COMMAND_FAILED,
        )
    return _first_line(result.stdout)


def _first_line(output: str) -> str:
    """The passphrase in a command's output: its first line, as ``pass``
    stores it (later lines hold metadata), without the line break but with
    any surrounding spaces, which may belong to the passphrase."""
    return output.partition("\n")[0].removesuffix("\r")


def retrieve_passphrase(
    passphrase_id: str,
    provider: CredentialProvider,
    command_template: list[str] | None = None,
    environ: Mapping[str, str] = os.environ,
    prompt: PromptFn | None = None,
    run: CommandFn = run_captured,
) -> str:
    """``environ`` is where the ``env`` provider looks the passphrase up;
    ``prompt`` is how the ``prompt`` provider asks for it; ``run`` is how the
    ``command`` provider runs its command.
    """
    match provider:
        case CredentialProvider.KEYRING:
            return _from_keyring(passphrase_id)
        case CredentialProvider.PROMPT:
            return _from_prompt(passphrase_id, prompt)
        case CredentialProvider.ENV:
            return _from_env(passphrase_id, environ)
        case CredentialProvider.COMMAND:
            return _from_command(passphrase_id, command_template, run)


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
        if passphrase_id not in self._cache:
            self._cache[passphrase_id] = SecretStr(retrieve_fn(passphrase_id))
        return self._cache[passphrase_id].get_secret_value()

    def clear(self) -> None:
        self._cache.clear()


def build_passphrase_fn(
    provider: CredentialProvider,
    command_template: list[str] | None,
    *,
    prompt: PromptFn,
    environ: Mapping[str, str] = os.environ,
    run: CommandFn = run_captured,
) -> tuple[Callable[[str], str], PassphraseCache]:
    """Build a passphrase retrieval function backed by a per-run cache.

    *prompt*, *environ* and *run* are handed to :func:`retrieve_passphrase`:
    each is used only by its own provider.
    """
    cache = PassphraseCache()

    def passphrase_fn(passphrase_id: str) -> str:
        return cache.get_or_retrieve(
            passphrase_id,
            lambda pid: retrieve_passphrase(
                pid, provider, command_template, environ, prompt, run
            ),
        )

    return passphrase_fn, cache


def collect_passphrase_ids(config: Config) -> dict[str, list[str]]:
    """Map each configured passphrase-id to the volume slugs that use it.

    Covers *every* encrypted mount-managed volume in the config, whether or
    not its drive is currently plugged in.
    """
    encrypted = [
        (vol.mount.encryption.passphrase_id, vol.slug)
        for vol in config.volumes.values()
        if vol.mount is not None and vol.mount.encryption is not None
    ]
    return {
        pid: [slug for other_pid, slug in encrypted if other_pid == pid]
        for pid in dict.fromkeys(pid for pid, _ in encrypted)
    }


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


def is_prefetchable(provider: CredentialProvider) -> bool:
    """Whether :func:`prefetch_passphrases` retrieves passphrases for ``provider``."""
    return provider in _PREFETCHABLE_PROVIDERS


@dataclass(frozen=True)
class PassphrasePrefetch:
    passphrase_id: str
    volumes: tuple[str, ...]
    success: bool
    detail: str | None = None
    reason: CredentialErrorKind | None = None


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
    if not is_prefetchable(config.credential_provider):
        return []

    passphrase_ids = collect_passphrase_ids(config)
    return [
        _prefetch_one(
            pid,
            tuple(sorted(passphrase_ids[pid])),
            passphrase_fn,
            on_prefetch_start,
            on_prefetch_end,
        )
        for pid in sorted(passphrase_ids)
    ]


def _prefetch_one(
    pid: str,
    volumes: tuple[str, ...],
    passphrase_fn: Callable[[str], str],
    on_prefetch_start: Callable[[str], None] | None,
    on_prefetch_end: Callable[[str, PassphrasePrefetch], None] | None,
) -> PassphrasePrefetch:
    if on_prefetch_start is not None:
        on_prefetch_start(pid)
    try:
        passphrase_fn(pid)
        result = PassphrasePrefetch(pid, volumes, success=True)
    except CredentialError as e:
        result = PassphrasePrefetch(
            pid, volumes, success=False, detail=str(e), reason=e.reason
        )
    if on_prefetch_end is not None:
        on_prefetch_end(pid, result)
    return result


def prefetch_count(config: Config) -> int:
    """Number of passphrase-ids :func:`prefetch_passphrases` would retrieve."""
    return (
        len(collect_passphrase_ids(config))
        if is_prefetchable(config.credential_provider)
        else 0
    )
