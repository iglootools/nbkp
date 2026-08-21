"""Tests for nbkp.credentials."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from nbkp.config import (
    Config,
    CredentialProvider,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.credentials import (
    CredentialError,
    PassphraseCache,
    collect_passphrase_ids,
    prefetch_count,
    prefetch_passphrases,
    retrieve_passphrase,
)


class TestRetrievePassphraseKeyring:
    def test_returns_password_from_keyring(self) -> None:
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "secret123"
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            result = retrieve_passphrase("disk1", CredentialProvider.KEYRING)
        assert result == "secret123"
        mock_keyring.get_password.assert_called_once_with("nbkp", "disk1")

    def test_raises_when_password_not_found(self) -> None:
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None
        with (
            patch.dict("sys.modules", {"keyring": mock_keyring}),
            pytest.raises(CredentialError, match="No passphrase found"),
        ):
            retrieve_passphrase("disk1", CredentialProvider.KEYRING)

    def test_raises_when_keyring_not_installed(self) -> None:
        with (
            patch.dict("sys.modules", {"keyring": None}),
            pytest.raises(CredentialError, match="keyring package not installed"),
        ):
            retrieve_passphrase("disk1", CredentialProvider.KEYRING)


class TestRetrievePassphrasePrompt:
    def test_returns_prompted_value(self) -> None:
        with patch("nbkp.credentials.typer") as mock_typer:
            mock_typer.prompt.return_value = "typed-secret"
            result = retrieve_passphrase("disk1", CredentialProvider.PROMPT)
        assert result == "typed-secret"
        mock_typer.prompt.assert_called_once_with(
            "LUKS passphrase for disk1",
            hide_input=True,
        )


class TestRetrievePassphraseEnv:
    def test_returns_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NBKP_PASSPHRASE_DISK1", "env-secret")
        result = retrieve_passphrase("disk1", CredentialProvider.ENV)
        assert result == "env-secret"

    def test_converts_hyphens_to_underscores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NBKP_PASSPHRASE_MY_DISK", "env-secret")
        result = retrieve_passphrase("my-disk", CredentialProvider.ENV)
        assert result == "env-secret"

    def test_raises_when_env_not_set(self) -> None:
        with pytest.raises(CredentialError, match="NBKP_PASSPHRASE_DISK1"):
            retrieve_passphrase("disk1", CredentialProvider.ENV)


class TestRetrievePassphraseCommand:
    def test_returns_command_output(self) -> None:
        with patch("nbkp.credentials.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="cmd-secret\n", stderr=""
            )
            result = retrieve_passphrase(
                "disk1",
                CredentialProvider.COMMAND,
                command_template=["pass", "show", "nbkp/{id}"],
            )
        assert result == "cmd-secret"
        mock_run.assert_called_once_with(
            ["pass", "show", "nbkp/disk1"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_replaces_id_in_template(self) -> None:
        with patch("nbkp.credentials.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="secret", stderr=""
            )
            retrieve_passphrase(
                "my-drive",
                CredentialProvider.COMMAND,
                command_template=["op", "read", "op://vault/{id}/password"],
            )
        mock_run.assert_called_once_with(
            ["op", "read", "op://vault/my-drive/password"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_raises_on_command_failure(self) -> None:
        with patch("nbkp.credentials.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="not found"
            )
            with pytest.raises(CredentialError, match="failed.*exit 1"):
                retrieve_passphrase(
                    "disk1",
                    CredentialProvider.COMMAND,
                    command_template=["pass", "show", "nbkp/{id}"],
                )

    def test_raises_when_no_command_template(self) -> None:
        with pytest.raises(CredentialError, match="credential-command is required"):
            retrieve_passphrase("disk1", CredentialProvider.COMMAND)


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

        def retrieve(pid: str) -> str:
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
    volumes: dict[str, LocalVolume] = {
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
        volumes=volumes,  # type: ignore[arg-type]
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
                raise CredentialError("No passphrase found in keyring for id 'absent'")
            return "secret"

        results = {r.passphrase_id: r for r in prefetch_passphrases(cfg, passphrase_fn)}
        assert results["present"].success
        assert not results["absent"].success
        assert "No passphrase found" in (results["absent"].detail or "")

    def test_skipped_for_prompt_provider(self) -> None:
        cfg = _cfg(provider=CredentialProvider.PROMPT, encrypted={"a": "id"})
        calls: list[str] = []

        assert prefetch_passphrases(cfg, calls.append) == []  # type: ignore[arg-type]
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
        self, provider: CredentialProvider
    ) -> None:
        cfg = _cfg(provider=provider, encrypted={"a": "id"})
        assert prefetch_count(cfg) == 1
        assert [r.passphrase_id for r in prefetch_passphrases(cfg, lambda _: "s")] == [
            "id"
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
