from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from .base import ConfigModel, Slug
from .errors import ConfigValidationErrorKind, config_error


def _expand_user(v: Any) -> Any:
    """``~``-expand a path given as a string.

    Non-strings pass through so pydantic rejects them rather than silently
    dropping the value.
    """
    match v:
        case str():
            return str(Path(v).expanduser())
        case _:
            return v


class SshConnectionOptions(ConfigModel):
    """These fields map to parameters across three layers:
    - SSH client: ssh(1) -o options
    - Paramiko: SSHClient.connect() kwargs
      https://docs.paramiko.org/en/stable/api/client.html
    - Fabric: Connection() constructor
      https://docs.fabfile.org/en/stable/api/connection.html
    """

    # SSH: ConnectTimeout | Paramiko: timeout | Fabric: connect_timeout
    connect_timeout: int = Field(
        default=10, ge=1, description="Connection timeout in seconds"
    )
    # SSH: Compression | Paramiko: compress
    compress: bool = Field(default=False, description="Enable SSH compression")
    # SSH: ServerAliveInterval | Paramiko: transport.set_keepalive()
    server_alive_interval: int | None = Field(
        default=None, ge=1, description="Keepalive interval in seconds"
    )

    # Paramiko: allow_agent
    allow_agent: bool = Field(
        default=True, description="Use SSH agent for authentication"
    )
    # Paramiko: look_for_keys
    look_for_keys: bool = Field(default=True, description="Search ~/.ssh/ for keys")

    # Paramiko: banner_timeout
    banner_timeout: float | None = Field(
        default=None, ge=0, description="Wait time for SSH banner"
    )
    # Paramiko: auth_timeout
    auth_timeout: float | None = Field(
        default=None, ge=0, description="Wait time for auth response"
    )
    # Paramiko: channel_timeout (no SSH CLI equivalent)
    channel_timeout: float | None = Field(
        default=None,
        ge=0,
        description="Wait time for channel open (Paramiko/Fabric only)",
    )

    # SSH: StrictHostKeyChecking | Paramiko: SSHClient.set_missing_host_key_policy()
    strict_host_key_checking: bool = Field(
        default=True, description="Verify remote host key"
    )
    # SSH: UserKnownHostsFile | Paramiko: SSHClient.load_host_keys()
    known_hosts_file: str | None = Field(
        default=None, description="Custom known hosts file path"
    )

    @field_validator("known_hosts_file", mode="before")
    @classmethod
    def normalize_known_hosts_file(cls, v: Any) -> Any:
        # OpenSSH expands ``~`` in UserKnownHostsFile, Paramiko's
        # load_host_keys does not: expand it once here for both.
        return _expand_user(v)

    # SSH: ForwardAgent | Fabric: forward_agent
    forward_agent: bool = Field(
        default=False, description="Enable SSH agent forwarding"
    )

    # Paramiko: disabled_algorithms (no SSH CLI equivalent)
    disabled_algorithms: dict[str, list[str]] | None = Field(
        default=None,
        description="Disable specific SSH algorithms (Paramiko/Fabric only)",
    )


class SshEndpoint(ConfigModel):
    slug: Slug
    host: str = Field(..., min_length=1, description="Hostname or SSH config alias")
    port: int = Field(default=22, ge=1, le=65535, description="SSH port")
    user: str | None = Field(default=None, description="SSH username")
    key: str | None = Field(default=None, description="Path to private key file")

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v: Any) -> Any:
        return _expand_user(v)

    connection_options: SshConnectionOptions = Field(
        default_factory=lambda: SshConnectionOptions(),
        description="SSH connection options",
    )
    proxy_jump: str | None = Field(
        default=None,
        description="Slug of another endpoint to use as bastion",
    )
    proxy_jumps: list[str] | None = Field(
        default=None,
        description=(
            "Slugs for multi-hop proxy chain (mutually exclusive with `proxy-jump`)"
        ),
    )
    location: str | None = Field(
        default=None,
        description="Network location tag (e.g. `home`, `travel`)",
    )
    locations: list[str] | None = Field(
        default=None,
        description=("Multiple location tags (mutually exclusive with `location`)"),
    )
    extends: str | None = Field(
        default=None,
        description="Slug of parent endpoint to inherit from",
    )

    @model_validator(mode="after")
    def validate_proxy_exclusivity(self) -> SshEndpoint:
        if self.proxy_jump is not None and self.proxy_jumps is not None:
            raise config_error(
                ConfigValidationErrorKind.MUTUALLY_EXCLUSIVE,
                "proxy-jump and proxy-jumps are mutually exclusive",
            )
        return self

    @model_validator(mode="after")
    def validate_location_exclusivity(self) -> SshEndpoint:
        if self.location is not None and self.locations is not None:
            raise config_error(
                ConfigValidationErrorKind.MUTUALLY_EXCLUSIVE,
                "location and locations are mutually exclusive",
            )
        return self

    @property
    def proxy_jump_chain(self) -> list[str]:
        if self.proxy_jumps is not None:
            return list(self.proxy_jumps)
        elif self.proxy_jump is not None:
            return [self.proxy_jump]
        else:
            return []

    @property
    def location_list(self) -> list[str]:
        if self.locations is not None:
            return list(self.locations)
        elif self.location is not None:
            return [self.location]
        else:
            return []

    @property
    def destination(self) -> str:
        """``user@host``, or ``host`` when no user is set: ssh's destination."""
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def host_label(self) -> str:
        """The destination with its port when not 22 (``user@host:2222``)."""
        return (
            f"{self.destination}:{self.port}" if self.port != 22 else self.destination
        )
