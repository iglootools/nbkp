"""Tests for nbkp.credentials."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from nbkp.config import CredentialProvider
from nbkp.credentials import (
    CredentialError,
    PassphraseCache,
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
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            with pytest.raises(CredentialError, match="No passphrase found"):
                retrieve_passphrase("disk1", CredentialProvider.KEYRING)

    def test_raises_when_keyring_not_installed(self) -> None:
        with patch.dict("sys.modules", {"keyring": None}):
            with pytest.raises(CredentialError, match="keyring package not installed"):
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
