"""Fake check/troubleshoot data for manual testing."""

from .check import check_config as check_config, check_data as check_data
from .single import check_sync as check_sync, check_volume as check_volume
from .troubleshoot import (
    troubleshoot_config as troubleshoot_config,
    troubleshoot_data as troubleshoot_data,
)
