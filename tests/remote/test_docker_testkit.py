from pathlib import Path
from unittest.mock import MagicMock, patch  # noqa: TID251 -- (a) the Docker SDK client

import pytest

from nbkp.remote.testkit.docker import start_bastion_container, start_storage_container


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
