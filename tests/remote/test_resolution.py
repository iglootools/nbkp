"""Tests for nbkp.remote.resolution."""

from __future__ import annotations

from pathlib import Path

import paramiko

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshConnectionOptions,
    SshEndpoint,
)
from nbkp.config.epresolution import EndpointFilter, NetworkType
from nbkp.remote.resolution import (
    AddressLookup,
    dns_lookup,
    enrich_from_ssh_config,
    is_private_host,
    load_ssh_config,
    resolve_all_endpoints,
    resolve_host,
    resolve_hostname,
)

_NO_SSH_CONFIG = paramiko.SSHConfig()


def _ssh_config(text: str) -> paramiko.SSHConfig:
    return paramiko.SSHConfig.from_text(text)


def _fixed_lookup(table: dict[str, set[str]]) -> AddressLookup:
    """An address lookup answering from ``table`` (unknown hosts: None)."""
    return lambda host: table.get(host)


def _unresolvable(host: str) -> set[str] | None:
    return None


class TestLoadSshConfig:
    def test_missing_file_is_empty_config(self, tmp_path: Path) -> None:
        cfg = load_ssh_config(tmp_path / "absent")
        assert cfg.lookup("mynas") == {"hostname": "mynas"}

    def test_reads_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config"
        path.write_text("Host mynas\n  HostName 192.168.1.100\n")
        assert load_ssh_config(path).lookup("mynas")["hostname"] == "192.168.1.100"


class TestResolveHostname:
    """Tests for resolve_hostname (SSH config lookup)."""

    def test_from_ssh_config(self) -> None:
        cfg = _ssh_config("Host mynas\n  HostName 192.168.1.100\n")
        assert resolve_hostname("mynas", cfg) == "192.168.1.100"

    def test_no_ssh_config(self) -> None:
        assert resolve_hostname("mynas", _NO_SSH_CONFIG) == "mynas"

    def test_not_in_config(self) -> None:
        cfg = _ssh_config("Host other\n  HostName 10.0.0.1\n")
        assert resolve_hostname("mynas", cfg) == "mynas"

    def test_with_port_and_user(self) -> None:
        cfg = _ssh_config(
            "Host mynas\n  HostName 192.168.1.100\n  Port 2222\n  User backup\n"
        )
        # resolve_hostname only returns the hostname
        assert resolve_hostname("mynas", cfg) == "192.168.1.100"


class TestResolveHost:
    """Tests for resolve_host (SSH config + DNS)."""

    def test_via_ssh_config(self) -> None:
        cfg = _ssh_config("Host mynas\n  HostName 192.168.1.100\n")
        lookup = _fixed_lookup({"192.168.1.100": {"192.168.1.100"}})
        assert resolve_host("mynas", cfg, lookup) == {"192.168.1.100"}

    def test_unresolvable(self) -> None:
        assert (
            resolve_host("nonexistent.invalid", _NO_SSH_CONFIG, _unresolvable) is None
        )

    def test_dns_lookup_localhost(self) -> None:
        addrs = dns_lookup("localhost")
        assert addrs is not None
        assert len(addrs) > 0


class TestIsPrivateHost:
    """Tests for is_private_host (SSH config + DNS + IP)."""

    def test_private_via_ssh_config(self) -> None:
        cfg = _ssh_config("Host mynas\n  HostName 192.168.1.100\n")
        lookup = _fixed_lookup({"192.168.1.100": {"192.168.1.100"}})
        assert is_private_host("mynas", cfg, lookup) is True

    def test_public_via_ssh_config(self) -> None:
        cfg = _ssh_config("Host mypublic\n  HostName 8.8.8.8\n")
        lookup = _fixed_lookup({"8.8.8.8": {"8.8.8.8"}})
        assert is_private_host("mypublic", cfg, lookup) is False

    def test_mixed_addresses_are_public(self) -> None:
        lookup = _fixed_lookup({"dual": {"192.168.1.1", "8.8.8.8"}})
        assert is_private_host("dual", _NO_SSH_CONFIG, lookup) is False

    def test_unresolvable(self) -> None:
        assert (
            is_private_host("nonexistent.invalid", _NO_SSH_CONFIG, _unresolvable)
            is None
        )

    def test_localhost(self) -> None:
        lookup = _fixed_lookup({"localhost": {"127.0.0.1", "::1"}})
        assert is_private_host("localhost", _NO_SSH_CONFIG, lookup) is True


# ── SSH config enrichment ────────────────────────────────────


_NAS_SSH_CONFIG = _ssh_config(
    "Host mynas\n"
    "  HostName 192.168.1.100\n"
    "  Port 5022\n"
    "  User backup\n"
    "  IdentityFile ~/.ssh/nas_key\n"
)


class TestEnrichFromSshConfig:
    """Tests for enrich_from_ssh_config."""

    def test_fills_port_from_ssh_config(self) -> None:
        ep = SshEndpoint(slug="nas", host="mynas")
        assert enrich_from_ssh_config(ep, _NAS_SSH_CONFIG).port == 5022

    def test_fills_user_from_ssh_config(self) -> None:
        ep = SshEndpoint(slug="nas", host="mynas")
        assert enrich_from_ssh_config(ep, _NAS_SSH_CONFIG).user == "backup"

    def test_fills_key_from_ssh_config(self) -> None:
        ep = SshEndpoint(slug="nas", host="mynas")
        enriched = enrich_from_ssh_config(ep, _NAS_SSH_CONFIG)
        assert enriched.key is not None
        assert enriched.key.endswith("nas_key")
        # ~ should be expanded
        assert "~" not in enriched.key

    def test_explicit_port_takes_precedence(self) -> None:
        ep = SshEndpoint(slug="nas", host="mynas", port=2222)
        assert enrich_from_ssh_config(ep, _NAS_SSH_CONFIG).port == 2222

    def test_explicit_user_takes_precedence(self) -> None:
        ep = SshEndpoint(slug="nas", host="mynas", user="admin")
        assert enrich_from_ssh_config(ep, _NAS_SSH_CONFIG).user == "admin"

    def test_explicit_key_takes_precedence(self) -> None:
        ep = SshEndpoint(slug="nas", host="mynas", key="/my/key")
        assert enrich_from_ssh_config(ep, _NAS_SSH_CONFIG).key == "/my/key"

    def test_no_ssh_config_returns_unchanged(self) -> None:
        ep = SshEndpoint(slug="nas", host="mynas")
        assert enrich_from_ssh_config(ep, _NO_SSH_CONFIG) == ep

    def test_host_not_in_ssh_config(self) -> None:
        ep = SshEndpoint(slug="other", host="unknown")
        assert enrich_from_ssh_config(ep, _NAS_SSH_CONFIG) == ep

    def test_host_preserved_as_alias(self) -> None:
        """host should stay as the alias, not resolved."""
        ep = SshEndpoint(slug="nas", host="mynas")
        assert enrich_from_ssh_config(ep, _NAS_SSH_CONFIG).host == "mynas"

    def test_unrelated_fields_preserved(self) -> None:
        ep = SshEndpoint(
            slug="nas",
            host="mynas",
            proxy_jump="bastion",
            connection_options=SshConnectionOptions(
                forward_agent=True,
            ),
        )
        enriched = enrich_from_ssh_config(ep, _NAS_SSH_CONFIG)
        assert enriched.proxy_jump == "bastion"
        assert enriched.connection_options.forward_agent is True
        # SSH config fields still filled
        assert enriched.port == 5022
        assert enriched.user == "backup"

    def test_fills_all_fields_at_once(self) -> None:
        ep = SshEndpoint(slug="nas", host="mynas")
        enriched = enrich_from_ssh_config(ep, _NAS_SSH_CONFIG)
        assert enriched.port == 5022
        assert enriched.user == "backup"
        assert enriched.key is not None
        assert enriched.key.endswith("nas_key")


class TestResolveAllEndpoints:
    def _config(self) -> Config:
        return Config(
            ssh_endpoints={
                "nas-lan": SshEndpoint(slug="nas-lan", host="mynas"),
                "nas-wan": SshEndpoint(slug="nas-wan", host="nas.example.com"),
            },
            volumes={
                "local": LocalVolume(slug="local", path="/data"),
                "nas": RemoteVolume(
                    slug="nas",
                    ssh_endpoint="nas-lan",
                    ssh_endpoints=["nas-lan", "nas-wan"],
                    path="/backups",
                ),
            },
        )

    def test_enriches_with_given_ssh_config(self) -> None:
        resolved = resolve_all_endpoints(self._config(), ssh_config=_NAS_SSH_CONFIG)
        assert set(resolved) == {"nas"}
        assert resolved["nas"].server.port == 5022

    def test_network_filter_uses_given_lookup(self) -> None:
        lookup = _fixed_lookup(
            {"192.168.1.100": {"192.168.1.100"}, "nas.example.com": {"8.8.8.8"}}
        )
        resolved = resolve_all_endpoints(
            self._config(),
            EndpointFilter(network=NetworkType.PUBLIC),
            ssh_config=_NAS_SSH_CONFIG,
            lookup=lookup,
        )
        assert resolved["nas"].server.slug == "nas-wan"
