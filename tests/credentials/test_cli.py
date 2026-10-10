"""Tests for the credentials keyring-status command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import Result

from nbkp.cli import app
from nbkp.config import (
    Config,
    CredentialProvider,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.credentials import CredentialError, CredentialErrorReason
from tests.clihelpers import runner


def _config(
    provider: CredentialProvider = CredentialProvider.KEYRING,
    passphrase_id: str | None = "disk[1]",
) -> Config:
    mount = (
        MountConfig(
            device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
            encryption=LuksEncryptionConfig(passphrase_id=passphrase_id),
        )
        if passphrase_id is not None
        else None
    )
    return Config(
        credential_provider=provider,
        volumes={"usb": LocalVolume(slug="usb", path="/mnt/usb", mount=mount)},
    )


def _invoke(*args: str) -> Result:
    return runner.invoke(
        app, ["credentials", "keyring-status", "--config", "/fake.yaml", *args]
    )


class TestKeyringStatus:
    @patch("nbkp.credentials.cli.keyring_status_cmd.retrieve_passphrase")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_human_keeps_brackets_in_passphrase_id(
        self, mock_load: MagicMock, mock_retrieve: MagicMock
    ) -> None:
        mock_load.return_value = _config()
        mock_retrieve.return_value = "secret"
        result = _invoke()
        assert result.exit_code == 0
        assert "disk[1]" in result.output

    @patch("nbkp.credentials.cli.keyring_status_cmd.retrieve_passphrase")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_json_reports_reason(
        self, mock_load: MagicMock, mock_retrieve: MagicMock
    ) -> None:
        mock_load.return_value = _config()
        mock_retrieve.side_effect = CredentialError(
            "missing", reason=CredentialErrorReason.NOT_FOUND
        )
        result = _invoke("-o", "json")
        assert json.loads(result.stdout) == {
            "provider": "keyring",
            "checked": True,
            "passphrases": {
                "disk[1]": {
                    "volumes": ["usb"],
                    "available": False,
                    "error": "missing",
                    "reason": "not-found",
                }
            },
        }

    @patch("nbkp.credentials.cli.keyring_status_cmd.retrieve_passphrase")
    @patch("nbkp.config.cli.helpers.load_config")
    def test_prompt_provider_never_prompts(
        self, mock_load: MagicMock, mock_retrieve: MagicMock
    ) -> None:
        mock_load.return_value = _config(provider=CredentialProvider.PROMPT)
        result = _invoke("-o", "json")
        mock_retrieve.assert_not_called()
        data = json.loads(result.stdout)
        assert data["checked"] is False
        assert data["passphrases"] == {"disk[1]": {"volumes": ["usb"]}}

    @patch("nbkp.config.cli.helpers.load_config")
    def test_no_encrypted_volumes_json_is_valid(self, mock_load: MagicMock) -> None:
        mock_load.return_value = _config(passphrase_id=None)
        result = _invoke("-o", "json")
        assert result.exit_code == 0
        assert json.loads(result.stdout)["passphrases"] == {}
