"""Integration tests: SSH connection options.

Tests exercise real SSH connections to Docker containers with
various connection option combinations that are not covered by
the standard test fixtures (which always disable host key
checking).
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from nbkp.preflight import VolumeError, check_volume
from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshConnectionOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.remote.fabricssh import (
    run_remote_command as fabric_run_remote,
)
from nbkp.remote.sshexec import (
    run_remote_command as ssh_run_remote,
)
from nbkp.sync.rsync import run_rsync
from nbkp.remote.testkit.docker import (
    REMOTE_BACKUP_PATH,
    generate_ssh_keypair,
)
from nbkp.sync.testkit.seed import create_seed_sentinels

from tests._docker_fixtures import ssh_exec

# ── Helpers ──────────────────────────────────────────────────


def _extract_host_key(endpoint: SshEndpoint) -> str:
    """Extract the ed25519 host public key from a container.

    Returns the key in ``ssh-ed25519 AAAA...`` format.
    """
    result = ssh_exec(
        endpoint,
        "cat /etc/ssh/ssh_host_ed25519_key.pub",
    )
    # Format: "ssh-ed25519 AAAA... root@hostname\n"
    # We want just the key type + base64 data
    parts = result.stdout.strip().split()
    return f"{parts[0]} {parts[1]}"


def _write_known_hosts(
    tmp_path: Path,
    host: str,
    port: int,
    host_key: str,
) -> Path:
    """Write a known_hosts file for a given host:port.

    The *host_key* should be in ``ssh-ed25519 AAAA...`` format.
    """
    entry = f"[{host}]:{port} {host_key}"
    path = tmp_path / "known_hosts"
    path.write_text(entry + "\n")
    return path


def _generate_bogus_host_key() -> str:
    """Generate a random ed25519 public key (not matching any server)."""
    key = Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )
    return pub_bytes.decode()


def _make_endpoint(
    docker_ssh_endpoint: SshEndpoint,
    connection_options: SshConnectionOptions,
) -> SshEndpoint:
    """Create a new SshEndpoint copying host/port/user/key from
    the Docker fixture, with custom connection options."""
    return SshEndpoint(
        slug="test-ssh",
        host=docker_ssh_endpoint.host,
        port=docker_ssh_endpoint.port,
        user=docker_ssh_endpoint.user,
        key=docker_ssh_endpoint.key,
        connection_options=connection_options,
    )


@contextmanager
def _ssh_agent(
    private_key: Path,
) -> Generator[str, None, None]:
    """Start an ephemeral ssh-agent and load the given key.

    Sets ``SSH_AUTH_SOCK`` in the process environment for the
    duration of the context.  Kills the agent on exit.
    """
    proc = subprocess.run(
        ["ssh-agent", "-s"],
        capture_output=True,
        text=True,
        check=True,
    )
    env: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line and ";" in line:
            key, _, rest = line.partition("=")
            value = rest.split(";")[0]
            env[key] = value

    sock = env["SSH_AUTH_SOCK"]
    pid = env["SSH_AGENT_PID"]
    saved = os.environ.get("SSH_AUTH_SOCK")
    os.environ["SSH_AUTH_SOCK"] = sock

    subprocess.run(
        ["ssh-add", str(private_key)],
        check=True,
        capture_output=True,
    )

    try:
        yield sock
    finally:
        if saved is not None:
            os.environ["SSH_AUTH_SOCK"] = saved
        else:
            os.environ.pop("SSH_AUTH_SOCK", None)
        subprocess.run(
            ["ssh-agent", "-k"],
            env={
                **os.environ,
                "SSH_AGENT_PID": pid,
                "SSH_AUTH_SOCK": sock,
            },
            capture_output=True,
            check=False,
        )


# ── Host key verification ───────────────────────────────────


class TestHostKeyVerification:
    def test_check_succeeds_with_correct_known_hosts(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        host_key = _extract_host_key(docker_ssh_endpoint)
        known_hosts = _write_known_hosts(
            tmp_path,
            docker_ssh_endpoint.host,
            docker_ssh_endpoint.port,
            host_key,
        )

        endpoint = _make_endpoint(
            docker_ssh_endpoint,
            SshConnectionOptions(
                strict_host_key_checking=True,
                known_hosts_file=str(known_hosts),
            ),
        )

        vol = RemoteVolume(
            slug="test-remote",
            ssh_endpoint="test-ssh",
            path=REMOTE_BACKUP_PATH,
        )
        config = Config(
            ssh_endpoints={"test-ssh": endpoint},
            volumes={"test-remote": vol},
        )

        ssh_exec(
            docker_ssh_endpoint,
            f"touch {REMOTE_BACKUP_PATH}/.nbkp-vol",
        )

        resolved = resolve_all_endpoints(config)
        status = check_volume(vol, resolved)
        assert status.active is True

    def test_check_fails_with_wrong_known_hosts(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
    ) -> None:
        bogus_key = _generate_bogus_host_key()
        known_hosts = _write_known_hosts(
            tmp_path,
            docker_ssh_endpoint.host,
            docker_ssh_endpoint.port,
            bogus_key,
        )

        endpoint = _make_endpoint(
            docker_ssh_endpoint,
            SshConnectionOptions(
                strict_host_key_checking=True,
                known_hosts_file=str(known_hosts),
            ),
        )

        vol = RemoteVolume(
            slug="test-remote",
            ssh_endpoint="test-ssh",
            path=REMOTE_BACKUP_PATH,
        )
        config = Config(
            ssh_endpoints={"test-ssh": endpoint},
            volumes={"test-remote": vol},
        )

        resolved = resolve_all_endpoints(config)
        status = check_volume(vol, resolved)
        assert status.active is False
        assert VolumeError.UNREACHABLE in status.errors

    def test_rsync_succeeds_with_correct_known_hosts(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
    ) -> None:
        host_key = _extract_host_key(docker_ssh_endpoint)
        known_hosts = _write_known_hosts(
            tmp_path,
            docker_ssh_endpoint.host,
            docker_ssh_endpoint.port,
            host_key,
        )

        endpoint = _make_endpoint(
            docker_ssh_endpoint,
            SshConnectionOptions(
                strict_host_key_checking=True,
                known_hosts_file=str(known_hosts),
            ),
        )

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "hello.txt").write_text("strict-ssh")

        src_vol = LocalVolume(slug="src", path=str(src_dir))
        vol = RemoteVolume(
            slug="test-remote",
            ssh_endpoint="test-ssh",
            path=REMOTE_BACKUP_PATH,
        )
        config = Config(
            ssh_endpoints={"test-ssh": endpoint},
            volumes={"src": src_vol, "test-remote": vol},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="test-remote"),
            },
            syncs={
                "test-sync": SyncConfig(
                    slug="test-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        sync = config.syncs["test-sync"]

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)
        result = run_rsync(sync, config, resolved_endpoints=resolved)
        assert result.returncode == 0

        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/hello.txt",
        )
        assert check.stdout.strip() == "strict-ssh"


# ── Explicit key only (no agent, no key discovery) ──────────


class TestExplicitKeyOnly:
    def test_check_with_explicit_key_only(
        self,
        docker_ssh_endpoint: SshEndpoint,
    ) -> None:
        endpoint = _make_endpoint(
            docker_ssh_endpoint,
            SshConnectionOptions(
                strict_host_key_checking=False,
                known_hosts_file="/dev/null",
                allow_agent=False,
                look_for_keys=False,
            ),
        )

        vol = RemoteVolume(
            slug="test-remote",
            ssh_endpoint="test-ssh",
            path=REMOTE_BACKUP_PATH,
        )
        config = Config(
            ssh_endpoints={"test-ssh": endpoint},
            volumes={"test-remote": vol},
        )

        ssh_exec(
            docker_ssh_endpoint,
            f"touch {REMOTE_BACKUP_PATH}/.nbkp-vol",
        )

        resolved = resolve_all_endpoints(config)
        status = check_volume(vol, resolved)
        assert status.active is True

    def test_rsync_with_explicit_key_only(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
    ) -> None:
        endpoint = _make_endpoint(
            docker_ssh_endpoint,
            SshConnectionOptions(
                strict_host_key_checking=False,
                known_hosts_file="/dev/null",
                allow_agent=False,
                look_for_keys=False,
            ),
        )

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "data.txt").write_text("explicit-key")

        src_vol = LocalVolume(slug="src", path=str(src_dir))
        vol = RemoteVolume(
            slug="test-remote",
            ssh_endpoint="test-ssh",
            path=REMOTE_BACKUP_PATH,
        )
        config = Config(
            ssh_endpoints={"test-ssh": endpoint},
            volumes={"src": src_vol, "test-remote": vol},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="test-remote"),
            },
            syncs={
                "test-sync": SyncConfig(
                    slug="test-sync",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        sync = config.syncs["test-sync"]

        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        resolved = resolve_all_endpoints(config)
        result = run_rsync(sync, config, resolved_endpoints=resolved)
        assert result.returncode == 0

        check = ssh_exec(
            docker_ssh_endpoint,
            f"cat {REMOTE_BACKUP_PATH}/data.txt",
        )
        assert check.stdout.strip() == "explicit-key"


# ── Connection failure handling ──────────────────────────────


class TestConnectionFailure:
    def test_unreachable_volume_wrong_port(self) -> None:
        endpoint = SshEndpoint(
            slug="bad-port",
            host="127.0.0.1",
            port=1,
            user="testuser",
            key="/dev/null",
            connection_options=SshConnectionOptions(
                connect_timeout=2,
                strict_host_key_checking=False,
                known_hosts_file="/dev/null",
            ),
        )
        vol = RemoteVolume(
            slug="test-remote",
            ssh_endpoint="bad-port",
            path="/srv/backups",
        )
        config = Config(
            ssh_endpoints={"bad-port": endpoint},
            volumes={"test-remote": vol},
        )

        resolved = resolve_all_endpoints(config)
        status = check_volume(vol, resolved)
        assert status.active is False
        assert VolumeError.UNREACHABLE in status.errors

    def test_unreachable_volume_wrong_key(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
    ) -> None:
        # Generate a keypair that is NOT authorized
        wrong_key_dir = tmp_path / "wrong-keys"
        wrong_key_dir.mkdir()
        wrong_private, _ = generate_ssh_keypair(wrong_key_dir)

        endpoint = SshEndpoint(
            slug="bad-key",
            host=docker_ssh_endpoint.host,
            port=docker_ssh_endpoint.port,
            user="testuser",
            key=str(wrong_private),
            connection_options=SshConnectionOptions(
                connect_timeout=5,
                strict_host_key_checking=False,
                known_hosts_file="/dev/null",
            ),
        )
        vol = RemoteVolume(
            slug="test-remote",
            ssh_endpoint="bad-key",
            path=REMOTE_BACKUP_PATH,
        )
        config = Config(
            ssh_endpoints={"bad-key": endpoint},
            volumes={"test-remote": vol},
        )

        resolved = resolve_all_endpoints(config)
        status = check_volume(vol, resolved)
        assert status.active is False
        assert VolumeError.UNREACHABLE in status.errors


# ── Agent forwarding through bastion ────────────────────────


class TestAgentForwarding:
    """Verify SSH agent forwarding through a bastion host.

    Spawns an ephemeral ssh-agent, loads the test key, and
    verifies the forwarded agent is visible on the destination
    for both the Fabric/Paramiko path and the SSH CLI path.
    """

    @staticmethod
    def _forwarding_config(
        private_key: Path,
        bastion: SshEndpoint,
    ) -> tuple[Config, RemoteVolume]:
        """Build config with agent-forwarding endpoint."""
        destination = SshEndpoint(
            slug="fwd-dst",
            host="backup-server",
            port=22,
            user="testuser",
            key=str(private_key),
            proxy_jump="bastion",
            connection_options=SshConnectionOptions(
                strict_host_key_checking=False,
                known_hosts_file="/dev/null",
                forward_agent=True,
            ),
        )
        vol = RemoteVolume(
            slug="fwd-vol",
            ssh_endpoint="fwd-dst",
            path=REMOTE_BACKUP_PATH,
        )
        config = Config(
            ssh_endpoints={
                "bastion": bastion,
                "fwd-dst": destination,
            },
            volumes={"fwd-vol": vol},
        )
        return config, vol

    def test_fabric_forwarded_agent_on_destination(
        self,
        ssh_key_pair: tuple[Path, Path],
        docker_ssh_endpoint: SshEndpoint,
        bastion_container: SshEndpoint,
    ) -> None:
        """ssh-add -l shows key on destination (Fabric)."""
        private_key, _ = ssh_key_pair

        with _ssh_agent(private_key):
            config, vol = self._forwarding_config(
                private_key,
                bastion_container,
            )
            resolved = resolve_all_endpoints(config)
            ep = resolved[vol.slug]

            result = fabric_run_remote(
                ep.server,
                ["ssh-add", "-l"],
                ep.proxy_chain,
            )
            assert result.returncode == 0
            assert "ED25519" in result.stdout

    def test_cli_forwarded_agent_on_destination(
        self,
        ssh_key_pair: tuple[Path, Path],
        docker_ssh_endpoint: SshEndpoint,
        bastion_container: SshEndpoint,
    ) -> None:
        """ssh-add -l shows key on destination (SSH CLI)."""
        private_key, _ = ssh_key_pair

        with _ssh_agent(private_key):
            config, vol = self._forwarding_config(
                private_key,
                bastion_container,
            )
            resolved = resolve_all_endpoints(config)
            ep = resolved[vol.slug]

            result = ssh_run_remote(
                ep.server,
                ["ssh-add", "-l"],
                ep.proxy_chain,
            )
            assert result.returncode == 0
            assert "ED25519" in result.stdout
