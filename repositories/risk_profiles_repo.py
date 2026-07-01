"""Risk profiles repository."""

import database


def create(data: dict) -> dict:
    return database.create_risk_profile(data)


def list_profiles(limit: int = 500) -> list[dict]:
    return database.get_risk_profiles(limit=limit)


def exists(profile_id: int) -> bool:
    return database.risk_profile_exists(profile_id)


def link_instance(instance_id: str, risk_profile_id: int, deployment_state: str | None = None) -> dict | None:
    return database.link_strategy_instance_risk_profile(instance_id, risk_profile_id, deployment_state)

