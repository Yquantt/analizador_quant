"""Account repository backed by the existing SQLite database module."""

import database


def sync_from_config(accounts: list[dict]) -> int:
    return database.sync_accounts_from_config(accounts)


def upsert(account: dict) -> dict:
    return database.upsert_account(account)


def list_accounts(active_only: bool = True) -> list[dict]:
    return database.get_accounts(active_only=active_only)


def resolve_key(
    identifier: str,
    platform: str | None = None,
    server: str | None = None,
    account_type: str | None = None,
    alias: str | None = None,
    currency: str | None = None,
) -> str:
    return database.resolve_account_key(identifier, platform, server, account_type, alias, currency)


def get_open_positions(account_key: str) -> list[dict]:
    return database.get_open_positions(account_key)


def get_snapshots(account_key: str, limit: int = 500) -> list[dict]:
    return database.get_account_snapshots(account_key, limit)


def get_equity_points(account_key: str, limit: int = 2000) -> list[dict]:
    return database.get_equity_points(account_key, limit)


def upsert_discovered(account: dict) -> dict:
    return database.upsert_discovered_account(account)


def list_discovered(status: str | None = None, limit: int = 1000) -> list[dict]:
    return database.get_discovered_accounts(status=status, limit=limit)


def get_discovered(discovered_id: int) -> dict | None:
    return database.get_discovered_account(discovered_id)


def find_discovered(account_id: str, platform: str, server: str, account_type: str) -> dict | None:
    return database.find_discovered_account(account_id, platform, server, account_type)


def update_discovered_status(discovered_id: int, status: str, notes: str | None = None) -> dict:
    return database.update_discovered_account_status(discovered_id, status, notes)

