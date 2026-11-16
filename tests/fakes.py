"""Fake command and rsync runners, injected where production passes the real ones.

Tests hand these to the ``run`` / ``run_command`` / ``rsync`` parameters, or
to a whole command through :func:`fake_runtime`, instead of patching
``subprocess`` or the SSH transport by import path, so moving a helper
between modules does not break them.
"""

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nbkp.cliwiring.runtime import Runtime
from nbkp.config import Config, SyncConfig, Volume
from nbkp.credentials import CommandFn
from nbkp.remote.dispatch import CommandLocator, VolumeCommandRunner
from nbkp.remote.endpoints import ResolvedEndpoint, ResolvedEndpoints
from nbkp.sync.modes import RsyncRunner

type Response = subprocess.CompletedProcess[str] | Exception


def completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@dataclass(frozen=True)
class CommandCall:
    cmd: list[str]
    volume: Volume
    input: str | None
    endpoint: ResolvedEndpoint | None
    """Where a remote volume's command was routed (``None`` for a local one)."""


@dataclass(frozen=True)
class _Rule:
    prefix: tuple[str, ...]
    volume: str | None
    responses: list[Response]

    def matches(self, cmd: list[str], volume: Volume) -> bool:
        return tuple(cmd[: len(self.prefix)]) == self.prefix and (
            self.volume is None or self.volume == volume.slug
        )


@dataclass
class FakeCommandRunner:
    """A :class:`~nbkp.remote.dispatch.VolumeCommandRunner` answering from a script.

    Each rule maps a command prefix (``("ls",)``, ``("btrfs", "subvolume")``),
    optionally restricted to one volume slug, to a response, or to a list of
    responses consumed in order (the last one repeats).  The first matching
    rule wins; unmatched commands succeed with empty output.  An ``Exception``
    response is raised, like a transport failure.
    """

    rules: list[_Rule] = field(default_factory=list)
    calls: list[CommandCall] = field(default_factory=list)

    def on(
        self,
        *prefix: str,
        returns: Response | list[Response],
        volume: str | None = None,
    ) -> FakeCommandRunner:
        responses = returns if isinstance(returns, list) else [returns]
        self.rules.append(_Rule(prefix, volume, responses))
        return self

    def __call__(
        self,
        cmd: list[str],
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
        *,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(
            CommandCall(cmd, volume, input, resolved_endpoints.get(volume.slug))
        )
        response = self._response_for(cmd, volume)
        if isinstance(response, Exception):
            raise response
        else:
            return response

    def _response_for(self, cmd: list[str], volume: Volume) -> Response:
        rule = next((r for r in self.rules if r.matches(cmd, volume)), None)
        match rule:
            case None:
                return completed()
            case _Rule(responses=[only]):
                return only
            case _:
                return rule.responses.pop(0)

    @property
    def commands(self) -> list[list[str]]:
        return [c.cmd for c in self.calls]

    def commands_starting_with(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.commands if tuple(c[: len(prefix)]) == prefix]


@dataclass
class FakeRsync:
    """A :class:`~nbkp.sync.modes.RsyncRunner` returning *responses* in order.

    The last response repeats; records each call's sync slug and keyword
    arguments.
    """

    responses: list[Response] = field(default_factory=lambda: [completed(0, "done\n")])
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __call__(
        self, sync: SyncConfig, config: Config, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((sync.slug, kwargs))
        response = (
            self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        )
        if isinstance(response, Exception):
            raise response
        else:
            return response

    @property
    def synced(self) -> list[str]:
        return [slug for slug, _ in self.calls]

    @property
    def last_kwargs(self) -> dict[str, Any]:
        return self.calls[-1][1]


FIXED_NOW = datetime(2026, 3, 6, 14, 30, tzinfo=UTC)
"""The time a :func:`fake_runtime` clock reads, which names snapshots."""


def _no_credential_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    raise AssertionError(f"unexpected credential command: {command}")


_NOWHERE = Path("/nonexistent-nbkp-test")
"""Under it, the paths a fake runtime searches for a config: none exists."""


def fake_runtime(
    *,
    run: VolumeCommandRunner | None = None,
    which: CommandLocator | None = None,
    rsync: RsyncRunner | None = None,
    environ: Mapping[str, str] | None = None,
    run_credential_command: CommandFn | None = None,
    local_user: str = "backup",
    home: Path = _NOWHERE / "home",
) -> Runtime:
    """A :class:`~nbkp.cliwiring.runtime.Runtime` touching nothing real.

    Unless given: hosts answered by an empty :class:`FakeCommandRunner`
    (every command succeeds with no output), every tool on the
    orchestrator's ``PATH``, a :class:`FakeRsync`, a clock stopped at
    :data:`FIXED_NOW` on Linux, an empty environment, a home and platform
    config directories that do not exist (so the config search never finds
    the developer's), and no credential command (calling it fails the test).  Pass it to
    ``CliRunner().invoke(app, args, obj=...)``.
    """
    return Runtime(
        run=run if run is not None else FakeCommandRunner(),
        which=which if which is not None else (lambda tool: f"/usr/bin/{tool}"),
        rsync=rsync if rsync is not None else FakeRsync(),
        clock=lambda: FIXED_NOW,
        platform="linux",
        environ=environ if environ is not None else {},
        home=lambda: home,
        user_config_dir=lambda app: str(_NOWHERE / "user-config" / app),
        site_config_dir=lambda app: str(_NOWHERE / "site-config" / app),
        run_credential_command=run_credential_command or _no_credential_command,
        local_user=lambda: local_user,
    )
