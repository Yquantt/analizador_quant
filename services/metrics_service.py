"""Portfolio metrics service.

The pure calculation functions still live in ``app.py`` during this safe
extraction phase. Persistence and higher-level metric data access are isolated
here so route code does not call ``database.py`` directly.
"""

from repositories import metrics_repo


def persist_system_metrics(systems: list[dict], snapshot_rows: list[dict], rules_version: str) -> None:
    metric_rows = []
    for system in systems:
        metric_rows.append({
            "magic": system.get("magic"),
            "account_id": None,
            "metrics": system.get("metrics_global", {}),
            "action": system.get("action"),
            "rules_version": system.get("rules_version", rules_version),
        })
        for instance in system.get("instances", []):
            metric_rows.append({
                "magic": system.get("magic"),
                "account_id": instance.get("account_id"),
                "metrics": instance.get("metrics", {}),
                "action": instance.get("action"),
                "rules_version": instance.get("rules_version", rules_version),
            })
    metrics_repo.save_system_metrics(metric_rows)
    metrics_repo.save_strategy_snapshots(snapshot_rows)


def temporal_breakdown() -> dict:
    return metrics_repo.temporal_breakdown()


def pnl_cost_breakdown() -> dict:
    return metrics_repo.pnl_cost_breakdown()


def strategy_definitions(limit: int = 1000) -> list[dict]:
    return metrics_repo.list_strategy_definitions(limit)


def strategy_instances(strategy_id: str | None = None, limit: int = 2000) -> list[dict]:
    return metrics_repo.list_strategy_instances(strategy_id, limit)


def strategy_snapshots(**kwargs) -> list[dict]:
    return metrics_repo.list_strategy_snapshots(**kwargs)

