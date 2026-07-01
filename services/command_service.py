"""Command application service."""

from datetime import datetime, timezone

from repositories import accounts_repo, commands_repo
from services import audit_service


def rejection(reason: str, message: str, status_code: int, payload: dict) -> tuple[dict, int]:
    try:
        audit_service.log_command_rejection(reason, message, payload)
    except Exception:
        pass
    return {"ok": False, "error": message, "reason": reason}, status_code


def find_duplicate(account_key: str, system_id: str, magic, action: str) -> dict | None:
    for command in commands_repo.list_commands(500, active_only=True):
        params = command.get("parameters") or {}
        same_system = params.get("system_id") == system_id or command.get("system") == system_id
        same_magic = str(command.get("magic")) == str(magic)
        if (
            command.get("account_composite_key") == account_key
            and command.get("action") == action
            and (same_system or same_magic)
        ):
            return command
    return None


def open_positions_for_magic(account_key: str, magic, safe_int) -> list[dict]:
    positions = accounts_repo.get_open_positions(account_key)
    magic_int = safe_int(magic)
    if magic_int <= 0:
        return positions
    return [pos for pos in positions if safe_int(pos.get("magic_number") or pos.get("magic")) == magic_int]


def create_dashboard_command(
    body: dict,
    portfolio: dict,
    valid_actions: set[str],
    risk_increase_actions: set[str],
    account_matches_identifier,
    get_account_key,
    safe_int,
) -> tuple[dict, int]:
    system = str(body.get("system", "")).strip()
    action = str(body.get("action", "")).strip()
    account_id = str(body.get("account_id", "")).strip()
    account_key = str(body.get("account_key", "")).strip()
    platform = str(body.get("platform", "")).upper()

    if not system or action not in valid_actions:
        return rejection(
            "invalid_request",
            "system o action invalido",
            400,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform},
        )

    matched_system = next((s for s in portfolio["systems"] if s["id"] == system or s["label"] == system), None)
    if not matched_system:
        return rejection(
            "system_not_found",
            "sistema no encontrado",
            404,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform},
        )

    if not account_id and account_key:
        matched_instance = next((i for i in matched_system["instances"] if account_matches_identifier(i, account_key)), None)
        if matched_instance:
            account_id = matched_instance["account_id"]

    if not account_id:
        if len(matched_system["instances"]) != 1:
            return rejection(
                "account_required",
                "account_id requerido para sistemas multi-cuenta",
                400,
                {"system": system, "action": action, "account_key": account_key, "platform": platform},
            )
        account_id = matched_system["instances"][0]["account_id"]

    instance = next((i for i in matched_system["instances"] if account_matches_identifier(i, account_id)), None)
    if not instance:
        return rejection(
            "account_instance_not_found",
            "el sistema no tiene instancia en esa cuenta",
            400,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform},
        )

    requested_platform = platform
    if platform in ("MT4", "MT5") and platform != str(instance["platform"]).upper():
        return rejection(
            "platform_mismatch",
            "platform no coincide con la cuenta seleccionada",
            400,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": requested_platform, "expected_platform": instance["platform"]},
        )
    if platform not in ("MT4", "MT5"):
        platform = str(instance["platform"]).upper()

    account_key = instance.get("account_key") or get_account_key(platform, instance.get("server", ""), account_id, instance.get("account_type", "REAL"))
    db_accounts = {row["id"]: row for row in accounts_repo.list_accounts(active_only=False)}
    if account_key not in db_accounts:
        return rejection(
            "account_not_found",
            "cuenta no existe en SQLite",
            400,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform},
        )
    if safe_int(matched_system.get("magic")) <= 0:
        return rejection(
            "magic_required",
            "comando operativo requiere magic valido",
            400,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform, "magic": matched_system.get("magic")},
        )
    if not any(account_matches_identifier(i, account_key) and safe_int(i.get("metrics", {}).get("trades")) >= 0 for i in matched_system["instances"]):
        return rejection(
            "magic_account_not_found",
            "magic no existe en esa cuenta",
            400,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform, "magic": matched_system.get("magic")},
        )
    positions = open_positions_for_magic(account_key, matched_system.get("magic"), safe_int)
    if not positions:
        return rejection(
            "no_open_positions",
            "el sistema no tiene posiciones abiertas en esa cuenta",
            409,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform, "magic": matched_system.get("magic")},
        )
    duplicate = find_duplicate(account_key, matched_system["id"], matched_system.get("magic"), action)
    if duplicate:
        return rejection(
            "duplicate_command",
            "ya existe un comando activo equivalente para esa cuenta y sistema",
            409,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform, "magic": matched_system.get("magic"), "duplicate_command_id": duplicate.get("id")},
        )
    quarantine_blocks = (
        instance.get("action") == "cuarentena"
        or matched_system.get("action") == "cuarentena"
        or matched_system.get("action_global") == "cuarentena"
    )
    if action in risk_increase_actions and quarantine_blocks:
        return rejection(
            "quarantine_blocks_risk_increase",
            "no se puede aumentar riesgo de un sistema en cuarentena",
            409,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform, "magic": matched_system.get("magic"), "classification": instance.get("action")},
        )
    metrics = instance.get("metrics") or {}
    if (
        instance.get("action") == "revision_manual"
        or matched_system.get("action") == "revision_manual"
        or metrics.get("risk_metrics_reliable") is False
        or metrics.get("balance_drawdown_reliable") is not True
    ):
        return rejection(
            "risk_metrics_unreliable",
            "comando bloqueado: falta DD real confiable sobre balance/equity",
            409,
            {
                "system": system,
                "action": action,
                "account_id": account_id,
                "account_key": account_key,
                "platform": platform,
                "magic": matched_system.get("magic"),
                "classification": instance.get("action"),
                "drawdown_basis": metrics.get("drawdown_basis"),
                "balance_drawdown_reliable": metrics.get("balance_drawdown_reliable"),
            },
        )
    is_real = str(instance.get("account_type", "")).upper() == "REAL"
    confirmed = body.get("confirm") is True or body.get("confirmed") is True or str(body.get("confirmation", "")).upper() == "CONFIRM"
    if is_real and action in risk_increase_actions and not confirmed:
        return rejection(
            "real_account_confirmation_required",
            "cuenta REAL requiere confirmacion explicita para aumentar riesgo",
            409,
            {"system": system, "action": action, "account_id": account_id, "account_key": account_key, "platform": platform, "magic": matched_system.get("magic"), "account_type": instance.get("account_type")},
        )
    cmd = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "system": matched_system["label"],
        "magic": matched_system["magic"],
        "action": action,
        "parameters": {
            "system_id": matched_system["id"],
            "requested_action": action,
        },
        "account_composite_key": account_key,
        "account_id": account_id,
        "account_label": instance["account_label"],
        "platform": platform,
        "status": "pending",
        "source": "dashboard",
    }
    command_id = commands_repo.create(cmd)
    cmd["id"] = command_id
    cmd["command_id"] = command_id
    commands_repo.log(cmd)
    return {"ok": True, "command": cmd}, 200
