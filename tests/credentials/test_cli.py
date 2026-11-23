"""
Tests for the credentials keyring-status command.

The command reads passphrases through the runtime's environment (``env``
provider) and credential command runner (``command`` provider), which the
tests fake; the JSON view is also tested from prefetch results.
"""

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

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
from nbkp.credentials import CommandFn, CredentialErrorKind, PassphrasePrefetch
from nbkp.credentials.cli.keyring_status_cmd import keyring_status_json
from tests.fakes import completed, fake_runtime
from tests.fixtures.cli import runner


def _config(
    provider: CredentialProvider = CredentialProvider.ENV,
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
        credential_command=(
            ["pass", "show", "{id}"] if provider is CredentialProvider.COMMAND else None
        ),
        volumes={"usb": LocalVolume(slug="usb", path="/mnt/usb", mount=mount)},
    )


def _invoke(
    config_path: Path,
    *args: str,
    environ: Mapping[str, str] | None = None,
    run_credential_command: CommandFn | None = None,
) -> Result:
    return runner.invoke(
        app,
        ["credentials", "keyring-status", "--config", str(config_path), *args],
        obj=fake_runtime(
            environ=environ,
            run_credential_command=run_credential_command,
        ),
    )


class TestKeyringStatusJson:
    def test_reports_availability_and_reason(self) -> None:
        missing = PassphrasePrefetch(
            "disk[1]",
            ("usb",),
            success=False,
            detail="missing",
            kind=CredentialErrorKind.NOT_FOUND,
        )

        assert keyring_status_json(_config(CredentialProvider.KEYRING), [missing]) == {
            "provider": "keyring",
            "checked": True,
            "passphrases": {
                "disk[1]": {
                    "volumes": ["usb"],
                    "available": False,
                    "error": "missing",
                    "reason": "not-found",
                },
            },
        }

    def test_unchecked_provider_lists_ids_only(self) -> None:
        assert keyring_status_json(_config(CredentialProvider.PROMPT), []) == {
            "provider": "prompt",
            "checked": False,
            "passphrases": {"disk[1]": {"volumes": ["usb"]}},
        }


class TestKeyringStatus:
    def test_human_keeps_brackets_in_passphrase_id(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, _config())

        result = _invoke(config_path, environ={"NBKP_PASSPHRASE_DISK_1_": "secret"})

        assert result.exit_code == 0
        assert "disk[1]" in result.output
        assert "✓ available" in result.output

    def test_json_reports_why_a_passphrase_is_missing(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, _config(CredentialProvider.COMMAND))
        commands: list[list[str]] = []

        def pass_show(command: list[str]) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return completed(1, stderr="not in the password store")

        result = _invoke(config_path, "-o", "json", run_credential_command=pass_show)

        assert result.exit_code == 1
        assert commands == [["pass", "show", "disk[1]"]]
        [entry] = json.loads(result.stdout)["passphrases"].values()
        assert (entry["available"], entry["reason"]) == (False, "command-failed")
        assert "not in the password store" in entry["error"]

    def test_human_exits_1_when_a_passphrase_is_missing(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, _config())

        result = _invoke(config_path, environ={})

        assert result.exit_code == 1
        assert "missing" in result.output

    def test_prompt_provider_never_prompts(self, tmp_path: Path) -> None:
        config_path = write_config(
            tmp_path,
            _config(provider=CredentialProvider.PROMPT),
        )

        result = _invoke(config_path, "-o", "json")

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["checked"] is False
        assert data["passphrases"] == {"disk[1]": {"volumes": ["usb"]}}

    def test_human_prompt_provider_says_there_is_nothing_to_check(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = write_config(
            tmp_path,
            _config(provider=CredentialProvider.PROMPT),
        )

        result = _invoke(config_path)

        assert result.exit_code == 0
        assert result.stdout == ""
        assert "Credential provider 'prompt' asks for each passphrase" in (
            result.stderr
        )

    def test_human_no_encrypted_volumes(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, _config(passphrase_id=None))

        result = _invoke(config_path)

        assert result.exit_code == 0
        assert result.stdout == ""
        assert result.stderr == "No encrypted volumes found.\n"

    def test_no_encrypted_volumes_json_is_valid(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, _config(passphrase_id=None))
        result = _invoke(config_path, "-o", "json")
        assert result.exit_code == 0
        assert json.loads(result.stdout)["passphrases"] == {}
