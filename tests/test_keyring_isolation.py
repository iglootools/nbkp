"""The test session must never reach the developer's real OS keyring.

``tests/conftest.py`` points keyring at its fail backend; these tests guard
that isolation, so a test that loads a config with an encrypted volume can
never pop a Keychain approval dialog.
"""

import os

import pytest

from nbkp.config import CredentialProvider
from nbkp.credentials import (
    CredentialError,
    CredentialErrorKind,
    retrieve_passphrase,
)


def test_keyring_uses_the_fail_backend() -> None:
    keyring = pytest.importorskip("keyring")
    fail = pytest.importorskip("keyring.backends.fail")

    assert isinstance(keyring.get_keyring(), fail.Keyring)
    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.fail.Keyring"


def test_keyring_provider_reports_no_backend() -> None:
    pytest.importorskip("keyring")

    with pytest.raises(CredentialError) as exc_info:
        retrieve_passphrase("any-id", CredentialProvider.KEYRING)

    assert exc_info.value.reason == CredentialErrorKind.BACKEND_UNAVAILABLE
