"""Trade repository backed by the existing SQLite database module."""

import database


def upsert(trades: list[dict], account_id: str, platform: str) -> int:
    return database.upsert_trades(trades, account_id, platform)


def upsert_closed_only(trades: list[dict]) -> int:
    return database.upsert_closed_trades_only(trades)


def replace_all(trades: list[dict]) -> int:
    return database.replace_trades(trades)


def list_trades(account_id: str | None = None, magic: int | None = None) -> list[dict]:
    return database.get_trades(account_id, magic)


def count_closed() -> int:
    return database.count_closed_trades()


def raw_payload_hash(raw_payload: dict) -> str:
    return database.raw_payload_hash(raw_payload)


def start_import_run(account_key: str | None, source_file: str, hash_file: str | None = None) -> int:
    return database.start_import_run(account_key, source_file, hash_file=hash_file)


def finish_import_run(import_run_id: int, rows_processed: int, rows_inserted: int, status: str, error_log=None) -> None:
    database.finish_import_run(import_run_id, rows_processed, rows_inserted, status, error_log)


def record_raw_import(
    import_run_id: int | None,
    source_file: str,
    platform: str,
    account_key: str | None,
    account_id: str | None,
    row_number: int,
    raw_payload: dict,
    status: str,
    error_message: str | None = None,
) -> int:
    return database.record_raw_trade_import(
        import_run_id,
        source_file,
        platform,
        account_key,
        account_id,
        row_number,
        raw_payload,
        status,
        error_message,
    )


def list_raw_imports(import_run_id: int | None = None, limit: int = 500) -> list[dict]:
    return database.get_raw_trade_imports(import_run_id, limit)


def list_import_conflicts(limit: int = 500) -> list[dict]:
    return database.get_trade_import_conflicts(limit)


def get_file_state(source_file: str) -> dict | None:
    return database.get_trade_file_state(source_file)


def upsert_file_state(source_file: str, **kwargs) -> None:
    database.upsert_trade_file_state(source_file, **kwargs)


def list_file_states(limit: int = 1000) -> list[dict]:
    return database.get_trade_file_states(limit)


def has_data() -> bool:
    return database.db_has_data()

