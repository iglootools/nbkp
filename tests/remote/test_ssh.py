"""Tests for nbkp.ssh."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from nbkp.config import SshEndpoint, SshConnectionOptions
from nbkp.remote import (
    build_ssh_base_args,
    build_ssh_e_option,
    format_remote_path,
    run_remote_command,
)

_SSH_KEY = str(Path("~/.ssh/key").expanduser())

_DEFAULT_O_OPTIONS = [
    "-o",
    "ConnectTimeout=10",
    "-o",
    "BatchMode=yes",
]


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
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            f" -o ProxyCommand={inner}"
            " -p 2222"
            " -W %h:%p bastion2.example.com"
        )
        assert args == [
            "ssh",
            *_DEFAULT_O_OPTIONS,
            "-o",
            f"ProxyCommand={proxy_cmd}",
            "target.example.com",
        ]

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
            f"{_DEFAULT_E_PREFIX}" " -o StrictHostKeyChecking=no",
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
        import shlex

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
        proxy_cmd = (
            "ssh -o ConnectTimeout=10 -o BatchMode=yes"
            f" -o ProxyCommand={inner}"
            " -p 2222"
            " -W %h:%p bastion2.example.com"
        )
        import shlex

        quoted = shlex.quote(f"ProxyCommand={proxy_cmd}")
        assert result == [
            "-e",
            f"{_DEFAULT_E_PREFIX} -o {quoted}",
        ]


class TestFormatRemotePath:
    def test_with_user(self, ssh_endpoint: SshEndpoint) -> None:
        result = format_remote_path(ssh_endpoint, "/data")
        assert result == "backup@nas.example.com:/data"

    def test_without_user(self, ssh_endpoint_minimal: SshEndpoint) -> None:
        result = format_remote_path(ssh_endpoint_minimal, "/data")
        assert result == "nas2.example.com:/data"


class TestRunRemoteCommand:
    @patch("nbkp.remote.fabricssh.paramiko")
    @patch("nbkp.remote.fabricssh.Connection")
    def test_run_remote_command(
        self,
        mock_conn_cls: MagicMock,
        mock_paramiko: MagicMock,
        ssh_endpoint: SshEndpoint,
    ) -> None:
        mock_conn = mock_conn_cls.return_value.__enter__.return_value
        mock_result = MagicMock(exited=0, stdout="file1\nfile2\n", stderr="")
        mock_conn.run.return_value = mock_result

        result = run_remote_command(ssh_endpoint, ["ls", "/tmp"])

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
        mock_conn.run.assert_called_once_with(
            "ls /tmp",
            warn=True,
            hide=True,
            in_stream=False,
        )
        assert result.returncode == 0
        assert result.stdout == "file1\nfile2\n"
        assert result.stderr == ""

    @patch("nbkp.remote.fabricssh.paramiko")
    @patch("nbkp.remote.fabricssh.Connection")
    def test_channel_timeout_and_disabled_algorithms(
        self,
        mock_conn_cls: MagicMock,
        mock_paramiko: MagicMock,
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
        mock_conn = mock_conn_cls.return_value.__enter__.return_value
        mock_result = MagicMock(exited=0, stdout="ok\n", stderr="")
        mock_conn.run.return_value = mock_result

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

    @patch("nbkp.remote.fabricssh.paramiko")
    @patch("nbkp.remote.fabricssh.Connection")
    def test_server_alive_interval_calls_set_keepalive(
        self,
        mock_conn_cls: MagicMock,
        mock_paramiko: MagicMock,
    ) -> None:
        server = SshEndpoint(
            slug="keepalive",
            host="host.example.com",
            connection_options=SshConnectionOptions(
                server_alive_interval=60,
            ),
        )
        mock_conn = mock_conn_cls.return_value.__enter__.return_value
        mock_result = MagicMock(exited=0, stdout="ok\n", stderr="")
        mock_conn.run.return_value = mock_result

        run_remote_command(server, ["echo", "ok"])

        mock_conn.transport.set_keepalive.assert_called_once_with(60)

    @patch("nbkp.remote.fabricssh.paramiko")
    @patch("nbkp.remote.fabricssh.Connection")
    def test_proxy_server_creates_gateway(
        self,
        mock_conn_cls: MagicMock,
        mock_paramiko: MagicMock,
    ) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.example.com",
        )
        proxy = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            user="admin",
        )
        mock_conn = mock_conn_cls.return_value.__enter__.return_value
        mock_result = MagicMock(exited=0, stdout="ok\n", stderr="")
        mock_conn.run.return_value = mock_result

        run_remote_command(server, ["echo", "ok"], [proxy])

        # Connection is called twice: once for proxy, once
        # for target
        assert mock_conn_cls.call_count == 2
        target_call = mock_conn_cls.call_args_list[1]
        assert target_call.kwargs["gateway"] is not None
