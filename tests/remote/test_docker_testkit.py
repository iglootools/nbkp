import stat
from pathlib import Path
from unittest.mock import MagicMock, patch  # noqa: TID251 -- (a) the Docker SDK client

import docker.errors as docker_errors
import pytest

from nbkp.remote.testkit.constants import NETWORK_NAME
from nbkp.remote.testkit.docker import (
    create_docker_network,
    generate_ssh_keypair,
    start_bastion_container,
    start_storage_container,
)
from nbkp.remote.testkit.errors import DockerError, DockerErrorKind


@pytest.fixture
def docker_client() -> MagicMock:
    client = MagicMock()
    container = client.containers.run.return_value
    container.attrs = {
        "NetworkSettings": {"Ports": {"22/tcp": [{"HostPort": "32768"}]}}
    }
    return client


class TestSshPortBinding:
    def test_storage_container_publishes_ssh_on_loopback_only(
        self, docker_client: MagicMock, tmp_path: Path
    ) -> None:
        with patch(
            "nbkp.remote.testkit.docker.dockerlib.from_env", return_value=docker_client
        ):
            port = start_storage_container(tmp_path / "id.pub")

        assert port == 32768
        ports = docker_client.containers.run.call_args.kwargs["ports"]
        assert ports == {"22/tcp": ("127.0.0.1", 0)}

    def test_bastion_container_publishes_ssh_on_loopback_only(
        self, docker_client: MagicMock, tmp_path: Path
    ) -> None:
        with patch(
            "nbkp.remote.testkit.docker.dockerlib.from_env", return_value=docker_client
        ):
            start_bastion_container(tmp_path / "id.pub", "net")

        ports = docker_client.containers.run.call_args.kwargs["ports"]
        assert ports == {"22/tcp": ("127.0.0.1", 0)}


class TestNetworkRecreation:
    def _client_with_attached(self, error: Exception) -> MagicMock:
        client = MagicMock()
        old = client.networks.get.return_value
        old.attrs = {"Containers": {"stale": {}}}
        old.disconnect.side_effect = error
        return client

    def test_a_failed_disconnect_is_reported(self) -> None:
        """Regression: the APIError was swallowed with `pass`."""
        client = self._client_with_attached(docker_errors.APIError("refused"))
        with (
            patch("nbkp.remote.testkit.docker.dockerlib.from_env", return_value=client),
            pytest.raises(DockerError) as exc,
        ):
            create_docker_network()
        assert exc.value.kind == DockerErrorKind.REQUEST_FAILED
        assert "refused" in exc.value.detail
        client.networks.create.assert_not_called()

    def test_a_container_already_gone_is_not_an_error(self) -> None:
        client = self._client_with_attached(docker_errors.NotFound("gone"))
        with patch(
            "nbkp.remote.testkit.docker.dockerlib.from_env", return_value=client
        ):
            assert create_docker_network() == NETWORK_NAME
        client.networks.get.return_value.remove.assert_called_once()


class TestSshKeypair:
    def test_private_key_is_readable_by_its_owner_only(self, tmp_path: Path) -> None:
        private_key, public_key = generate_ssh_keypair(tmp_path)
        assert stat.S_IMODE(private_key.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
        assert public_key.read_text().startswith("ssh-ed25519 ")
