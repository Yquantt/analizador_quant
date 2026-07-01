from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import database


def fetch_account_data(data_folder: str | Path, account: dict) -> dict:
    folder = Path(data_folder)
    login = str(account.get("login") or account.get("id") or "")
    platform = str(account.get("platform", "MT4")).upper()
    account_type = str(account.get("type") or account.get("account_type") or "real").lower()

    snapshot_file, snapshot = _read_latest_account_snapshot(folder, login)
    server = str(snapshot.get("server") or account.get("server") or "unknown")
    currency = str(snapshot.get("currency") or account.get("currency") or "")
    snapshot_login = str(snapshot.get("account") or snapshot.get("account_id") or login)
    file_login = login if _matches_login({"account": snapshot_login}, login) else snapshot_login
    account_key = database.resolve_account_key(
        file_login,
        platform=str(snapshot.get("platform") or platform),
        server=server,
        account_type=account_type,
        alias=str(snapshot.get("account_label") or account.get("label") or file_login),
        currency=currency,
    )

    positions_file, positions = _read_latest_open_positions(folder, file_login, account_type, platform)
    return {
        "account_key": account_key,
        "login": file_login,
        "platform": str(snapshot.get("platform") or platform),
        "server": server,
        "currency": currency,
        "snapshot_file": str(snapshot_file) if snapshot_file else "",
        "positions_file": str(positions_file) if positions_file else "",
        "snapshot": snapshot,
        "positions": positions,
    }


def sync_account(data_folder: str | Path, account: dict) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    source = ""
    account_key = None
    try:
        data = fetch_account_data(data_folder, account)
        account_key = data["account_key"]
        positions = data["positions"]
        source = data.get("positions_file") or data.get("snapshot_file") or "monitor_files"
        changed_positions = update_open_positions(account_key, positions)
        snapshot_saved = save_snapshot(account_key, data["snapshot"])
        movements = sync_deposits_withdrawals(data_folder, account_key, data["login"])
        database.log_import_run(
            account_key,
            source,
            rows_processed=len(positions) + (1 if data["snapshot"] else 0) + movements["processed"],
            rows_inserted=changed_positions + (1 if snapshot_saved else 0) + movements["inserted"],
            status="ok",
            started_at=started,
        )
        return {
            "ok": True,
            "account_key": account_key,
            "positions": len(positions),
            "positions_changed": changed_positions,
            "snapshot_saved": snapshot_saved,
            "movements": movements,
        }
    except Exception as exc:
        database.log_import_run(
            account_key,
            source or "monitor_files",
            rows_processed=0,
            rows_inserted=0,
            status="error",
            error_log={"error": str(exc)},
            started_at=started,
        )
        return {"ok": False, "account_key": account_key, "error": str(exc)}


def sync_all(data_folder: str | Path, accounts: list[dict]) -> list[dict]:
    return [sync_account(data_folder, account) for account in accounts]


def update_open_positions(account_key: str, positions: list[dict]) -> int:
    return database.upsert_open_positions(account_key, positions)


def save_snapshot(account_key: str, data: dict) -> bool:
    if not data:
        return False
    return database.save_account_snapshot(account_key, data)


def sync_deposits_withdrawals(data_folder: str | Path, account_key: str, login: str) -> dict:
    folder = Path(data_folder)
    processed = 0
    inserted = 0
    for path in folder.glob(f"trades_*_{login}.csv"):
        for row in _read_csv_rows(path):
            comment = str(row.get("comment") or row.get("Comment") or "").lower()
            trade_type = str(row.get("type") or row.get("Type") or row.get("action") or "").lower()
            if trade_type in ("buy", "sell") and not any(token in comment for token in ("deposit", "withdraw", "balance", "credit")):
                continue
            amount = row.get("profit") or row.get("Profit") or row.get("net_profit") or row.get("NetProfit") or row.get("amount")
            if amount in (None, ""):
                continue
            processed += 1
            value = float(str(amount).replace(",", "."))
            movement = {
                "timestamp": row.get("close_time") or row.get("CloseTime") or row.get("timestamp"),
                "amount": value,
                "type": "deposit" if value >= 0 else "withdrawal",
                "description": row.get("comment") or row.get("Comment") or trade_type,
            }
            if database.upsert_deposit_withdrawal(account_key, movement):
                inserted += 1
    return {"processed": processed, "inserted": inserted}


def _read_latest_account_snapshot(folder: Path, login: str) -> tuple[Path | None, dict]:
    candidates = _candidate_files(folder, "account_history", login)
    for path in candidates:
        if path.suffix.lower() == ".json":
            payload = _read_json(path)
            snapshot = payload.get("snapshot", payload)
            if _matches_login(snapshot, login):
                return path, snapshot
        else:
            rows = _read_csv_rows(path)
            if rows:
                rows = [r for r in rows if _matches_login(r, login)] or rows
                return path, rows[-1]
    return None, {}


def _read_latest_open_positions(folder: Path, login: str, account_type: str, platform: str = "MT4") -> tuple[Path | None, list[dict]]:
    candidates = _candidate_files(folder, "open_trades", login)
    if str(platform).upper() != "MT5":
        candidates.extend(sorted(folder.glob(f"open_trades_{account_type.upper()}.*"), key=lambda p: p.stat().st_mtime, reverse=True))
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.suffix.lower() == ".json":
            payload = _read_json(path)
            if not _matches_login(payload, login):
                continue
            rows = payload.get("trades", payload.get("positions", []))
            if isinstance(rows, list):
                return path, rows
        else:
            rows = _read_csv_rows(path)
            keyed_rows = [row for row in rows if _matches_login(row, login)]
            if keyed_rows:
                return path, keyed_rows
            if rows and any(row.get("account") or row.get("account_id") or row.get("account_number") for row in rows):
                continue
            return path, rows
    return None, []


def _candidate_files(folder: Path, prefix: str, login: str) -> list[Path]:
    patterns = []
    for alias in _login_aliases(login):
        patterns.extend([
            f"{prefix}_*_{alias}.json",
            f"{prefix}_*_{alias}.csv",
            f"{prefix}_{alias}.json",
            f"{prefix}_{alias}.csv",
        ])
    patterns.extend([
        f"{prefix}.json",
        f"{prefix}.csv",
    ])
    files = []
    for pattern in patterns:
        files.extend(folder.glob(pattern))
    return sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)


def _read_json(path: Path) -> dict:
    last_error = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "latin1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return json.load(f)
        except Exception as exc:
            last_error = exc
    raise last_error


def _read_csv_rows(path: Path) -> list[dict]:
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "latin1"):
        try:
            with open(path, "r", encoding=encoding, newline="") as f:
                sample = f.read(2048)
                f.seek(0)
                dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
                return [dict(row) for row in csv.DictReader(f, dialect=dialect)]
        except Exception:
            continue
    return []


def _matches_login(row: dict, login: str) -> bool:
    if not login:
        return True
    value = str(row.get("account") or row.get("account_id") or row.get("account_number") or "")
    return value == "" or value in _login_aliases(login)


def _login_aliases(login: str) -> list[str]:
    aliases = [str(login)]
    try:
        numeric = int(str(login))
        if numeric < 0:
            aliases.append(str(numeric + 2**32))
        elif numeric > 2**31 - 1:
            aliases.append(str(numeric - 2**32))
    except Exception:
        pass
    return list(dict.fromkeys(aliases))
