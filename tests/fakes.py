"""Fake command and rsync runners, injected where production passes the real ones.

Tests hand these to the ``run`` / ``run_command`` / ``rsync`` parameters
instead of patching ``subprocess`` or the SSH transport by import path, so
moving a helper between modules does not break them.
"""

import subprocess
from dataclasses import dataclass, field
from typing import Any

from nbkp.config import Config, SyncConfig, Volume
from nbkp.remote.endpoints import ResolvedEndpoints

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


@dataclass
class FakeCommandRunner:
    """A :class:`~nbkp.remote.dispatch.VolumeCommandRunner` answering from a script.

    Each rule maps a command prefix (``("ls",)``, ``("btrfs", "subvolume")``)
    to a response, or to a list of responses consumed in order (the last one
    repeats).  The first matching rule wins; unmatched commands succeed with
    empty output.  An ``Exception`` response is raised, like a transport
    failure.
    """

    rules: list[tuple[tuple[str, ...], list[Response]]] = field(default_factory=list)
    calls: list[CommandCall] = field(default_factory=list)

    def on(self, *prefix: str, returns: Response | list[Response]) -> FakeCommandRunner:
        self.rules.append((prefix, returns if isinstance(returns, list) else [returns]))
        return self

    def __call__(
        self,
        cmd: list[str],
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
        *,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(CommandCall(cmd, volume, input))
        response = self._response_for(cmd)
        if isinstance(response, Exception):
            raise response
        else:
            return response

    def _response_for(self, cmd: list[str]) -> Response:
        queue = next((q for p, q in self.rules if tuple(cmd[: len(p)]) == p), None)
        match queue:
            case None:
                return completed()
            case [only]:
                return only
            case _:
                return queue.pop(0)

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
