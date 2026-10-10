"""Command wiring shared by several CLI commands.

Each domain module owns its own ``cli`` sub-app; what more than one of those
sub-apps needs — loading the config, resolving endpoints, the managed mount
lifecycle and preflight checks with progress — lives here instead, so that no
module reaches into another module's ``cli`` package.

Only ``cli`` sub-apps import this package; core and ``output`` code never do.
Import from the submodules directly.
"""
