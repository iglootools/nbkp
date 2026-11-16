"""Tests for the credentials keyring-status command."""

import json
from pathlib import Path
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
from nbkp.config.testkit import write_config
from nbkp.credentials import CredentialError, CredentialErrorReason
from tests.clidisplay import runner


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


def _invoke(config_path: Path, *args: str) -> Result:
    return runner.invoke(
        app, ["credentials", "keyring-status", "--config", str(config_path), *args]
    )


class TestKeyringStatus:
    @patch("nbkp.credentials.cli.keyring_status_cmd.retrieve_passphrase")
    def test_human_keeps_brackets_in_passphrase_id(
        self, mock_retrieve: MagicMock, tmp_path: Path
    ) -> None:
        config_path = write_config(tmp_path, _config())
        mock_retrieve.return_value = "secret"
        result = _invoke(config_path)
        assert result.exit_code == 0
        assert "disk[1]" in result.output

    @patch("nbkp.credentials.cli.keyring_status_cmd.retrieve_passphrase")
    def test_json_reports_reason(
        self, mock_retrieve: MagicMock, tmp_path: Path
    ) -> None:
        config_path = write_config(tmp_path, _config())
        mock_retrieve.side_effect = CredentialError(
            "missing", reason=CredentialErrorReason.NOT_FOUND
        )
        result = _invoke(config_path, "-o", "json")
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
    def test_prompt_provider_never_prompts(
        self, mock_retrieve: MagicMock, tmp_path: Path
    ) -> None:
        config_path = write_config(
            tmp_path, _config(provider=CredentialProvider.PROMPT)
        )
        result = _invoke(config_path, "-o", "json")
        mock_retrieve.assert_not_called()
        data = json.loads(result.stdout)
        assert data["checked"] is False
        assert data["passphrases"] == {"disk[1]": {"volumes": ["usb"]}}

    def test_no_encrypted_volumes_json_is_valid(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, _config(passphrase_id=None))
        result = _invoke(config_path, "-o", "json")
        assert result.exit_code == 0
        assert json.loads(result.stdout)["passphrases"] == {}
