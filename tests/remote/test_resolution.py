"""Tests for nbkp.remote.resolution."""

from __future__ import annotations

import socket

import paramiko
import pytest

from nbkp.config import SshConnectionOptions, SshEndpoint
from nbkp.remote.resolution import (
    enrich_from_ssh_config,
    is_private_host,
    resolve_host,
    resolve_hostname,
)


class TestResolveHostname:
    """Tests for resolve_hostname (SSH config lookup)."""

    def test_from_ssh_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ssh_config = paramiko.SSHConfig.from_text(
            "Host mynas\n  HostName 192.168.1.100\n"
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: ssh_config,
        )
        assert resolve_hostname("mynas") == "192.168.1.100"

    def test_no_ssh_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: None,
        )
        assert resolve_hostname("mynas") == "mynas"

    def test_not_in_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ssh_config = paramiko.SSHConfig.from_text(
            "Host other\n  HostName 10.0.0.1\n"
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: ssh_config,
        )
        assert resolve_hostname("mynas") == "mynas"

    def test_with_port_and_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ssh_config = paramiko.SSHConfig.from_text(
            "Host mynas\n"
            "  HostName 192.168.1.100\n"
            "  Port 2222\n"
            "  User backup\n"
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: ssh_config,
        )
        # resolve_hostname only returns the hostname
        assert resolve_hostname("mynas") == "192.168.1.100"


class TestResolveHost:
    """Tests for resolve_host (SSH config + DNS)."""

    def test_via_ssh_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ssh_config = paramiko.SSHConfig.from_text(
            "Host mynas\n  HostName 127.0.0.1\n"
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: ssh_config,
        )
        addrs = resolve_host("mynas")
        assert addrs is not None
        assert "127.0.0.1" in addrs

    def test_unresolvable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: None,
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution.socket.getaddrinfo",
            _raise_gaierror,
        )
        assert resolve_host("nonexistent.invalid") is None

    def test_direct_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: None,
        )
        addrs = resolve_host("localhost")
        assert addrs is not None
        assert len(addrs) > 0


class TestIsPrivateHost:
    """Tests for is_private_host (SSH config + DNS + IP)."""

    def test_private_via_ssh_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ssh_config = paramiko.SSHConfig.from_text(
            "Host mynas\n  HostName 192.168.1.100\n"
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: ssh_config,
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution.socket.getaddrinfo",
            lambda host, port: [
                (None, None, None, None, ("192.168.1.100", 0))
            ],
        )
        assert is_private_host("mynas") is True

    def test_public_via_ssh_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ssh_config = paramiko.SSHConfig.from_text(
            "Host mypublic\n  HostName 8.8.8.8\n"
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: ssh_config,
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution.socket.getaddrinfo",
            lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
        )
        assert is_private_host("mypublic") is False

    def test_unresolvable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: None,
        )
        monkeypatch.setattr(
            "nbkp.remote.resolution.socket.getaddrinfo",
            _raise_gaierror,
        )
        assert is_private_host("nonexistent.invalid") is None

    def test_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: None,
        )
        assert is_private_host("localhost") is True


def _raise_gaierror(*args: object, **kwargs: object) -> None:
    raise socket.gaierror("mocked DNS failure")


# ── SSH config enrichment ────────────────────────────────────


_SSH_CONFIG_TEXT = (
    "Host mynas\n"
    "  HostName 192.168.1.100\n"
    "  Port 5022\n"
    "  User backup\n"
    "  IdentityFile ~/.ssh/nas_key\n"
)


def _mock_ssh_config(
    monkeypatch: pytest.MonkeyPatch,
    text: str | None = _SSH_CONFIG_TEXT,
) -> None:
    """Patch _load_ssh_config to return a test config."""
    if text is None:
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: None,
        )
    else:
        cfg = paramiko.SSHConfig.from_text(text)
        monkeypatch.setattr(
            "nbkp.remote.resolution._load_ssh_config",
            lambda: cfg,
        )


class TestEnrichFromSshConfig:
    """Tests for enrich_from_ssh_config."""

    def test_fills_port_from_ssh_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="nas", host="mynas")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.port == 5022

    def test_fills_user_from_ssh_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="nas", host="mynas")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.user == "backup"

    def test_fills_key_from_ssh_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="nas", host="mynas")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.key is not None
        assert enriched.key.endswith("nas_key")
        # ~ should be expanded
        assert "~" not in enriched.key

    def test_explicit_port_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="nas", host="mynas", port=2222)
        enriched = enrich_from_ssh_config(ep)
        assert enriched.port == 2222

    def test_explicit_user_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="nas", host="mynas", user="admin")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.user == "admin"

    def test_explicit_key_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="nas", host="mynas", key="/my/key")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.key == "/my/key"

    def test_no_ssh_config_returns_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch, text=None)
        ep = SshEndpoint(slug="nas", host="mynas")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.port == 22
        assert enriched.user is None
        assert enriched.key is None

    def test_host_not_in_ssh_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="other", host="unknown")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.port == 22
        assert enriched.user is None
        assert enriched.key is None

    def test_host_preserved_as_alias(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """host should stay as the alias, not resolved."""
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="nas", host="mynas")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.host == "mynas"

    def test_unrelated_fields_preserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(
            slug="nas",
            host="mynas",
            proxy_jump="bastion",
            connection_options=SshConnectionOptions(
                forward_agent=True,
            ),
        )
        enriched = enrich_from_ssh_config(ep)
        assert enriched.proxy_jump == "bastion"
        assert enriched.connection_options.forward_agent is True
        # SSH config fields still filled
        assert enriched.port == 5022
        assert enriched.user == "backup"

    def test_fills_all_fields_at_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_ssh_config(monkeypatch)
        ep = SshEndpoint(slug="nas", host="mynas")
        enriched = enrich_from_ssh_config(ep)
        assert enriched.port == 5022
        assert enriched.user == "backup"
        assert enriched.key is not None
        assert enriched.key.endswith("nas_key")
