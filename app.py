"""
QA Portfolio Commander - servidor Flask local.

Modelo actual:
- Cuenta: entidad configurada en config.json.
- Sistema/EA: entidad global identificada por magic; si no hay magic se aísla por comentario + cuenta.
- Instancia: ejecución de un sistema dentro de una cuenta concreta.
"""

import glob
import json
import math
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_cors import CORS
from openai import AuthenticationError, OpenAI
CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "data_folder": r"C:\Users\marin\AppData\Roaming\MetaQuotes\Terminal\Common\Files\QuantAnalyzer",
    "mt4_data_folder": r"C:\Users\marin\AppData\Roaming\MetaQuotes\Terminal\Common\Files\QuantAnalyzer",
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",
    "accounts": [
        {
            "id": "123456",
            "type": "REAL",
            "label": "Broker A Principal",
            "platform": "MT4",
            "color": "#4CAF50",
        },
        {
            "id": "789012",
            "type": "DEMO",
            "label": "Pruebas Agresivas",
            "platform": "MT5",
            "color": "#2196F3",
        },
    ],
    "risk_thresholds": {
        "quarantine_pf_below": 1.0,
        "quarantine_dd_above": 50.0,
        "reduce_risk_dd_above": 35.0,
        "increase_risk_pf_above": 3.5,
        "increase_risk_dd_below": 25.0,
        "min_trades": 15,
    },
    "exclude_comment_patterns": [
        "deposited",
        "deposit",
        "withdrawal",
        "div.",
        "dividend",
        "balance",
        "credit",
        "bonus",
        "correction",
    ],
    "server_port": 5000,
    "auto_reload_seconds": 300,
}

# CSVs reales observados en Common/Files:
# - trades_DEMO_2089113323.csv
# - trades_REAL_2120003119.csv
# - trades_REAL_-294886245.csv
# - trades_TLXQ_-294886245.csv
# El refactor anterior solo aceptaba REAL/DEMO y cuentas positivas, por eso ignoraba TLXQ y cuentas negativas.
CSV_NAME_RE = re.compile(r"^trades_([^_]+)_(-?\d+)\.csv$", re.IGNORECASE)
COLUMN_MAP = {
    "Profit": "profit",
    "MagicNumber": "magic",
    "Magic": "magic",
    "OpenTime": "open_time",
    "CloseTime": "close_time",
    "Lots": "lots",
    "Symbol": "symbol",
    "Type": "type",
    "Comment": "comment",
    "OpenPrice": "open_price",
    "ClosePrice": "close_price",
    "Ticket": "ticket",
    "Swap": "swap",
    "Commission": "commission",
    "NetProfit": "net_profit",
    "Pips": "pips",
    "Account": "account",
    "AccountLabel": "account_label",
    "Broker": "broker",
    "Currency": "currency",
    "Platform": "platform",
}
NUMERIC_COLUMNS = ["profit", "swap", "commission", "net_profit", "pips", "lots", "open_price", "close_price"]
VALID_ACTIONS = {
    "close_by_magic",
    "reduce_lots",
    "increase_lots_25",
    "increase_lots_50",
    "set_max_lots",
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(loaded)
    cfg["accounts"] = normalize_accounts_config(cfg.get("accounts", []))
    return cfg


def normalize_accounts_config(accounts) -> list[dict]:
    normalized = []

    if isinstance(accounts, dict):
        for key, acc in accounts.items():
            item = dict(acc)
            item.setdefault("id", str(key))
            item.setdefault("type", item.get("label", key).upper() if str(item.get("label", "")).upper() in ("REAL", "DEMO") else "REAL")
            item.setdefault("label", str(item["id"]))
            item.setdefault("platform", "MT4")
            item.setdefault("color", "#4f8ef7")
            normalized.append(item)
    else:
        for acc in accounts:
            item = dict(acc)
            item.setdefault("id", str(item.get("account", item.get("label", "unknown"))))
            item.setdefault("type", "REAL")
            item.setdefault("label", item["id"])
            item.setdefault("platform", "MT4")
            item.setdefault("color", "#4f8ef7")
            normalized.append(item)

    for item in normalized:
        item["id"] = str(item["id"])
        item["type"] = str(item.get("type", "REAL")).upper()
        if item["type"] not in ("REAL", "DEMO"):
            item["type"] = "REAL"
        item["platform"] = str(item.get("platform", "MT4")).upper()
        if item["platform"] not in ("MT4", "MT5"):
            item["platform"] = "MT4"
        item["label"] = str(item.get("label", item["id"]))
        item["color"] = str(item.get("color", "#4f8ef7"))
        item["csv_pattern"] = f"trades_{item['type']}_{item['id']}.csv"

    return normalized


cfg = load_config()
app = Flask(__name__)
CORS(app)

_cache = {
    "portfolio": None,
    "last_loaded": None,
    "load_errors": [],
    "command_log": [],
}


def get_data_folder() -> Path:
    return Path(cfg.get("data_folder") or cfg.get("mt4_data_folder"))


def account_index() -> dict[str, dict]:
    return {acc["id"]: acc for acc in cfg.get("accounts", [])}


def parse_account_from_csv_name(path: str) -> tuple[str | None, str | None]:
    match = CSV_NAME_RE.match(Path(path).name)
    if not match:
        return None, None
    return match.group(2), match.group(1).upper()


def normalize_label(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def infer_account_type(label: str) -> str:
    normalized = normalize_label(label)
    if normalized == "demo" or normalized.startswith("demo"):
        return "DEMO"
    return "REAL"


def normalize_csv_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [COLUMN_MAP.get(str(c).strip(), str(c).strip().lower()) for c in df.columns]
    return df


def coerce_trade_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_csv_columns(df)
    for column in NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)

    if "magic" not in df.columns:
        df["magic"] = 0
    df["magic"] = pd.to_numeric(df["magic"], errors="coerce").fillna(0).astype(int)

    for column in ("open_time", "close_time"):
        if column not in df.columns:
            df[column] = pd.NaT
        df[column] = pd.to_datetime(df[column], errors="coerce")

    if "close_time" in df.columns:
        df = df[df["close_time"].notna()].copy()
    return df


def read_trade_csv(path: str) -> pd.DataFrame:
    last_error = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "latin1"):
        try:
            df = pd.read_csv(path, sep=None, engine="python", encoding=encoding)
            return coerce_trade_dataframe(df)
        except Exception as exc:
            last_error = exc
    raise last_error


def read_json_file(path: Path) -> dict:
    last_error = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "latin1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return json.load(f)
        except Exception as exc:
            last_error = exc
    raise last_error


def detect_platform(df: pd.DataFrame, account: dict | None) -> str:
    if "platform" in df.columns and len(df):
        value = str(df.iloc[0].get("platform", "")).upper()
        if value in ("MT4", "MT5"):
            return value
    if account:
        return account.get("platform", "MT4")
    return "MT4"


def safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except Exception:
        return default


def normalize_trade(row, platform: str, account: dict, source_file: str) -> dict:
    data = row.to_dict() if hasattr(row, "to_dict") else dict(row)
    profit = safe_float(data.get("profit"))
    swap = safe_float(data.get("swap"))
    commission = safe_float(data.get("commission"))
    net_profit = safe_float(data.get("net_profit"), profit + swap + commission)

    return {
        "ticket": str(data.get("ticket", "")),
        "symbol": str(data.get("symbol", "")),
        "type": str(data.get("type", "")).upper(),
        "lots": safe_float(data.get("lots")),
        "open_price": safe_float(data.get("open_price")),
        "close_price": safe_float(data.get("close_price")),
        "open_time": data.get("open_time"),
        "close_time": data.get("close_time"),
        "profit": profit,
        "swap": swap,
        "commission": commission,
        "net_profit": net_profit,
        "pips": safe_float(data.get("pips")),
        "magic": safe_int(data.get("magic")),
        "comment": str(data.get("comment", "")),
        "account_id": account["id"],
        "account_type": account["type"],
        "account_label": account["label"],
        "account_color": account["color"],
        "platform": platform,
        "broker": str(data.get("broker", "")),
        "currency": str(data.get("currency", "")),
        "source_file": source_file,
    }


def is_valid_trade(trade: dict) -> bool:
    comment = str(trade.get("comment", "")).lower()
    patterns = [p.lower() for p in cfg.get("exclude_comment_patterns", [])]
    return not any(pattern in comment for pattern in patterns)


def calc_profit_factor(profits: pd.Series) -> float:
    gross_profit = profits[profits > 0].sum()
    gross_loss = abs(profits[profits < 0].sum())
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return round(gross_profit / gross_loss, 4)


def calc_max_drawdown(profits: pd.Series) -> float:
    if len(profits) < 2:
        return 0.0
    equity = profits.cumsum()
    peak = equity.cummax()
    dd = (equity - peak) / peak.abs().replace(0, 1)
    return round(abs(dd.min()) * 100, 2)


def safe_sharpe(profits: pd.Series) -> float:
    if len(profits) < 2:
        return float(profits.sum()) if len(profits) == 1 else 0.0
    mean = profits.mean()
    std = profits.std()
    if std == 0 or math.isnan(std):
        return 23.0 if mean > 0 else 0.0
    return round((mean / std) * math.sqrt(252), 4)


def determine_action(metrics: dict) -> str:
    t = cfg["risk_thresholds"]
    trades = metrics.get("trades", 0)
    pf = metrics.get("profit_factor", 0.0)
    dd = metrics.get("max_drawdown_pct", 0.0)

    if trades < t["min_trades"]:
        return "insuficiente_data"
    if pf < t["quarantine_pf_below"] or dd > t["quarantine_dd_above"]:
        return "cuarentena"
    if dd > t["reduce_risk_dd_above"]:
        return "reducir_riesgo"
    if pf >= t["increase_risk_pf_above"] and dd <= t["increase_risk_dd_below"]:
        return "aumentar_riesgo"
    return "mantener"


def compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {
            "trades": 0,
            "net_profit": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_pips": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "first_trade": "n/a",
            "last_trade": "n/a",
        }

    df = pd.DataFrame(trades).sort_values("close_time")
    profits = pd.to_numeric(df["net_profit"], errors="coerce").fillna(0.0)
    close_times = pd.to_datetime(df["close_time"], errors="coerce")

    metrics = {
        "trades": int(len(df)),
        "net_profit": round(float(profits.sum()), 2),
        "profit_factor": 0.0,
        "max_drawdown_pct": 0.0,
        "avg_pips": 0.0,
        "sharpe": 0.0,
        "win_rate": 0.0,
        "first_trade": str(close_times.min())[:10] if close_times.notna().any() else "n/a",
        "last_trade": str(close_times.max())[:10] if close_times.notna().any() else "n/a",
    }

    try:
        pf = calc_profit_factor(profits)
        metrics["profit_factor"] = 99.0 if math.isinf(pf) else float(pf)
    except Exception:
        metrics["profit_factor"] = 0.0

    try:
        metrics["max_drawdown_pct"] = float(calc_max_drawdown(profits))
    except Exception:
        metrics["max_drawdown_pct"] = 0.0

    try:
        metrics["avg_pips"] = round(float(pd.to_numeric(df["pips"], errors="coerce").fillna(0.0).mean()), 2) if "pips" in df else 0.0
    except Exception:
        metrics["avg_pips"] = 0.0

    try:
        metrics["sharpe"] = float(safe_sharpe(profits))
    except Exception:
        metrics["sharpe"] = 0.0

    try:
        metrics["win_rate"] = round(float((profits > 0).sum()) / len(df) * 100, 1)
    except Exception:
        metrics["win_rate"] = 0.0

    return metrics


def compute_global_metrics(instances: list[dict]) -> dict:
    trades = []
    for instance in instances:
        trades.extend(instance.get("trades", []))
    return compute_metrics(trades)


def system_key_for_trade(trade: dict) -> tuple[str, str, int | None]:
    magic = safe_int(trade.get("magic"))
    if magic > 0:
        return f"magic:{magic}", f"magic:{magic}", magic

    comment = str(trade.get("comment", "")).strip()
    if comment and comment.lower() not in ("0", "nan"):
        label = f"comment:{comment[:40]}"
        return f"{label}|account:{trade['account_id']}", label, None

    label = f"symbol:{trade.get('symbol', 'unknown')}"
    return f"{label}|account:{trade['account_id']}", label, None


def serialize_trade(trade: dict) -> dict:
    out = dict(trade)
    for field in ("open_time", "close_time"):
        value = out.get(field)
        if hasattr(value, "isoformat"):
            out[field] = value.isoformat()
        else:
            out[field] = str(value) if value is not None else ""
    return out


def group_systems_globally(all_trades: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}

    for trade in all_trades:
        key, label, magic = system_key_for_trade(trade)
        system = grouped.setdefault(
            key,
            {
                "id": key,
                "label": label,
                "magic": magic,
                "instances_map": {},
            },
        )

        acct_id = trade["account_id"]
        instance = system["instances_map"].setdefault(
            acct_id,
            {
                "account_id": acct_id,
                "account_type": trade["account_type"],
                "account_label": trade["account_label"],
                "account_color": trade["account_color"],
                "platform": trade["platform"],
                "trades": [],
            },
        )
        instance["trades"].append(trade)

    systems = []
    for system in grouped.values():
        instances = []
        metrics_per_account = {}
        instance_actions = set()

        for instance in system.pop("instances_map").values():
            metrics = compute_metrics(instance["trades"])
            action = determine_action(metrics)
            instance_actions.add(action)
            instance["metrics"] = metrics
            instance["action"] = action
            metrics_per_account[instance["account_id"]] = metrics
            instance["trades"] = [serialize_trade(t) for t in instance["trades"]]
            instances.append(instance)

        metrics_global = compute_global_metrics(instances)
        action_global = determine_action(metrics_global)
        conflict = action_global == "mantener" and "cuarentena" in instance_actions

        systems.append(
            {
                **system,
                "instances": sorted(instances, key=lambda item: item["account_label"]),
                "accounts_count": len(instances),
                "metrics_global": metrics_global,
                "metrics_per_account": metrics_per_account,
                "action": "conflicto" if conflict else action_global,
                "action_global": action_global,
                "conflict": conflict,
                "instance_actions": sorted(instance_actions),
                # Legacy fields consumed by older UI/chat callers.
                "trades": metrics_global["trades"],
                "net_profit": metrics_global["net_profit"],
                "profit_factor": metrics_global["profit_factor"],
                "max_drawdown_pct": metrics_global["max_drawdown_pct"],
                "avg_pips": metrics_global["avg_pips"],
                "sharpe": metrics_global["sharpe"],
                "win_rate": metrics_global["win_rate"],
                "first_trade": metrics_global["first_trade"],
                "last_trade": metrics_global["last_trade"],
            }
        )

    systems.sort(key=lambda item: item["metrics_global"]["sharpe"], reverse=True)
    return systems


def account_summary(accounts: list[dict], trades: list[dict]) -> list[dict]:
    summaries = []
    systems_by_account = {}
    for trade in trades:
        key, _label, _magic = system_key_for_trade(trade)
        systems_by_account.setdefault(trade["account_id"], set()).add(key)

    for account in accounts:
        account_trades = [t for t in trades if t["account_id"] == account["id"]]
        metrics = compute_metrics(account_trades)
        account_state = read_account_state(account["id"], account)
        summaries.append(
            {
                **account,
                "metrics": metrics,
                "net_profit": metrics["net_profit"],
                "balance": account_state.get("balance"),
                "equity": account_state.get("equity"),
                "margin_free": account_state.get("margin_free"),
                "free_margin": account_state.get("margin_free"),
                "account_state_source": account_state.get("source_file"),
                "account_state_format": account_state.get("source_format"),
                "active_systems": len(systems_by_account.get(account["id"], set())),
                "trades": metrics["trades"],
            }
        )
    return summaries


def read_account_history_csv(path: Path) -> pd.DataFrame:
    last_error = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "latin1"):
        try:
            df = pd.read_csv(path, sep=None, engine="python", encoding=encoding)
            df = normalize_csv_columns(df)
            return df
        except Exception as exc:
            last_error = exc
    raise last_error


def score_account_state_file(path: Path, account: dict | None) -> tuple[int, float]:
    match = re.match(r"^account_history_(.+?)_-?\d+\.(csv|json)$", path.name, re.IGNORECASE)
    file_label = match.group(1) if match else ""
    score = 0

    if account and file_label:
        label_norm = normalize_label(file_label)
        if label_norm == normalize_label(account.get("label")):
            score += 300
        if label_norm == normalize_label(account.get("type")):
            score += 200
    elif file_label:
        score += 50

    if path.suffix.lower() == ".json":
        score += 10

    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0

    return score, modified


def account_state_candidates(account_id: str, account: dict | None = None) -> list[Path]:
    folder = get_data_folder()
    patterns = [
        f"account_history_*_{account_id}.json",
        f"account_history_*_{account_id}.csv",
        f"account_history_{account_id}.json",
        f"account_history_{account_id}.csv",
        "account_history.json",
        "account_history.csv",
    ]
    seen = set()
    candidates = []
    for pattern in patterns:
        for path in folder.glob(pattern):
            if path in seen:
                continue
            seen.add(path)
            candidates.append(path)
    return sorted(candidates, key=lambda path: score_account_state_file(path, account), reverse=True)


def read_account_state(account_id: str, account: dict | None = None) -> dict:
    state = {"balance": None, "equity": None, "margin_free": None, "source_file": None, "source_format": None}

    for path in account_state_candidates(account_id, account):
        try:
            if path.suffix.lower() == ".json":
                payload = read_json_file(path)
                snapshot = payload.get("snapshot", payload)
                file_account = str(snapshot.get("account", snapshot.get("account_id", "")))
                if file_account not in ("", account_id):
                    continue
                state["balance"] = safe_float(snapshot.get("balance"), None)
                state["equity"] = safe_float(snapshot.get("equity"), None)
                state["margin_free"] = safe_float(snapshot.get("margin_free", snapshot.get("free_margin")), None)
                state["source_file"] = path.name
                state["source_format"] = "json"
                return state

            df = read_account_history_csv(path)
            if df.empty:
                continue
            if "account" in df.columns:
                df = df[df["account"].astype(str) == account_id]
            elif "account_id" in df.columns:
                df = df[df["account_id"].astype(str) == account_id]
            if df.empty:
                continue
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
                df = df.sort_values("timestamp")
            last = df.iloc[-1]
            state["balance"] = safe_float(last.get("balance"), None)
            state["equity"] = safe_float(last.get("equity"), None)
            state["margin_free"] = safe_float(last.get("free_margin", last.get("margin_free")), None)
            state["source_file"] = path.name
            state["source_format"] = "csv"
            return state
        except Exception:
            continue
    return state


def read_account_equity(account_id: str) -> float | None:
    return read_account_state(account_id).get("equity")


def equity_curves_by_account(trades: list[dict], accounts: list[dict]) -> dict[str, list[dict]]:
    curves = {}
    for account in accounts:
        account_trades = [t for t in trades if t["account_id"] == account["id"]]
        df = pd.DataFrame(account_trades)
        if df.empty:
            curves[account["id"]] = []
            continue
        df["close_time"] = pd.to_datetime(df["close_time"], errors="coerce")
        df = df.dropna(subset=["close_time"]).sort_values("close_time")
        equity = 0.0
        points = []
        for _, row in df.iterrows():
            equity += safe_float(row["net_profit"])
            points.append({"ts": row["close_time"].isoformat(), "equity": round(equity, 2)})
        curves[account["id"]] = points
    return curves


def discover_trade_files(folder: Path) -> list[str]:
    return sorted(glob.glob(str(folder / "trades_*.csv")))


def score_trade_file(path: str, account: dict | None) -> tuple[int, float]:
    account_id, file_label = parse_account_from_csv_name(path)
    label_norm = normalize_label(file_label)
    score = 0

    if account:
        account_label_norm = normalize_label(account.get("label"))
        account_type_norm = normalize_label(account.get("type"))
        csv_pattern = str(account.get("csv_pattern", "")).lower()
        file_name = Path(path).name.lower()

        if label_norm and label_norm == account_label_norm:
            score += 500
        if file_name == csv_pattern:
            score += 300
        if label_norm and label_norm == account_type_norm:
            score += 200
    else:
        if label_norm not in ("real", "demo"):
            score += 100

    try:
        modified = Path(path).stat().st_mtime
    except OSError:
        modified = 0.0

    return score, modified


def select_trade_files(folder: Path, accounts_by_id: dict[str, dict], warnings: list[str]) -> list[str]:
    grouped: dict[str, list[str]] = {}

    for fpath in discover_trade_files(folder):
        account_id, _account_type_from_name = parse_account_from_csv_name(fpath)
        if not account_id:
            warnings.append(f"{Path(fpath).name}: nombre CSV no cumple trades_<label>_<cuenta>.csv")
            continue
        grouped.setdefault(account_id, []).append(fpath)

    selected = []
    for account_id, files in grouped.items():
        account = accounts_by_id.get(account_id)
        ranked = sorted(files, key=lambda path: score_trade_file(path, account), reverse=True)
        selected.append(ranked[0])

        for skipped in ranked[1:]:
            warnings.append(
                f"{Path(skipped).name}: omitido porque {Path(ranked[0]).name} fue seleccionado para cuenta {account_id}"
            )

    return sorted(selected)


def load_portfolio() -> dict:
    folder = get_data_folder()
    accounts = cfg.get("accounts", [])
    accounts_by_id = account_index()
    effective_accounts_by_id = {account["id"]: dict(account) for account in accounts}
    errors = []
    warnings = []
    all_trades = []
    debug_files = []

    for fpath in select_trade_files(folder, accounts_by_id, warnings):
        account_id, account_type_from_name = parse_account_from_csv_name(fpath)

        account = accounts_by_id.get(account_id)
        if not account:
            warnings.append(f"{Path(fpath).name}: cuenta {account_id} no registrada en config.json")
            account = {
                "id": account_id,
                "type": account_type_from_name if account_type_from_name in ("REAL", "DEMO") else infer_account_type(account_type_from_name),
                "label": account_type_from_name if account_type_from_name not in ("REAL", "DEMO") else f"Cuenta {account_id}",
                "platform": "MT4",
                "color": "#6b7280",
            }
            accounts_by_id[account_id] = account
            effective_accounts_by_id.setdefault(account_id, dict(account))

        try:
            df = read_trade_csv(fpath)
            if account_id not in effective_accounts_by_id:
                effective_accounts_by_id[account_id] = dict(account)
            if account_id not in {a["id"] for a in accounts} and "account_label" in df.columns and len(df):
                csv_label = str(df.iloc[0].get("account_label", "")).strip()
                if csv_label and csv_label.lower() not in ("nan", "none"):
                    effective_accounts_by_id[account_id]["label"] = csv_label
                    account["label"] = csv_label
                    inferred_type = infer_account_type(csv_label)
                    effective_accounts_by_id[account_id]["type"] = inferred_type
                    account["type"] = inferred_type
            platform = detect_platform(df, account)
            effective_accounts_by_id[account_id]["platform"] = platform
            account["platform"] = platform
            debug_files.append({
                "file": Path(fpath).name,
                "columns": list(df.columns),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "rows": int(len(df)),
                "sample_values": df[["profit", "magic", "open_time", "close_time"]].head(5).astype(str).to_dict(orient="records") if len(df) else [],
            })
            for _, row in df.iterrows():
                trade = normalize_trade(row, platform, account, Path(fpath).name)
                if is_valid_trade(trade):
                    all_trades.append(trade)
        except Exception as exc:
            errors.append(f"{Path(fpath).name}: {exc}")

    systems = group_systems_globally(all_trades)
    effective_accounts = list(effective_accounts_by_id.values())
    accounts_summary = account_summary(effective_accounts, all_trades)

    action_counts = {}
    conflict_count = 0
    for system in systems:
        action_counts[system["action"]] = action_counts.get(system["action"], 0) + 1
        if system["conflict"]:
            conflict_count += 1

    total_profit = round(sum(s["metrics_global"]["net_profit"] for s in systems), 2)
    summary = {
        "total_trades": len(all_trades),
        "total_systems": len(systems),
        "total_accounts": len(effective_accounts),
        "total_profit": total_profit,
        "action_counts": action_counts,
        "conflicts": conflict_count,
        "loaded_at": datetime.now().isoformat(),
        "data_folder": str(folder),
    }

    return {
        "accounts": accounts_summary,
        "systems": systems,
        "summary": summary,
        "equity_curves": equity_curves_by_account(all_trades, effective_accounts),
        "warnings": warnings,
        "errors": errors,
        "debug": {
            "files": debug_files,
            "first_trades": [serialize_trade(t) for t in all_trades[:5]],
        },
        "loaded_at": datetime.now().isoformat(),
        "total_trades": len(all_trades),
    }


def get_portfolio(force_reload: bool = False) -> dict:
    now = datetime.now()
    reload_secs = cfg.get("auto_reload_seconds", 300)
    should_reload = (
        force_reload
        or _cache["portfolio"] is None
        or _cache["last_loaded"] is None
        or (now - _cache["last_loaded"]).seconds > reload_secs
    )
    if should_reload:
        _cache["portfolio"] = load_portfolio()
        _cache["last_loaded"] = now
    return _cache["portfolio"]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
def api_portfolio():
    force = request.args.get("reload", "false").lower() == "true"
    return jsonify(get_portfolio(force_reload=force))


@app.route("/api/portfolio/summary")
@app.route("/api/summary")
def api_portfolio_summary():
    return jsonify(get_portfolio().get("summary", {}))


@app.route("/api/accounts")
def api_accounts():
    data = get_portfolio()
    return jsonify({"accounts": data.get("accounts", []), "count": len(data.get("accounts", []))})


@app.route("/api/debug/trades")
def api_debug_trades():
    data = get_portfolio(force_reload=request.args.get("reload", "false").lower() == "true")
    debug = data.get("debug", {})
    return jsonify({
        "loaded_at": data.get("loaded_at"),
        "summary": data.get("summary", {}),
        "warnings": data.get("warnings", []),
        "errors": data.get("errors", []),
        "files": debug.get("files", []),
        "first_5_trades": debug.get("first_trades", [])[:5],
    })


@app.route("/api/debug/accounts")
def api_debug_accounts():
    data = get_portfolio(force_reload=request.args.get("reload", "false").lower() == "true")
    rows = []
    for account in data.get("accounts", []):
        candidates = []
        for path in account_state_candidates(account["id"], account):
            candidates.append({
                "file": path.name,
                "size": path.stat().st_size if path.exists() else None,
                "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None,
            })
        rows.append({
            "id": account["id"],
            "label": account.get("label"),
            "type": account.get("type"),
            "platform": account.get("platform"),
            "balance": account.get("balance"),
            "equity": account.get("equity"),
            "margin_free": account.get("margin_free"),
            "source_file": account.get("account_state_source"),
            "source_format": account.get("account_state_format"),
            "candidate_files": candidates,
        })
    return jsonify({"accounts": rows, "count": len(rows), "data_folder": str(get_data_folder())})


@app.route("/api/accounts/<account_id>/systems")
def api_account_systems(account_id):
    systems = []
    for system in get_portfolio().get("systems", []):
        instances = [i for i in system["instances"] if i["account_id"] == account_id]
        if not instances:
            continue
        scoped = dict(system)
        scoped["instances"] = instances
        systems.append(scoped)
    return jsonify({"systems": systems, "count": len(systems)})


@app.route("/api/systems")
def api_systems():
    systems = get_portfolio().get("systems", [])
    account_id = request.args.get("account_id")
    account_type = request.args.get("type")
    action = request.args.get("action")

    if account_id:
        systems = [s for s in systems if any(i["account_id"] == account_id for i in s["instances"])]
    if account_type and account_type.upper() in ("REAL", "DEMO"):
        systems = [s for s in systems if any(i["account_type"] == account_type.upper() for i in s["instances"])]
    if action:
        systems = [s for s in systems if s["action"] == action or action in s.get("instance_actions", [])]

    return jsonify({"systems": systems, "count": len(systems)})


@app.route("/api/systems/<system_id>")
def api_system_detail(system_id):
    wanted = system_id
    if wanted.isdigit():
        wanted = f"magic:{wanted}"
    for system in get_portfolio().get("systems", []):
        if system["id"] == wanted or system["label"] == wanted or str(system.get("magic")) == system_id:
            return jsonify({"exists": True, "system": system})
    return jsonify({"exists": False, "error": "sistema no encontrado"}), 404


@app.route("/api/command", methods=["POST"])
def api_command():
    body = request.get_json(force=True)
    system = str(body.get("system", "")).strip()
    action = str(body.get("action", "")).strip()
    account_id = str(body.get("account_id", "")).strip()
    platform = str(body.get("platform", "")).upper()

    if not system or action not in VALID_ACTIONS:
        return jsonify({"ok": False, "error": "system o action invalido"}), 400

    portfolio = get_portfolio()
    matched_system = next((s for s in portfolio["systems"] if s["id"] == system or s["label"] == system), None)
    if not matched_system:
        return jsonify({"ok": False, "error": "sistema no encontrado"}), 404

    if not account_id:
        if len(matched_system["instances"]) != 1:
            return jsonify({"ok": False, "error": "account_id requerido para sistemas multi-cuenta"}), 400
        account_id = matched_system["instances"][0]["account_id"]

    instance = next((i for i in matched_system["instances"] if i["account_id"] == account_id), None)
    if not instance:
        return jsonify({"ok": False, "error": "el sistema no tiene instancia en esa cuenta"}), 400

    if platform not in ("MT4", "MT5"):
        platform = instance["platform"]

    cmd = {
        "ts": datetime.now().isoformat(),
        "system": matched_system["label"],
        "magic": matched_system["magic"],
        "action": action,
        "account_id": account_id,
        "account_label": instance["account_label"],
        "platform": platform,
        "status": "pending",
        "source": "dashboard",
    }

    _cache["command_log"].insert(0, cmd)
    _write_commands_file()
    return jsonify({"ok": True, "command": cmd})


@app.route("/api/commands")
def api_commands():
    return jsonify({"commands": _cache["command_log"]})


@app.route("/api/commands/clear", methods=["POST"])
def api_commands_clear():
    _cache["command_log"] = []
    _write_commands_file()
    return jsonify({"ok": True})


@app.route("/api/result")
def api_result():
    result_path = get_data_folder() / "result.json"
    if not result_path.exists():
        return jsonify({"exists": False, "message": "result.json no encontrado"})
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            return jsonify({"exists": True, "data": json.load(f)})
    except Exception as exc:
        return jsonify({"exists": False, "error": str(exc)})


@app.route("/api/config")
def api_config():
    safe_cfg = {k: v for k, v in cfg.items() if "api_key" not in k.lower()}
    return jsonify(safe_cfg)


@app.route("/api/config", methods=["POST"])
def api_config_update():
    global cfg
    body = request.get_json(force=True)
    if "risk_thresholds" in body:
        cfg["risk_thresholds"].update(body["risk_thresholds"])
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        get_portfolio(force_reload=True)
        return jsonify({"ok": True, "thresholds": cfg["risk_thresholds"]})
    return jsonify({"ok": False, "error": "Solo se pueden actualizar risk_thresholds"}), 400


# === AI CHAT ===

def get_chat_config() -> dict:
    return {
        "api_key": (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or cfg.get("deepseek_api_key", "")
            or cfg.get("openai_api_key", "")
            or cfg.get("anthropic_api_key", "")
        ),
        "base_url": cfg.get("deepseek_base_url") or "https://api.deepseek.com",
        "model": cfg.get("deepseek_model") or "deepseek-chat",
    }

def build_portfolio_context() -> str:
    """Construye el contexto inyectado al chat IA sin exponer archivos completos."""
    portfolio = get_portfolio()
    summary = portfolio.get("summary", {})
    accounts = portfolio.get("accounts", [])
    systems = portfolio.get("systems", [])
    conflicts = [s for s in systems if s.get("conflict")]

    account_lines = []
    for account in accounts:
        metrics = account.get("metrics", {})
        account_lines.append(
            "- {id} | {label} | {type} | {platform} | PnL={pnl} | sistemas={systems} | trades={trades}".format(
                id=account.get("id"),
                label=account.get("label"),
                type=account.get("type"),
                platform=account.get("platform"),
                pnl=metrics.get("net_profit", account.get("net_profit", 0)),
                systems=account.get("active_systems", 0),
                trades=metrics.get("trades", account.get("trades", 0)),
            )
        )

    system_lines = []
    for system in systems[:80]:
        instance_bits = []
        for instance in system.get("instances", []):
            m = instance.get("metrics", {})
            instance_bits.append(
                "{account_id}:{action}:PnL={pnl}:PF={pf}:DD={dd}%".format(
                    account_id=instance.get("account_id"),
                    action=instance.get("action"),
                    pnl=m.get("net_profit", 0),
                    pf=m.get("profit_factor", 0),
                    dd=m.get("max_drawdown_pct", 0),
                )
            )
        gm = system.get("metrics_global", {})
        system_lines.append(
            "- {label} | global={action} | cuentas={count} | PnL={pnl} | PF={pf} | DD={dd}% | instancias=[{instances}]".format(
                label=system.get("label"),
                action=system.get("action"),
                count=system.get("accounts_count", 0),
                pnl=gm.get("net_profit", 0),
                pf=gm.get("profit_factor", 0),
                dd=gm.get("max_drawdown_pct", 0),
                instances="; ".join(instance_bits),
            )
        )

    conflict_lines = [
        "- {label}: global={global_action}, instancias={actions}".format(
            label=s.get("label"),
            global_action=s.get("action_global"),
            actions=", ".join(s.get("instance_actions", [])),
        )
        for s in conflicts
    ]

    return """ESTADO ACTUAL DEL PORTAFOLIO
Resumen:
- cuentas={accounts}
- sistemas_globales={systems}
- trades={trades}
- pnl_total={pnl}
- conflictos={conflicts}

Cuentas:
{account_lines}

Sistemas activos:
{system_lines}

Alertas de conflicto:
{conflict_lines}
""".format(
        accounts=summary.get("total_accounts", 0),
        systems=summary.get("total_systems", 0),
        trades=summary.get("total_trades", 0),
        pnl=summary.get("total_profit", 0),
        conflicts=summary.get("conflicts", 0),
        account_lines="\n".join(account_lines) or "- Sin cuentas cargadas",
        system_lines="\n".join(system_lines) or "- Sin sistemas cargados",
        conflict_lines="\n".join(conflict_lines) or "- Sin conflictos activos",
    )


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(force=True)
    message = str(body.get("message", "")).strip()
    history = body.get("history", [])

    if not message:
        return jsonify({"error": "Mensaje vacio"}), 400

    portfolio_context = build_portfolio_context()
    chat_cfg = get_chat_config()

    system_prompt = f"""Eres un analizador cuantitativo experto en portafolios multi-cuenta de trading algorítmico en MetaTrader.

Usa exclusivamente el contexto del portafolio para hablar de sistemas, magic numbers, cuentas, clasificaciones y comandos. Si falta un dato, dilo.

{portfolio_context}

UMBRALES ACTIVOS:
{json.dumps(cfg.get('risk_thresholds', {}), indent=2, ensure_ascii=False)}

CAPACIDADES:
- El dashboard escribe commands.json para QA_Commander.
- Los comandos multi-cuenta incluyen account_id y platform.
- AutoExecute debe mantenerse en false hasta validar flujo completo.

Responde en español, conciso, práctico y orientado a decisiones."""

    def valid_history_items(items):
        cleaned = []
        for item in items:
            role = item.get("role")
            content = str(item.get("content", "")).strip()
            if role in ("user", "assistant") and content:
                cleaned.append({"role": role, "content": content})
        return cleaned[-20:]

    def generate():
        try:
            if not chat_cfg["api_key"]:
                yield f"data: {json.dumps({'error': 'API key no configurada. Define DEEPSEEK_API_KEY o deepseek_api_key en config.json'})}\n\n"
                return

            client = OpenAI(api_key=chat_cfg["api_key"], base_url=chat_cfg["base_url"])
            msgs = [{"role": "system", "content": system_prompt}]
            msgs.extend(valid_history_items(history))
            msgs.append({"role": "user", "content": message})

            stream = client.chat.completions.create(
                model=chat_cfg["model"],
                max_tokens=1200,
                messages=msgs,
                stream=True,
            )
            for chunk in stream:
                text = chunk.choices[0].delta.content or ""
                if text:
                    yield f"data: {json.dumps({'text': text})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except AuthenticationError:
            yield f"data: {json.dumps({'error': 'API key invalida. Revisa DEEPSEEK_API_KEY o deepseek_api_key en config.json'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _write_commands_file():
    folder = get_data_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "2.0",
            "generated": datetime.now().isoformat(),
            "commands": _cache["command_log"],
        }
        with open(folder / "commands.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        _cache["load_errors"] = [f"Error escribiendo commands.json: {exc}"]


def _background_reload():
    interval = cfg.get("auto_reload_seconds", 300)
    while True:
        time.sleep(interval)
        try:
            _cache["portfolio"] = load_portfolio()
            _cache["last_loaded"] = datetime.now()
        except Exception:
            pass


reload_thread = threading.Thread(target=_background_reload, daemon=True)
reload_thread.start()

if __name__ == "__main__":
    port = cfg.get("server_port", 5000)
    print(f"QA Portfolio Commander - http://localhost:{port}")
    print(f"Data folder: {get_data_folder()}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
