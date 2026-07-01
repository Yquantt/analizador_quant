"""Data quality service."""

import time
from pathlib import Path

import database
from repositories import accounts_repo, trades_repo


def status_for(checks: list[dict]) -> str:
    statuses = {check.get("status") for check in checks}
    if "critical" in statuses:
        return "critical"
    if "warning" in statuses:
        return "warning"
    return "ok"


def csv_freshness_check(data_folder: Path, max_age_hours: int = 24) -> dict:
    patterns = [
        "trades_*.csv",
        "open_trades_*.csv",
        "running_eas_*.csv",
        "account_history_*.csv",
    ]
    now = time.time()
    stale = []
    files_seen = 0
    for pattern in patterns:
        for path in data_folder.glob(pattern):
            files_seen += 1
            age_hours = (now - path.stat().st_mtime) / 3600
            if age_hours > max_age_hours:
                stale.append({"file": path.name, "age_hours": round(age_hours, 2)})
    if files_seen == 0:
        return {
            "name": "stale_csv_files",
            "status": "warning",
            "count": 0,
            "message": "No CSV monitor files found",
            "payload": {"data_folder": str(data_folder), "max_age_hours": max_age_hours},
        }
    return {
        "name": "stale_csv_files",
        "status": "warning" if stale else "ok",
        "count": len(stale),
        "message": "CSV files are fresh" if not stale else f"CSV files older than {max_age_hours} hours detected",
        "payload": {"data_folder": str(data_folder), "files_seen": files_seen, "stale": stale[:50]},
    }


def trade_file_state_quality_check() -> dict:
    try:
        states = trades_repo.list_file_states()
    except Exception as exc:
        return {
            "name": "trade_file_state",
            "status": "warning",
            "count": 1,
            "message": f"No se pudo leer trade_file_state: {exc}",
            "payload": {},
        }
    errored = [row for row in states if row.get("last_error")]
    return {
        "name": "trade_file_state",
        "status": "warning" if errored else "ok",
        "count": len(errored),
        "message": "Trade file imports are clean" if not errored else "Trade files ignored or failed during incremental import",
        "payload": {"errors": errored[:50]},
    }


def discovered_accounts_quality_checks(blocked_files: list[dict] | None = None) -> list[dict]:
    try:
        rows = accounts_repo.list_discovered()
    except Exception as exc:
        return [{
            "name": "discovered_accounts",
            "status": "warning",
            "count": 1,
            "message": f"No se pudo leer discovered_accounts: {exc}",
            "payload": {},
        }]
    pending = [row for row in rows if row.get("status") == "pending_approval"]
    rejected = [row for row in rows if row.get("status") == "rejected"]
    ignored = [row for row in rows if row.get("status") == "ignored"]
    blocked_files = blocked_files or []
    return [
        {
            "name": "discovered_accounts_pending",
            "status": "warning" if pending else "ok",
            "count": len(pending),
            "message": "No pending discovered accounts" if not pending else "Discovered accounts pending approval",
            "payload": {"accounts": pending[:50]},
        },
        {
            "name": "blocked_trade_files",
            "status": "warning" if blocked_files else "ok",
            "count": len(blocked_files),
            "message": "No blocked trade files" if not blocked_files else "Trade files blocked because account is not approved",
            "payload": {"files": blocked_files[:50]},
        },
        {
            "name": "rejected_accounts_seen_again",
            "status": "warning" if rejected else "ok",
            "count": len(rejected),
            "message": "No rejected accounts seen" if not rejected else "Rejected discovered accounts are still present in monitor files",
            "payload": {"accounts": rejected[:50]},
        },
        {
            "name": "ignored_accounts_seen_again",
            "status": "ok",
            "count": len(ignored),
            "message": "Ignored discovered accounts tracked",
            "payload": {"accounts": ignored[:50]},
        },
    ]


def build_status(
    data_folder: Path,
    blocked_files: list[dict] | None = None,
    csv_max_age_hours: int = 24,
    stale_positions_hours: int = 24,
    old_commands_minutes: int = 30,
    equity_gap_minutes: int = 180,
) -> dict:
    checks = database.get_data_quality_checks(
        stale_positions_hours=stale_positions_hours,
        old_commands_minutes=old_commands_minutes,
        equity_gap_minutes=equity_gap_minutes,
    )
    checks.append(csv_freshness_check(data_folder, csv_max_age_hours))
    checks.append(trade_file_state_quality_check())
    checks.extend(discovered_accounts_quality_checks(blocked_files))
    return {"status": status_for(checks), "checks": checks}

