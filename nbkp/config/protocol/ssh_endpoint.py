"""SSH endpoint and connection options."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from .base import Slug, _BaseModel


class SshConnectionOptions(_BaseModel):
    """SSH connection options.

    These fields map to parameters across three layers:
    - SSH client: ssh(1) -o options
    - Paramiko: SSHClient.connect() kwargs
      https://docs.paramiko.org/en/stable/api/client.html
    - Fabric: Connection() constructor
      https://docs.fabfile.org/en/stable/api/connection.html
    """

    model_config = ConfigDict(frozen=True)

    # Connection
    # SSH: ConnectTimeout | Paramiko: timeout | Fabric: connect_timeout
    connect_timeout: int = Field(
        default=10, ge=1, description="Connection timeout in seconds"
    )
    # SSH: Compression | Paramiko: compress
    compress: bool = Field(default=False, description="Enable SSH compression")
    # SSH: ServerAliveInterval | Paramiko: transport.set_keepalive()
    server_alive_interval: Optional[int] = Field(
        default=None, ge=1, description="Keepalive interval in seconds"
    )

    # Authentication
    # Paramiko: allow_agent — use SSH agent for key lookup
    allow_agent: bool = Field(
        default=True, description="Use SSH agent for authentication"
    )
    # Paramiko: look_for_keys — search ~/.ssh/ for keys
    look_for_keys: bool = Field(default=True, description="Search ~/.ssh/ for keys")

    # Timeouts
    # Paramiko: banner_timeout — wait for SSH banner
    banner_timeout: Optional[float] = Field(
        default=None, ge=0, description="Wait time for SSH banner"
    )
    # Paramiko: auth_timeout — wait for auth response
    auth_timeout: Optional[float] = Field(
        default=None, ge=0, description="Wait time for auth response"
    )
    # Paramiko: channel_timeout — wait for channel open
    channel_timeout: Optional[float] = Field(
        default=None,
        ge=0,
        description="Wait time for channel open (Paramiko/Fabric only)",
    )

    # Host key verification
    # SSH: StrictHostKeyChecking
    # Paramiko: SSHClient.set_missing_host_key_policy()
    strict_host_key_checking: bool = Field(
        default=True, description="Verify remote host key"
    )
    # SSH: UserKnownHostsFile
    # Paramiko: SSHClient.load_host_keys()
    known_hosts_file: Optional[str] = Field(
        default=None, description="Custom known hosts file path"
    )

    # Forwarding
    # SSH: ForwardAgent | Fabric: forward_agent
    forward_agent: bool = Field(
        default=False, description="Enable SSH agent forwarding"
    )

    # Algorithm restrictions
    # Paramiko: disabled_algorithms — disable specific algorithms
    # (Paramiko/Fabric only — no SSH CLI equivalent)
    disabled_algorithms: Optional[Dict[str, List[str]]] = Field(
        default=None,
        description="Disable specific SSH algorithms (Paramiko/Fabric only)",
    )


class SshEndpoint(_BaseModel):
    model_config = ConfigDict(frozen=True)
    slug: Slug
    host: str = Field(..., min_length=1, description="Hostname or SSH config alias")
    port: int = Field(default=22, ge=1, le=65535, description="SSH port")
    user: Optional[str] = Field(default=None, description="SSH username")
    key: Optional[str] = Field(default=None, description="Path to private key file")

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v: Any) -> str | None:
        if not isinstance(v, str):
            return None
        return str(Path(v).expanduser())

    connection_options: SshConnectionOptions = Field(
        default_factory=lambda: SshConnectionOptions(),
        description="SSH connection options",
    )
    proxy_jump: Optional[str] = Field(
        default=None,
        description="Slug of another endpoint to use as bastion",
    )
    proxy_jumps: Optional[List[str]] = Field(
        default=None,
        description=(
            "Slugs for multi-hop proxy chain (mutually exclusive with `proxy-jump`)"
        ),
    )
    location: Optional[str] = Field(
        default=None,
        description="Network location tag (e.g. `home`, `travel`)",
    )
    locations: Optional[List[str]] = Field(
        default=None,
        description=("Multiple location tags (mutually exclusive with `location`)"),
    )
    extends: Optional[str] = Field(
        default=None,
        description="Slug of parent endpoint to inherit from",
    )

    @model_validator(mode="after")
    def validate_proxy_exclusivity(self) -> SshEndpoint:
        if self.proxy_jump is not None and self.proxy_jumps is not None:
            raise ValueError("proxy-jump and proxy-jumps are mutually exclusive")
        return self

    @model_validator(mode="after")
    def validate_location_exclusivity(self) -> SshEndpoint:
        if self.location is not None and self.locations is not None:
            raise ValueError("location and locations are mutually exclusive")
        return self

    @property
    def proxy_jump_chain(self) -> list[str]:
        """Return the proxy-jump chain as a list of slugs."""
        if self.proxy_jumps is not None:
            return list(self.proxy_jumps)
        elif self.proxy_jump is not None:
            return [self.proxy_jump]
        else:
            return []

    @property
    def location_list(self) -> list[str]:
        """Return locations as a list."""
        if self.locations is not None:
            return list(self.locations)
        elif self.location is not None:
            return [self.location]
        else:
            return []
