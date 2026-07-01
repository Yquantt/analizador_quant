"""Audit application service."""

from repositories import audit_repo


def log_command_rejection(reason: str, message: str, payload: dict) -> None:
    audit_repo.log_event(
        event_type="command_failed",
        entity_type="command_request",
        entity_id=payload.get("system") or payload.get("system_id"),
        account_key=payload.get("account_key") or payload.get("account_composite_key"),
        severity="warning",
        message=f"Command rejected: {message}",
        payload={**payload, "reason": reason},
    )


def list_events(filters: dict, limit: int = 500) -> list[dict]:
    return audit_repo.list_events(
        event_type=filters.get("event_type"),
        entity_type=filters.get("entity_type"),
        entity_id=filters.get("entity_id"),
        account_key=filters.get("account_key"),
        severity=filters.get("severity"),
        limit=limit,
    )

