"""Metrics repository backed by the existing SQLite database module."""

import database


def save_system_metrics(metrics: list[dict]) -> None:
    database.save_system_metrics(metrics)


def save_strategy_snapshots(snapshots: list[dict]) -> int:
    return database.save_strategy_metric_snapshots(snapshots)


def list_strategy_snapshots(**kwargs) -> list[dict]:
    return database.get_strategy_metric_snapshots(**kwargs)


def list_strategy_definitions(limit: int = 1000) -> list[dict]:
    return database.get_strategy_definitions(limit)


def list_strategy_instances(strategy_id: str | None = None, limit: int = 2000) -> list[dict]:
    return database.get_strategy_instances(strategy_id, limit)


def temporal_breakdown() -> dict:
    return database.get_temporal_performance_breakdown()


def pnl_cost_breakdown() -> dict:
    return database.get_pnl_cost_breakdown()

