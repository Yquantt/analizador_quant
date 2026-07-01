"""Deployment events repository."""

import database


def create(data: dict) -> dict:
    return database.create_deployment_event(data)


def list_events(
    strategy_id: str | None = None,
    instance_id: str | None = None,
    account_key: str | None = None,
    limit: int = 500,
) -> list[dict]:
    return database.get_deployment_events(
        strategy_id=strategy_id,
        instance_id=instance_id,
        account_key=account_key,
        limit=limit,
    )

