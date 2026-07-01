"""Strategy versions repository."""

import database


def create(data: dict) -> dict:
    return database.create_strategy_version(data)


def list_versions(strategy_id: str | None = None, limit: int = 500) -> list[dict]:
    return database.get_strategy_versions(strategy_id=strategy_id, limit=limit)


def exists(version_id: int) -> bool:
    return database.strategy_version_exists(version_id)


def link_instance(instance_id: str, strategy_version_id: int) -> dict | None:
    return database.link_strategy_instance_version(instance_id, strategy_version_id)

