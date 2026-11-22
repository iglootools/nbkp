"""Host key verification on the Paramiko/Fabric path, against an in-process
SSH server on localhost that offers no authentication method: a connection
gets past host key verification and then fails authentication, so the type
of the error tells which of the two refused it."""

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import paramiko
import pytest
from pytest import MonkeyPatch  # noqa: TID251 -- (c) HOME, read by Paramiko

from nbkp.config import SshConnectionOptions, SshEndpoint
from nbkp.preflight.volume_checks import unreachable_diagnostics
from nbkp.remote import run_remote_command
from nbkp.remote.errors import UnknownHostKeyError


class _NoAuthServer(paramiko.ServerInterface):
    def get_allowed_auths(  # noqa: DC04 -- Paramiko's ServerInterface calls it
        self, username: str
    ) -> str:
        return "publickey"


class _LocalSshServer:
    def __init__(self) -> None:
        self.host_key = paramiko.ECDSAKey.generate()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._listener.getsockname()[1]
        self._transports: list[paramiko.Transport] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            transport = paramiko.Transport(sock)
            transport.add_server_key(self.host_key)
            self._transports.append(transport)
            try:
                transport.start_server(server=_NoAuthServer())
            except paramiko.SSHException, EOFError:
                pass

    def close(self) -> None:
        self._listener.close()
        for transport in self._transports:
            transport.close()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path) -> Iterator[None]:
    """Keep the developer's ~/.ssh/known_hosts and ~/.ssh/config out of it."""
    with MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(tmp_path))
        yield


@pytest.fixture
def ssh_server() -> Iterator[_LocalSshServer]:
    server = _LocalSshServer()
    yield server
    server.close()


def _endpoint(port: int, options: SshConnectionOptions) -> SshEndpoint:
    return SshEndpoint(
        slug="local",
        host="127.0.0.1",
        port=port,
        user="nobody",
        connection_options=options.model_copy(
            update={"allow_agent": False, "look_for_keys": False}
        ),
    )


def _known_hosts(tmp_path: Path, server: _LocalSshServer) -> Path:
    path = tmp_path / "known_hosts"
    entry = f"[127.0.0.1]:{server.port}"
    path.write_text(
        f"{entry} {server.host_key.get_name()} {server.host_key.get_base64()}\n"
    )
    return path


class TestStrictHostKeyChecking:
    def test_default_options_refuse_unknown_host(
        self, ssh_server: _LocalSshServer
    ) -> None:
        endpoint = _endpoint(ssh_server.port, SshConnectionOptions())

        with pytest.raises(UnknownHostKeyError):
            run_remote_command(endpoint, ["true"])

    def test_missing_known_hosts_file_refuses_host(
        self, ssh_server: _LocalSshServer, tmp_path: Path
    ) -> None:
        options = SshConnectionOptions(known_hosts_file=str(tmp_path / "absent"))

        with pytest.raises(UnknownHostKeyError):
            run_remote_command(_endpoint(ssh_server.port, options), ["true"])

    def test_known_host_passes_verification(
        self, ssh_server: _LocalSshServer, tmp_path: Path
    ) -> None:
        options = SshConnectionOptions(
            known_hosts_file=str(_known_hosts(tmp_path, ssh_server))
        )

        with pytest.raises(paramiko.SSHException) as excinfo:
            run_remote_command(_endpoint(ssh_server.port, options), ["true"])
        assert not isinstance(
            excinfo.value, UnknownHostKeyError | paramiko.BadHostKeyException
        )

    def test_known_hosts_in_home_is_consulted(
        self, ssh_server: _LocalSshServer, tmp_path: Path
    ) -> None:
        (tmp_path / ".ssh").mkdir()
        _known_hosts(tmp_path, ssh_server).rename(tmp_path / ".ssh" / "known_hosts")
        endpoint = _endpoint(ssh_server.port, SshConnectionOptions())

        with pytest.raises(paramiko.SSHException) as excinfo:
            run_remote_command(endpoint, ["true"])
        assert not isinstance(excinfo.value, UnknownHostKeyError)

    def test_disabled_checking_trusts_unknown_host(
        self, ssh_server: _LocalSshServer
    ) -> None:
        options = SshConnectionOptions(
            strict_host_key_checking=False, known_hosts_file="/dev/null"
        )

        with pytest.raises(paramiko.SSHException) as excinfo:
            run_remote_command(_endpoint(ssh_server.port, options), ["true"])
        assert not isinstance(excinfo.value, UnknownHostKeyError)

    def test_disabled_checking_accepts_missing_known_hosts_file(
        self, ssh_server: _LocalSshServer, tmp_path: Path
    ) -> None:
        options = SshConnectionOptions(
            strict_host_key_checking=False,
            known_hosts_file=str(tmp_path / "absent"),
        )

        with pytest.raises(paramiko.SSHException) as excinfo:
            run_remote_command(_endpoint(ssh_server.port, options), ["true"])
        assert not isinstance(excinfo.value, UnknownHostKeyError)


def test_unknown_host_is_an_auth_failure() -> None:
    diagnostics = unreachable_diagnostics(UnknownHostKeyError("nas.example.com"))
    assert diagnostics.ssh_auth_failed is True
