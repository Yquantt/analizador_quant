"""Strategy identity, version and deployment governance service."""

import database
from repositories import deployment_events_repo, risk_profiles_repo, strategy_versions_repo


class StrategyIdentityValidationError(ValueError):
    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _require(data: dict, field: str) -> None:
    if data.get(field) in (None, ""):
        raise StrategyIdentityValidationError(f"{field} requerido", f"missing_{field}")


def create_strategy_version(data: dict) -> dict:
    _require(data, "strategy_id")
    _require(data, "version_name")
    return strategy_versions_repo.create(dict(data))


def list_strategy_versions(filters: dict) -> list[dict]:
    return strategy_versions_repo.list_versions(
        strategy_id=filters.get("strategy_id"),
        limit=int(filters.get("limit") or 500),
    )


def create_risk_profile(data: dict) -> dict:
    _require(data, "name")
    return risk_profiles_repo.create(dict(data))


def list_risk_profiles(filters: dict) -> list[dict]:
    return risk_profiles_repo.list_profiles(limit=int(filters.get("limit") or 500))


def create_deployment_event(data: dict) -> dict:
    _require(data, "event_type")
    if data.get("strategy_version_id") not in (None, "") and not strategy_versions_repo.exists(int(data["strategy_version_id"])):
        raise StrategyIdentityValidationError("strategy_version_id no existe", "strategy_version_not_found")
    if data.get("risk_profile_id") not in (None, "") and not risk_profiles_repo.exists(int(data["risk_profile_id"])):
        raise StrategyIdentityValidationError("risk_profile_id no existe", "risk_profile_not_found")
    event = deployment_events_repo.create(dict(data))
    instance_id = data.get("instance_id")
    if instance_id and data.get("strategy_version_id") not in (None, ""):
        link_strategy_instance_version(instance_id, int(data["strategy_version_id"]))
    if instance_id and data.get("risk_profile_id") not in (None, ""):
        link_strategy_instance_risk_profile(instance_id, int(data["risk_profile_id"]), data.get("new_state"))
    return event


def list_deployment_events(filters: dict) -> list[dict]:
    return deployment_events_repo.list_events(
        strategy_id=filters.get("strategy_id"),
        instance_id=filters.get("instance_id"),
        account_key=filters.get("account_key"),
        limit=int(filters.get("limit") or 500),
    )


def link_strategy_instance_version(instance_id: str, strategy_version_id: int) -> dict:
    if not strategy_versions_repo.exists(int(strategy_version_id)):
        raise StrategyIdentityValidationError("strategy_version_id no existe", "strategy_version_not_found")
    row = strategy_versions_repo.link_instance(instance_id, int(strategy_version_id))
    if not row:
        raise StrategyIdentityValidationError("instance_id no existe", "strategy_instance_not_found")
    return row


def link_strategy_instance_risk_profile(instance_id: str, risk_profile_id: int, deployment_state: str | None = None) -> dict:
    if not risk_profiles_repo.exists(int(risk_profile_id)):
        raise StrategyIdentityValidationError("risk_profile_id no existe", "risk_profile_not_found")
    row = risk_profiles_repo.link_instance(instance_id, int(risk_profile_id), deployment_state)
    if not row:
        raise StrategyIdentityValidationError("instance_id no existe", "strategy_instance_not_found")
    return row


def enrich_systems(systems: list[dict]) -> list[dict]:
    instance_ids = [
        instance.get("instance_id")
        for system in systems
        for instance in system.get("instances", [])
        if instance.get("instance_id")
    ]
    if not instance_ids:
        return systems
    identity = database.get_strategy_identity_map(instance_ids)
    for system in systems:
        system_version = None
        system_risk = None
        system_state = None
        for instance in system.get("instances", []):
            row = identity.get(instance.get("instance_id")) or {}
            for key in ("strategy_version_id", "version_name", "parameters_hash", "risk_profile_id", "risk_profile_name", "deployment_state"):
                if row.get(key) is not None:
                    instance[key] = row.get(key)
            system_version = system_version or row.get("strategy_version_id")
            system_risk = system_risk or row.get("risk_profile_id")
            system_state = system_state or row.get("deployment_state")
            if row.get("version_name") and not system.get("version_name"):
                system["version_name"] = row.get("version_name")
            if row.get("parameters_hash") and not system.get("parameters_hash"):
                system["parameters_hash"] = row.get("parameters_hash")
            if row.get("risk_profile_name") and not system.get("risk_profile_name"):
                system["risk_profile_name"] = row.get("risk_profile_name")
        if system_version is not None:
            system["strategy_version_id"] = system_version
        if system_risk is not None:
            system["risk_profile_id"] = system_risk
        if system_state is not None:
            system["deployment_state"] = system_state
    return systems
