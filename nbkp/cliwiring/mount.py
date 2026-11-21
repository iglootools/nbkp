from collections.abc import Callable, Generator
from contextlib import contextmanager

import typer
from rich.console import Console, Group, RenderableType
from rich.text import Text

from ..clidisplay import OutputFormat
from ..config import Config
from ..credentials import build_passphrase_fn
from ..disks import (
    MountObservation,
    UmountResult,
    any_failed,
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
from .lock import is_run_lock_needed, run_lock_or_exit
from .mount_progress import LifecycleProgress, mount_display_names
from .runtime import Runtime


def _format_mount_status(
    progress: LifecycleProgress,
    mount_observations: dict[str, MountObservation],
) -> RenderableType:
    """The post-mount status table (human output only)."""
    return build_mount_status_table(
        [
            (progress.display_names.get(slug, slug), obs)
            for slug, obs in mount_observations.items()
        ],
        strictness=progress.strictness,
    )


@contextmanager
def lifecycle_session(
    cfg: Config,
    resolved: ResolvedEndpoints,
    *,
    runtime: Runtime,
    enabled: bool,
    names: list[str] | None = None,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    umounting: bool = True,
) -> Generator[tuple[Callable[[str], str], LifecycleProgress]]:
    """The passphrase function and the progress bars (shown when *enabled*)
    for a mount lifecycle; on exit, the bars are stopped and the cached
    passphrases cleared."""
    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider,
        cfg.credential_command,
        prompt=prompt_passphrase,
        environ=runtime.environ,
        run=runtime.run_credential_command,
    )
    progress = LifecycleProgress.create(
        cfg,
        resolved,
        enabled=enabled,
        names=names,
        strictness=strictness,
        umounting=umounting,
    )
    try:
        yield passphrase_fn, progress
    finally:
        progress.stop_all()
        cache.clear()


@contextmanager
def managed_mount(
    cfg: Config,
    resolved: ResolvedEndpoints,
    *,
    runtime: Runtime,
    writes: bool,
    mount: bool = True,
    umount: bool = True,
    output_format: OutputFormat = OutputFormat.HUMAN,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    umount_results: list[UmountResult] | None = None,
) -> Generator[tuple[Config, dict[str, MountObservation]]]:
    """Thin wrapper around :func:`disks.context.managed_mount` that adds
    Rich display callbacks, credential management and the run lock.

    Yields ``(resolved_config, mount_observations)``.  ``resolved_config``
    is *cfg* with discovered mountpoints filled in for mount-managed volumes
    that omitted ``path``.  Observations capture the runtime state discovered
    during mount so that preflight checks can reuse it instead of re-probing.

    Parameters
    ----------
    runtime:
        Runs the udisks commands and the probes on the volumes' hosts, and
        supplies the credential providers' environment and command runner,
        and the run lock.
    writes:
        Whether the block modifies a destination (a sync, a prune).  The run
        lock is held from before the first mount to after the last umount
        when the block writes, mounts or umounts; when another command holds
        it, the command exits 1 before mounting anything.
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
    with run_lock_or_exit(
        runtime.run_lock,
        runtime.run_lock_path(),
        output_format,
        needed=is_run_lock_needed(writes=writes, mount=mount, umount=umount),
    ):
        yield from _managed_mount_reporting_umount_failures(
            cfg,
            resolved,
            runtime=runtime,
            mount=mount,
            umount=umount,
            output_format=output_format,
            strictness=strictness,
            umount_results=umount_results,
        )


def _managed_mount_reporting_umount_failures(
    cfg: Config,
    resolved: ResolvedEndpoints,
    *,
    runtime: Runtime,
    mount: bool,
    umount: bool,
    output_format: OutputFormat,
    strictness: Strictness,
    umount_results: list[UmountResult] | None,
) -> Generator[tuple[Config, dict[str, MountObservation]]]:
    use_progress = output_format is OutputFormat.HUMAN
    collected = umount_results if umount_results is not None else []
    try:
        yield from _managed_mount_with_progress(
            cfg,
            resolved,
            runtime=runtime,
            mount=mount,
            umount=umount,
            use_progress=use_progress,
            strictness=strictness,
            collected=collected,
        )
    except BaseException:
        # The body failed (or was interrupted) and exits with its own error,
        # but a drive left mounted or unlocked must still be reported: with
        # -o json no progress line has shown it.
        if umount_results is None:
            _print_umount_failures(collected, mount_display_names(cfg))
        raise
    if umount_results is None:
        _exit_on_umount_failure(collected, mount_display_names(cfg))


def _managed_mount_with_progress(
    cfg: Config,
    resolved: ResolvedEndpoints,
    *,
    runtime: Runtime,
    mount: bool,
    umount: bool,
    use_progress: bool,
    strictness: Strictness,
    collected: list[UmountResult],
) -> Generator[tuple[Config, dict[str, MountObservation]]]:
    with lifecycle_session(
        cfg, resolved, runtime=runtime, enabled=use_progress, strictness=strictness
    ) as (passphrase_fn, progress):

        def on_umount_end(slug: str, result: UmountResult) -> None:
            collected.append(result)
            progress.on_umount_end(slug, result)

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
                Console().print(_format_mount_status(progress, mount_observations))
            yield result


def _print_umount_failures(
    results: list[UmountResult], display_names: dict[str, str]
) -> None:
    """Printed to stderr in every output format, since stdout already holds
    the command's report."""
    if any_failed(results):
        Console(stderr=True).print(format_umount_failures(results, display_names))


def _exit_on_umount_failure(
    results: list[UmountResult], display_names: dict[str, str]
) -> None:
    """Fail a command whose body completed when a volume could not be
    unmounted or locked."""
    _print_umount_failures(results, display_names)
    if any_failed(results):
        raise typer.Exit(1)


def format_umount_failures(
    results: list[UmountResult], display_names: dict[str, str]
) -> Group:
    """One line per volume that could not be unmounted or locked, for stderr."""
    return Group(
        *(
            Text.assemble(
                ("Umount failed: ", "bold red"),
                f"{display_names.get(r.volume_slug, r.volume_slug)}: ",
                umount_failure_detail(r) or "",
                f" ({umount_failure_warning(r)})",
            )
            for r in results
            if not r.success
        )
    )
