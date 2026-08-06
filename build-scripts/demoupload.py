"""Publish the demo recorded by CI to asciinema.org.

Publishing from CI is disabled: GitHub-hosted runners have no route to
asciinema.org (see .github/workflows/demo.yml).  The release pipeline records the
demo and keeps it as a workflow artifact instead; this is the manual half.

The cast title is written into the recording's header by demo/record-demo.sh at
record time, so uploading the artifact unchanged reproduces the naming the CI
upload used to publish.  On success the README's asciicast embed is repointed at
the new recording and the change committed, since a published demo nobody links to
is not much use.

Usage:
    python build-scripts/demoupload.py               # newest successful run on main
    python build-scripts/demoupload.py --run-id 123  # a specific run
    python build-scripts/demoupload.py --dry-run     # fetch and report, do not upload
    python build-scripts/demoupload.py --no-readme   # upload without touching README
    python build-scripts/demoupload.py --no-commit   # update README but do not commit
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

WORKFLOW = "demo.yml"
ARTIFACT_NAME = "demo-recording"
CAST_FILENAME = "demo.cast"
# Release-triggered runs land on the default branch and their recordings carry a
# release version; a PR-branch run would title the cast with a .devN version.
PREFERRED_BRANCH = "main"
_RUN_FIELDS = "databaseId,createdAt,headBranch,displayTitle"

README = Path("README.md")
# [![asciicast](<url>.svg)](<url>) — the embed near the top of the README.
_EMBED_RE = re.compile(r"\[!\[asciicast\]\([^)]*\)\]\([^)]*\)")
# asciinema prints the recording URL on success. The server need not be
# asciinema.org, so match any host rather than hard-coding one.
_RECORDING_URL_RE = re.compile(r"https?://\S+?/a/[A-Za-z0-9]+")


@dataclass(frozen=True)
class WorkflowRun:
    """A successful `demo` workflow run holding a recording artifact."""

    run_id: str
    created_at: str
    branch: str
    title: str

    @staticmethod
    def from_json(payload: dict[str, object]) -> WorkflowRun:
        """Build from one entry of `gh run list --json`."""
        return WorkflowRun(
            run_id=str(payload.get("databaseId", "")),
            created_at=str(payload.get("createdAt", "")),
            branch=str(payload.get("headBranch", "")),
            title=str(payload.get("displayTitle", "")),
        )

    def describe(self) -> str:
        """One-line summary for the console."""
        return f"{self.created_at}  {self.branch}  {self.title}"


def _capture(command: list[str]) -> str:
    """Run *command*, returning stdout; exit with its stderr on failure."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print(
            f"{command[0]} not found — is it installed? "
            "`gh` and `asciinema` both come from mise.toml.",
            file=sys.stderr,
        )
        raise typer.Exit(1) from None
    if result.returncode != 0:
        print(
            f"{' '.join(command)} failed ({result.returncode}):\n{result.stderr}",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    return result.stdout


def _list_runs(branch: str | None) -> list[WorkflowRun]:
    """Successful runs of the demo workflow, newest first."""
    scope = ["--branch", branch] if branch else []
    output = _capture(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            WORKFLOW,
            "--status",
            "success",
            "--limit",
            "1",
            "--json",
            _RUN_FIELDS,
            *scope,
        ]
    )
    return [WorkflowRun.from_json(entry) for entry in json.loads(output or "[]")]


def _view_run(run_id: str) -> WorkflowRun:
    """Summary of one run, by id."""
    output = _capture(["gh", "run", "view", run_id, "--json", _RUN_FIELDS])
    return WorkflowRun.from_json(json.loads(output))


def _latest_run() -> WorkflowRun:
    """Newest successful run, preferring the default branch.

    Tag-triggered runs report the tag as ``headBranch``, so a branch-scoped search
    can legitimately come back empty; fall back to any successful run rather than
    failing on a release that was published from a tag.
    """
    candidates = [
        *_list_runs(PREFERRED_BRANCH),
        *_list_runs(None),
    ]
    if not candidates:
        print(
            "No successful demo workflow run found.\n"
            "Record locally and use `mise run demo-upload`, or trigger a run with "
            "`gh workflow run demo.yml`.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    return candidates[0]


def _download_cast(run: WorkflowRun, destination: Path) -> Path:
    """Download the run's artifact into *destination* and return the cast file."""
    _capture(
        [
            "gh",
            "run",
            "download",
            run.run_id,
            "--name",
            ARTIFACT_NAME,
            "--dir",
            str(destination),
        ]
    )
    casts = sorted(destination.rglob(CAST_FILENAME))
    if not casts:
        print(
            f"No {CAST_FILENAME} in the {ARTIFACT_NAME} artifact of run {run.run_id}.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    return casts[0]


def _cast_title(cast: Path) -> str:
    """Title recorded in the cast header, or a placeholder when absent."""
    with cast.open(encoding="utf-8") as handle:
        header = handle.readline()
    try:
        parsed = json.loads(header)
    except json.JSONDecodeError:
        return "(unreadable header)"
    title = parsed.get("title") if isinstance(parsed, dict) else None
    return str(title) if title else "(none)"


def _upload(cast: Path) -> str:
    """Upload *cast* and return everything asciinema printed.

    Output is captured rather than streamed so the recording URL can be read back
    for the README, then echoed verbatim so nothing asciinema says is swallowed.
    """
    try:
        result = subprocess.run(
            ["asciinema", "upload", str(cast)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("asciinema not found — it comes from mise.toml.", file=sys.stderr)
        raise typer.Exit(1) from None
    if result.returncode != 0:
        print(
            f"asciinema upload failed ({result.returncode}).\n"
            "If this is a connection timeout, check that this machine has a route "
            "to asciinema.org — GitHub-hosted runners do not, which is why this "
            "task exists.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    combined = f"{result.stdout}{result.stderr}"
    print(combined.rstrip("\n"))
    return combined


def parse_recording_url(upload_output: str) -> str | None:
    """The recording URL asciinema reported, or None if it said no such thing."""
    match = _RECORDING_URL_RE.search(upload_output)
    return match.group(0).rstrip(".,") if match else None


def render_embed(url: str) -> str:
    """The README's asciicast embed for *url*."""
    return f"[![asciicast]({url}.svg)]({url})"


@dataclass(frozen=True)
class ReadmeUpdate:
    """Outcome of repointing the README embed."""

    found: bool
    changed: bool
    content: str


def apply_embed(readme_text: str, url: str) -> ReadmeUpdate:
    """Repoint the asciicast embed in *readme_text* at *url*.

    Pure: returns the new content and what happened, leaving the caller to write it
    and to decide whether a missing embed is an error.
    """
    embed = render_embed(url)
    updated, count = _EMBED_RE.subn(lambda _: embed, readme_text, count=1)
    return ReadmeUpdate(
        found=count == 1, changed=updated != readme_text, content=updated
    )


def _readme_was_dirty() -> bool:
    """Whether README.md already had uncommitted changes before this run.

    Checked up front: a pathspec-limited `git commit` takes the whole file, so
    committing on top of an in-progress edit would sweep it up.
    """
    return any(
        subprocess.run(
            ["git", "diff", *scope, "--quiet", "--", str(README)], check=False
        ).returncode
        != 0
        for scope in ([], ["--cached"])
    )


def _commit_readme(url: str, run: WorkflowRun) -> None:
    """Commit the README embed change on its own."""
    cast_id = url.rsplit("/", 1)[-1]
    message = (
        f"docs(readme): point the demo at {cast_id}\n\n"
        f"Uploaded the recording from demo workflow run {run.run_id} "
        f"({run.branch}) with `mise run demo-upload-latest`.\n\n"
        f"{url}\n"
    )
    _capture(["git", "commit", "--message", message, "--", str(README)])
    print(f"Committed {_capture(['git', 'log', '-1', '--oneline']).strip()}")


def _update_readme(url: str) -> bool:
    """Repoint the README embed at *url*; True when the file was rewritten."""
    if not README.exists():
        print(f"{README} not found — cannot update the embed.", file=sys.stderr)
        raise typer.Exit(1)
    result = apply_embed(README.read_text(encoding="utf-8"), url)
    if not result.found:
        print(
            f"No asciicast embed found in {README}; expected a line like\n"
            f"  {render_embed(url)}\n"
            "The upload succeeded — add or fix that line by hand.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    if not result.changed:
        print(f"{README} already points at this recording.")
        return False
    README.write_text(result.content, encoding="utf-8")
    print(f"Updated {README}")
    return True


app = typer.Typer(add_completion=False)


@app.command()
def main(
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Upload the recording from this workflow run"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Fetch and report, but do not upload"),
    ] = False,
    update_readme: Annotated[
        bool,
        typer.Option(
            "--readme/--no-readme",
            help="Repoint the README asciicast embed at the new recording",
        ),
    ] = True,
    commit: Annotated[
        bool,
        typer.Option("--commit/--no-commit", help="Commit the README change"),
    ] = True,
) -> None:
    """Download the demo recorded by CI and upload it to asciinema.org."""
    readme_was_dirty = update_readme and commit and _readme_was_dirty()
    run = _view_run(run_id) if run_id else _latest_run()
    print(f"Run {run.run_id}:\n  {run.describe()}")

    with tempfile.TemporaryDirectory() as workdir:
        cast = _download_cast(run, Path(workdir))
        print(f"Title: {_cast_title(cast)}")
        if dry_run:
            print(f"Dry run — not uploading {cast.name}")
            return
        output = _upload(cast)

    if not update_readme:
        return
    url = parse_recording_url(output)
    if url is None:
        print(
            "The upload succeeded but no recording URL could be found in "
            f"asciinema's output, so {README} was left alone. Update the embed by "
            "hand.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    if not _update_readme(url):
        return
    if not commit:
        print(f"Not committing — `git add {README}` and commit when ready.")
        return
    if readme_was_dirty:
        print(
            f"{README} already had uncommitted changes before this run, so it was "
            "not committed — a pathspec commit would take those too. Review the "
            "diff and commit yourself.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    _commit_readme(url, run)


if __name__ == "__main__":
    app()
