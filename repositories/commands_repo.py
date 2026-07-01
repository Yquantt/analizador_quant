"""Command repository backed by the existing SQLite database module."""

import database


def create(command: dict) -> int:
    return database.create_command(command)


def log(command: dict) -> int:
    return database.log_command(command)


def list_commands(limit: int = 100, active_only: bool = False) -> list[dict]:
    return database.get_commands(limit, active_only=active_only)


def mark_sent(command_ids: list[int]) -> None:
    database.mark_commands_sent(command_ids)


def acknowledge(command_id: int, response: dict | str | None = None) -> bool:
    return database.acknowledge_command(command_id, response)


def complete(command_id: int, status: str, response=None, error_message: str | None = None) -> bool:
    return database.complete_command(command_id, status, response=response, error_message=error_message)


def clear_active() -> int:
    return database.clear_active_commands()


def resolve(command_id: int, result: str) -> None:
    database.resolve_command(command_id, result)


def list_log(limit: int = 100) -> list[dict]:
    return database.get_command_log(limit)


def encode_result(result) -> str:
    return database.encode_result(result)

