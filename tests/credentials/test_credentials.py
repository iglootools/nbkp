import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import keyring
import pytest
from keyring import errors as keyring_errors
from keyring.backend import KeyringBackend
from pytest import MonkeyPatch  # noqa: PT013, TID251 -- (b) the keyring extra, simulated as missing

from nbkp.config import (
    Config,
    CredentialProvider,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    Volume,
    passphrase_env_var,
)
from nbkp.credentials import (
    CommandFn,
    CredentialError,
    CredentialErrorKind,
    PassphraseCache,
    PromptNotSuppliedError,
    collect_passphrase_ids,
    prefetch_count,
    prefetch_passphrases,
    retrieve_passphrase,
)


class _StoreKeyring(KeyringBackend):
    """A keyring backend over a dict, or failing with *error* when given."""

    # keyring types priority as a read-only class property, yet its own
    # backends, and its documentation, override it with a plain number.
    priority = 1  # pyright: ignore[reportAssignmentType]  # noqa: DC06 -- keyring reads it

    def __init__(
        self,
        passwords: dict[tuple[str, str], str] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._passwords = passwords or {}
        self._error = error

    def get_password(self, service: str, username: str) -> str | None:
        if self._error is not None:
            raise self._error
        return self._passwords.get((service, username))

    def set_password(  # noqa: DC04 -- abstract in KeyringBackend
        self,
        service: str,
        username: str,
        password: str,
    ) -> None:
        self._passwords[(service, username)] = password


@pytest.fixture
def use_keyring() -> Iterator[Callable[[KeyringBackend], None]]:
    """
    Install a keyring backend for one test, through keyring's own API.

    The backend is process-wide state: conftest points it at the fail
    backend for the session, and this restores that afterwards.
    """
    previous = keyring.get_keyring()
    yield keyring.set_keyring
    keyring.set_keyring(previous)


class TestRetrievePassphraseKeyring:
    def test_returns_password_from_keyring(
        self,
        use_keyring: Callable[[KeyringBackend], None],
    ) -> None:
        use_keyring(_StoreKeyring({("nbkp", "disk1"): "secret123"}))
        result = retrieve_passphrase("disk1", CredentialProvider.KEYRING)
        assert result == "secret123"

    def test_raises_when_password_not_found(
        self,
        use_keyring: Callable[[KeyringBackend], None],
    ) -> None:
        use_keyring(_StoreKeyring({("nbkp", "other"): "secret123"}))
        with pytest.raises(CredentialError) as excinfo:
            retrieve_passphrase("disk1", CredentialProvider.KEYRING)
        assert excinfo.value.kind == CredentialErrorKind.NOT_FOUND

    @pytest.mark.parametrize(
        "backend_error",
        [
            keyring_errors.NoKeyringError("no backend"),
            keyring_errors.KeyringLocked("x"),
        ],
    )
    def test_backend_error_becomes_credential_error(
        self,
        backend_error: Exception,
        use_keyring: Callable[[KeyringBackend], None],
    ) -> None:
        use_keyring(_StoreKeyring(error=backend_error))
        with pytest.raises(CredentialError) as excinfo:
            retrieve_passphrase("disk1", CredentialProvider.KEYRING)
        assert excinfo.value.kind == CredentialErrorKind.BACKEND_UNAVAILABLE

    def test_raises_when_keyring_not_installed(self) -> None:
        # The test environment installs every extra, so the missing one is
        # simulated: a None entry makes `import keyring` raise ImportError.
        with MonkeyPatch.context() as patch:
            patch.setitem(sys.modules, "keyring", None)
            with pytest.raises(CredentialError) as excinfo:
                retrieve_passphrase("disk1", CredentialProvider.KEYRING)
        assert excinfo.value.kind == CredentialErrorKind.NOT_INSTALLED


class TestRetrievePassphrasePrompt:
    def test_returns_prompted_value(self) -> None:
        asked: list[str] = []

        def prompt(passphrase_id: str) -> str:
            asked.append(passphrase_id)
            return "typed-secret"

        result = retrieve_passphrase("disk1", CredentialProvider.PROMPT, prompt=prompt)
        assert result == "typed-secret"
        assert asked == ["disk1"]

    def test_missing_prompt_function_raises(self) -> None:
        with pytest.raises(PromptNotSuppliedError):
            retrieve_passphrase("disk1", CredentialProvider.PROMPT)


class TestRetrievePassphraseEnv:
    def test_returns_env_value(self) -> None:
        result = retrieve_passphrase(
            "disk1",
            CredentialProvider.ENV,
            environ={"NBKP_PASSPHRASE_DISK1": "env-secret"},
        )
        assert result == "env-secret"

    def test_converts_hyphens_to_underscores(self) -> None:
        result = retrieve_passphrase(
            "my-disk",
            CredentialProvider.ENV,
            environ={"NBKP_PASSPHRASE_MY_DISK": "env-secret"},
        )
        assert result == "env-secret"

    def test_maps_every_non_alphanumeric_to_underscore(self) -> None:
        assert passphrase_env_var("nas.disk-1") == "NBKP_PASSPHRASE_NAS_DISK_1"

    def test_raises_when_env_not_set(self) -> None:
        with pytest.raises(CredentialError) as excinfo:
            retrieve_passphrase("disk1", CredentialProvider.ENV, environ={})
        assert excinfo.value.kind == CredentialErrorKind.ENV_UNSET
        assert excinfo.value.env_var == passphrase_env_var("disk1")


def _command_runner(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> tuple[list[list[str]], CommandFn]:
    """Return a CommandFn answering every command alike, and the commands it ran."""
    ran: list[list[str]] = []

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return ran, run


class TestRetrievePassphraseCommand:
    def test_returns_command_output(self) -> None:
        ran, run = _command_runner(stdout="cmd-secret\n")

        result = retrieve_passphrase(
            "disk1",
            CredentialProvider.COMMAND,
            command_template=["pass", "show", "nbkp/{id}"],
            run=run,
        )

        assert result == "cmd-secret"
        assert ran == [["pass", "show", "nbkp/disk1"]]

    def test_replaces_id_in_template(self) -> None:
        ran, run = _command_runner(stdout="secret")

        retrieve_passphrase(
            "my-drive",
            CredentialProvider.COMMAND,
            command_template=["op", "read", "op://vault/{id}/password"],
            run=run,
        )

        assert ran == [["op", "read", "op://vault/my-drive/password"]]

    def test_expands_tilde_in_the_program_only(self) -> None:
        ran, run = _command_runner(stdout="secret")

        retrieve_passphrase(
            "disk1",
            CredentialProvider.COMMAND,
            command_template=["~/bin/get-pass", "~{id}"],
            run=run,
        )

        assert ran == [[str(Path("~/bin/get-pass").expanduser()), "~disk1"]]

    def test_raises_on_command_failure(self) -> None:
        _, run = _command_runner(returncode=1, stderr="not found")

        with pytest.raises(CredentialError) as excinfo:
            retrieve_passphrase(
                "disk1",
                CredentialProvider.COMMAND,
                command_template=["pass", "show", "nbkp/{id}"],
                run=run,
            )
        assert excinfo.value.kind == CredentialErrorKind.COMMAND_FAILED

    def test_default_runner_captures_stdout(self) -> None:
        """The real runner, with a harmless command: stdout is the secret."""
        result = retrieve_passphrase(
            "disk1",
            CredentialProvider.COMMAND,
            command_template=["printf", "%s\\n", "secret-{id}"],
        )
        assert result == "secret-disk1"

    @pytest.mark.parametrize(
        ("stdout", "passphrase"),
        [
            ("secret\nlogin: me\nurl: example.com\n", "secret"),
            ("  spaced secret \n", "  spaced secret "),
            ("crlf-secret\r\n", "crlf-secret"),
            ("no-newline", "no-newline"),
        ],
    )
    def test_passphrase_is_first_line_of_output(
        self,
        stdout: str,
        passphrase: str,
    ) -> None:
        _, run = _command_runner(stdout=stdout)

        result = retrieve_passphrase(
            "disk1",
            CredentialProvider.COMMAND,
            command_template=["pass", "show", "nbkp/{id}"],
            run=run,
        )

        assert result == passphrase

    def test_raises_when_executable_missing(self) -> None:
        """The real runner, with a command that does not exist."""
        with pytest.raises(CredentialError) as excinfo:
            retrieve_passphrase(
                "disk1",
                CredentialProvider.COMMAND,
                command_template=["nbkp-no-such-executable", "{id}"],
            )
        assert excinfo.value.kind == CredentialErrorKind.COMMAND_NOT_RUNNABLE
        assert excinfo.value.command == ["nbkp-no-such-executable", "disk1"]

    def test_raises_when_no_command_template(self) -> None:
        with pytest.raises(CredentialError) as excinfo:
            retrieve_passphrase("disk1", CredentialProvider.COMMAND)
        assert excinfo.value.kind == CredentialErrorKind.COMMAND_MISSING


class TestRetrievePassphraseEmpty:
    def test_empty_env_var_is_rejected(self) -> None:
        with pytest.raises(CredentialError) as excinfo:
            retrieve_passphrase(
                "disk1",
                CredentialProvider.ENV,
                environ={"NBKP_PASSPHRASE_DISK1": ""},
            )
        assert excinfo.value.kind == CredentialErrorKind.EMPTY
        assert (excinfo.value.provider, excinfo.value.passphrase_id) == (
            CredentialProvider.ENV,
            "disk1",
        )

    @pytest.mark.parametrize("stdout", ["", "\n", "\nsecond line\n"])
    def test_command_printing_no_passphrase_is_rejected(self, stdout: str) -> None:
        _, run = _command_runner(stdout=stdout)

        with pytest.raises(CredentialError) as excinfo:
            retrieve_passphrase(
                "disk1",
                CredentialProvider.COMMAND,
                command_template=["pass", "show", "nbkp/{id}"],
                run=run,
            )
        assert excinfo.value.kind == CredentialErrorKind.EMPTY
        assert excinfo.value.provider == CredentialProvider.COMMAND


class TestPassphraseCache:
    def test_caches_on_first_call(self) -> None:
        cache = PassphraseCache()
        calls = 0

        def retrieve(pid: str) -> str:
            nonlocal calls
            calls += 1
            return f"secret-{pid}"

        result1 = cache.get_or_retrieve("disk1", retrieve)
        result2 = cache.get_or_retrieve("disk1", retrieve)
        assert result1 == "secret-disk1"
        assert result2 == "secret-disk1"
        assert calls == 1

    def test_different_ids_cached_separately(self) -> None:
        cache = PassphraseCache()

        def retrieve(pid: str) -> str:
            return f"secret-{pid}"

        assert cache.get_or_retrieve("disk1", retrieve) == "secret-disk1"
        assert cache.get_or_retrieve("disk2", retrieve) == "secret-disk2"

    def test_clear_removes_all(self) -> None:
        cache = PassphraseCache()
        calls = 0

        def retrieve(_pid: str) -> str:
            nonlocal calls
            calls += 1
            return "secret"

        cache.get_or_retrieve("disk1", retrieve)
        assert calls == 1
        cache.clear()
        cache.get_or_retrieve("disk1", retrieve)
        assert calls == 2


def _cfg(
    *,
    provider: CredentialProvider = CredentialProvider.KEYRING,
    encrypted: dict[str, str] | None = None,
    plain: list[str] | None = None,
) -> Config:
    """Build a config whose volumes map slug -> passphrase-id (or no encryption)."""
    volumes: dict[str, Volume] = {
        slug: LocalVolume(
            slug=slug,
            path=f"/mnt/{slug}",
            mount=MountConfig(
                device_uuid=f"{i:08x}-0000-0000-0000-000000000000",
                encryption=LuksEncryptionConfig(passphrase_id=pid),
            ),
        )
        for i, (slug, pid) in enumerate(sorted((encrypted or {}).items()))
    }
    for slug in plain or []:
        volumes[slug] = LocalVolume(slug=slug, path=f"/mnt/{slug}")
    return Config(
        volumes=volumes,
        credential_provider=provider,
        credential_command=(
            ["pass", "show", "nbkp/{id}"]
            if provider is CredentialProvider.COMMAND
            else None
        ),
    )


class TestCollectPassphraseIds:
    def test_groups_volumes_sharing_an_id(self) -> None:
        cfg = _cfg(encrypted={"a": "shared", "b": "shared", "c": "own"})
        assert collect_passphrase_ids(cfg) == {"shared": ["a", "b"], "own": ["c"]}

    def test_ignores_unencrypted_and_unmanaged_volumes(self) -> None:
        cfg = _cfg(encrypted={"a": "only"}, plain=["b"])
        assert collect_passphrase_ids(cfg) == {"only": ["a"]}

    def test_empty_without_encrypted_volumes(self) -> None:
        assert collect_passphrase_ids(_cfg(plain=["a"])) == {}


class TestPrefetchPassphrases:
    def test_retrieves_every_configured_id_once(self) -> None:
        cfg = _cfg(encrypted={"a": "shared", "b": "shared", "c": "own"})
        seen: list[str] = []

        def passphrase_fn(pid: str) -> str:
            seen.append(pid)
            return "secret"

        results = prefetch_passphrases(cfg, passphrase_fn)
        # Deduplicated by passphrase-id and ordered by id.
        assert seen == ["own", "shared"]
        assert [r.passphrase_id for r in results] == ["own", "shared"]
        assert all(r.success for r in results)
        assert [r.volumes for r in results] == [("c",), ("a", "b")]

    def test_failure_is_reported_not_raised(self) -> None:
        cfg = _cfg(encrypted={"a": "present", "b": "absent"})

        def passphrase_fn(pid: str) -> str:
            if pid == "absent":
                raise CredentialError(
                    CredentialErrorKind.NOT_FOUND,
                    passphrase_id=pid,
                )
            return "secret"

        results = {r.passphrase_id: r for r in prefetch_passphrases(cfg, passphrase_fn)}
        assert results["present"].success
        assert results["present"].error is None
        assert not results["absent"].success
        error = results["absent"].error
        assert error is not None
        assert error.kind == CredentialErrorKind.NOT_FOUND

    def test_skipped_for_prompt_provider(self) -> None:
        cfg = _cfg(provider=CredentialProvider.PROMPT, encrypted={"a": "id"})
        calls: list[str] = []

        def passphrase_fn(passphrase_id: str) -> str:
            calls.append(passphrase_id)
            return "unused"

        assert prefetch_passphrases(cfg, passphrase_fn) == []
        assert calls == []
        assert prefetch_count(cfg) == 0

    @pytest.mark.parametrize(
        "provider",
        [
            CredentialProvider.KEYRING,
            CredentialProvider.ENV,
            CredentialProvider.COMMAND,
        ],
    )
    def test_enabled_for_non_interactive_providers(
        self,
        provider: CredentialProvider,
    ) -> None:
        cfg = _cfg(provider=provider, encrypted={"a": "id"})
        assert prefetch_count(cfg) == 1
        assert [r.passphrase_id for r in prefetch_passphrases(cfg, lambda _: "s")] == [
            "id",
        ]

    def test_callbacks_fire_per_id(self) -> None:
        cfg = _cfg(encrypted={"a": "one", "b": "two"})
        started: list[str] = []
        ended: list[tuple[str, bool]] = []

        prefetch_passphrases(
            cfg,
            lambda _: "secret",
            on_prefetch_start=started.append,
            on_prefetch_end=lambda pid, r: ended.append((pid, r.success)),
        )
        assert started == ["one", "two"]
        assert ended == [("one", True), ("two", True)]

    def test_warms_the_shared_cache_so_unlock_does_not_retrieve_again(self) -> None:
        """Prefetch + unlock must hit the credential store once per id."""
        cfg = _cfg(encrypted={"a": "id"})
        retrievals: list[str] = []
        cache = PassphraseCache()

        def passphrase_fn(pid: str) -> str:
            return cache.get_or_retrieve(pid, lambda p: (retrievals.append(p), "s")[1])

        prefetch_passphrases(cfg, passphrase_fn)
        passphrase_fn("id")  # what mount_volume does at unlock time
        assert retrievals == ["id"]

    def test_no_encrypted_volumes_is_a_noop(self) -> None:
        cfg = _cfg(plain=["a"])
        assert prefetch_passphrases(cfg, lambda _: "s") == []
        assert prefetch_count(cfg) == 0
