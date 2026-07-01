"""Idempotent SQLite migration runner for QA Portfolio Commander."""

from __future__ import annotations

import json
import argparse
import os
from pathlib import Path

import database


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


DEFAULT_DB_PATH = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "QuantAnalyzer" / "qa_portfolio.db"


def load_data_folder() -> Path:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return Path(cfg.get("data_folder") or cfg.get("mt4_data_folder"))
    return Path.home() / "AppData/Roaming/MetaQuotes/Terminal/Common/Files/QuantAnalyzer"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run QA Portfolio Commander SQLite migrations.")
    parser.add_argument(
        "--db-path",
        default=None,
        help="Ruta exacta a qa_portfolio.db. Tiene prioridad sobre --data-folder y config.json.",
    )
    parser.add_argument(
        "--data-folder",
        default=None,
        help="Carpeta donde vive qa_portfolio.db.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    if args.db_path:
        db_path = Path(args.db_path)
        database.init_db_path(db_path)
    elif args.data_folder:
        db_path = Path(args.data_folder) / "qa_portfolio.db"
        database.init_db(str(Path(args.data_folder)))
    elif CONFIG_PATH.exists():
        data_folder = load_data_folder()
        db_path = data_folder / "qa_portfolio.db"
        database.init_db(str(data_folder))
    else:
        db_path = DEFAULT_DB_PATH
        database.init_db_path(db_path)
    synced = database.sync_accounts_from_config(cfg.get("accounts", []))
    print(f"Migration complete: {db_path}")
    print("Applied: phase_0_commands_schema")
    print("Applied: phase_1_central_trading_schema")
    print("Applied: phase_4_audit_schema")
    print("Applied: phase_5_pnl_quality_schema")
    print("Applied: phase_6_performance_indexes")
    print("Applied: phase_7_strategy_governance_schema")
    print(f"Config accounts synced: {synced}")


if __name__ == "__main__":
    main()
