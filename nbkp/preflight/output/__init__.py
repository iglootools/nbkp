"""Human and JSON rendering of preflight results and their remediation steps."""

from nbkp.preflight.output.check import format_human_check
from nbkp.preflight.output.messages import format_problem, format_problems
from nbkp.preflight.output.remediation import RemediationFacts, TroubleshootContext
from nbkp.preflight.output.troubleshoot import (
    TroubleshootIssue,
    collect_issues,
    format_human_troubleshoot,
    troubleshoot_json,
)

__all__ = [
    "RemediationFacts",
    "TroubleshootContext",
    "TroubleshootIssue",
    "collect_issues",
    "format_human_check",
    "format_human_troubleshoot",
    "format_problem",
    "format_problems",
    "troubleshoot_json",
]
