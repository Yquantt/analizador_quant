"""Broker-local synchronization service."""

from pathlib import Path

import sync_broker


def sync_all(data_folder: Path, accounts: list[dict]) -> dict:
    return sync_broker.sync_all(data_folder, accounts)

