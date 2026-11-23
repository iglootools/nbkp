"""Tests for credential error rendering."""

import pytest

from nbkp.config import CredentialProvider
from nbkp.credentials import CredentialError, CredentialErrorKind
from nbkp.credentials.output import credential_error_message


class TestCredentialErrorMessage:
    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (
                CredentialError(CredentialErrorKind.NOT_FOUND, passphrase_id="disk1"),
                (
                    "No passphrase found in keyring for id 'disk1'."
                    " Store it with: keyring set nbkp disk1"
                ),
            ),
            (
                CredentialError(
                    CredentialErrorKind.ENV_UNSET,
                    env_var="NBKP_PASSPHRASE_DISK1",
                ),
                (
                    "Environment variable 'NBKP_PASSPHRASE_DISK1' not set."
                    " Export it with: export NBKP_PASSPHRASE_DISK1=..."
                ),
            ),
            (
                CredentialError(
                    CredentialErrorKind.EMPTY,
                    provider=CredentialProvider.ENV,
                    passphrase_id="disk1",
                ),
                (
                    "Credential provider 'env' returned an empty passphrase"
                    " for id 'disk1'."
                ),
            ),
            (
                CredentialError(
                    CredentialErrorKind.COMMAND_FAILED,
                    command=["pass", "show", "nbkp/my disk"],
                    exit_code=1,
                    detail="not found",
                ),
                (
                    "Credential command failed (exit 1): pass show 'nbkp/my disk'"
                    "\nstderr: not found"
                ),
            ),
        ],
    )
    def test_renders_kind_and_fields(
        self,
        error: CredentialError,
        message: str,
    ) -> None:
        assert credential_error_message(error) == message

    def test_install_hint_keeps_the_quoted_extra(self) -> None:
        message = credential_error_message(
            CredentialError(CredentialErrorKind.NOT_INSTALLED),
        )
        assert "uv tool install 'nbkp[keyring]'" in message
