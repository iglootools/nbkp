"""
Progress display for the credential → mount → umount lifecycle.

Shared by ``disks mount`` / ``disks umount`` and the ``managed_mount``
context manager used by ``run`` / ``preflight`` / ``snapshots``, which used
to each carry their own copy of these callbacks.
"""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from nbkp.clidisplay import StepProgressBar
from nbkp.config import Config
from nbkp.credentials import PassphrasePrefetch, prefetch_count
from nbkp.credentials.output import credential_error_message
from nbkp.disks import (
    MountResult,
    UmountResult,
    display_name,
    mount_count,
    mount_result_severity,
)
from nbkp.disks.output import (
    mount_failure_detail,
    umount_failure_detail,
    umount_failure_warning,
)
from nbkp.policy import Severity, Strictness
from nbkp.remote.endpoints import ResolvedEndpoints


def mount_display_names(cfg: Config) -> dict[str, str]:
    """Return the display name of each mount-managed volume, keyed by slug."""
    return {
        slug: display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }


@dataclass(frozen=True)
class LifecycleProgress:
    """
    Progress bars and lifecycle callbacks; every bar is ``None`` when hidden.

    Rich permits only one live display at a time, so the credential bar is
    stopped when the first mount starts (``stop`` is idempotent).
    """

    display_names: dict[str, str]
    strictness: Strictness
    credential_bar: StepProgressBar | None
    mount_bar: StepProgressBar | None
    umount_bar: StepProgressBar | None

    @staticmethod
    def create(
        cfg: Config,
        resolved: ResolvedEndpoints,
        *,
        enabled: bool,
        names: list[str] | None = None,
        strictness: Strictness = Strictness.IGNORE_INACTIVE,
        credentials: bool = True,
        mounting: bool = True,
        umounting: bool = True,
    ) -> LifecycleProgress:
        """Build the bars the requested phases need (none when not *enabled*)."""
        total = mount_count(cfg, resolved, names)
        credentials_total = prefetch_count(cfg) if credentials else 0
        return LifecycleProgress(
            display_names=mount_display_names(cfg),
            strictness=strictness,
            credential_bar=(
                StepProgressBar(credentials_total)
                if enabled and credentials_total > 0
                else None
            ),
            mount_bar=(StepProgressBar(total) if enabled and mounting else None),
            umount_bar=(StepProgressBar(total) if enabled and umounting else None),
        )

    def _name(self, slug: str) -> str:
        return self.display_names.get(slug, slug)

    def on_prefetch_start(self, passphrase_id: str) -> None:
        """Show that the credential *passphrase_id* is being loaded."""
        if self.credential_bar is not None:
            self.credential_bar.on_start(f"Loading credential {passphrase_id}...")

    def on_prefetch_end(self, passphrase_id: str, result: PassphrasePrefetch) -> None:
        """Record a credential load's outcome; a failure is only a warning."""
        if self.credential_bar is not None:
            self.credential_bar.on_end(
                f"credential {passphrase_id}",
                # A passphrase that cannot be retrieved is only a problem for
                # a drive that is actually plugged in, and the mount step
                # reports that.  Prefetch failures are therefore warnings
                # regardless of strictness.
                Severity.OK if result.success else Severity.WARNING,
                (
                    credential_error_message(result.error)
                    if result.error is not None
                    else None
                ),
            )

    def on_mount_start(self, slug: str) -> None:
        """Stop the credential bar and show that volume *slug* is being mounted."""
        self.stop_credentials()
        if self.mount_bar is not None:
            self.mount_bar.on_start(f"Mounting {self._name(slug)}...")

    def on_mount_end(self, slug: str, result: MountResult) -> None:
        """Record a mount's outcome, its severity set by the strictness."""
        if self.mount_bar is not None:
            self.mount_bar.on_end(
                f"mount {self._name(slug)}",
                mount_result_severity(result, self.strictness),
                mount_failure_detail(result),
            )

    def on_umount_start(self, slug: str) -> None:
        """Show that volume *slug* is being unmounted."""
        if self.umount_bar is not None:
            self.umount_bar.on_start(f"Umounting {self._name(slug)}...")

    def on_umount_end(self, slug: str, result: UmountResult) -> None:
        """Record an umount's outcome; a failure is an error."""
        if self.umount_bar is not None:
            self.umount_bar.on_end(
                f"umount {self._name(slug)}",
                Severity.OK if result.success else Severity.ERROR,
                umount_failure_detail(result),
                umount_failure_warning(result),
            )

    @contextmanager
    def hidden(self) -> Generator[None]:
        """Take the bar on screen off it for the block, as a passphrase prompt needs."""
        with ExitStack() as stack:
            for bar in (self.credential_bar, self.mount_bar, self.umount_bar):
                if bar is not None:
                    stack.enter_context(bar.hidden())
            yield

    def stop_credentials(self) -> None:
        """Stop the credential bar, if shown; safe to call more than once."""
        if self.credential_bar is not None:
            self.credential_bar.stop()

    def stop_mounting(self) -> None:
        """Stop the credential and mount bars (end of the mount phase)."""
        self.stop_credentials()
        if self.mount_bar is not None:
            self.mount_bar.stop()

    def stop_all(self) -> None:
        """Stop every bar (end of the lifecycle)."""
        self.stop_mounting()
        if self.umount_bar is not None:
            self.umount_bar.stop()
