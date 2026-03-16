"""Preflight output formatting: check tables and troubleshoot instructions."""

from .check import print_human_check
from .troubleshoot import print_human_troubleshoot

__all__ = [
    "print_human_check",
    "print_human_troubleshoot",
]
