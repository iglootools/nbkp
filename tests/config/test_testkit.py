"""``write_config`` must hand the loader a file it reads back unchanged.

CLI tests rely on it to run the real loader instead of injecting a
``Config``: a field the round trip lost or altered would silently change
what those tests exercise.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from nbkp.config import (
    Config,
    CredentialProvider,
    LocalVolume,
    MountConfig,
    RsyncOptions,
    SshEndpoint,
    load_config,
)
from nbkp.config.testkit import config_show_config, write_config
from nbkp.preflight.testkit.check import check_config
from nbkp.preflight.testkit.troubleshoot import troubleshoot_config
from nbkp.sync.testkit.seed import build_chain_config, build_local_chain_config
from tests.fixtures.preflight import config_with_locations, sample_config


def _chain_config() -> Config:
    bastion = SshEndpoint(slug="bastion", host="bastion.example.com", port=2222)
    proxied = SshEndpoint(
        slug="via-bastion", host="storage", user="backup", proxy_jump="bastion"
    )
    return build_chain_config(
        Path("/seed"),
        bastion,
        proxied,
        luks_uuid="0f0e0d0c-0b0a-0908-0706-050403020100",
        rsync_options=RsyncOptions(compress=True, extra_options=["--bwlimit=1000"]),
        max_snapshots=3,
        credential_provider=CredentialProvider.ENV,
    )


def _discovered_mount_config() -> Config:
    """A mount-managed volume without ``path``: serialized as ``path: null``."""
    return Config(
        volumes={
            "usb": LocalVolume(
                slug="usb",
                mount=MountConfig(device_uuid="01234567-89ab-cdef-0123-456789abcdef"),
            )
        }
    )


def _command_credentials_config() -> Config:
    return Config(
        credential_provider=CredentialProvider.COMMAND,
        credential_command=["pass", "show", "nbkp/{id}"],
    )


@pytest.mark.parametrize(
    "build",
    [
        Config,
        sample_config,
        config_with_locations,
        config_show_config,
        check_config,
        troubleshoot_config,
        _chain_config,
        _discovered_mount_config,
        _command_credentials_config,
        lambda: build_local_chain_config(Path("/seed"), max_snapshots=2),
    ],
)
def test_write_config_round_trips(build: Callable[[], Config], tmp_path: Path) -> None:
    config = build()

    path = write_config(tmp_path, config)

    assert path == tmp_path / "config.yaml"
    assert load_config(str(path)) == config


def test_write_config_takes_a_file_name(tmp_path: Path) -> None:
    path = write_config(tmp_path, sample_config(), name="other.yaml")

    assert path == tmp_path / "other.yaml"
    assert load_config(str(path)) == sample_config()
