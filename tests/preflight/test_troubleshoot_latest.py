"""
Tests for the ``latest`` fix troubleshoot prints.

The fix for a missing or invalid symlink.

While ``latest`` points to ``/dev/null``, a hard-link sync deletes every
snapshot as a leftover of a failed run, so the fix points it at the newest
snapshot and keeps ``/dev/null`` for an endpoint that has none.  The newest
snapshot is listed while *gathering* facts (on a fake host here).
"""

import paramiko

from nbkp.preflight import DestinationEndpointErrorKind, SourceEndpointErrorKind
from nbkp.preflight.output import remediation as rem, troubleshoot as ts
from tests.fakes import FakeCommandRunner, completed
from tests.fixtures.preflight import btrfs_config, local_vol_statuses, sync_status

_OLD = "2026-01-01T00:00:00.000Z"
_NEW = "2026-01-02T00:00:00.000Z"
_SNAPSHOTS = "/mnt/dst/snapshots"


def _issues(
    *,
    dst_ep_errors: list[DestinationEndpointErrorKind] | None = None,
    src_ep_errors: list[SourceEndpointErrorKind] | None = None,
) -> list[ts.TroubleshootIssue]:
    config = btrfs_config()
    status = sync_status(
        config,
        local_vol_statuses(config),
        "btrfs-sync",
        dst_ep_errors=dst_ep_errors,
        src_ep_errors=src_ep_errors,
    )
    return ts.collect_issues({}, {}, {status.slug: status})


_INVALID_DST = _issues(
    dst_ep_errors=[DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID],
)


def _remediation(
    issues: list[ts.TroubleshootIssue],
    facts: rem.RemediationFacts,
) -> str:
    ctx = rem.TroubleshootContext(config=btrfs_config(), facts=facts)
    data = ts.troubleshoot_json(issues, ctx, has_fatal_errors=True)
    match data["issues"]:
        case [{"remediation": str() as remediation}]:
            return remediation
        case other:
            raise AssertionError(other)


class TestLatestFixRendering:
    def test_points_latest_at_the_newest_snapshot(self) -> None:
        facts = rem.RemediationFacts(newest_snapshots={"ep-dst": _NEW})
        out = _remediation(_INVALID_DST, facts)
        assert f"ln -sfn snapshots/{_NEW} /mnt/dst/latest" in out
        assert "/dev/null" not in out

    def test_keeps_dev_null_when_there_is_no_snapshot(self) -> None:
        facts = rem.RemediationFacts(newest_snapshots={"ep-dst": None})
        out = _remediation(_INVALID_DST, facts)
        assert "ln -sfn /dev/null /mnt/dst/latest" in out

    def test_warns_against_dev_null_when_the_snapshots_are_unknown(self) -> None:
        out = _remediation(_INVALID_DST, rem.RemediationFacts())
        assert "ln -sfn 'snapshots/<newest-snapshot>' /mnt/dst/latest" in out
        assert "deletes every snapshot" in " ".join(out.split())

    def test_source_endpoint_fix_uses_the_source_snapshots(self) -> None:
        issues = _issues(
            src_ep_errors=[SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND],
        )
        facts = rem.RemediationFacts(newest_snapshots={"ep-src": _NEW})
        out = _remediation(issues, facts)
        assert f"ln -sfn snapshots/{_NEW} /mnt/src/latest" in out


class TestNewestSnapshotGathering:
    def test_gathers_the_newest_snapshot_by_time(self) -> None:
        # The macOS name form sorts first by name but is the newer one.
        mac_newest = "2026-01-03T00-00-00.000Z"
        listing = f"{_OLD}\n{mac_newest}\n{_NEW}\nlost+found\n"
        run = FakeCommandRunner().on("ls", returns=completed(stdout=listing))
        facts = ts.gather_remediation_facts(
            _INVALID_DST,
            rem.TroubleshootContext(config=btrfs_config()),
            run=run,
        )
        assert facts.newest_snapshots == {"ep-dst": mac_newest}
        assert run.commands == [["ls", _SNAPSHOTS]]

    def test_no_snapshot_when_the_snapshots_dir_is_absent(self) -> None:
        run = (
            FakeCommandRunner()
            .on("ls", returns=completed(2, stderr="No such file or directory"))
            .on("test", returns=completed(1))
        )
        facts = ts.gather_remediation_facts(
            _INVALID_DST,
            rem.TroubleshootContext(config=btrfs_config()),
            run=run,
        )
        assert facts.newest_snapshots == {"ep-dst": None}

    def test_unknown_when_the_listing_fails(self) -> None:
        run = FakeCommandRunner().on(
            "ls",
            returns=completed(2, stderr="permission denied"),
        )
        facts = ts.gather_remediation_facts(
            _INVALID_DST,
            rem.TroubleshootContext(config=btrfs_config()),
            run=run,
        )
        assert facts.newest_snapshots == {}

    def test_unknown_when_the_connection_fails(self) -> None:
        run = FakeCommandRunner().on("ls", returns=paramiko.SSHException("closed"))
        facts = ts.gather_remediation_facts(
            _INVALID_DST,
            rem.TroubleshootContext(config=btrfs_config()),
            run=run,
        )
        assert facts.newest_snapshots == {}

    def test_only_lists_for_a_latest_fix(self) -> None:
        run = FakeCommandRunner()
        issues = _issues(dst_ep_errors=[DestinationEndpointErrorKind.NOT_WRITABLE])
        facts = ts.gather_remediation_facts(
            issues,
            rem.TroubleshootContext(config=btrfs_config()),
            run=run,
        )
        assert facts.newest_snapshots == {}
        assert run.commands == []
