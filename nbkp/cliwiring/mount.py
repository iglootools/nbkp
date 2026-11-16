from collections.abc import Generator
from contextlib import contextmanager

import typer
from rich.console import Console
from rich.text import Text

from ..clidisplay import OutputFormat
from ..config import Config
from ..credentials import build_passphrase_fn
from ..disks import (
    MountObservation,
    UmountResult,
    managed_mount as _disks_managed_mount,
)
from ..disks.output import (
    build_mount_status_table,
    umount_failure_detail,
    umount_failure_warning,
)
from ..policy import Strictness
from ..remote.endpoints import ResolvedEndpoints
from .credentials import prompt_passphrase
from .mount_progress import LifecycleProgress
from .runtime import Runtime


def _print_mount_status(
    progress: LifecycleProgress,
    mount_observations: dict[str, MountObservation],
) -> None:
    """Print the post-mount status table (human output only)."""
    Console().print(
        build_mount_status_table(
            [
                (progress.display_names.get(slug, slug), obs)
                for slug, obs in mount_observations.items()
            ],
            strictness=progress.strictness,
        )
    )


@contextmanager
def managed_mount(
    cfg: Config,
    resolved: ResolvedEndpoints,
    *,
    runtime: Runtime,
    mount: bool = True,
    umount: bool = True,
    output_format: OutputFormat = OutputFormat.HUMAN,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    umount_results: list[UmountResult] | None = None,
) -> Generator[tuple[Config, dict[str, MountObservation]]]:
    """Thin wrapper around :func:`disks.context.managed_mount` that adds
    Rich display callbacks and credential management.

    Yields ``(resolved_config, mount_observations)``.  ``resolved_config``
    is *cfg* with discovered mountpoints filled in for mount-managed volumes
    that omitted ``path``.  Observations capture the runtime state discovered
    during mount so that preflight checks can reuse it instead of re-probing.

    Parameters
    ----------
    runtime:
        Runs the udisks commands and the probes on the volumes' hosts, and
        supplies the credential providers' environment and command runner.
    mount:
        When ``False`` (or no volumes have mount config), mounting and
        umounting are both skipped.
    umount:
        When ``False``, the umount phase is skipped even if volumes
        were mounted.  Useful for debugging (``run --no-umount``).
    output_format:
        Controls whether Rich spinner / result lines are printed.  The
        credential-prefetch phase gets its own progress bar, shown only when
        the provider is prefetchable and encrypted volumes are configured.
    strictness:
        Picks the per-mount severity icon when the operation fails.
        See :func:`nbkp.disks.mount_result_severity`.
    umount_results:
        When given, the umount results are appended to it once the block
        exits, and the caller reports them (``run`` puts them in its
        output).  Otherwise a failed umount — a drive possibly left mounted
        or unlocked — is reported on stderr and fails a command that would
        have succeeded (exit 1).
    """
    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider,
        cfg.credential_command,
        prompt=prompt_passphrase,
        environ=runtime.environ,
        run=runtime.run_credential_command,
    )
    use_progress = output_format is OutputFormat.HUMAN
    progress = LifecycleProgress.create(
        cfg, resolved, enabled=use_progress, strictness=strictness
    )
    collected = umount_results if umount_results is not None else []

    def on_umount_end(slug: str, result: UmountResult) -> None:
        collected.append(result)
        progress.on_umount_end(slug, result)

    # try/finally instead of `with` because the bars are conditionally
    # created (None when output is JSON), and cache.clear() must also run.
    try:
        with _disks_managed_mount(
            cfg,
            resolved,
            passphrase_fn,
            mount=mount,
            umount=umount,
            on_prefetch_start=progress.on_prefetch_start,
            on_prefetch_end=progress.on_prefetch_end,
            on_mount_start=progress.on_mount_start,
            on_mount_end=progress.on_mount_end,
            on_umount_start=progress.on_umount_start,
            on_umount_end=on_umount_end,
            run=runtime.run,
        ) as result:
            progress.stop_mounting()
            _resolved_config, mount_observations = result
            if use_progress and mount_observations:
                _print_mount_status(progress, mount_observations)
            yield result
    finally:
        progress.stop_all()
        cache.clear()
    if umount_results is None:
        _exit_on_umount_failure(collected, progress.display_names)


def _exit_on_umount_failure(
    results: list[UmountResult], display_names: dict[str, str]
) -> None:
    """Fail the command when a volume could not be unmounted or locked.

    Reached only when the command body completed: a body that already
    failed exits with its own error.  Printed to stderr in every output
    format, since stdout already holds the command's report.
    """
    if any(not r.success for r in results):
        print_umount_failures(results, display_names)
        raise typer.Exit(1)


def print_umount_failures(
    results: list[UmountResult], display_names: dict[str, str]
) -> None:
    """One stderr line per volume that could not be unmounted or locked."""
    console = Console(stderr=True)
    for r in results:
        if not r.success:
            console.print(
                Text.assemble(
                    ("Umount failed: ", "bold red"),
                    f"{display_names.get(r.volume_slug, r.volume_slug)}: ",
                    umount_failure_detail(r) or "",
                    f" ({umount_failure_warning(r)})",
                )
            )
