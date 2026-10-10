"""Progress display for the credential → mount → umount lifecycle.

Shared by ``disks mount`` / ``disks umount`` and the ``managed_mount``
context manager used by ``run`` / ``preflight`` / ``snapshots``, which used
to each carry their own copy of these callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..clihelpers import Severity, Strictness, classify_severity
from ..config import Config
from ..credentials import PassphrasePrefetch, prefetch_count
from ..disks.lifecycle import MountResult, UmountResult, mount_count
from ..disks.models import MountFailureReason
from ..disks.output import display_name
from .mount_progress_bar import (
    DisksProgressBar,
    format_credential_result,
    format_mount_result,
    format_umount_result,
)

# Mount failure reasons that correspond to "expected inactive" preflight
# states (e.g. drive not plugged in maps to VolumeError.DEVICE_NOT_PRESENT
# in INACTIVE_VOLUME_ERRORS; UNREACHABLE maps to SshEndpointError.UNREACHABLE
# in INACTIVE_SSH_ERRORS).  Everything else — PASSPHRASE_NOT_AVAILABLE on a
# plugged-in drive included — is a real failure.
_INACTIVE_MOUNT_REASONS: frozenset[MountFailureReason] = frozenset(
    {
        MountFailureReason.DEVICE_NOT_PRESENT,
        MountFailureReason.UNREACHABLE,
    }
)


def mount_result_severity(
    result: MountResult,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Map a mount lifecycle result to display severity under *strictness*.

    A drive not being plugged in is an expected condition (the user
    backs up to removable media), so under the default
    ``IGNORE_INACTIVE`` it renders as a warning.  Under ``IGNORE_NONE``
    every mount failure is fatal, so it renders as an error to stay
    consistent with the preflight abort that will follow.  Under
    ``IGNORE_ALL`` every mount failure is non-fatal and renders as a
    warning.
    """
    return (
        Severity.OK
        if result.success
        else classify_severity(
            result.failure_reason in _INACTIVE_MOUNT_REASONS, strictness
        )
    )


def mount_display_names(cfg: Config) -> dict[str, str]:
    """Display name of every mount-managed volume, keyed by slug."""
    return {
        slug: display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }


@dataclass(frozen=True)
class LifecycleProgress:
    """Progress bars and lifecycle callbacks; every bar is ``None`` when hidden.

    Rich permits only one live display at a time, so the credential bar is
    stopped when the first mount starts (``stop`` is idempotent).
    """

    display_names: dict[str, str]
    strictness: Strictness
    credential_bar: DisksProgressBar | None
    mount_bar: DisksProgressBar | None
    umount_bar: DisksProgressBar | None

    @staticmethod
    def create(
        cfg: Config,
        *,
        enabled: bool,
        names: list[str] | None = None,
        strictness: Strictness = Strictness.IGNORE_INACTIVE,
        credentials: bool = True,
        mounting: bool = True,
        umounting: bool = True,
    ) -> LifecycleProgress:
        """Build the bars the requested phases need (none when not *enabled*)."""
        total = mount_count(cfg, names)
        credentials_total = prefetch_count(cfg) if credentials else 0
        return LifecycleProgress(
            display_names=mount_display_names(cfg),
            strictness=strictness,
            credential_bar=(
                DisksProgressBar(
                    credentials_total, "Loading credential", format_credential_result
                )
                if enabled and credentials_total > 0
                else None
            ),
            mount_bar=(
                DisksProgressBar(total, "Mounting", format_mount_result)
                if enabled and mounting
                else None
            ),
            umount_bar=(
                DisksProgressBar(total, "Umounting", format_umount_result)
                if enabled and umounting
                else None
            ),
        )

    def _name(self, slug: str) -> str:
        return self.display_names.get(slug, slug)

    def on_prefetch_start(self, passphrase_id: str) -> None:
        if self.credential_bar is not None:
            self.credential_bar.on_start(passphrase_id)

    def on_prefetch_end(self, passphrase_id: str, result: PassphrasePrefetch) -> None:
        if self.credential_bar is not None:
            self.credential_bar.on_end(
                passphrase_id,
                # A passphrase that cannot be retrieved is only a problem for
                # a drive that is actually plugged in, and the mount step
                # reports that.  Prefetch failures are therefore warnings
                # regardless of strictness.
                Severity.OK if result.success else Severity.WARNING,
                result.detail,
            )

    def on_mount_start(self, slug: str) -> None:
        self.stop_credentials()
        if self.mount_bar is not None:
            self.mount_bar.on_start(self._name(slug))

    def on_mount_end(self, slug: str, result: MountResult) -> None:
        if self.mount_bar is not None:
            self.mount_bar.on_end(
                self._name(slug),
                mount_result_severity(result, self.strictness),
                result.detail,
            )

    def on_umount_start(self, slug: str) -> None:
        if self.umount_bar is not None:
            self.umount_bar.on_start(self._name(slug))

    def on_umount_end(self, slug: str, result: UmountResult) -> None:
        if self.umount_bar is not None:
            self.umount_bar.on_end(
                self._name(slug),
                Severity.OK if result.success else Severity.ERROR,
                result.detail,
                result.warning,
            )

    def stop_credentials(self) -> None:
        if self.credential_bar is not None:
            self.credential_bar.stop()

    def stop_mounting(self) -> None:
        """Stop the credential and mount bars (end of the mount phase)."""
        self.stop_credentials()
        if self.mount_bar is not None:
            self.mount_bar.stop()

    def stop_all(self) -> None:
        self.stop_mounting()
        if self.umount_bar is not None:
            self.umount_bar.stop()
