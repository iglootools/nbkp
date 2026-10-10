"""Rendering details of `preflight troubleshoot`: markup safety, suggested
commands, warnings, and the JSON form."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from nbkp.clihelpers.invocation import Invocation
from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.preflight.output import (
    TroubleshootContext,
    collect_issues,
    print_human_troubleshoot,
    troubleshoot_json,
)
from nbkp.preflight.status import (
    HostToolCapabilities,
    MountToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SshEndpointToolNeeds,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)


def _render(
    vol_statuses: dict[str, VolumeStatus],
    config: Config,
    ctx: TroubleshootContext | None = None,
    ssh: dict[str, SshEndpointStatus] | None = None,
) -> str:
    buf = StringIO()
    print_human_troubleshoot(
        ssh or {},
        vol_statuses,
        {},
        config,
        console=Console(file=buf, width=200, color_system=None),
        context=ctx,
    )
    return buf.getvalue()


def _volume_status(vol: LocalVolume, error: VolumeError) -> VolumeStatus:
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
            {"v": _volume_status(vol, VolumeError.SENTINEL_NOT_FOUND)}, config
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
            {"v": _volume_status(vol, VolumeError.PASSPHRASE_NOT_AVAILABLE)}, config
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
            {"usb": _volume_status(vol, VolumeError.VOLUME_NOT_MOUNTED)}, config, ctx
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
            {"usb": _volume_status(vol, VolumeError.POLKIT_RULES_MISSING)}, config, ctx
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
