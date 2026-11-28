"""Credential retrieval for LUKS passphrases."""

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import SecretStr

from nbkp.config import Config, CredentialProvider, passphrase_env_var


class CredentialErrorKind(StrEnum):
    """Why a passphrase could not be retrieved from its provider."""

    NOT_INSTALLED = "not-installed"
    NOT_FOUND = "not-found"
    BACKEND_UNAVAILABLE = "backend-unavailable"
    ENV_UNSET = "env-unset"
    COMMAND_MISSING = "command-missing"
    COMMAND_FAILED = "command-failed"
    COMMAND_NOT_RUNNABLE = "command-not-runnable"
    EMPTY = "empty"


class CredentialError(Exception):
    """
    A passphrase could not be retrieved; *kind* says why.

    The fields that apply to *kind* are set: *passphrase_id* for the keyring,
    *env_var* for the environment, *command* (and *exit_code*) for a credential
    command, *detail* for the underlying error or the command's stderr, and
    *provider* and *passphrase_id* for an empty passphrase. The message users
    see is rendered by ``nbkp.credentials.output`` from these fields.
    """

    def __init__(
        self,
        kind: CredentialErrorKind,
        *,
        passphrase_id: str | None = None,
        env_var: str | None = None,
        command: list[str] | None = None,
        exit_code: int | None = None,
        detail: str | None = None,
        provider: CredentialProvider | None = None,
    ) -> None:
        """Build the error from its kind and the fields that apply to it."""
        self.kind = kind
        self.passphrase_id = passphrase_id
        self.env_var = env_var
        self.command = command
        self.exit_code = exit_code
        self.detail = detail
        self.provider = provider
        super().__init__(
            ": ".join(
                str(part)
                for part in (kind, passphrase_id, env_var, provider, exit_code, detail)
                if part is not None
            ),
        )


class PromptNotSuppliedError(ValueError):
    """
    A ``prompt``-provider passphrase was retrieved without a prompt function.

    A wiring bug, not a user error: every caller that can retrieve such a
    passphrase must pass the CLI prompt.
    """

    def __init__(self) -> None:
        """Build the error; its message names the missing prompt function."""
        super().__init__(
            "credential-provider 'prompt' needs a prompt function; none was supplied",
        )


def _from_keyring(passphrase_id: str) -> str:
    try:
        import keyring  # noqa: PLC0415 -- optional extra (nbkp[keyring])
        from keyring.errors import KeyringError  # noqa: PLC0415 -- optional extra
    except ImportError:
        raise CredentialError(CredentialErrorKind.NOT_INSTALLED) from None

    try:
        password = keyring.get_password("nbkp", passphrase_id)
    except KeyringError as e:
        # No usable backend (headless host, timer session) or a locked
        # store: a retrieval failure like any other, so prefetch reports it
        # as a warning and unlock as PASSPHRASE_NOT_AVAILABLE.
        raise CredentialError(
            CredentialErrorKind.BACKEND_UNAVAILABLE,
            passphrase_id=passphrase_id,
            detail=str(e),
        ) from e
    if password is None:
        raise CredentialError(
            CredentialErrorKind.NOT_FOUND,
            passphrase_id=passphrase_id,
        )
    return password


type PassphraseFn = Callable[[str], str]
"""Returns the passphrase of a passphrase-id, raising :class:`CredentialError`."""


type PromptFn = Callable[[str], str]
"""Asks the operator for the passphrase of a passphrase-id (``prompt`` provider).

Supplied by the CLI layer, so this package stays free of terminal I/O.
"""


def _from_prompt(passphrase_id: str, prompt: PromptFn | None) -> str:
    if prompt is None:
        raise PromptNotSuppliedError
    return prompt(passphrase_id)


def _from_env(passphrase_id: str, environ: Mapping[str, str]) -> str:
    env_var = passphrase_env_var(passphrase_id)
    value = environ.get(env_var)
    if value is None:
        raise CredentialError(CredentialErrorKind.ENV_UNSET, env_var=env_var)
    return value


type CommandFn = Callable[[list[str]], subprocess.CompletedProcess[str]]
"""Runs the ``command`` provider's command, capturing its output as text."""


def run_captured(command: list[str]) -> subprocess.CompletedProcess[str]:
    """
    Run *command* and return its result, stdout and stderr captured as text.

    A non-zero exit is returned, not raised; a command that cannot be started
    raises ``OSError``.
    """
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _from_command(
    passphrase_id: str,
    command_template: list[str] | None,
    run: CommandFn,
) -> str:
    if command_template is None:
        raise CredentialError(CredentialErrorKind.COMMAND_MISSING)
    program, *args = (arg.replace("{id}", passphrase_id) for arg in command_template)
    # No shell runs the command, so nothing else expands a `~/bin/get-pass` the
    # user wrote. Only the program is a path for certain; the arguments may be
    # anything, a `~` inside a vault reference included.
    command = [str(Path(program).expanduser()), *args]
    try:
        result = run(command)
    except OSError as e:
        # Left uncaught, this would crash prefetch, or reach unlock as an
        # OSError and be mislabelled an unreachable host.
        raise CredentialError(
            CredentialErrorKind.COMMAND_NOT_RUNNABLE,
            command=command,
            detail=str(e),
        ) from e
    if result.returncode != 0:
        raise CredentialError(
            CredentialErrorKind.COMMAND_FAILED,
            command=command,
            exit_code=result.returncode,
            detail=result.stderr.strip(),
        )
    return _first_line(result.stdout)


def _first_line(output: str) -> str:
    """
    Return the passphrase in a command's output.

    Its first line, as ``pass`` stores it (later lines hold metadata), without the line
    break but with any surrounding spaces, which may belong to the passphrase.
    """
    return output.partition("\n")[0].removesuffix("\r")


def retrieve_passphrase(
    passphrase_id: str,
    provider: CredentialProvider,
    command_template: list[str] | None = None,
    environ: Mapping[str, str] = os.environ,
    prompt: PromptFn | None = None,
    *,
    run: CommandFn = run_captured,
) -> str:
    """
    Retrieve a passphrase from its configured provider; an empty one is an error.

    ``environ`` is where the ``env`` provider looks the passphrase up; ``prompt`` is how
    the ``prompt`` provider asks for it; ``run`` is how the ``command`` provider runs
    its command.
    """
    match provider:
        case CredentialProvider.KEYRING:
            passphrase = _from_keyring(passphrase_id)
        case CredentialProvider.PROMPT:
            passphrase = _from_prompt(passphrase_id, prompt)
        case CredentialProvider.ENV:
            passphrase = _from_env(passphrase_id, environ)
        case CredentialProvider.COMMAND:
            passphrase = _from_command(passphrase_id, command_template, run)
    if not passphrase:
        # An empty variable or a command printing nothing: no LUKS key is empty, and
        # unlock would only fail with udisks' generic error, not naming the provider.
        raise CredentialError(
            CredentialErrorKind.EMPTY,
            passphrase_id=passphrase_id,
            provider=provider,
        )
    return passphrase


class PassphraseCache:
    """
    In-memory cache for passphrases during a single run.

    Values are stored as ``SecretStr`` to prevent accidental logging.
    """

    def __init__(self) -> None:
        """Build an empty cache."""
        self._cache: dict[str, SecretStr] = {}

    def get_or_retrieve(
        self,
        passphrase_id: str,
        retrieve_fn: PassphraseFn,
    ) -> str:
        """
        Return the passphrase of *passphrase_id*, retrieving it on first use.

        *retrieve_fn* is called only on a miss; an exception it raises propagates
        and nothing is cached, so the next call retries.
        """
        if passphrase_id not in self._cache:
            self._cache[passphrase_id] = SecretStr(retrieve_fn(passphrase_id))
        return self._cache[passphrase_id].get_secret_value()

    def clear(self) -> None:
        """Forget every cached passphrase, so none outlives the run."""
        self._cache.clear()


def build_passphrase_fn(
    provider: CredentialProvider,
    command_template: list[str] | None,
    *,
    prompt: PromptFn,
    environ: Mapping[str, str] = os.environ,
    run: CommandFn = run_captured,
) -> tuple[PassphraseFn, PassphraseCache]:
    """
    Build a passphrase retrieval function backed by a per-run cache.

    *prompt*, *environ* and *run* are handed to :func:`retrieve_passphrase`:
    each is used only by its own provider.
    """
    cache = PassphraseCache()

    def passphrase_fn(passphrase_id: str) -> str:
        return cache.get_or_retrieve(
            passphrase_id,
            lambda pid: retrieve_passphrase(
                pid,
                provider,
                command_template,
                environ,
                prompt,
                run=run,
            ),
        )

    return passphrase_fn, cache


def collect_passphrase_ids(config: Config) -> dict[str, list[str]]:
    """
    Map each configured passphrase-id to the volume slugs that use it.

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
    },
)


def is_prefetchable(provider: CredentialProvider) -> bool:
    """Whether :func:`prefetch_passphrases` retrieves passphrases for ``provider``."""
    return provider in _PREFETCHABLE_PROVIDERS


@dataclass(frozen=True)
class PassphrasePrefetch:
    """
    The outcome of prefetching one passphrase-id, and the volumes that use it.

    On failure, *error* says why the passphrase could not be retrieved.
    """

    passphrase_id: str
    volumes: tuple[str, ...]
    success: bool
    error: CredentialError | None = None


def prefetch_passphrases(
    config: Config,
    passphrase_fn: PassphraseFn,
    *,
    on_prefetch_start: Callable[[str], None] | None = None,
    on_prefetch_end: Callable[[str, PassphrasePrefetch], None] | None = None,
) -> list[PassphrasePrefetch]:
    """
    Warm the passphrase cache with *every* configured passphrase-id.

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
    passphrase_fn: PassphraseFn,
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
            pid,
            volumes,
            success=False,
            error=e,
        )
    if on_prefetch_end is not None:
        on_prefetch_end(pid, result)
    return result


def prefetch_count(config: Config) -> int:
    """Count the passphrase-ids :func:`prefetch_passphrases` would retrieve."""
    return (
        len(collect_passphrase_ids(config))
        if is_prefetchable(config.credential_provider)
        else 0
    )
