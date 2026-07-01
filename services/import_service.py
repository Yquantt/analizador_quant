"""Incremental trade import service.

This module is the target home for closed-trade import logic. The current safe
refactor keeps the legacy implementation callable from ``app.py`` while routes
and scheduler depend on this named service boundary.
"""


def sync_closed_trades_incremental(legacy_sync, data_folder=None) -> dict:
    return legacy_sync(data_folder)

