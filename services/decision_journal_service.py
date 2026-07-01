"""Decision journal service.

This service records human/system decisions for traceability only. It does not
trigger commands or alter operational state.
"""

from repositories import decision_journal_repo

VALID_DECISION_TYPES = {
    "mantener",
    "observar",
    "reducir_riesgo",
    "pausar",
    "desactivar",
    "reactivar",
    "aumentar_riesgo",
    "cerrar_posiciones",
}

VALID_REASON_CATEGORIES = {
    "drawdown",
    "racha_perdedora",
    "degradacion_metricas",
    "baja_data",
    "error_ejecucion",
    "evento_macro",
    "regla_del_sistema",
    "decision_emocional",
    "manual_override",
}


class DecisionValidationError(ValueError):
    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _validate_entry(entry: dict) -> None:
    decision_type = str(entry.get("decision_type") or "").strip()
    reason_category = str(entry.get("reason_category") or "").strip()
    if decision_type not in VALID_DECISION_TYPES:
        raise DecisionValidationError("decision_type invalido", "invalid_decision_type")
    if reason_category not in VALID_REASON_CATEGORIES:
        raise DecisionValidationError("reason_category invalido", "invalid_reason_category")

    snapshot_id = entry.get("metrics_snapshot_id")
    if snapshot_id not in (None, ""):
        try:
            snapshot_id = int(snapshot_id)
        except Exception as exc:
            raise DecisionValidationError("metrics_snapshot_id invalido", "invalid_metrics_snapshot_id") from exc
        if not decision_journal_repo.metrics_snapshot_exists(snapshot_id):
            raise DecisionValidationError("metrics_snapshot_id no existe", "metrics_snapshot_not_found")


def create_decision(entry: dict) -> dict:
    normalized = dict(entry or {})
    normalized["decision_type"] = str(normalized.get("decision_type") or "").strip()
    normalized["reason_category"] = str(normalized.get("reason_category") or "").strip()
    if normalized.get("platform"):
        normalized["platform"] = str(normalized["platform"]).upper()
    _validate_entry(normalized)
    return decision_journal_repo.create(normalized)


def list_decisions(filters: dict) -> list[dict]:
    return decision_journal_repo.list_entries(
        strategy_id=filters.get("strategy_id"),
        account_key=filters.get("account_key"),
        account_id=filters.get("account_id"),
        decision_type=filters.get("decision_type"),
        limit=int(filters.get("limit") or 100),
    )


def get_decision(decision_id: int) -> dict | None:
    return decision_journal_repo.get(decision_id)


def update_outcome(decision_id: int, payload: dict) -> dict | None:
    return decision_journal_repo.update_outcome(
        decision_id,
        outcome_7d=payload.get("outcome_7d"),
        outcome_30d=payload.get("outcome_30d"),
    )

