"""Tests for the dry-run label."""

from rich.console import Console
from rich.text import Text

from nbkp.theme import DRY_RUN_LABEL, dry_run_labeled, dry_run_table_title


def _render(text: Text) -> str:
    console = Console(width=200, color_system=None)
    with console.capture() as capture:
        console.print(text)
    return capture.get().rstrip("\n")


class TestDryRunLabeled:
    def test_starts_the_line_and_survives_rendering(self) -> None:
        """Rendered from a markup str, ``[dry-run]`` would vanish as a tag."""
        assert _render(dry_run_labeled("Would mount enc")) == (
            f"{DRY_RUN_LABEL} Would mount enc"
        )

    def test_keeps_the_line_styles(self) -> None:
        labeled = dry_run_labeled(Text("Would mount enc", style="bold"))

        assert [str(labeled[s.start : s.end]) for s in labeled.spans] == [
            "Would mount enc",
        ]


class TestDryRunTableTitle:
    def test_labels_only_a_dry_run(self) -> None:
        assert str(dry_run_table_title("Sync results:", dry_run=True)) == (
            "[dry-run] Sync results:"
        )
        assert str(dry_run_table_title("Sync results:", dry_run=False)) == (
            "Sync results:"
        )

    def test_is_styled_as_a_table_title(self) -> None:
        assert dry_run_table_title("Sync results:", dry_run=True).style == (
            "table.title"
        )
