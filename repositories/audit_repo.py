"""Audit repository backed by the existing SQLite database module."""

import database


def log_event(
    event_type: str,
    entity_type: str,
    entity_id: str | None = None,
    account_key: str | None = None,
    severity: str = "info",
    message: str | None = None,
    payload: dict | None = None,
) -> int:
    return database.log_audit_event(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        account_key=account_key,
        severity=severity,
        message=message,
        payload=payload,
    )


def list_events(
    event_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    account_key: str | None = None,
    severity: str | None = None,
    limit: int = 500,
) -> list[dict]:
    return database.get_audit_events(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        account_key=account_key,
        severity=severity,
        limit=limit,
    )

