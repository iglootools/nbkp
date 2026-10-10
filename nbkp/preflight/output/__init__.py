"""Preflight output formatting: check tables and troubleshoot instructions."""

from .check import print_human_check
from .remediation import RemediationFacts, TroubleshootContext
from .troubleshoot import (
    TroubleshootIssue,
    collect_issues,
    print_human_troubleshoot,
    troubleshoot_json,
)

__all__ = [
    "RemediationFacts",
    "TroubleshootContext",
    "TroubleshootIssue",
    "collect_issues",
    "print_human_check",
    "print_human_troubleshoot",
    "troubleshoot_json",
]
