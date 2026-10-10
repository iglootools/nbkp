"""Command wiring shared by several CLI commands.

Each domain module owns its own ``cli`` sub-app; what more than one of those
sub-apps needs — loading the config, resolving endpoints, the managed mount
lifecycle, preflight checks with progress, and the invocation flags echoed in
follow-up suggestions — lives here instead, so that no module reaches into
another module's ``cli`` package.

Import from the submodules directly. This package deliberately re-exports
nothing: ``commands.invocation`` is imported by ``preflight.output``, and an
eager ``__init__`` pulling in ``commands.preflight`` would turn that into an
import cycle.
"""
