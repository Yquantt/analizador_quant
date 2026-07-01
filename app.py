"""
QA Portfolio Commander - servidor Flask local.

Modelo actual:
- Cuenta: entidad configurada en config.json.
- Sistema/EA: entidad global identificada por magic; si no hay magic se aísla por comentario + cuenta.
- Instancia: ejecución de un sistema dentro de una cuenta concreta.
"""

import glob
import hashlib
import json
import logging
import math
import numbers
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import database
import pandas as pd
from flask import Flask, Response, jsonify, make_response, render_template, request, stream_with_context
from flask_cors import CORS
from flask.json.provider import DefaultJSONProvider
from openai import AuthenticationError, OpenAI
from repositories import accounts_repo, commands_repo, metrics_repo, trades_repo
from routes import register_blueprints
from services import audit_service, broker_sync_service, command_service, data_quality_service, decision_journal_service, metrics_service, performance_metrics_service, strategy_identity_service
CONFIG_PATH = Path(__file__).parent / "config.json"
logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "data_folder": str(Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "QuantAnalyzer"),
    "mt4_data_folder": str(Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "QuantAnalyzer"),
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
    "broker_sync_seconds": 60,
}

RULES_VERSION = "v1.0"


def sanitize_json_value(value):
    if isinstance(value, dict):
        return {key: sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            return None
    return value


class SanitizingJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        return super().dumps(sanitize_json_value(obj), **kwargs)

    def response(self, *args, **kwargs):
        args = tuple(sanitize_json_value(arg) for arg in args)
        kwargs = {key: sanitize_json_value(value) for key, value in kwargs.items()}
        return super().response(*args, **kwargs)

# CSVs reales observados en Common/Files:
# - trades_DEMO_123456.csv
# - trades_REAL_789012.csv
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
    "Fees": "fees",
    "Fee": "fees",
    "GrossProfit": "gross_profit",
    "GrossLoss": "gross_loss",
    "SpreadCostEstimated": "spread_cost_estimated",
    "SlippageEstimated": "slippage_estimated",
    "NetProfit": "net_profit",
    "Pips": "pips",
    "Account": "account",
    "AccountLabel": "account_label",
    "Broker": "broker",
    "Server": "server",
    "Currency": "currency",
    "Platform": "platform",
}
NUMERIC_COLUMNS = [
    "profit",
    "gross_profit",
    "gross_loss",
    "swap",
    "commission",
    "fees",
    "spread_cost_estimated",
    "slippage_estimated",
    "net_profit",
    "pips",
    "lots",
    "open_price",
    "close_price",
]
VALID_ACTIONS = {
    "close_by_magic",
    "reduce_lots",
    "increase_lots_25",
    "increase_lots_50",
    "set_max_lots",
}
RISK_INCREASE_ACTIONS = {"increase_lots_25", "increase_lots_50", "set_max_lots"}


def get_account_key(platform: str, server: str, login: str, account_type: str) -> str:
    parts = [
        str(platform or "MT").upper(),
        re.sub(r"[^A-Za-z0-9]+", "_", str(server or "unknown")).strip("_") or "unknown",
        str(login or "unknown"),
        str(account_type or "real").lower(),
    ]
    return "_".join(parts)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"config.json no es JSON valido en linea {exc.lineno}, columna {exc.colno}: {exc.msg}.\n"
            "Si el error dice 'Invalid \\escape', cambia rutas Windows como "
            "'C:\\Users\\Administrator\\...' por 'C:/Users/Administrator/...' "
            "o duplica cada barra: 'C:\\\\Users\\\\Administrator\\\\...'."
        ) from exc

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
        item["login"] = str(item.get("login", item["id"]))
        item["server"] = str(item.get("server", "unknown") or "unknown")
        item["label"] = str(item.get("label", item["id"]))
        item["color"] = str(item.get("color", "#4f8ef7"))
        item["csv_pattern"] = f"trades_{item['type']}_{item['id']}.csv"
        item["account_key"] = get_account_key(item["platform"], item["server"], item["login"], item["type"])

    return normalized


cfg = load_config()
app = Flask(__name__)
app.json = SanitizingJSONProvider(app)
CORS(app)
register_blueprints(app)

_cache = {
    "portfolio": None,
    "last_loaded": None,
    "load_errors": [],
    "command_log": [],
    "last_closed_trade_sync": None,
}

UNRECONCILED_EQUITY_LOGINS = set()
UNRELIABLE_RISK_METRIC_WARNING = (
    "Metrica no confiable: falta drawdown real sobre balance/equity. "
    "Usar DD de curva PnL solo como dato analitico y revisar manualmente antes de operar."
)


def get_data_folder() -> Path:
    return Path(cfg.get("data_folder") or cfg.get("mt4_data_folder"))


try:
    database.init_db(str(get_data_folder()))
except Exception as exc:
    logger.exception("Error inicializando SQLite: %s", exc)


def account_index() -> dict[str, dict]:
    index = {}
    for acc in cfg.get("accounts", []):
        index[acc["id"]] = acc
        index[acc.get("account_key", acc["id"])] = acc
        index[acc.get("login", acc["id"])] = acc
    return index


def account_matches_identifier(instance: dict, identifier: str) -> bool:
    wanted = str(identifier)
    return wanted in {
        str(instance.get("account_id", "")),
        str(instance.get("account_key", "")),
        str(instance.get("login", "")),
    }


def parse_account_from_csv_name(path: str) -> tuple[str | None, str | None]:
    match = CSV_NAME_RE.match(Path(path).name)
    if not match:
        return None, None
    return match.group(2), match.group(1).upper()


def account_id_aliases(account_id: str) -> list[str]:
    aliases = [str(account_id)]
    try:
        numeric = int(str(account_id))
        if numeric < 0:
            aliases.append(str(numeric + 2**32))
        elif numeric > 2**31 - 1:
            aliases.append(str(numeric - 2**32))
    except Exception:
        pass
    return list(dict.fromkeys(aliases))


def canonical_account_id(account_id: str | None, accounts_by_id: dict[str, dict]) -> str | None:
    if not account_id:
        return account_id
    raw = str(account_id)
    if raw in accounts_by_id:
        return raw
    for alias in account_id_aliases(raw):
        if alias in accounts_by_id:
            return alias
    return raw


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


def infer_server_from_trade_dataframe(df: pd.DataFrame, account: dict | None) -> str:
    current = str((account or {}).get("server") or "").strip()
    if current and current.lower() != "unknown":
        return current
    if len(df):
        for column in ("server", "broker"):
            if column in df.columns:
                value = str(df.iloc[0].get(column) or "").strip()
                if value and value.lower() not in ("nan", "none", "unknown"):
                    return value
    return current or "unknown"


def account_type_for_import(account: dict, account_type_from_name: str | None) -> str:
    account_type = str(account_type_from_name or account.get("type") or "REAL").upper()
    return account_type if account_type in ("REAL", "DEMO") else "REAL"


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
    fees = safe_float(data.get("fees"))
    spread_cost_estimated = safe_float(data.get("spread_cost_estimated"))
    slippage_estimated = safe_float(data.get("slippage_estimated"))
    gross_profit = safe_float(data.get("gross_profit"), max(profit, 0.0))
    gross_loss = safe_float(data.get("gross_loss"), abs(min(profit, 0.0)))
    net_profit = safe_float(
        data.get("net_profit"),
        profit + swap + commission + fees - spread_cost_estimated - slippage_estimated,
    )

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
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "swap": swap,
        "commission": commission,
        "fees": fees,
        "spread_cost_estimated": spread_cost_estimated,
        "slippage_estimated": slippage_estimated,
        "net_profit": net_profit,
        "balance_before": safe_float(data.get("balance_before"), None),
        "balance_after": safe_float(data.get("balance_after"), None),
        "pips": safe_float(data.get("pips")),
        "magic": safe_int(data.get("magic")),
        "comment": str(data.get("comment", "")),
        "account_id": account["id"],
        "account_key": account.get("account_key") or get_account_key(platform, account.get("server", "unknown"), account["id"], account["type"]),
        "login": account.get("login", account["id"]),
        "account_type": account["type"],
        "account_label": account["label"],
        "account_color": account["color"],
        "platform": platform,
        "server": account.get("server", "unknown"),
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


def has_unreconciled_equity_identity(account_id=None, login=None, account_key=None, server=None) -> bool:
    identifiers = {str(value) for value in (account_id, login) if value is not None}
    account_key_text = str(account_key or "")
    server_text = str(server or "").lower()
    account_key_is_unreconciled = "_unknown_" in account_key_text.lower() and any(
        login in account_key_text for login in UNRECONCILED_EQUITY_LOGINS
    )
    missing_canonical_account_key = not account_key_text or "_unknown_" in account_key_text.lower()
    return account_key_is_unreconciled or (
        missing_canonical_account_key
        and server_text == "unknown"
        and bool(identifiers & UNRECONCILED_EQUITY_LOGINS)
    )


def mask_unreliable_risk_metrics(metrics: dict) -> dict:
    metrics["sharpe"] = None
    metrics["max_drawdown_pct"] = None
    metrics["current_drawdown_pct"] = None
    metrics["balance_drawdown_reliable"] = False
    metrics["drawdown_basis"] = "unreliable"
    metrics["risk_metrics_reliable"] = False
    metrics["sharpe_warning"] = UNRELIABLE_RISK_METRIC_WARNING
    metrics["drawdown_warning"] = UNRELIABLE_RISK_METRIC_WARNING
    return metrics


def determine_action(metrics: dict) -> str:
    t = cfg["risk_thresholds"]
    trades = metrics.get("trades", 0)
    pf = metrics.get("profit_factor", 0.0)
    dd = safe_float(metrics.get("balance_max_drawdown_pct", metrics.get("max_drawdown_pct")), 0.0)

    if trades < t["min_trades"]:
        return "insuficiente_data"
    if metrics.get("risk_metrics_reliable") is False or metrics.get("balance_drawdown_reliable") is not True:
        return "revision_manual"
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
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown": 0.0,
            "equity_current": 0.0,
            "equity_peak": 0.0,
            "return_pct": 0.0,
            "current_drawdown": 0.0,
            "current_drawdown_pct": 0.0,
            "pnl_curve_equity_current": 0.0,
            "pnl_curve_equity_peak": 0.0,
            "pnl_curve_current_drawdown": 0.0,
            "pnl_curve_current_drawdown_pct": 0.0,
            "pnl_curve_max_drawdown": 0.0,
            "pnl_curve_max_drawdown_pct": 0.0,
            "balance_drawdown_reliable": False,
            "balance_equity_current": None,
            "balance_equity_peak": None,
            "balance_current_drawdown": None,
            "balance_current_drawdown_pct": None,
            "balance_max_drawdown": None,
            "balance_max_drawdown_pct": None,
            "drawdown_basis": "unavailable",
            "return_over_drawdown": 0.0,
            "recovery_factor": 0.0,
            "avg_pips": 0.0,
            "pips_avg": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "avg_trade": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "payoff_ratio": 0.0,
            "trades_last_20": 0,
            "trades_last_50": 0,
            "trades_last_100": 0,
            "current_winning_streak": 0,
            "current_losing_streak": 0,
            "longest_winning_streak": 0,
            "longest_losing_streak": 0,
            "average_winning_streak": 0.0,
            "average_losing_streak": 0.0,
            "average_holding_hours": 0.0,
            "zero_duration_trades": 0,
            "risk_metrics_reliable": True,
            "first_trade": "n/a",
            "last_trade": "n/a",
        }

    df = pd.DataFrame(trades).sort_values("close_time")
    profits = pd.to_numeric(df["net_profit"], errors="coerce").fillna(0.0)
    profit_values = [float(value) for value in profits.tolist()]
    close_times = pd.to_datetime(df["close_time"], errors="coerce")
    open_times = pd.to_datetime(df["open_time"], errors="coerce") if "open_time" in df else pd.Series(dtype="datetime64[ns]")
    holding_hours = (close_times - open_times).dt.total_seconds() / 3600 if len(open_times) == len(close_times) else pd.Series(dtype=float)
    positive_holding_hours = holding_hours[holding_hours > 0]
    zero_duration_trades = int((holding_hours == 0).sum()) if len(holding_hours) else 0

    metrics = {
        "trades": int(len(df)),
        "net_profit": round(float(profits.sum()), 2),
        "gross_profit": round(float(profits[profits > 0].sum()), 2),
        "gross_loss": round(float(abs(profits[profits < 0].sum())), 2),
        "profit_factor": 0.0,
        "max_drawdown_pct": 0.0,
        "avg_pips": 0.0,
        "pips_avg": 0.0,
        "sharpe": 0.0,
        "win_rate": 0.0,
        "expectancy": round(float(profits.mean()), 4) if len(profits) else 0.0,
        "avg_trade": round(float(profits.mean()), 4) if len(profits) else 0.0,
        "average_holding_hours": round(float(positive_holding_hours.mean()), 4) if len(positive_holding_hours) else 0.0,
        "zero_duration_trades": zero_duration_trades,
        "risk_metrics_reliable": True,
        "first_trade": str(close_times.min())[:10] if close_times.notna().any() else "n/a",
        "last_trade": str(close_times.max())[:10] if close_times.notna().any() else "n/a",
    }

    try:
        pf = calc_profit_factor(profits)
        metrics["profit_factor"] = 99.0 if math.isinf(pf) else float(pf)
    except Exception:
        metrics["profit_factor"] = 0.0

    try:
        pnl_curve_metrics = performance_metrics_service.calculate_equity_metrics(profit_values)
        metrics.update({
            "pnl_curve_equity_current": pnl_curve_metrics.get("equity_current"),
            "pnl_curve_equity_peak": pnl_curve_metrics.get("equity_peak"),
            "pnl_curve_current_drawdown": pnl_curve_metrics.get("current_drawdown"),
            "pnl_curve_current_drawdown_pct": pnl_curve_metrics.get("current_drawdown_pct"),
            "pnl_curve_max_drawdown": pnl_curve_metrics.get("max_drawdown"),
            "pnl_curve_max_drawdown_pct": pnl_curve_metrics.get("max_drawdown_pct"),
        })
        balance_metrics = performance_metrics_service.calculate_balance_drawdown_metrics(
            df["balance_after"].tolist() if "balance_after" in df else []
        )
        metrics.update(balance_metrics)
        if balance_metrics.get("balance_drawdown_reliable") is True:
            metrics.update({
                "equity_current": balance_metrics.get("balance_equity_current"),
                "equity_peak": balance_metrics.get("balance_equity_peak"),
                "current_drawdown": balance_metrics.get("balance_current_drawdown"),
                "current_drawdown_pct": balance_metrics.get("balance_current_drawdown_pct"),
                "max_drawdown": balance_metrics.get("balance_max_drawdown"),
                "max_drawdown_pct": balance_metrics.get("balance_max_drawdown_pct"),
                "return_over_drawdown": round(metrics["net_profit"] / balance_metrics["balance_max_drawdown"], 4)
                if balance_metrics.get("balance_max_drawdown") else 0.0,
                "recovery_factor": round(metrics["net_profit"] / balance_metrics["balance_max_drawdown"], 4)
                if balance_metrics.get("balance_max_drawdown") else 0.0,
            })
        else:
            metrics.update({
                "equity_current": None,
                "equity_peak": None,
                "current_drawdown": None,
                "current_drawdown_pct": None,
                "max_drawdown": pnl_curve_metrics.get("max_drawdown"),
                "max_drawdown_pct": None,
                "return_over_drawdown": 0.0,
                "recovery_factor": 0.0,
                "risk_metrics_reliable": False,
                "drawdown_warning": UNRELIABLE_RISK_METRIC_WARNING,
            })
    except Exception:
        metrics["max_drawdown"] = 0.0
        metrics["max_drawdown_pct"] = None
        metrics["risk_metrics_reliable"] = False
        metrics["balance_drawdown_reliable"] = False
        metrics["drawdown_basis"] = "error"
        metrics["drawdown_warning"] = UNRELIABLE_RISK_METRIC_WARNING

    try:
        metrics.update(performance_metrics_service.calculate_quality_metrics(profit_values))
        metrics.update(performance_metrics_service.calculate_streak_metrics(profit_values))
        metrics.update(performance_metrics_service.calculate_recent_trade_counts(profit_values))
    except Exception:
        pass

    try:
        metrics["avg_pips"] = round(float(pd.to_numeric(df["pips"], errors="coerce").fillna(0.0).mean()), 2) if "pips" in df else 0.0
        metrics["pips_avg"] = metrics["avg_pips"]
    except Exception:
        metrics["avg_pips"] = 0.0
        metrics["pips_avg"] = 0.0

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
    metrics = compute_metrics(trades)
    if len(instances) <= 1:
        return metrics

    instance_metrics = [instance.get("metrics", {}) for instance in instances]
    reliable_metrics = [metric for metric in instance_metrics if metric.get("balance_drawdown_reliable") is True]
    if len(reliable_metrics) != len(instance_metrics):
        metrics.update({
            "risk_metrics_reliable": False,
            "balance_drawdown_reliable": False,
            "balance_equity_current": None,
            "balance_equity_peak": None,
            "balance_current_drawdown": None,
            "balance_current_drawdown_pct": None,
            "balance_max_drawdown": None,
            "balance_max_drawdown_pct": None,
            "current_drawdown": None,
            "current_drawdown_pct": None,
            "max_drawdown_pct": None,
            "drawdown_basis": "mixed_accounts_unreliable",
            "drawdown_warning": UNRELIABLE_RISK_METRIC_WARNING,
        })
        return metrics

    max_dd_metric = max(reliable_metrics, key=lambda metric: safe_float(metric.get("balance_max_drawdown_pct")))
    max_current_metric = max(reliable_metrics, key=lambda metric: safe_float(metric.get("balance_current_drawdown_pct")))
    max_drawdown = max(safe_float(metric.get("balance_max_drawdown")) for metric in reliable_metrics)
    metrics.update({
        "risk_metrics_reliable": True,
        "balance_drawdown_reliable": True,
        "balance_equity_current": None,
        "balance_equity_peak": None,
        "balance_current_drawdown": max_current_metric.get("balance_current_drawdown"),
        "balance_current_drawdown_pct": max_current_metric.get("balance_current_drawdown_pct"),
        "balance_max_drawdown": max_drawdown,
        "balance_max_drawdown_pct": max_dd_metric.get("balance_max_drawdown_pct"),
        "current_drawdown": max_current_metric.get("balance_current_drawdown"),
        "current_drawdown_pct": max_current_metric.get("balance_current_drawdown_pct"),
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_dd_metric.get("balance_max_drawdown_pct"),
        "drawdown_basis": "max_instance_balance_after",
        "return_over_drawdown": round(metrics["net_profit"] / max_drawdown, 4) if max_drawdown else 0.0,
        "recovery_factor": round(metrics["net_profit"] / max_drawdown, 4) if max_drawdown else 0.0,
    })
    return metrics


def system_key_for_trade(trade: dict) -> tuple[str, str, int | None, str | None]:
    magic = safe_int(trade.get("magic"))
    strategy_id = trade.get("strategy_id")
    if magic > 0:
        return f"magic:{magic}", f"magic:{magic}", magic, strategy_id

    comment = str(trade.get("comment", "")).strip()
    if comment and comment.lower() not in ("0", "nan"):
        label = f"comment:{comment[:40]}"
        return f"{label}|account:{trade['account_id']}", label, None, strategy_id

    label = f"symbol:{trade.get('symbol', 'unknown')}"
    return f"{label}|account:{trade['account_id']}", label, None, strategy_id


def enrich_trade_strategy_identity(trade: dict) -> dict:
    if trade.get("strategy_id") and trade.get("instance_id"):
        return trade
    try:
        identity = database.strategy_identity_from_trade(trade)
        trade["strategy_id"] = trade.get("strategy_id") or identity.get("strategy_id")
        trade["instance_id"] = trade.get("instance_id") or identity.get("instance_id")
    except Exception as exc:
        logger.debug("No se pudo resolver identidad de estrategia para trade %s: %s", trade.get("ticket"), exc)
    return trade


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
        trade = enrich_trade_strategy_identity(trade)
        key, label, magic, strategy_id = system_key_for_trade(trade)
        system = grouped.setdefault(
            key,
            {
                "id": key,
                "label": label,
                "magic": magic,
                "strategy_id": strategy_id,
                "instances_map": {},
            },
        )
        if not system.get("strategy_id") and strategy_id:
            system["strategy_id"] = strategy_id

        acct_id = trade["account_id"]
        instance = system["instances_map"].setdefault(
            acct_id,
            {
                "account_id": acct_id,
                "account_key": trade.get("account_key", acct_id),
                "login": trade.get("login", acct_id),
                "account_type": trade["account_type"],
                "account_label": trade["account_label"],
                "account_color": trade["account_color"],
                "platform": trade["platform"],
                "server": trade.get("server", "unknown"),
                "strategy_id": trade.get("strategy_id", strategy_id),
                "instance_id": trade.get("instance_id"),
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
            if has_unreconciled_equity_identity(
                account_id=instance.get("account_id"),
                login=instance.get("login"),
                account_key=instance.get("account_key"),
                server=instance.get("server"),
            ):
                mask_unreliable_risk_metrics(metrics)
                instance["quality_warnings"] = [UNRELIABLE_RISK_METRIC_WARNING]
            action = determine_action(metrics)
            instance_actions.add(action)
            instance["metrics"] = metrics
            instance["action"] = action
            instance["rules_version"] = RULES_VERSION
            metrics_per_account[instance["account_id"]] = metrics
            instance["trades"] = [serialize_trade(t) for t in instance["trades"]]
            instances.append(instance)

        metrics_global = compute_global_metrics(instances)
        global_quality_warnings = []
        if any(not i.get("metrics", {}).get("risk_metrics_reliable", True) for i in instances):
            mask_unreliable_risk_metrics(metrics_global)
            global_quality_warnings.append(UNRELIABLE_RISK_METRIC_WARNING)
        action_global = determine_action(metrics_global)
        conflict = action_global == "mantener" and "cuarentena" in instance_actions
        if not system.get("strategy_id"):
            system["strategy_id"] = next((i.get("strategy_id") for i in instances if i.get("strategy_id")), None)

        systems.append(
            {
                **system,
                "instances": sorted(instances, key=lambda item: item["account_label"]),
                "accounts_count": len(instances),
                "metrics_global": metrics_global,
                "metrics_per_account": metrics_per_account,
                "action": "conflicto" if conflict else action_global,
                "action_global": action_global,
                "rules_version": RULES_VERSION,
                "conflict": conflict,
                "instance_actions": sorted(instance_actions),
                "quality_warnings": global_quality_warnings,
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

    systems.sort(
        key=lambda item: item["metrics_global"]["sharpe"]
        if item["metrics_global"].get("sharpe") is not None
        else float("-inf"),
        reverse=True,
    )
    return systems


def account_summary(accounts: list[dict], trades: list[dict]) -> list[dict]:
    summaries = []
    systems_by_account = {}
    for trade in trades:
        key, _label, _magic, _strategy_id = system_key_for_trade(trade)
        systems_by_account.setdefault(trade["account_id"], set()).add(key)

    for account in accounts:
        account_trades = [t for t in trades if t["account_id"] == account["id"]]
        metrics = compute_metrics(account_trades)
        quality_warnings = []
        if has_unreconciled_equity_identity(
            account_id=account.get("id"),
            login=account.get("login"),
            account_key=account.get("account_key"),
            server=account.get("server"),
        ):
            mask_unreliable_risk_metrics(metrics)
            quality_warnings.append(UNRELIABLE_RISK_METRIC_WARNING)
        account_state = read_account_state(account["id"], account)
        summaries.append(
            {
                **account,
                "account_key": account.get("account_key") or get_account_key(account.get("platform"), account.get("server", "unknown"), account["id"], account.get("type", "REAL")),
                "login": account.get("login", account["id"]),
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
                "quality_warnings": quality_warnings,
            }
        )
    return summaries


def build_strategy_metric_snapshot_rows(systems: list[dict]) -> list[dict]:
    rows = []
    for system in systems:
        global_metrics = system.get("metrics_global", {})
        rows.append(
            {
                "strategy_id": system.get("strategy_id"),
                "instance_id": None,
                "account_key": None,
                "metrics": global_metrics,
                "classification": system.get("action"),
                "recommended_action": system.get("action"),
                "rules_version": system.get("rules_version", RULES_VERSION),
                "payload": {
                    "scope": "global_system",
                    "system_id": system.get("id"),
                    "label": system.get("label"),
                    "magic": system.get("magic"),
                    "action_global": system.get("action_global"),
                    "conflict": system.get("conflict", False),
                    "instance_actions": system.get("instance_actions", []),
                    "metrics": global_metrics,
                },
            }
        )
        for instance in system.get("instances", []):
            metrics = instance.get("metrics", {})
            rows.append(
                {
                    "strategy_id": instance.get("strategy_id") or system.get("strategy_id"),
                    "instance_id": instance.get("instance_id"),
                    "account_key": instance.get("account_key") or instance.get("account_id"),
                    "metrics": metrics,
                    "classification": instance.get("action"),
                    "recommended_action": instance.get("action"),
                    "rules_version": instance.get("rules_version", RULES_VERSION),
                    "payload": {
                        "scope": "strategy_instance",
                        "system_id": system.get("id"),
                        "label": system.get("label"),
                        "magic": system.get("magic"),
                        "account_id": instance.get("account_id"),
                        "account_key": instance.get("account_key"),
                        "platform": instance.get("platform"),
                        "server": instance.get("server"),
                        "metrics": metrics,
                    },
                }
            )
    return rows


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
    patterns = []
    for alias in account_id_aliases(account_id):
        patterns.extend([
            f"account_history_*_{alias}.json",
            f"account_history_*_{alias}.csv",
            f"account_history_{alias}.json",
            f"account_history_{alias}.csv",
        ])
    patterns.extend(["account_history.json", "account_history.csv"])
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
    valid_account_ids = set(account_id_aliases(account_id))

    for path in account_state_candidates(account_id, account):
        try:
            if path.suffix.lower() == ".json":
                payload = read_json_file(path)
                snapshot = payload.get("snapshot", payload)
                file_account = str(snapshot.get("account", snapshot.get("account_id", "")))
                if file_account not in ("", *valid_account_ids):
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
                df = df[df["account"].astype(str).isin(valid_account_ids)]
            elif "account_id" in df.columns:
                df = df[df["account_id"].astype(str).isin(valid_account_ids)]
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
        account_matches = account_id in account_id_aliases(account["id"])
        account_label_norm = normalize_label(account.get("label"))
        account_type_norm = normalize_label(account.get("type"))
        csv_pattern = str(account.get("csv_pattern", "")).lower()
        file_name = Path(path).name.lower()

        if account_matches:
            score += 400
        if label_norm and label_norm == account_label_norm:
            score += 500
        if file_name == csv_pattern:
            score += 300
        if label_norm and label_norm == account_type_norm:
            score += 200
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
        account_id = canonical_account_id(account_id, accounts_by_id)
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


def allowed_trade_accounts() -> dict[str, dict]:
    allowed = account_index()

    def should_prefer_db_account(existing: dict | None, candidate: dict) -> bool:
        if not existing:
            return True
        existing_server = str(existing.get("server") or "unknown").lower()
        candidate_server = str(candidate.get("server") or "unknown").lower()
        if existing_server == "unknown" and candidate_server != "unknown":
            return True
        if str(existing.get("type") or "").upper() != str(candidate.get("type") or "").upper() and candidate_server != "unknown":
            return True
        if not existing.get("account_key") and candidate.get("account_key"):
            return True
        return False

    try:
        for row in database.get_accounts(active_only=True):
            login = str(row.get("login") or "")
            if not login:
                continue
            account = {
                "id": login,
                "login": login,
                "type": str(row.get("account_type") or "REAL").upper(),
                "label": row.get("alias") or login,
                "platform": row.get("platform") or "MT4",
                "server": row.get("server") or "unknown",
                "color": "#6b7280",
                "account_key": row.get("id"),
                "currency": row.get("currency") or "",
            }
            if should_prefer_db_account(allowed.get(login), account):
                allowed[login] = account
            allowed[row.get("id")] = account
    except Exception as exc:
        logger.exception("Error leyendo cuentas permitidas desde SQLite: %s", exc)
    return {str(k): v for k, v in allowed.items() if k}


def file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv_preview(path: Path) -> dict:
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "latin1"):
        try:
            df = pd.read_csv(path, sep=None, engine="python", encoding=encoding, nrows=1)
            if not df.empty:
                return df.iloc[0].to_dict()
        except Exception:
            continue
    return {}


def parse_account_from_monitor_name(path: Path) -> tuple[str | None, str | None]:
    match = re.match(r"^(?:account_history|open_trades|running_eas)_(.+?)_(-?\d+)\.(?:csv|json)$", path.name, re.IGNORECASE)
    if match:
        return match.group(2), match.group(1).upper()
    return None, None


def infer_discovered_account_metadata(path: Path, fallback_account_id: str | None = None, fallback_type: str | None = None) -> dict | None:
    row = {}
    try:
        if path.suffix.lower() == ".json":
            payload = read_json_file(path)
            row = payload.get("snapshot", payload)
            if not isinstance(row, dict):
                row = {}
        elif path.suffix.lower() == ".csv":
            row = read_csv_preview(path)
    except Exception:
        row = {}

    file_account_id, file_type = parse_account_from_csv_name(str(path))
    if not file_account_id:
        file_account_id, file_type = parse_account_from_monitor_name(path)
    account_id = str(
        row.get("account")
        or row.get("account_id")
        or row.get("account_number")
        or fallback_account_id
        or file_account_id
        or ""
    ).strip()
    if not account_id:
        return None
    platform = str(row.get("platform") or "MT4").upper()
    if platform not in ("MT4", "MT5"):
        platform = "MT4"
    label = str(row.get("account_label") or row.get("label") or fallback_type or file_type or account_id)
    account_type = str(fallback_type or file_type or infer_account_type(label)).upper()
    if account_type not in ("REAL", "DEMO"):
        account_type = infer_account_type(label)
    server = str(row.get("server") or row.get("broker") or "unknown")
    broker = str(row.get("broker") or row.get("broker_name") or server)
    return {
        "account_id": account_id,
        "platform": platform,
        "account_type": account_type,
        "label": label,
        "server": server,
        "broker_name": broker,
        "source_file": path.name,
        "payload": {"file": str(path), "sample": row},
    }


def record_discovered_account(path: Path, fallback_account_id: str | None = None, fallback_type: str | None = None) -> dict | None:
    metadata = infer_discovered_account_metadata(path, fallback_account_id, fallback_type)
    if not metadata:
        return None
    try:
        discovered = database.upsert_discovered_account(metadata)
        try:
            database.log_audit_event(
                event_type="account_discovered",
                entity_type="account",
                entity_id=metadata["account_id"],
                account_key=discovered.get("account_key") if discovered else metadata.get("account_key"),
                severity="warning" if (discovered or {}).get("status") == "pending_approval" else "info",
                message=f"Account discovered from {path.name}: {metadata['account_id']}",
                payload=metadata,
            )
        except Exception:
            logger.exception("Error registrando auditoria account_discovered para %s", path.name)
        return discovered
    except Exception:
        logger.exception("Error registrando cuenta descubierta desde %s", path.name)
        return None


def scan_discovered_account_files(data_folder: Path | str | None = None) -> list[dict]:
    folder = Path(data_folder) if data_folder else get_data_folder()
    allowed = allowed_trade_accounts()
    discovered = []
    patterns = [
        "trades_*.csv",
        "account_history*.csv",
        "account_history*.json",
        "open_trades*.csv",
        "open_trades*.json",
        "running_eas*.csv",
        "running_eas*.json",
    ]
    seen_files = set()
    for pattern in patterns:
        for path in folder.glob(pattern):
            if path in seen_files:
                continue
            seen_files.add(path)
            metadata = infer_discovered_account_metadata(path)
            if not metadata:
                continue
            account_id = canonical_account_id(metadata["account_id"], allowed)
            if allowed.get(account_id):
                continue
            row = record_discovered_account(path, account_id, metadata.get("account_type"))
            if row:
                discovered.append(row)
    return discovered


def discovered_block_reason(discovered: dict | None) -> str:
    status = (discovered or {}).get("status") or "pending_approval"
    if status == "rejected":
        return "account_rejected"
    if status == "ignored":
        return "account_ignored"
    if status == "approved":
        return "account_approved_not_synced"
    return "account_pending_approval"


def sync_closed_trades_incremental(data_folder: Path | str | None = None) -> dict:
    folder = Path(data_folder) if data_folder else get_data_folder()
    allowed_accounts = allowed_trade_accounts()
    discovered_seen = scan_discovered_account_files(folder)
    warnings = []
    selected_files = select_trade_files(folder, allowed_accounts, warnings)
    summary = {
        "files_checked": 0,
        "files_changed": 0,
        "new_trades_inserted": 0,
        "duplicates_skipped": 0,
        "rows_processed": 0,
        "files": [],
        "discovered_accounts_seen": discovered_seen,
        "blocked_files": [],
        "warnings": list(warnings),
        "errors": [],
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }

    for fpath in selected_files:
        path = Path(fpath)
        summary["files_checked"] += 1
        raw_account_id, account_type_from_name = parse_account_from_csv_name(fpath)
        account_id = canonical_account_id(raw_account_id, allowed_accounts)
        account = allowed_accounts.get(account_id)
        stat = path.stat()
        modified = file_mtime_iso(path)
        file_info = {
            "file": path.name,
            "source_file": str(path),
            "account_id": account_id,
            "last_modified_at": modified,
            "file_size": stat.st_size,
            "changed": False,
            "rows": 0,
            "new_trades_inserted": 0,
            "duplicates_skipped": 0,
            "errors": [],
        }

        if not account:
            discovered = record_discovered_account(path, account_id, account_type_from_name)
            reason = discovered_block_reason(discovered)
            message = f"{path.name}: cuenta {account_id} detectada pero no autorizada ({reason})"
            summary["warnings"].append(message)
            file_info["ignored"] = True
            file_info["ignore_reason"] = reason
            file_info["discovered_account"] = discovered
            summary["blocked_files"].append({
                "source_file": path.name,
                "reason": reason,
                "account_id": account_id,
                "platform": (discovered or {}).get("platform"),
                "status": (discovered or {}).get("status"),
            })
            database.upsert_trade_file_state(
                str(path),
                account_id=account_id,
                last_modified_at=modified,
                file_size=stat.st_size,
                last_error=message,
            )
            try:
                database.log_audit_event(
                    event_type="trade_file_ignored",
                    entity_type="trade_file",
                    entity_id=path.name,
                    severity="warning",
                    message=message,
                    payload={"source_file": str(path), "account_id": account_id, "reason": reason, "discovered_account": discovered},
                )
            except Exception:
                logger.exception("Error registrando trade_file_ignored para %s", path.name)
            summary["files"].append(file_info)
            continue

        state = database.get_trade_file_state(str(path))
        if (
            state
            and state.get("last_modified_at") == modified
            and int(state.get("file_size") or -1) == int(stat.st_size)
            and not state.get("last_error")
        ):
            file_info["changed"] = False
            file_info["last_successful_import_at"] = state.get("last_successful_import_at")
            file_info["rows"] = state.get("last_row_count") or 0
            summary["files"].append(file_info)
            continue

        summary["files_changed"] += 1
        file_info["changed"] = True
        import_run_id = None
        raw_errors = []
        raw_ok = 0
        account_trades = []
        file_hash = ""
        try:
            file_hash = file_sha256(path)
            if state and state.get("last_imported_hash") == file_hash and not state.get("last_error"):
                database.upsert_trade_file_state(
                    str(path),
                    account_key=account.get("account_key"),
                    account_id=account_id,
                    platform=account.get("platform"),
                    last_modified_at=modified,
                    file_size=stat.st_size,
                    last_row_count=state.get("last_row_count"),
                    last_imported_hash=file_hash,
                    last_successful_import_at=state.get("last_successful_import_at"),
                    last_error=None,
                )
                file_info["changed"] = False
                file_info["hash_unchanged"] = True
                file_info["rows"] = state.get("last_row_count") or 0
                file_info["last_successful_import_at"] = state.get("last_successful_import_at")
                summary["files_changed"] -= 1
                summary["files"].append(file_info)
                continue

            df = read_trade_csv(str(path))
            file_info["rows"] = int(len(df))
            summary["rows_processed"] += int(len(df))
            platform = detect_platform(df, account)
            account = dict(account)
            account["platform"] = platform
            account["type"] = account_type_for_import(account, account_type_from_name)
            account["server"] = infer_server_from_trade_dataframe(df, account)
            account["account_key"] = get_account_key(
                platform,
                account.get("server", "unknown"),
                account.get("login", account["id"]),
                account.get("type", "REAL"),
            )
            import_run_id = database.start_import_run(account["account_key"], str(path), hash_file=file_hash)

            for row_idx, row in df.iterrows():
                raw_payload = row.to_dict() if hasattr(row, "to_dict") else dict(row)
                try:
                    trade = normalize_trade(row, platform, account, path.name)
                    if not is_valid_trade(trade):
                        raise ValueError("trade excluido por filtros de comentario")
                    raw_import_id = database.record_raw_trade_import(
                        import_run_id,
                        path.name,
                        platform,
                        account.get("account_key"),
                        account_id,
                        int(row_idx) + 1,
                        raw_payload,
                        "ok",
                    )
                    trade["import_run_id"] = import_run_id
                    trade["source_file"] = path.name
                    trade["source_row_hash"] = database.raw_payload_hash(raw_payload)
                    trade["raw_import_id"] = raw_import_id
                    trade["normalized_at"] = datetime.now(timezone.utc).isoformat()
                    account_trades.append(trade)
                    raw_ok += 1
                except Exception as exc:
                    error = {"row": int(row_idx) + 1, "error": str(exc)}
                    raw_errors.append(error)
                    try:
                        database.record_raw_trade_import(
                            import_run_id,
                            path.name,
                            platform,
                            account.get("account_key"),
                            account_id,
                            int(row_idx) + 1,
                            raw_payload,
                            "error",
                            str(exc),
                        )
                    except Exception:
                        logger.exception("Error registrando raw row incremental ERROR en %s", path.name)

            before_count = database.count_closed_trades()
            database.upsert_closed_trades_only(account_trades)
            after_count = database.count_closed_trades()
            inserted = max(0, after_count - before_count)
            duplicates = max(0, len(account_trades) - inserted)
            status = "ok" if not raw_errors else "partial"
            database.finish_import_run(
                import_run_id,
                rows_processed=int(len(df)),
                rows_inserted=inserted,
                status=status,
                error_log=raw_errors[:100],
            )
            database.upsert_trade_file_state(
                str(path),
                account_key=account.get("account_key"),
                account_id=account_id,
                platform=platform,
                last_modified_at=modified,
                file_size=stat.st_size,
                last_row_count=int(len(df)),
                last_imported_hash=file_hash,
                last_successful_import_at=datetime.now(timezone.utc).isoformat(),
                last_error=None if status == "ok" else json.dumps(raw_errors[:5], ensure_ascii=False),
            )
            file_info.update({
                "platform": platform,
                "account_key": account.get("account_key"),
                "import_run_id": import_run_id,
                "raw_ok": raw_ok,
                "raw_errors": len(raw_errors),
                "new_trades_inserted": inserted,
                "duplicates_skipped": duplicates,
            })
            summary["new_trades_inserted"] += inserted
            summary["duplicates_skipped"] += duplicates
        except Exception as exc:
            message = f"{path.name}: {exc}"
            logger.exception("Error en importacion incremental de %s", path.name)
            summary["errors"].append(message)
            file_info["errors"].append(message)
            if import_run_id:
                try:
                    database.finish_import_run(import_run_id, file_info["rows"], 0, "error", [{"error": str(exc)}])
                except Exception:
                    logger.exception("Error cerrando import_run incremental fallido para %s", path.name)
            database.upsert_trade_file_state(
                str(path),
                account_key=account.get("account_key") if account else None,
                account_id=account_id,
                platform=account.get("platform") if account else None,
                last_modified_at=modified,
                file_size=stat.st_size,
                last_row_count=file_info["rows"],
                last_imported_hash=file_hash or None,
                last_error=str(exc),
            )
        summary["files"].append(file_info)

    return summary


def build_portfolio(
    all_trades: list[dict],
    effective_accounts: list[dict],
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    debug_files: list[dict] | None = None,
    source: str = "csv",
) -> dict:
    warnings = warnings or []
    errors = errors or []
    debug_files = debug_files or []

    systems = group_systems_globally(all_trades)
    accounts_summary = account_summary(effective_accounts, all_trades)

    try:
        metrics_service.persist_system_metrics(systems, build_strategy_metric_snapshot_rows(systems), RULES_VERSION)
    except Exception as exc:
        logger.exception("Error guardando metricas en SQLite: %s", exc)

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
        "data_folder": str(get_data_folder()),
        "source": source,
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


def load_portfolio_from_db() -> dict:
    db_trades = database.get_trades()
    accounts_by_id = account_index()
    effective_accounts_by_id = {account["id"]: dict(account) for account in cfg.get("accounts", [])}
    deduped_trades = {}

    for trade in db_trades:
        trade["account_id"] = canonical_account_id(trade.get("account_id"), accounts_by_id)
        account = effective_accounts_by_id.get(trade["account_id"]) or accounts_by_id.get(trade["account_id"])
        if not account:
            account = {
                "id": trade["account_id"],
                "login": trade["account_id"],
                "type": infer_account_type(trade.get("account_label", "")),
                "label": trade.get("account_label") or f"Cuenta {trade['account_id']}",
                "platform": trade.get("platform", "MT4"),
                "server": trade.get("server", "unknown"),
                "color": "#6b7280",
            }
            account["account_key"] = trade.get("account_key") or get_account_key(account["platform"], account["server"], account["login"], account["type"])
            effective_accounts_by_id[trade["account_id"]] = account
        trade["account_type"] = account.get("type", "REAL")
        trade["account_label"] = account.get("label", trade.get("account_label", trade["account_id"]))
        trade["account_key"] = trade.get("account_key") or account.get("account_key") or get_account_key(trade.get("platform"), account.get("server", "unknown"), trade["account_id"], account.get("type", "REAL"))
        trade["login"] = account.get("login", trade["account_id"])
        trade["account_color"] = account.get("color", "#6b7280")
        trade["platform"] = trade.get("platform") or account.get("platform", "MT4")
        trade.setdefault("broker", "")
        trade.setdefault("currency", "")
        dedupe_key = (trade["account_id"], trade["platform"], str(trade.get("ticket", "")))
        deduped_trades[dedupe_key] = trade

    db_trades = sorted(
        deduped_trades.values(),
        key=lambda item: (str(item.get("close_time", "")), str(item.get("account_id", "")), str(item.get("ticket", ""))),
    )

    debug_files = [{
        "file": "qa_portfolio.db",
        "columns": [
            "account_id", "platform", "magic", "ticket", "symbol", "trade_type",
            "open_time", "close_time", "open_price", "close_price", "lots", "profit",
            "pips", "comment", "label",
        ],
        "dtypes": {},
        "rows": len(db_trades),
        "sample_values": [
            {
                "profit": str(t.get("profit", "")),
                "magic": str(t.get("magic", "")),
                "open_time": str(t.get("open_time", "")),
                "close_time": str(t.get("close_time", "")),
            }
            for t in db_trades[:5]
        ],
    }]
    return build_portfolio(
        db_trades,
        list(effective_accounts_by_id.values()),
        warnings=[],
        errors=[],
        debug_files=debug_files,
        source="sqlite",
    )


def load_portfolio_from_csv(rebuild_db: bool = False) -> dict:
    folder = get_data_folder()
    accounts = cfg.get("accounts", [])
    configured_account_ids = {a["id"] for a in accounts}
    accounts_by_id = allowed_trade_accounts()
    effective_accounts_by_id = {account["id"]: dict(account) for account in accounts}
    errors = []
    warnings = []
    all_trades = []
    debug_files = []

    for fpath in select_trade_files(folder, accounts_by_id, warnings):
        raw_account_id, account_type_from_name = parse_account_from_csv_name(fpath)
        account_id = canonical_account_id(raw_account_id, accounts_by_id)

        account = accounts_by_id.get(account_id)
        if not account:
            discovered = record_discovered_account(Path(fpath), account_id, account_type_from_name)
            reason = discovered_block_reason(discovered)
            warnings.append(f"{Path(fpath).name}: cuenta {account_id} no autorizada ({reason})")
            try:
                database.log_audit_event(
                    event_type="unknown_account_detected",
                    entity_type="account",
                    entity_id=account_id,
                    severity="warning",
                    message=f"CSV import ignored account not approved: {account_id}",
                    payload={"source_file": Path(fpath).name, "account_id": account_id, "reason": reason, "discovered_account": discovered},
                )
            except Exception as exc:
                logger.exception("Error registrando auditoria unknown_account_detected: %s", exc)
            continue

        try:
            df = read_trade_csv(fpath)
            if account_id not in effective_accounts_by_id:
                effective_accounts_by_id[account_id] = dict(account)
            if account_id not in configured_account_ids and "account_label" in df.columns and len(df):
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
            account["type"] = account_type_for_import(account, account_type_from_name)
            effective_accounts_by_id[account_id]["type"] = account["type"]
            account["server"] = infer_server_from_trade_dataframe(df, account)
            effective_accounts_by_id[account_id]["server"] = account["server"]
            account["account_key"] = get_account_key(platform, account.get("server", "unknown"), account.get("login", account["id"]), account.get("type", "REAL"))
            effective_accounts_by_id[account_id]["account_key"] = account["account_key"]
            import_run_id = None
            raw_ok = 0
            raw_errors = 0
            raw_error_messages = []
            try:
                import_run_id = database.start_import_run(account["account_key"], Path(fpath).name)
            except Exception as exc:
                logger.exception("Error creando import_run para %s: %s", Path(fpath).name, exc)
            debug_files.append({
                "file": Path(fpath).name,
                "columns": list(df.columns),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "rows": int(len(df)),
                "sample_values": df[["profit", "magic", "open_time", "close_time"]].head(5).astype(str).to_dict(orient="records") if len(df) else [],
            })
            account_trades = []
            for row_idx, row in df.iterrows():
                raw_payload = row.to_dict() if hasattr(row, "to_dict") else dict(row)
                try:
                    trade = normalize_trade(row, platform, account, Path(fpath).name)
                    if not is_valid_trade(trade):
                        raise ValueError("trade excluido por filtros de comentario")
                    raw_import_id = database.record_raw_trade_import(
                        import_run_id,
                        Path(fpath).name,
                        platform,
                        account.get("account_key"),
                        account_id,
                        int(row_idx) + 1,
                        raw_payload,
                        "ok",
                    )
                    trade["import_run_id"] = import_run_id
                    trade["source_file"] = Path(fpath).name
                    trade["source_row_hash"] = database.raw_payload_hash(raw_payload)
                    trade["raw_import_id"] = raw_import_id
                    trade["normalized_at"] = datetime.now(timezone.utc).isoformat()
                    raw_ok += 1
                    account_trades.append(trade)
                    all_trades.append(trade)
                except Exception as exc:
                    raw_errors += 1
                    raw_error_messages.append({"row": int(row_idx) + 1, "error": str(exc)})
                    try:
                        database.record_raw_trade_import(
                            import_run_id,
                            Path(fpath).name,
                            platform,
                            account.get("account_key"),
                            account_id,
                            int(row_idx) + 1,
                            raw_payload,
                            "error",
                            str(exc),
                        )
                    except Exception as raw_exc:
                        logger.exception("Error registrando raw row ERROR en %s: %s", Path(fpath).name, raw_exc)
            if not rebuild_db:
                try:
                    inserted = database.upsert_trades(account_trades, account_id, platform)
                    debug_files[-1]["sqlite_inserted"] = inserted
                except Exception as exc:
                    logger.exception("Error insertando trades en SQLite desde %s: %s", Path(fpath).name, exc)
                    warnings.append(f"{Path(fpath).name}: SQLite no disponible, se uso CSV en memoria")
            if import_run_id:
                database.finish_import_run(
                    import_run_id,
                    rows_processed=int(len(df)),
                    rows_inserted=len(account_trades),
                    status="ok" if raw_errors == 0 else "partial",
                    error_log=raw_error_messages[:100],
                )
                debug_files[-1]["raw_import_run_id"] = import_run_id
                debug_files[-1]["raw_ok"] = raw_ok
                debug_files[-1]["raw_errors"] = raw_errors
        except Exception as exc:
            logger.exception("Error cargando CSV %s: %s", Path(fpath).name, exc)
            errors.append(f"{Path(fpath).name}: {exc}")

    if rebuild_db:
        try:
            replaced = database.replace_trades(all_trades)
            for item in debug_files:
                item["sqlite_rebuilt"] = True
            warnings.append(f"SQLite reconstruido desde CSV: {replaced} trades activos")
        except Exception as exc:
            logger.exception("Error reconstruyendo SQLite desde CSV: %s", exc)
            warnings.append(f"SQLite no se pudo reconstruir; se uso CSV en memoria: {exc}")

    return build_portfolio(
        all_trades,
        list(effective_accounts_by_id.values()),
        warnings=warnings,
        errors=errors,
        debug_files=debug_files,
        source="csv",
    )


def load_portfolio(force_csv: bool = False) -> dict:
    if force_csv:
        logger.warning("force_csv solicitado, ignorado: el dashboard usa SQLite como fuente de verdad")
    try:
        if database.db_has_data():
            return load_portfolio_from_db()
    except Exception as exc:
        logger.exception("SQLite no disponible para dashboard: %s", exc)

    sync_summary = sync_closed_trades_incremental(get_data_folder())
    _cache["last_closed_trade_sync"] = sync_summary
    try:
        if database.db_has_data():
            db_portfolio = load_portfolio_from_db()
            db_portfolio.setdefault("warnings", []).append("SQLite inicializado desde CSV antes de renderizar dashboard")
            db_portfolio.setdefault("debug", {})["incremental_sync"] = sync_summary
            return db_portfolio
    except Exception as exc:
        logger.exception("SQLite no disponible despues de sync incremental: %s", exc)

    return build_portfolio(
        [],
        list(cfg.get("accounts", [])),
        warnings=["SQLite no contiene trades; dashboard no usa CSV como fuente de lectura"],
        errors=sync_summary.get("errors", []),
        debug_files=sync_summary.get("files", []),
        source="sqlite",
    )


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
    response = make_response(render_template("index.html"))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/api/portfolio")
def api_portfolio():
    force = request.args.get("reload", "false").lower() == "true"
    return jsonify(get_portfolio(force_reload=force))


@app.route("/api/portfolio/summary")
@app.route("/api/summary")
def api_portfolio_summary():
    return jsonify(get_portfolio().get("summary", {}))


@app.route("/api/sync/trades", methods=["POST"])
def api_sync_trades():
    sync_summary = sync_closed_trades_incremental(get_data_folder())
    _cache["last_closed_trade_sync"] = sync_summary
    _cache["portfolio"] = load_portfolio()
    _cache["last_loaded"] = datetime.now()
    return jsonify({
        "ok": not sync_summary.get("errors"),
        "closed_trade_sync": sync_summary,
        "summary": _cache["portfolio"].get("summary", {}),
    })


@app.route("/api/accounts", methods=["GET", "POST"])
def api_accounts():
    if request.method == "POST":
        if not _check_api_key():
            return jsonify({"ok": False, "error": "api key invalida"}), 401
        body = request.get_json(force=True)
        if not body.get("login") and not body.get("id"):
            return jsonify({"ok": False, "error": "login requerido"}), 400
        try:
            account = database.upsert_account(body)
            return jsonify({"ok": True, "account": account})
        except Exception as exc:
            logger.exception("Error registrando cuenta en SQLite: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    data = get_portfolio()
    db_accounts = []
    try:
        db_accounts = database.get_accounts()
    except Exception as exc:
        logger.exception("Error leyendo cuentas SQLite: %s", exc)
    return jsonify({
        "accounts": data.get("accounts", []),
        "db_accounts": db_accounts,
        "count": len(data.get("accounts", [])),
        "db_count": len(db_accounts),
    })


@app.route("/api/accounts/discovered")
def api_discovered_accounts():
    status = request.args.get("status")
    rows = database.get_discovered_accounts(status=status)
    return jsonify({"accounts": rows, "count": len(rows), "status": status or "all"})


@app.route("/api/accounts/discovered/<int:discovered_id>/approve", methods=["POST"])
def api_approve_discovered_account(discovered_id):
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    discovered = database.get_discovered_account(discovered_id)
    if not discovered:
        return jsonify({"ok": False, "error": "cuenta descubierta no encontrada"}), 404
    body = request.get_json(silent=True) or {}
    account_type = str(body.get("type") or body.get("account_type") or discovered.get("account_type") or "REAL").upper()
    if account_type not in ("REAL", "DEMO"):
        return jsonify({"ok": False, "error": "type debe ser REAL o DEMO"}), 400
    account = {
        "id": discovered["account_id"],
        "login": discovered["account_id"],
        "type": account_type,
        "label": body.get("label") or discovered.get("label") or discovered["account_id"],
        "platform": discovered.get("platform") or "MT4",
        "server": discovered.get("server") or "unknown",
        "currency": body.get("currency") or "",
        "color": body.get("color") or "#6b7280",
    }
    try:
        db_account = database.upsert_account(account)
        updated = database.update_discovered_account_status(discovered_id, "approved", body.get("notes"))
        database.log_audit_event(
            event_type="discovered_account_approved",
            entity_type="account",
            entity_id=discovered["account_id"],
            account_key=db_account.get("id"),
            severity="info",
            message=f"Discovered account approved: {discovered['account_id']}",
            payload={"discovered": discovered, "account": account, "overrides": body},
        )
        sync_summary = sync_closed_trades_incremental(get_data_folder())
        _cache["last_closed_trade_sync"] = sync_summary
        if sync_summary.get("new_trades_inserted", 0) > 0:
            _cache["portfolio"] = load_portfolio()
            _cache["last_loaded"] = datetime.now()
        else:
            _cache["portfolio"] = None
            _cache["last_loaded"] = None
        return jsonify({
            "ok": True,
            "status": "approved",
            "account_id": discovered["account_id"],
            "account_key": db_account.get("id"),
            "account": db_account,
            "discovered_account": updated,
            "closed_trade_sync": sync_summary,
            "message": "Account approved and will be imported automatically",
        })
    except Exception as exc:
        logger.exception("Error aprobando cuenta descubierta %s", discovered_id)
        return jsonify({"ok": False, "error": str(exc)}), 500


def update_discovered_account_action(discovered_id: int, status: str):
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    body = request.get_json(silent=True) or {}
    discovered = database.get_discovered_account(discovered_id)
    if not discovered:
        return jsonify({"ok": False, "error": "cuenta descubierta no encontrada"}), 404
    updated = database.update_discovered_account_status(discovered_id, status, body.get("notes"))
    database.log_audit_event(
        event_type=f"discovered_account_{status}",
        entity_type="account",
        entity_id=discovered["account_id"],
        account_key=discovered.get("account_key"),
        severity="warning" if status == "rejected" else "info",
        message=f"Discovered account {status}: {discovered['account_id']}",
        payload={"before": discovered, "after": updated, "notes": body.get("notes")},
    )
    return jsonify({"ok": True, "status": status, "account_id": discovered["account_id"], "discovered_account": updated})


@app.route("/api/accounts/discovered/<int:discovered_id>/reject", methods=["POST"])
def api_reject_discovered_account(discovered_id):
    return update_discovered_account_action(discovered_id, "rejected")


@app.route("/api/accounts/discovered/<int:discovered_id>/ignore", methods=["POST"])
def api_ignore_discovered_account(discovered_id):
    return update_discovered_account_action(discovered_id, "ignored")


@app.route("/api/debug/trades")
def api_debug_trades():
    data = get_portfolio(force_reload=request.args.get("reload", "false").lower() == "true")
    debug = data.get("debug", {})
    trade_file_states = []
    discovered_accounts = []
    db_accounts = []
    try:
        trade_file_states = database.get_trade_file_states()
    except Exception as exc:
        logger.exception("Error leyendo trade_file_state: %s", exc)
    try:
        discovered_accounts = database.get_discovered_accounts()
    except Exception as exc:
        logger.exception("Error leyendo discovered_accounts: %s", exc)
    try:
        db_accounts = database.get_accounts()
    except Exception as exc:
        logger.exception("Error leyendo accounts para debug: %s", exc)
    blocked_files = []
    for item in (_cache.get("last_closed_trade_sync") or {}).get("blocked_files", []):
        blocked_files.append(item)
    pending = [a for a in discovered_accounts if a.get("status") == "pending_approval"]
    rejected = [a for a in discovered_accounts if a.get("status") == "rejected"]
    ignored = [a for a in discovered_accounts if a.get("status") == "ignored"]
    return jsonify({
        "loaded_at": data.get("loaded_at"),
        "summary": data.get("summary", {}),
        "warnings": data.get("warnings", []),
        "errors": data.get("errors", []),
        "files": debug.get("files", []),
        "incremental_sync": _cache.get("last_closed_trade_sync"),
        "trade_file_state": trade_file_states,
        "authorized_accounts": len(db_accounts),
        "discovered_pending_accounts": len(pending),
        "discovered_rejected_accounts": len(rejected),
        "discovered_ignored_accounts": len(ignored),
        "discovered_accounts": discovered_accounts,
        "blocked_files": blocked_files,
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
            "account_key": account.get("account_key"),
            "login": account.get("login", account["id"]),
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


@app.route("/api/debug/raw-imports")
def api_debug_raw_imports():
    import_run_id = request.args.get("import_run_id")
    limit = safe_int(request.args.get("limit"), 500)
    rows = database.get_raw_trade_imports(safe_int(import_run_id) if import_run_id else None, limit)
    return jsonify({"rows": rows, "count": len(rows)})


@app.route("/api/debug/trade-conflicts")
def api_debug_trade_conflicts():
    limit = safe_int(request.args.get("limit"), 500)
    rows = database.get_trade_import_conflicts(limit)
    return jsonify({"conflicts": rows, "count": len(rows)})


@app.route("/api/audit-events")
def api_audit_events():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    limit = safe_int(request.args.get("limit"), 500)
    rows = audit_service.list_events(request.args, limit)
    return jsonify({"events": rows, "count": len(rows)})


def data_quality_status(checks: list[dict]) -> str:
    statuses = {check.get("status") for check in checks}
    if "critical" in statuses:
        return "critical"
    if "warning" in statuses:
        return "warning"
    return "ok"


def csv_freshness_check(max_age_hours: int = 24) -> dict:
    folder = get_data_folder()
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
        for path in folder.glob(pattern):
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
            "payload": {"data_folder": str(folder), "max_age_hours": max_age_hours},
        }
    return {
        "name": "stale_csv_files",
        "status": "warning" if stale else "ok",
        "count": len(stale),
        "message": "CSV files are fresh" if not stale else f"CSV files older than {max_age_hours} hours detected",
        "payload": {"data_folder": str(folder), "files_seen": files_seen, "stale": stale[:50]},
    }


def trade_file_state_quality_check() -> dict:
    try:
        states = database.get_trade_file_states()
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


def discovered_accounts_quality_checks() -> list[dict]:
    try:
        rows = database.get_discovered_accounts()
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
    blocked_files = (_cache.get("last_closed_trade_sync") or {}).get("blocked_files", [])
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


@app.route("/api/data-quality")
def api_data_quality():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    csv_max_age_hours = safe_int(request.args.get("csv_max_age_hours"), 24)
    stale_positions_hours = safe_int(request.args.get("stale_positions_hours"), 24)
    old_commands_minutes = safe_int(request.args.get("old_commands_minutes"), 30)
    equity_gap_minutes = safe_int(request.args.get("equity_gap_minutes"), 180)
    blocked_files = (_cache.get("last_closed_trade_sync") or {}).get("blocked_files", [])
    return jsonify(data_quality_service.build_status(
        get_data_folder(),
        blocked_files=blocked_files,
        csv_max_age_hours=csv_max_age_hours,
        stale_positions_hours=stale_positions_hours,
        old_commands_minutes=old_commands_minutes,
        equity_gap_minutes=equity_gap_minutes,
    ))


@app.route("/api/temporal-analysis")
def api_temporal_analysis():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    return jsonify(metrics_service.temporal_breakdown())


@app.route("/api/pnl-breakdown")
def api_pnl_breakdown():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    return jsonify(metrics_service.pnl_cost_breakdown())


@app.route("/api/accounts/<account_id>/systems")
def api_account_systems(account_id):
    systems = []
    for system in get_portfolio().get("systems", []):
        instances = [i for i in system["instances"] if account_matches_identifier(i, account_id)]
        if not instances:
            continue
        scoped = dict(system)
        scoped["instances"] = instances
        systems.append(scoped)
    return jsonify({"systems": systems, "count": len(systems)})


def resolve_account_identifier(identifier: str) -> str:
    for account in cfg.get("accounts", []):
        if identifier in {account.get("account_key"), account.get("id"), account.get("login")}:
            return database.resolve_account_key(
                account.get("login", account.get("id")),
                platform=account.get("platform"),
                server=account.get("server", "unknown"),
                account_type=account.get("type", "real"),
                alias=account.get("label"),
                currency=account.get("currency", ""),
            )
    return database.resolve_account_key(identifier)


def account_rows_with_twin_fallback(account_key: str, table_name: str, loader):
    rows = loader(account_key)
    resolved_key = account_key
    fallback = None
    if not rows:
        fallback = database.find_account_twin_with_data(account_key, table_name)
        if fallback:
            resolved_key = fallback["id"]
            rows = loader(resolved_key)
    return rows, resolved_key, fallback


def broker_sync_accounts() -> list[dict]:
    accounts = [dict(account) for account in cfg.get("accounts", [])]
    seen = {str(account.get("account_key") or account.get("id")) for account in accounts}
    seen_identity = {
        (
            str(account.get("platform", "")).upper(),
            str(account.get("login", account.get("id", ""))),
            str(account.get("type", account.get("account_type", ""))).lower(),
        )
        for account in accounts
    }
    try:
        for row in database.get_accounts():
            identity = (
                str(row.get("platform", "")).upper(),
                str(row.get("login", "")),
                str(row.get("account_type", "")).lower(),
            )
            if row["id"] in seen or identity in seen_identity:
                continue
            accounts.append({
                "id": row.get("login"),
                "login": row.get("login"),
                "platform": row.get("platform"),
                "server": row.get("server"),
                "type": row.get("account_type"),
                "label": row.get("alias"),
                "currency": row.get("currency"),
                "account_key": row.get("id"),
            })
            seen.add(row["id"])
            seen_identity.add(identity)
    except Exception as exc:
        logger.exception("Error preparando cuentas para sync broker: %s", exc)
    return accounts


@app.route("/api/accounts/<account_id>/equity")
def api_account_equity_points(account_id):
    account_key = resolve_account_identifier(account_id)
    limit = safe_int(request.args.get("limit"), 2000)
    points, resolved_key, fallback = account_rows_with_twin_fallback(
        account_key,
        "equity_points",
        lambda key: database.get_equity_points(key, limit),
    )
    return jsonify({
        "account_key": resolved_key,
        "requested_account_key": account_key,
        "fallback_account_key": fallback["id"] if fallback else None,
        "identity_fallback": bool(fallback),
        "warning": "Datos devueltos desde cuenta gemela por login+platform" if fallback else None,
        "points": points,
    })


@app.route("/api/accounts/<account_id>/snapshots")
def api_account_snapshots(account_id):
    account_key = resolve_account_identifier(account_id)
    limit = safe_int(request.args.get("limit"), 500)
    snapshots, resolved_key, fallback = account_rows_with_twin_fallback(
        account_key,
        "account_snapshots",
        lambda key: database.get_account_snapshots(key, limit),
    )
    return jsonify({
        "account_key": resolved_key,
        "requested_account_key": account_key,
        "fallback_account_key": fallback["id"] if fallback else None,
        "identity_fallback": bool(fallback),
        "warning": "Datos devueltos desde cuenta gemela por login+platform" if fallback else None,
        "snapshots": snapshots,
    })


@app.route("/api/accounts/<account_id>/open-positions")
def api_account_open_positions(account_id):
    account_key = resolve_account_identifier(account_id)
    positions, resolved_key, fallback = account_rows_with_twin_fallback(
        account_key,
        "open_positions",
        database.get_open_positions,
    )
    return jsonify({
        "account_key": resolved_key,
        "requested_account_key": account_key,
        "fallback_account_key": fallback["id"] if fallback else None,
        "identity_fallback": bool(fallback),
        "warning": "Datos devueltos desde cuenta gemela por login+platform" if fallback else None,
        "positions": positions,
    })


@app.route("/api/sync/broker", methods=["POST"])
def api_sync_broker():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    results = broker_sync_service.sync_all(get_data_folder(), broker_sync_accounts())
    closed_trade_sync = sync_closed_trades_incremental(get_data_folder())
    _cache["last_closed_trade_sync"] = closed_trade_sync
    if closed_trade_sync.get("new_trades_inserted", 0) > 0:
        _cache["portfolio"] = load_portfolio()
        _cache["last_loaded"] = datetime.now()
    return jsonify({
        "ok": all(item.get("ok") for item in results) and not closed_trade_sync.get("errors"),
        "results": results,
        "closed_trade_sync": closed_trade_sync,
    })


@app.route("/api/systems")
def api_systems():
    systems = get_portfolio().get("systems", [])
    account_id = request.args.get("account_id")
    account_key = request.args.get("account_key")
    account_type = request.args.get("type")
    action = request.args.get("action")

    if account_id:
        systems = [s for s in systems if any(account_matches_identifier(i, account_id) for i in s["instances"])]
    if account_key:
        systems = [s for s in systems if any(account_matches_identifier(i, account_key) for i in s["instances"])]
    if account_type and account_type.upper() in ("REAL", "DEMO"):
        systems = [s for s in systems if any(i["account_type"] == account_type.upper() for i in s["instances"])]
    if action:
        systems = [s for s in systems if s["action"] == action or action in s.get("instance_actions", [])]

    systems = strategy_identity_service.enrich_systems(systems)
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


@app.route("/api/strategies")
def api_strategy_definitions():
    limit = safe_int(request.args.get("limit"), 1000)
    strategies = metrics_service.strategy_definitions(limit)
    return jsonify({"strategies": strategies, "count": len(strategies)})


@app.route("/api/strategy-instances")
def api_strategy_instances():
    limit = safe_int(request.args.get("limit"), 2000)
    strategy_id = request.args.get("strategy_id")
    instances = metrics_service.strategy_instances(strategy_id, limit)
    return jsonify({"instances": instances, "count": len(instances)})


@app.route("/api/strategy-versions", methods=["GET"])
def api_strategy_versions():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    rows = strategy_identity_service.list_strategy_versions({
        "strategy_id": request.args.get("strategy_id"),
        "limit": safe_int(request.args.get("limit"), 500),
    })
    return jsonify({"strategy_versions": rows, "count": len(rows)})


@app.route("/api/strategy-versions", methods=["POST"])
def api_strategy_versions_create():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    try:
        row = strategy_identity_service.create_strategy_version(request.get_json(force=True))
        return jsonify({"ok": True, "strategy_version": row}), 201
    except strategy_identity_service.StrategyIdentityValidationError as exc:
        return jsonify({"ok": False, "error": str(exc), "reason": exc.reason}), 400
    except Exception as exc:
        logger.exception("Error creando strategy_version: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/risk-profiles", methods=["GET"])
def api_risk_profiles():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    rows = strategy_identity_service.list_risk_profiles({"limit": safe_int(request.args.get("limit"), 500)})
    return jsonify({"risk_profiles": rows, "count": len(rows)})


@app.route("/api/risk-profiles", methods=["POST"])
def api_risk_profiles_create():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    try:
        row = strategy_identity_service.create_risk_profile(request.get_json(force=True))
        return jsonify({"ok": True, "risk_profile": row}), 201
    except strategy_identity_service.StrategyIdentityValidationError as exc:
        return jsonify({"ok": False, "error": str(exc), "reason": exc.reason}), 400
    except Exception as exc:
        logger.exception("Error creando risk_profile: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/deployment-events", methods=["GET"])
def api_deployment_events():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    rows = strategy_identity_service.list_deployment_events({
        "strategy_id": request.args.get("strategy_id"),
        "instance_id": request.args.get("instance_id"),
        "account_key": request.args.get("account_key"),
        "limit": safe_int(request.args.get("limit"), 500),
    })
    return jsonify({"deployment_events": rows, "count": len(rows)})


@app.route("/api/deployment-events", methods=["POST"])
def api_deployment_events_create():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    try:
        row = strategy_identity_service.create_deployment_event(request.get_json(force=True))
        return jsonify({"ok": True, "deployment_event": row}), 201
    except strategy_identity_service.StrategyIdentityValidationError as exc:
        return jsonify({"ok": False, "error": str(exc), "reason": exc.reason}), 400
    except Exception as exc:
        logger.exception("Error creando deployment_event: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/strategy-metrics")
def api_strategy_metric_snapshots():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    limit = safe_int(request.args.get("limit"), 500)
    strategy_id = request.args.get("strategy_id")
    instance_id = request.args.get("instance_id")
    account_key = request.args.get("account_key")
    if not account_key and request.args.get("account_id"):
        account_key = resolve_account_identifier(request.args.get("account_id"))
    snapshots = metrics_service.strategy_snapshots(
        strategy_id=strategy_id,
        instance_id=instance_id,
        account_key=account_key,
        limit=limit,
    )
    return jsonify({"snapshots": snapshots, "count": len(snapshots)})


@app.route("/api/decisions", methods=["GET"])
def api_decisions():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    filters = {
        "strategy_id": request.args.get("strategy_id"),
        "account_key": request.args.get("account_key"),
        "account_id": request.args.get("account_id"),
        "decision_type": request.args.get("decision_type"),
        "limit": safe_int(request.args.get("limit"), 100),
    }
    rows = decision_journal_service.list_decisions(filters)
    return jsonify({"decisions": rows, "count": len(rows)})


@app.route("/api/decisions", methods=["POST"])
def api_decisions_create():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    body = request.get_json(force=True)
    try:
        decision = decision_journal_service.create_decision(body)
        return jsonify({"ok": True, "decision": decision}), 201
    except decision_journal_service.DecisionValidationError as exc:
        return jsonify({"ok": False, "error": str(exc), "reason": exc.reason}), 400
    except Exception as exc:
        logger.exception("Error creando decision journal: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/decisions/<int:decision_id>", methods=["GET"])
def api_decision_detail(decision_id):
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    decision = decision_journal_service.get_decision(decision_id)
    if not decision:
        return jsonify({"ok": False, "error": "decision no encontrada"}), 404
    return jsonify({"decision": decision})


@app.route("/api/decisions/<int:decision_id>/outcome", methods=["POST"])
def api_decision_outcome(decision_id):
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    body = request.get_json(force=True)
    decision = decision_journal_service.update_outcome(decision_id, body)
    if not decision:
        return jsonify({"ok": False, "error": "decision no encontrada"}), 404
    return jsonify({"ok": True, "decision": decision})


def command_rejection_response(reason: str, message: str, status_code: int, payload: dict):
    try:
        database.log_audit_event(
            event_type="command_failed",
            entity_type="command_request",
            entity_id=payload.get("system") or payload.get("system_id"),
            account_key=payload.get("account_key") or payload.get("account_composite_key"),
            severity="warning",
            message=f"Command rejected: {message}",
            payload={**payload, "reason": reason},
        )
    except Exception as exc:
        logger.exception("Error registrando rechazo de comando: %s", exc)
    return jsonify({"ok": False, "error": message, "reason": reason}), status_code


def command_is_duplicate(account_key: str, system_id: str, magic, action: str) -> dict | None:
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


def open_positions_for_magic(account_key: str, magic) -> list[dict]:
    positions = database.get_open_positions(account_key)
    magic_int = safe_int(magic)
    if magic_int <= 0:
        return positions
    return [pos for pos in positions if safe_int(pos.get("magic_number") or pos.get("magic")) == magic_int]


@app.route("/api/command", methods=["POST"])
def api_command():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    body = request.get_json(force=True)
    try:
        payload, status_code = command_service.create_dashboard_command(
            body,
            get_portfolio(),
            VALID_ACTIONS,
            RISK_INCREASE_ACTIONS,
            account_matches_identifier,
            get_account_key,
            safe_int,
        )
    except Exception as exc:
        logger.exception("Error registrando comando en SQLite: %s", exc)
        return jsonify({"ok": False, "error": f"no se pudo registrar comando: {exc}"}), 500

    if payload.get("ok"):
        _cache["command_log"].insert(0, payload["command"])
        _write_commands_file()
    return jsonify(payload), status_code


@app.route("/api/commands")
def api_commands():
    try:
        _sync_command_result_files()
        active = commands_repo.list_commands(200, active_only=True)
        if active:
            return jsonify({"commands": active})
    except Exception as exc:
        logger.exception("Error leyendo commands SQLite: %s", exc)
    return jsonify({"commands": _cache["command_log"]})


@app.route("/api/commands/history")
def api_commands_history():
    try:
        _sync_command_result_files()
        rows = commands_repo.list_commands(100, active_only=False)
        return jsonify({"commands": rows, "count": len(rows)})
    except Exception as exc:
        logger.exception("Error leyendo historial de comandos SQLite: %s", exc)
        return jsonify({"commands": [], "count": 0, "error": str(exc)}), 500


@app.route("/api/commands/clear", methods=["POST"])
def api_commands_clear():
    try:
        commands_repo.clear_active()
    except Exception as exc:
        logger.exception("Error expirando comandos activos: %s", exc)
    _cache["command_log"] = []
    _write_commands_file()
    return jsonify({"ok": True})


def _api_key_required() -> bool:
    return bool(os.environ.get("QA_API_KEY") or cfg.get("api_key"))


def _check_api_key() -> bool:
    expected = os.environ.get("QA_API_KEY") or cfg.get("api_key")
    if not expected:
        return True
    return request.headers.get("X-API-Key") == expected


@app.route("/api/command/ack", methods=["POST"])
def api_command_ack():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    body = request.get_json(force=True)
    command_id = safe_int(body.get("command_id") or body.get("id"))
    if command_id <= 0:
        return jsonify({"ok": False, "error": "command_id requerido"}), 400
    ok = commands_repo.acknowledge(command_id, body)
    _write_commands_file()
    return jsonify({"ok": ok})


@app.route("/api/command/result", methods=["POST"])
def api_command_result():
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    body = request.get_json(force=True)
    command_id = safe_int(body.get("command_id") or body.get("id"))
    status = str(body.get("status") or ("executed" if body.get("success", True) else "failed")).lower()
    if command_id <= 0:
        return jsonify({"ok": False, "error": "command_id requerido"}), 400
    ok = commands_repo.complete(
        command_id,
        status,
        response=body,
        error_message=str(body.get("error_message", "")) or None,
    )
    _write_commands_file()
    return jsonify({"ok": ok})


@app.route("/api/result")
def api_result():
    result_path = get_data_folder() / "result.json"
    synced = _sync_command_result_files()
    if not result_path.exists():
        if synced:
            return jsonify({"exists": True, "synced": synced})
        return jsonify({"exists": False, "message": "result.json no encontrado"})
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            command_id = safe_int(data.get("command_id") or data.get("last_command_id"))
            if command_id > 0:
                status = "executed" if safe_int(data.get("failed")) == 0 else "failed"
                commands_repo.complete(command_id, status, response=data)
            else:
                pending = [row for row in commands_repo.list_log(100) if not row.get("resolved_at")]
                if pending:
                    commands_repo.resolve(pending[0]["id"], commands_repo.encode_result(data))
        except Exception as db_exc:
            logger.exception("Error actualizando command_log con result.json: %s", db_exc)
        return jsonify({"exists": True, "data": data})
    except Exception as exc:
        logger.exception("Error leyendo result.json: %s", exc)
        return jsonify({"exists": False, "error": str(exc)})


def _sync_command_result_files() -> list[dict]:
    synced = []
    folder = get_data_folder()
    for path in folder.glob("result*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            command_id = safe_int(data.get("command_id") or data.get("last_command_id"))
            if command_id <= 0:
                continue
            status = str(data.get("status", "")).lower()
            if status == "ack":
                ok = commands_repo.acknowledge(command_id, data)
            else:
                final_status = status if status in ("executed", "failed", "expired") else ("executed" if data.get("success", True) else "failed")
                ok = commands_repo.complete(command_id, final_status, response=data, error_message=data.get("error_message"))
            if ok:
                synced.append({"file": path.name, "command_id": command_id, "status": status or "executed"})
        except Exception as exc:
            logger.exception("Error sincronizando %s: %s", path.name, exc)
    if synced:
        _write_commands_file()
    return synced


@app.route("/api/config")
def api_config():
    safe_cfg = {k: v for k, v in cfg.items() if "api_key" not in k.lower()}
    return jsonify(safe_cfg)


@app.route("/api/config", methods=["POST"])
def api_config_update():
    global cfg
    if not _check_api_key():
        return jsonify({"ok": False, "error": "api key invalida"}), 401
    body = request.get_json(force=True)
    if "risk_thresholds" in body:
        before = dict(cfg.get("risk_thresholds", {}))
        cfg["risk_thresholds"].update(body["risk_thresholds"])
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        get_portfolio(force_reload=True)
        try:
            database.log_audit_event(
                event_type="config_updated",
                entity_type="config",
                entity_id="risk_thresholds",
                severity="info",
                message="Risk thresholds updated",
                payload={"before": before, "after": cfg["risk_thresholds"]},
            )
        except Exception as exc:
            logger.exception("Error registrando auditoria de config_updated: %s", exc)
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
                "{account_id}:{action}:PnL={pnl}:PF={pf}:DD_real={dd}%:DD_PnL={pnl_dd}%:basis={basis}".format(
                    account_id=instance.get("account_id"),
                    action=instance.get("action"),
                    pnl=m.get("net_profit", 0),
                    pf=m.get("profit_factor", 0),
                    dd=m.get("balance_max_drawdown_pct", m.get("max_drawdown_pct", "n/a")),
                    pnl_dd=m.get("pnl_curve_max_drawdown_pct", "n/a"),
                    basis=m.get("drawdown_basis", "n/a"),
                )
            )
        gm = system.get("metrics_global", {})
        system_lines.append(
            "- {label} | global={action} | cuentas={count} | PnL={pnl} | PF={pf} | DD_real={dd}% | DD_PnL={pnl_dd}% | basis={basis} | instancias=[{instances}]".format(
                label=system.get("label"),
                action=system.get("action"),
                count=system.get("accounts_count", 0),
                pnl=gm.get("net_profit", 0),
                pf=gm.get("profit_factor", 0),
                dd=gm.get("balance_max_drawdown_pct", gm.get("max_drawdown_pct", "n/a")),
                pnl_dd=gm.get("pnl_curve_max_drawdown_pct", "n/a"),
                basis=gm.get("drawdown_basis", "n/a"),
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
        commands = commands_repo.list_commands(500, active_only=True)
        command_ids = [int(cmd["id"]) for cmd in commands]
        commands_repo.mark_sent(command_ids)
        commands = commands_repo.list_commands(500, active_only=True)
        for cmd in commands:
            cmd["command_id"] = cmd["id"]
            cmd["ts"] = cmd.get("created_at")
        payload = {
            "version": "3.0",
            "generated": datetime.now().isoformat(),
            "requires_command_id": True,
            "commands": list(reversed(commands)),
        }
        with open(folder / "commands.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        _cache["load_errors"] = [f"Error escribiendo commands.json: {exc}"]


def _background_reload():
    reload_interval = max(10, int(cfg.get("auto_reload_seconds", 300)))
    sync_interval = max(10, int(cfg.get("broker_sync_seconds", 60)))
    last_reload = datetime.min
    last_sync = datetime.min
    while True:
        time.sleep(5)
        now = datetime.now()
        try:
            _sync_command_result_files()
            if (now - last_sync).total_seconds() >= sync_interval:
                broker_sync_service.sync_all(get_data_folder(), broker_sync_accounts())
                closed_trade_sync = sync_closed_trades_incremental(get_data_folder())
                _cache["last_closed_trade_sync"] = closed_trade_sync
                if closed_trade_sync.get("new_trades_inserted", 0) > 0:
                    _cache["portfolio"] = load_portfolio()
                    _cache["last_loaded"] = now
                last_sync = now
            if (now - last_reload).total_seconds() >= reload_interval:
                _cache["portfolio"] = load_portfolio()
                _cache["last_loaded"] = now
                last_reload = now
        except Exception:
            logger.exception("Error en scheduler de sincronizacion")


reload_thread = threading.Thread(target=_background_reload, daemon=True)
reload_thread.start()

if __name__ == "__main__":
    port = cfg.get("server_port", 5000)
    print(f"QA Portfolio Commander - http://localhost:{port}")
    print(f"Data folder: {get_data_folder()}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
