"""Rendering details of `preflight troubleshoot`: markup safety, suggested
commands, warnings, and the JSON form."""

from io import StringIO
from pathlib import Path

from rich.console import Console

from nbkp.clidisplay.invocation import Invocation
from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    SshConnectionOptions,
    SshEndpoint,
)
from nbkp.disks import MountToolCapabilities
from nbkp.preflight.output import (
    RemediationFacts,
    TroubleshootContext,
    TroubleshootIssue,
    collect_issues,
    format_human_troubleshoot,
    troubleshoot_json,
)
from nbkp.preflight.status.ssh import (
    HostToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SshEndpointToolNeeds,
)
from nbkp.preflight.status.volume import (
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)
from nbkp.preflight.testkit import troubleshoot_config, troubleshoot_data


def _render(
    vol_statuses: dict[str, VolumeStatus],
    config: Config,
    ctx: TroubleshootContext | None = None,
    ssh: dict[str, SshEndpointStatus] | None = None,
) -> str:
    buf = StringIO()
    Console(file=buf, width=200, color_system=None).print(
        format_human_troubleshoot(
            ssh or {},
            vol_statuses,
            {},
            config,
            context=ctx,
        )
    )
    return buf.getvalue()


def _volume_status(vol: LocalVolume, error: VolumeErrorKind) -> VolumeStatus:
    return VolumeStatus(
        slug=vol.slug,
        config=vol,
        ssh_endpoint_status=SshEndpointStatus(
            slug="localhost", diagnostics=SshEndpointDiagnostics(), errors=[]
        ),
        diagnostics=VolumeDiagnostics(),
        errors=[error],
    )


class TestMarkupSafety:
    def test_bracketed_path_is_printed_verbatim(self) -> None:
        """Regression: config paths were interpolated into markup strings."""
        vol = LocalVolume(slug="v", path="/mnt/[bold]disk")
        config = Config(volumes={"v": vol})
        out = _render(
            {"v": _volume_status(vol, VolumeErrorKind.SENTINEL_NOT_FOUND)}, config
        )
        assert "/mnt/[bold]disk" in out

    def test_bracketed_passphrase_id_is_printed_verbatim(self) -> None:
        vol = LocalVolume(
            slug="v",
            path="/mnt/v",
            mount=MountConfig(
                device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
                encryption=LuksEncryptionConfig(passphrase_id="id[red]x"),
            ),
        )
        config = Config(volumes={"v": vol})
        out = _render(
            {"v": _volume_status(vol, VolumeErrorKind.PASSPHRASE_NOT_AVAILABLE)}, config
        )
        assert "keyring set nbkp 'id[red]x'" in out


class TestSuggestedCommands:
    def test_mount_suggestion_carries_invocation_flags(self) -> None:
        vol = LocalVolume(
            slug="usb",
            path="/mnt/usb",
            mount=MountConfig(device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        )
        config = Config(volumes={"usb": vol})
        ctx = TroubleshootContext(
            config=config,
            invocation=Invocation(
                config_path=Path("/home/me/nbkp/config.yaml"),
                locations=("home",),
                cwd=Path("/home/me"),
            ),
        )
        out = _render(
            {"usb": _volume_status(vol, VolumeErrorKind.VOLUME_NOT_MOUNTED)},
            config,
            ctx,
        )
        assert "nbkp disks mount -n usb -c nbkp/config.yaml -l home" in out

    def test_polkit_fix_uses_supplied_local_user(self) -> None:
        vol = LocalVolume(
            slug="usb",
            path="/mnt/usb",
            mount=MountConfig(device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        )
        config = Config(volumes={"usb": vol})
        ctx = TroubleshootContext(config=config, local_user="alice")
        out = _render(
            {"usb": _volume_status(vol, VolumeErrorKind.POLKIT_RULES_MISSING)},
            config,
            ctx,
        )
        assert 'subject.user == "alice"' in out
        assert "nbkp disks setup-auth -u alice" in out


def _warning_ssh_status() -> SshEndpointStatus:
    return SshEndpointStatus.from_diagnostics(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(
            host_tools=HostToolCapabilities(
                has_rsync=True,
                rsync_version_ok=True,
                has_btrfs=True,
                has_stat=True,
                has_findmnt=True,
            ),
            mount_tools=MountToolCapabilities(
                has_udisksctl=True,
                udisksd_running=True,
                has_btrfs_module=False,
                has_findmnt=True,
                has_lsblk=True,
            ),
        ),
        needs=SshEndpointToolNeeds(has_mount_volumes=True, has_btrfs_mount=True),
    )


class TestWarnings:
    def test_warning_rendered_with_advice(self) -> None:
        out = _render({}, Config(), ssh={"localhost": _warning_ssh_status()})
        assert "warning: udisks2 btrfs module not installed" in out
        assert "udisks2-btrfs" in out
        assert "No issues found" not in out

    def test_json_marks_warning_kind(self) -> None:
        issues = collect_issues({"localhost": _warning_ssh_status()}, {}, {})
        data = troubleshoot_json(
            issues, TroubleshootContext(config=Config()), has_fatal_errors=False
        )
        [issue] = data["issues"]  # type: ignore[misc]
        assert issue["kind"] == "warning"
        assert issue["code"] == "UDISKS_BTRFS_MODULE_MISSING"
        assert "udisks2-btrfs" in issue["remediation"]


class TestLatestSnapshotWritable:
    def _issues_and_ctx(self) -> tuple[list[TroubleshootIssue], TroubleshootContext]:
        config = troubleshoot_config()
        sync = troubleshoot_data(config).sync_statuses["latest-snapshot-writable"]
        return (
            collect_issues({}, {}, {sync.slug: sync}),
            TroubleshootContext(config=config, facts=RemediationFacts()),
        )

    def test_fix_makes_the_snapshot_read_only_again(self) -> None:
        issues, ctx = self._issues_and_ctx()
        data = troubleshoot_json(issues, ctx, has_fatal_errors=False)
        [issue] = data["issues"]  # type: ignore[misc]
        assert (issue["kind"], issue["code"], issue["severity"]) == (
            "warning",
            "LATEST_SNAPSHOT_WRITABLE",
            "warning",
        )
        assert (
            "btrfs property set /mnt/usb-16/snapshots/2024-01-01T00:00:00.000Z ro true"
            in issue["remediation"]
        )


class TestAuthFailedFix:
    def _render_auth_failed(self, endpoint: SshEndpoint) -> str:
        config = Config(ssh_endpoints={endpoint.slug: endpoint})
        status = SshEndpointStatus.from_diagnostics(
            slug=endpoint.slug,
            diagnostics=SshEndpointDiagnostics(
                ssh_reachable=False,
                ssh_auth_failed=True,
                ssh_error="host key for nas.lan not found in known_hosts",
            ),
        )
        return _render({}, config, ssh={endpoint.slug: status})

    def test_suggests_accepting_an_unknown_host_key(self) -> None:
        out = self._render_auth_failed(
            SshEndpoint(slug="nas", host="nas.lan", port=2222, user="bk")
        )
        assert "If the host key is not known yet" in out
        assert "ssh -p 2222 bk@nas.lan true" in out
        assert "ssh-keygen -R '[nas.lan]:2222'" in out

    def test_targets_the_configured_known_hosts_file(self) -> None:
        out = self._render_auth_failed(
            SshEndpoint(
                slug="nas",
                host="nas.lan",
                connection_options=SshConnectionOptions(
                    known_hosts_file="/etc/nbkp/known_hosts"
                ),
            )
        )
        assert "ssh -o UserKnownHostsFile=/etc/nbkp/known_hosts nas.lan true" in out
        assert "ssh-keygen -f /etc/nbkp/known_hosts -R nas.lan" in out
