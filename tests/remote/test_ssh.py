import errno
import io
import os
import re
import shlex
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch  # noqa: TID251 -- (a) Fabric's Connection

import pytest

from nbkp.config import LocalVolume, RemoteVolume, SshConnectionOptions, SshEndpoint
from nbkp.remote import (
    build_ssh_base_args,
    build_ssh_e_option,
    format_remote_path,
    run_remote_command,
)
from nbkp.remote.endpoints import ResolvedEndpoint
from nbkp.remote.pool import connection_scope
from nbkp.remote.ssh import wrap_cmd

_SSH_KEY = str(Path("~/.ssh/key").expanduser())

_DEFAULT_O_OPTIONS = [
    "-o",
    "ConnectTimeout=10",
    "-o",
    "BatchMode=yes",
]


def _expand_proxy_command(option: str, host: str) -> list[str]:
    """
    Expand a ``ProxyCommand=`` value's tokens as ssh does, and split it as sh does.

    Covers the tokens nbkp emits: ``%%``, ``%h`` and ``%p`` (port 22).
    """
    value = option.removeprefix("ProxyCommand=")
    expanded = re.sub(
        r"%(.)",
        lambda m: {"%": "%", "h": host, "p": "22"}[m.group(1)],
        value,
    )
    return shlex.split(expanded)


class TestBuildSshBaseArgs:
    def test_minimal(self, ssh_endpoint_minimal: SshEndpoint) -> None:
        args = build_ssh_base_args(ssh_endpoint_minimal)
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "nas2.example.com",
        ]

    def test_full(self, ssh_endpoint: SshEndpoint) -> None:
        args = build_ssh_base_args(ssh_endpoint)
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-p",
            "5022",
            "-i",
            _SSH_KEY,
            "backup@nas.example.com",
        ]

    def test_with_ssh_options(self) -> None:
        server = SshEndpoint(
            slug="host-server",
            host="host.example.com",
            connection_options=SshConnectionOptions(
                strict_host_key_checking=False,
                known_hosts_file="/dev/null",
            ),
        )
        args = build_ssh_base_args(server)
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "host.example.com",
        ]

    def test_disabled_checking_with_known_hosts_file_keeps_warnings(self) -> None:
        server = SshEndpoint(
            slug="host-server",
            host="host.example.com",
            connection_options=SshConnectionOptions(
                strict_host_key_checking=False,
                known_hosts_file="/etc/nbkp/known_hosts",
            ),
        )
        args = build_ssh_base_args(server)
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/etc/nbkp/known_hosts",
            "host.example.com",
        ]

    def test_known_hosts_file_with_whitespace_is_quoted(self) -> None:
        server = SshEndpoint(
            slug="host-server",
            host="host.example.com",
            connection_options=SshConnectionOptions(
                known_hosts_file='/srv/my keys/"known" hosts',
            ),
        )
        args = build_ssh_base_args(server)
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            'UserKnownHostsFile="/srv/my keys/\\"known\\" hosts"',
            "host.example.com",
        ]

    def test_custom_connect_timeout(self) -> None:
        server = SshEndpoint(
            slug="slow-server",
            host="slow.example.com",
            connection_options=SshConnectionOptions(connect_timeout=30),
        )
        args = build_ssh_base_args(server)
        assert args == [
            "ssh",
            "-o",
            "ConnectTimeout=30",
            "-o",
            "BatchMode=yes",
            "slow.example.com",
        ]

    def test_compress(self) -> None:
        server = SshEndpoint(
            slug="compressed",
            host="host.example.com",
            connection_options=SshConnectionOptions(compress=True),
        )
        args = build_ssh_base_args(server)
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            "Compression=yes",
            "host.example.com",
        ]

    def test_forward_agent(self) -> None:
        server = SshEndpoint(
            slug="forwarded",
            host="host.example.com",
            connection_options=SshConnectionOptions(forward_agent=True),
        )
        args = build_ssh_base_args(server)
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            "ForwardAgent=yes",
            "host.example.com",
        ]

    def test_server_alive_interval(self) -> None:
        server = SshEndpoint(
            slug="keepalive",
            host="host.example.com",
            connection_options=SshConnectionOptions(
                server_alive_interval=60,
            ),
        )
        args = build_ssh_base_args(server)
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            "ServerAliveInterval=60",
            "host.example.com",
        ]

    def test_proxy_jump_with_user_and_port(self) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.example.com",
        )
        proxy = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            port=2222,
            user="admin",
        )
        args = build_ssh_base_args(server, [proxy])
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -p 2222"
            " -W %h:%p admin@bastion.example.com"
        )
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            f"ProxyCommand={proxy_cmd}",
            "target.example.com",
        ]

    def test_proxy_jump_default_port(self) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.example.com",
        )
        proxy = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            user="admin",
        )
        args = build_ssh_base_args(server, [proxy])
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -W %h:%p admin@bastion.example.com"
        )
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            f"ProxyCommand={proxy_cmd}",
            "target.example.com",
        ]

    def test_proxy_jump_no_user(self) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.example.com",
        )
        proxy = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            port=2222,
        )
        args = build_ssh_base_args(server, [proxy])
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -p 2222"
            " -W %h:%p bastion.example.com"
        )
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            f"ProxyCommand={proxy_cmd}",
            "target.example.com",
        ]

    def test_proxy_chain_multi_hop(self) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.example.com",
        )
        proxy1 = SshEndpoint(
            slug="bastion1",
            host="bastion1.example.com",
            user="user1",
        )
        proxy2 = SshEndpoint(
            slug="bastion2",
            host="bastion2.example.com",
            port=2222,
        )
        args = build_ssh_base_args(server, [proxy1, proxy2])
        inner = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -W %%h:%%p user1@bastion1.example.com"
        )
        # The hop runs through ``sh -c``: the nested ProxyCommand is one
        # shell word, so it must be quoted.
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -p 2222"
            f" -o {shlex.quote(f'ProxyCommand={inner}')}"
            " -W %h:%p bastion2.example.com"
        )
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            f"ProxyCommand={proxy_cmd}",
            "target.example.com",
        ]

    def test_proxy_command_keeps_percent_in_proxy_settings_literal(self) -> None:
        """
        Ssh expands ``%`` tokens in a ProxyCommand before running it.

        A ``%`` in a hop's key or user must reach that hop's ``ssh`` unchanged, at
        every nesting level, while each hop's ``%h:%p`` still names the next host.
        """
        server = SshEndpoint(slug="target", host="target.example.com")
        outer = SshEndpoint(
            slug="outer",
            host="outer.example.com",
            user="u%n",
            key="/keys/outer%d",
        )
        inner = SshEndpoint(
            slug="inner",
            host="inner.example.com",
            user="v%r",
            key="/keys/inner%h",
        )
        args = build_ssh_base_args(server, [outer, inner])
        top_cmd = _expand_proxy_command(
            args[args.index("-o", len(_DEFAULT_O_OPTIONS)) + 1],
            host="target.example.com",
        )

        assert top_cmd[-1] == "v%r@inner.example.com"
        assert top_cmd[top_cmd.index("-i") + 1] == "/keys/inner%h"
        assert top_cmd[top_cmd.index("-W") + 1] == "target.example.com:22"
        outer_cmd = _expand_proxy_command(
            next(a for a in top_cmd if a.startswith("ProxyCommand=")),
            host="inner.example.com",
        )
        assert outer_cmd[-1] == "u%n@outer.example.com"
        assert outer_cmd[outer_cmd.index("-i") + 1] == "/keys/outer%d"
        assert outer_cmd[outer_cmd.index("-W") + 1] == "inner.example.com:22"

    def test_proxy_chain_empty(self) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.example.com",
        )
        args = build_ssh_base_args(server, [])
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "target.example.com",
        ]


_DEFAULT_E_PREFIX = "ssh -o ConnectTimeout=10 -o BatchMode=yes"


class TestBuildSshEOption:
    def test_minimal(self, ssh_endpoint_minimal: SshEndpoint) -> None:
        result = build_ssh_e_option(ssh_endpoint_minimal)
        assert result == ["-e", _DEFAULT_E_PREFIX]

    def test_full(self, ssh_endpoint: SshEndpoint) -> None:
        result = build_ssh_e_option(ssh_endpoint)
        assert result == [
            "-e",
            f"{_DEFAULT_E_PREFIX} -p 5022 -i {_SSH_KEY}",
        ]

    def test_with_ssh_options(self) -> None:
        server = SshEndpoint(
            slug="host-server",
            host="host.example.com",
            connection_options=SshConnectionOptions(
                strict_host_key_checking=False,
            ),
        )
        result = build_ssh_e_option(server)
        assert result == [
            "-e",
            f"{_DEFAULT_E_PREFIX} -o StrictHostKeyChecking=no",
        ]

    def test_proxy_jump(self) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.example.com",
        )
        proxy = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            port=2222,
            user="admin",
        )
        result = build_ssh_e_option(server, [proxy])
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -p 2222"
            " -W %h:%p admin@bastion.example.com"
        )
        quoted = shlex.quote(f"ProxyCommand={proxy_cmd}")
        assert result == [
            "-e",
            f"{_DEFAULT_E_PREFIX} -o {quoted}",
        ]

    def test_proxy_chain_multi_hop(self) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.example.com",
        )
        proxy1 = SshEndpoint(
            slug="bastion1",
            host="bastion1.example.com",
            user="user1",
        )
        proxy2 = SshEndpoint(
            slug="bastion2",
            host="bastion2.example.com",
            port=2222,
        )
        result = build_ssh_e_option(server, [proxy1, proxy2])
        inner = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -W %%h:%%p user1@bastion1.example.com"
        )
        # The hop runs through ``sh -c``: the nested ProxyCommand is one
        # shell word, so it must be quoted.
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            " -p 2222"
            f" -o {shlex.quote(f'ProxyCommand={inner}')}"
            " -W %h:%p bastion2.example.com"
        )
        quoted = shlex.quote(f"ProxyCommand={proxy_cmd}")
        assert result == [
            "-e",
            f"{_DEFAULT_E_PREFIX} -o {quoted}",
        ]


class TestBuildSshEOptionQuoting:
    def test_key_with_space_is_quoted(self) -> None:
        server = SshEndpoint(slug="s", host="h", key="/keys/my key")
        [flag, value] = build_ssh_e_option(server)
        assert flag == "-e"
        assert shlex.split(value) == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-i",
            "/keys/my key",
        ]

    def test_multi_hop_round_trips(self) -> None:
        server = SshEndpoint(slug="t", host="target")
        hops = [
            SshEndpoint(slug="b1", host="b1", user="u1"),
            SshEndpoint(slug="b2", host="b2"),
        ]
        [_, value] = build_ssh_e_option(server, hops)
        # What rsync splits back out is exactly the argv ssh would get.
        assert shlex.split(value) == build_ssh_base_args(server, hops)[:-1]


class TestWrapCmd:
    def test_local_quotes_arguments(self) -> None:
        vol = LocalVolume(slug="v", path="/mnt/my data")
        assert wrap_cmd(["mkdir", "-p", "/mnt/my data"], vol, {}) == (
            "mkdir -p '/mnt/my data'"
        )

    def test_remote_nests_quoted_command(self) -> None:
        vol = RemoteVolume(slug="v", ssh_endpoint="nas", path="/srv/my data")
        resolved = {
            "v": ResolvedEndpoint(
                server=SshEndpoint(slug="nas", host="nas.lan", user="bk"),
            ),
        }
        line = wrap_cmd(["touch", "/srv/my data/.nbkp-vol"], vol, resolved)
        ssh_argv = shlex.split(line)
        assert ssh_argv[:2] == ["ssh", "bk@nas.lan"]
        # The remote shell re-parses the single command argument.
        assert shlex.split(ssh_argv[2]) == ["touch", "/srv/my data/.nbkp-vol"]

    def test_remote_tilde_stays_expandable(self) -> None:
        vol = RemoteVolume(slug="v", ssh_endpoint="nas", path="~/backups")
        resolved = {"v": ResolvedEndpoint(server=SshEndpoint(slug="nas", host="h"))}
        line = wrap_cmd(["mkdir", "-p", "~/backups/my dir"], vol, resolved)
        assert shlex.split(line)[2] == "mkdir -p ~/'backups/my dir'"


class TestFormatRemotePath:
    def test_with_user(self, ssh_endpoint: SshEndpoint) -> None:
        result = format_remote_path(ssh_endpoint, "/data")
        assert result == "backup@nas.example.com:/data"

    def test_without_user(self, ssh_endpoint_minimal: SshEndpoint) -> None:
        result = format_remote_path(ssh_endpoint_minimal, "/data")
        assert result == "nas2.example.com:/data"


_TARGET = SshEndpoint(slug="target", host="target.example.com")
_OTHER = SshEndpoint(slug="other", host="other.example.com")
_BASTION = SshEndpoint(slug="bastion", host="bastion.example.com")


def _fake_connection(**kwargs: object) -> MagicMock:
    """Return a distinct, connected Fabric Connection mock per construction."""
    conn = MagicMock(name=str(kwargs["host"]))
    conn.host = kwargs["host"]
    conn.gateway = kwargs["gateway"]
    conn.is_connected = True
    conn.create_session.return_value = _Channel(stdout="ok\n")
    return conn


class _Channel:
    """
    A Paramiko session channel running one command.

    *reads_input* False is a command that exits without reading it: the
    channel is already closed when the input is sent, as Paramiko reports.
    """

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        exit_status: int = 0,
        *,
        reads_input: bool = True,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status
        self.reads_input = reads_input
        self.command: str | None = None
        self.received = b""
        self.input_closed = False
        self.closed = False

    def exec_command(self, command: str) -> None:
        self.command = command

    def sendall(self, data: bytes) -> None:
        if not self.reads_input:
            raise OSError(errno.EPIPE, os.strerror(errno.EPIPE))
        self.received += data

    def shutdown_write(self) -> None:
        self.input_closed = True

    def makefile(self, _mode: str) -> io.BytesIO:
        return io.BytesIO(self.stdout.encode())

    def makefile_stderr(self, _mode: str) -> io.BytesIO:
        return io.BytesIO(self.stderr.encode())

    def recv_exit_status(self) -> int:
        return self.exit_status

    def close(self) -> None:
        self.closed = True


def _leave_as_built(conn: MagicMock) -> None:
    pass


def _answer_with(channel: _Channel, conn: MagicMock) -> None:
    conn.create_session.return_value = channel


def _built(mock_conn_cls: MagicMock, host: str) -> list[MagicMock]:
    """Return the connections built to *host*, in construction order."""
    return [conn for conn in mock_conn_cls.built if conn.host == host]


@pytest.fixture
def mock_conn_cls() -> Iterator[MagicMock]:
    """
    Yield Fabric's Connection class, building a :func:`_fake_connection` per call.

    ``configure`` adjusts each one as it is built.
    """
    with patch("nbkp.remote.fabricssh.Connection") as cls:
        cls.built = []
        cls.configure = _leave_as_built

        def build(**kwargs: object) -> MagicMock:
            conn = _fake_connection(**kwargs)
            cls.configure(conn)
            cls.built.append(conn)
            return conn

        cls.side_effect = build
        yield cls


class _Transport:
    """The Paramiko transport of an open connection, recording keepalives."""

    def __init__(self) -> None:
        self.keepalives: list[int] = []

    def set_keepalive(self, interval: int) -> None:
        self.keepalives.append(interval)


class TestRunRemoteCommand:
    def test_leading_tilde_left_for_the_remote_shell(
        self,
        mock_conn_cls: MagicMock,
        ssh_endpoint: SshEndpoint,
    ) -> None:
        run_remote_command(ssh_endpoint, ["test", "-f", "~/my backups/.nbkp-vol"])

        (conn,) = _built(mock_conn_cls, "nas.example.com")
        channel = conn.create_session.return_value
        assert channel.command == "test -f ~/'my backups/.nbkp-vol'"

    def test_run_remote_command(
        self,
        mock_conn_cls: MagicMock,
        ssh_endpoint: SshEndpoint,
    ) -> None:
        channel = _Channel(stdout="file1\nfile2\n")
        mock_conn_cls.configure = partial(_answer_with, channel)

        result = run_remote_command(ssh_endpoint, ["ls", "/tmp"])  # noqa: S108 -- lists the container's /tmp, writes nothing

        mock_conn_cls.assert_called_once_with(
            host="nas.example.com",
            port=5022,
            user="backup",
            connect_kwargs={
                "allow_agent": True,
                "look_for_keys": True,
                "compress": False,
                "key_filename": _SSH_KEY,
            },
            connect_timeout=10,
            forward_agent=False,
            gateway=None,
        )
        assert channel.command == "ls /tmp"
        # A command that reads stdin gets EOF rather than waiting forever.
        assert (channel.received, channel.input_closed) == (b"", True)
        assert channel.closed
        assert result.returncode == 0
        assert result.stdout == "file1\nfile2\n"
        assert result.stderr == ""

    def test_channel_timeout_and_disabled_algorithms(
        self,
        mock_conn_cls: MagicMock,
    ) -> None:
        server = SshEndpoint(
            slug="advanced",
            host="host.example.com",
            connection_options=SshConnectionOptions(
                channel_timeout=30.0,
                disabled_algorithms={
                    "ciphers": ["aes128-cbc"],
                },
            ),
        )

        run_remote_command(server, ["echo", "ok"])

        mock_conn_cls.assert_called_once_with(
            host="host.example.com",
            port=22,
            user=None,
            connect_kwargs={
                "allow_agent": True,
                "look_for_keys": True,
                "compress": False,
                "channel_timeout": 30.0,
                "disabled_algorithms": {
                    "ciphers": ["aes128-cbc"],
                },
            },
            connect_timeout=10,
            forward_agent=False,
            gateway=None,
        )

    def test_server_alive_interval_calls_set_keepalive(
        self,
        mock_conn_cls: MagicMock,
    ) -> None:
        server = SshEndpoint(
            slug="keepalive",
            host="host.example.com",
            connection_options=SshConnectionOptions(
                server_alive_interval=60,
            ),
        )
        transport = _Transport()

        def no_transport_until_open(conn: MagicMock) -> None:
            # Like Fabric, no transport until the connection is opened.
            conn.transport = None
            conn.open.side_effect = lambda: setattr(conn, "transport", transport)

        mock_conn_cls.configure = no_transport_until_open

        run_remote_command(server, ["echo", "ok"])

        assert transport.keepalives == [60]

    def test_input_is_sent_then_closed(self, mock_conn_cls: MagicMock) -> None:
        channel = _Channel(stdout="unlocked\n")
        mock_conn_cls.configure = partial(_answer_with, channel)

        result = run_remote_command(
            _TARGET,
            ["udisksctl", "unlock", "--key-file", "/dev/stdin"],
            stdin="pw",
        )

        assert channel.command == "udisksctl unlock --key-file /dev/stdin"
        assert channel.received == b"pw"
        assert channel.input_closed
        assert channel.closed
        assert (result.returncode, result.stdout) == (0, "unlocked\n")

    def test_command_exiting_before_reading_input_keeps_stderr(
        self,
        mock_conn_cls: MagicMock,
    ) -> None:
        """
        A command exiting before reading its input keeps its stderr.

        The remote message is what tells a polkit refusal from a device udisks does not
        know yet, so it must survive the unread input.
        """
        channel = _Channel(
            stderr="Error looking up object for device /dev/disk/by-uuid/x\n",
            exit_status=1,
            reads_input=False,
        )
        mock_conn_cls.configure = partial(_answer_with, channel)

        result = run_remote_command(_TARGET, ["udisksctl", "unlock"], stdin="pw")

        assert result.returncode == 1
        assert result.stderr == (
            "Error looking up object for device /dev/disk/by-uuid/x\n"
        )
        assert channel.closed

    def test_proxy_server_creates_gateway(self, mock_conn_cls: MagicMock) -> None:
        proxy = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            user="admin",
        )

        run_remote_command(_TARGET, ["echo", "ok"], [proxy])

        (bastion,) = _built(mock_conn_cls, "bastion.example.com")
        (target,) = _built(mock_conn_cls, "target.example.com")
        assert bastion.gateway is None
        assert target.gateway is bastion


class TestRunRemoteCommandWithoutScope:
    def test_connects_for_every_command(self, mock_conn_cls: MagicMock) -> None:
        run_remote_command(_TARGET, ["true"])
        run_remote_command(_TARGET, ["true"])

        first, second = _built(mock_conn_cls, "target.example.com")
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_closes_every_proxy_hop(self, mock_conn_cls: MagicMock) -> None:
        run_remote_command(_TARGET, ["true"], [_BASTION])

        (bastion,) = _built(mock_conn_cls, "bastion.example.com")
        (target,) = _built(mock_conn_cls, "target.example.com")
        assert target.gateway is bastion
        target.close.assert_called_once()
        bastion.close.assert_called_once()


class TestRunRemoteCommandInScope:
    def test_reuses_one_connection_per_endpoint(self, mock_conn_cls: MagicMock) -> None:
        with connection_scope():
            run_remote_command(_TARGET, ["true"])
            run_remote_command(_TARGET, ["ls", "/"])
            run_remote_command(_OTHER, ["true"])
            (target,) = _built(mock_conn_cls, "target.example.com")
            target.open.assert_called_once()
            assert target.create_session.call_count == 2
            target.close.assert_not_called()
            (other,) = _built(mock_conn_cls, "other.example.com")

        target.close.assert_called_once()
        other.close.assert_called_once()

    def test_reuses_proxy_hops_and_closes_them_with_the_scope(
        self,
        mock_conn_cls: MagicMock,
    ) -> None:
        with connection_scope():
            run_remote_command(_TARGET, ["true"], [_BASTION])
            run_remote_command(_TARGET, ["true"], [_BASTION])
            # Through a proxy, a different route to the same host.
            run_remote_command(_TARGET, ["true"])

        (bastion,) = _built(mock_conn_cls, "bastion.example.com")
        proxied, direct = _built(mock_conn_cls, "target.example.com")
        assert proxied.gateway is bastion
        assert direct.gateway is None
        assert proxied.create_session.call_count == 2
        for conn in (bastion, proxied, direct):
            conn.close.assert_called_once()

    def test_sends_stdin_on_a_reused_connection(self, mock_conn_cls: MagicMock) -> None:
        with connection_scope():
            run_remote_command(_TARGET, ["true"])
            run_remote_command(_TARGET, ["udisksctl", "unlock"], stdin="secret")

        (target,) = _built(mock_conn_cls, "target.example.com")
        assert target.create_session.return_value.received == b"secret"

    def test_reconnects_when_the_transport_dropped(
        self,
        mock_conn_cls: MagicMock,
    ) -> None:
        with connection_scope():
            run_remote_command(_TARGET, ["true"])
            (stale,) = _built(mock_conn_cls, "target.example.com")
            stale.is_connected = False
            run_remote_command(_TARGET, ["true"])

            stale.close.assert_called_once()
            stale.create_session.assert_called_once()
            _, fresh = _built(mock_conn_cls, "target.example.com")
            fresh.create_session.assert_called_once()

    def test_retries_once_when_a_reused_connection_fails(
        self,
        mock_conn_cls: MagicMock,
    ) -> None:
        with connection_scope():
            run_remote_command(_TARGET, ["true"])
            (stale,) = _built(mock_conn_cls, "target.example.com")
            stale.create_session.side_effect = EOFError()
            result = run_remote_command(_TARGET, ["ls"])

            stale.close.assert_called_once()
            _, fresh = _built(mock_conn_cls, "target.example.com")
            fresh.create_session.assert_called_once()
            assert result.returncode == 0
            assert result.stdout == "ok\n"

    def test_a_fresh_connection_failure_is_not_retried(
        self,
        mock_conn_cls: MagicMock,
    ) -> None:
        def reset(conn: MagicMock) -> None:
            conn.create_session.side_effect = OSError("Connection reset")

        mock_conn_cls.configure = reset
        with connection_scope(), pytest.raises(OSError, match="reset"):
            run_remote_command(_TARGET, ["true"])

        (target,) = _built(mock_conn_cls, "target.example.com")
        target.close.assert_called_once()

    def test_a_failed_connect_is_not_kept(self, mock_conn_cls: MagicMock) -> None:
        def refuse(conn: MagicMock) -> None:
            conn.open.side_effect = OSError("refused")

        with connection_scope():
            mock_conn_cls.configure = refuse
            with pytest.raises(OSError, match="refused"):
                run_remote_command(_TARGET, ["true"])
            mock_conn_cls.configure = _leave_as_built
            result = run_remote_command(_TARGET, ["true"])

            refused, connected = _built(mock_conn_cls, "target.example.com")
            refused.close.assert_called_once()
            connected.close.assert_not_called()
        assert result.returncode == 0
