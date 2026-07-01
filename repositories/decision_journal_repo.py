"""Decision journal repository backed by SQLite."""

import database


def create(entry: dict) -> dict:
    return database.create_decision_journal_entry(entry)


def get(decision_id: int) -> dict | None:
    return database.get_decision_journal_entry(decision_id)


def list_entries(
    strategy_id: str | None = None,
    account_key: str | None = None,
    account_id: str | None = None,
    decision_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    return database.get_decision_journal_entries(
        strategy_id=strategy_id,
        account_key=account_key,
        account_id=account_id,
        decision_type=decision_type,
        limit=limit,
    )


def update_outcome(decision_id: int, outcome_7d=None, outcome_30d=None) -> dict | None:
    return database.update_decision_journal_outcome(decision_id, outcome_7d=outcome_7d, outcome_30d=outcome_30d)


def metrics_snapshot_exists(snapshot_id: int) -> bool:
    return database.strategy_metric_snapshot_exists(snapshot_id)

