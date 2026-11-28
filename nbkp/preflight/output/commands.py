"""The follow-up commands preflight's reports suggest."""

from typing import Protocol


class SuggestedCommands(Protocol):
    """
    Builds the ``nbkp`` commands a check or troubleshoot report suggests.

    Each suggestion must copy-paste as-is, so it carries the config file and the
    selection flags of the command that printed the report. Those flags are the
    CLI's: it supplies the implementation (``nbkp.cliwiring.invocation``), and
    the reports name only the command to run.
    """

    @property
    def exclude_locations(self) -> tuple[str, ...]:
        """The locations the command excluded (``-L``), to explain a skipped host."""
        ...

    def command(self, *args: str, endpoint_flags: bool = True) -> str:
        """
        Shell-quoted ``nbkp <args>`` with the command's config and selection.

        *endpoint_flags* is ``False`` for commands that take no endpoint
        selection.
        """
        ...

    def troubleshoot_command(self) -> str:
        """Shell-quoted ``nbkp preflight troubleshoot`` for the same setup."""
        ...
