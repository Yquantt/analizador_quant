import json
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


_db_path: Path | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Database not initialized. Call init_db(data_folder) first.")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(data_folder: str) -> None:
    """Create qa_portfolio.db in data_folder and initialize tables."""
    global _db_path
    folder = Path(data_folder)
    folder.mkdir(parents=True, exist_ok=True)
    init_db_path(folder / "qa_portfolio.db")

def init_db_path(db_path: str | Path) -> None:
    """Initialize the SQLite schema at an explicit database path."""
    global _db_path
    _db_path = Path(db_path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(_db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id TEXT NOT NULL,
              platform TEXT NOT NULL,
              magic INTEGER,
              ticket TEXT,
              symbol TEXT,
              trade_type TEXT,
              open_time TEXT,
              close_time TEXT,
              open_price REAL,
              close_price REAL,
              lots REAL,
              profit REAL,
              pips REAL,
              comment TEXT,
              label TEXT,
              imported_at TEXT,
              UNIQUE(account_id, ticket, platform)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_metrics (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              magic INTEGER,
              account_id TEXT,
              trades_count INTEGER,
              net_profit REAL,
              profit_factor REAL,
              max_drawdown REAL,
              avg_pips REAL,
              sharpe REAL,
              win_rate REAL,
              first_trade TEXT,
              last_trade TEXT,
              action TEXT,
              calculated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS command_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              system TEXT,
              magic INTEGER,
              action TEXT,
              account_id TEXT,
              platform TEXT,
              sent_at TEXT,
              result TEXT,
              resolved_at TEXT
            )
            """
        )
        migrate_audit_schema(conn)
        migrate_commands_schema(conn)
        migrate_phase1_schema(conn)
        migrate_trade_file_state_schema(conn)
        migrate_discovered_accounts_schema(conn)
        migrate_decision_journal_schema(conn)
        migrate_strategy_identity_schema(conn)
        migrate_governance_schema(conn)
        migrate_performance_indexes(conn)


def get_account_key(platform: str, server: str, login: str, account_type: str) -> str:
    def clean(value: str, fallback: str) -> str:
        text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or fallback)).strip("_")
        return text or fallback

    return "{platform}_{server}_{login}_{kind}".format(
        platform=clean(platform, "MT").upper(),
        server=clean(server, "unknown"),
        login=clean(login, "unknown"),
        kind=clean(account_type, "real").lower(),
    )


def trade_hash(account_id: str, ticket: str, symbol: str, open_time, close_time, profit) -> str:
    raw = "|".join(
        [
            str(account_id or ""),
            str(ticket or ""),
            str(symbol or ""),
            _as_iso_text(open_time) or "",
            _as_iso_text(close_time) or "",
            str(profit or 0),
        ]
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def trade_natural_key(platform: str, account_key: str, ticket: str, magic, symbol: str, open_time, close_time) -> str:
    raw = "|".join(
        [
            str(platform or "").upper(),
            str(account_key or ""),
            str(ticket or ""),
            str(magic if magic not in (None, "") else 0),
            str(symbol or "").upper(),
            _as_iso_text(open_time) or "",
            _as_iso_text(close_time) or "",
        ]
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def trade_hash_v2(platform: str, account_key: str, ticket: str, magic, symbol: str, open_time, close_time, profit, volume) -> str:
    raw = "|".join(
        [
            str(platform or "").upper(),
            str(account_key or ""),
            str(ticket or ""),
            str(magic if magic not in (None, "") else 0),
            str(symbol or "").upper(),
            _as_iso_text(open_time) or "",
            _as_iso_text(close_time) or "",
            str(_to_float(profit, 0.0)),
            str(_to_float(volume, 0.0)),
        ]
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def raw_payload_hash(raw_payload: dict) -> str:
    raw_json = json.dumps(_json_safe(raw_payload), ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw_json.encode("utf-8")).hexdigest()


def migrate_phase1_schema(conn: sqlite3.Connection) -> None:
    """Create the central trading schema and backfill legacy trade rows."""
    migrate_audit_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brokers (
          id TEXT PRIMARY KEY,
          name TEXT,
          server TEXT,
          timezone TEXT,
          commission_model TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
          id TEXT PRIMARY KEY,
          broker_id TEXT,
          login TEXT NOT NULL,
          platform TEXT NOT NULL,
          server TEXT,
          account_type TEXT,
          alias TEXT,
          currency TEXT,
          leverage TEXT,
          created_at TEXT NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(broker_id) REFERENCES brokers(id)
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_identity ON accounts(platform, server, login, account_type)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_definitions (
          strategy_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT,
          symbol TEXT,
          timeframe TEXT,
          source TEXT,
          version TEXT,
          status TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_definitions_name ON strategy_definitions(name, version)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_instances (
          instance_id TEXT PRIMARY KEY,
          strategy_id TEXT NOT NULL,
          account_key TEXT NOT NULL,
          platform TEXT,
          magic INTEGER,
          symbol TEXT,
          timeframe TEXT,
          risk_profile TEXT,
          started_at TEXT,
          ended_at TEXT,
          is_active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(strategy_id) REFERENCES strategy_definitions(strategy_id),
          FOREIGN KEY(account_key) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_instances_strategy ON strategy_instances(strategy_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_instances_account ON strategy_instances(account_key)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_instances_unique_magic ON strategy_instances(account_key, platform, magic, symbol, timeframe)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategies (
          id TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          magic_number INTEGER,
          name TEXT,
          description TEXT,
          symbols TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategies_account_magic ON strategies(account_id, magic_number)")
    _ensure_column(conn, "strategies", "strategy_definition_id", "TEXT")
    _ensure_column(conn, "strategies", "strategy_instance_id", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closed_trades (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id TEXT NOT NULL,
          strategy_id TEXT,
          legacy_account_id TEXT,
          ticket TEXT,
          symbol TEXT,
          action TEXT,
          volume REAL,
          open_price REAL,
          close_price REAL,
          open_time TEXT,
          close_time TEXT,
          swap REAL,
          commission REAL,
          profit REAL,
          balance_before REAL,
          balance_after REAL,
          hash_uniq TEXT NOT NULL UNIQUE,
          imported_at TEXT,
          FOREIGN KEY(account_id) REFERENCES accounts(id),
          FOREIGN KEY(strategy_id) REFERENCES strategies(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_account_time ON closed_trades(account_id, close_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_strategy ON closed_trades(strategy_id)")
    _ensure_column(conn, "closed_trades", "import_run_id", "INTEGER")
    _ensure_column(conn, "closed_trades", "source_file", "TEXT")
    _ensure_column(conn, "closed_trades", "source_row_hash", "TEXT")
    _ensure_column(conn, "closed_trades", "raw_import_id", "INTEGER")
    _ensure_column(conn, "closed_trades", "normalized_at", "TEXT")
    _ensure_column(conn, "closed_trades", "natural_key", "TEXT")
    _ensure_column(conn, "closed_trades", "hash_version", "INTEGER")
    _ensure_column(conn, "closed_trades", "last_seen_at", "TEXT")
    _ensure_column(conn, "closed_trades", "gross_profit", "REAL")
    _ensure_column(conn, "closed_trades", "gross_loss", "REAL")
    _ensure_column(conn, "closed_trades", "fees", "REAL")
    _ensure_column(conn, "closed_trades", "spread_cost_estimated", "REAL")
    _ensure_column(conn, "closed_trades", "slippage_estimated", "REAL")
    _ensure_column(conn, "closed_trades", "net_profit", "REAL")
    _ensure_column(conn, "closed_trades", "balance_reconstructed", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "accounts", "excluded_from_portfolio", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "accounts", "portfolio_exclusion_note", "TEXT")
    conn.execute("UPDATE closed_trades SET net_profit = COALESCE(net_profit, profit)")
    conn.execute("UPDATE closed_trades SET gross_profit = COALESCE(gross_profit, CASE WHEN net_profit > 0 THEN net_profit ELSE 0 END)")
    conn.execute("UPDATE closed_trades SET gross_loss = COALESCE(gross_loss, CASE WHEN net_profit < 0 THEN ABS(net_profit) ELSE 0 END)")
    conn.execute("UPDATE closed_trades SET fees = COALESCE(fees, 0), spread_cost_estimated = COALESCE(spread_cost_estimated, 0), slippage_estimated = COALESCE(slippage_estimated, 0)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_import_run ON closed_trades(import_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_raw_import ON closed_trades(raw_import_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_source_hash ON closed_trades(source_file, source_row_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_natural_key ON closed_trades(natural_key)")
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ignore_legacy_closed_trades_when_csv
        BEFORE INSERT ON closed_trades
        WHEN NEW.source_file = 'legacy_trades_table'
         AND (
              SELECT COUNT(*)
                FROM closed_trades
               WHERE source_file IS NOT NULL
                 AND source_file <> 'legacy_trades_table'
             ) > 0
        BEGIN
          SELECT RAISE(IGNORE);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ignore_null_source_closed_trades_when_csv
        BEFORE INSERT ON closed_trades
        WHEN NEW.source_file IS NULL
         AND (
              SELECT COUNT(*)
                FROM closed_trades
               WHERE source_file IS NOT NULL
                 AND source_file <> 'legacy_trades_table'
             ) > 0
        BEGIN
          SELECT RAISE(IGNORE);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ignore_legacy_closed_trades_update_when_csv
        BEFORE UPDATE OF source_file ON closed_trades
        WHEN NEW.source_file = 'legacy_trades_table'
         AND (
              SELECT COUNT(*)
                FROM closed_trades
               WHERE source_file IS NOT NULL
                 AND source_file <> 'legacy_trades_table'
             ) > 0
        BEGIN
          SELECT RAISE(IGNORE);
        END
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_import_conflicts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          import_run_id INTEGER,
          raw_import_id INTEGER,
          existing_trade_id INTEGER,
          account_key TEXT,
          natural_key TEXT,
          existing_hash TEXT,
          incoming_hash TEXT,
          conflict_type TEXT NOT NULL,
          existing_payload_json TEXT,
          incoming_payload_json TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(import_run_id) REFERENCES import_runs(id),
          FOREIGN KEY(raw_import_id) REFERENCES raw_trade_imports(id),
          FOREIGN KEY(existing_trade_id) REFERENCES closed_trades(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_import_conflicts_run ON trade_import_conflicts(import_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_import_conflicts_natural_key ON trade_import_conflicts(natural_key)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS open_positions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id TEXT NOT NULL,
          ticket TEXT NOT NULL,
          symbol TEXT,
          action TEXT,
          volume REAL,
          open_price REAL,
          sl REAL,
          tp REAL,
          open_time TEXT,
          swap REAL,
          unrealized_profit REAL,
          last_updated_at TEXT NOT NULL,
          UNIQUE(account_id, ticket),
          FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_open_positions_account_updated ON open_positions(account_id, last_updated_at)")
    _ensure_column(conn, "open_positions", "strategy_id", "TEXT")
    _ensure_column(conn, "open_positions", "magic_number", "INTEGER")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          balance REAL,
          equity REAL,
          margin REAL,
          free_margin REAL,
          margin_level REAL,
          drawdown_percent REAL,
          UNIQUE(account_id, timestamp),
          FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_snapshots_account_ts ON account_snapshots(account_id, timestamp)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS equity_points (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          equity REAL,
          balance REAL,
          unrealized_pnl REAL,
          UNIQUE(account_id, timestamp),
          FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_points_account_ts ON equity_points(account_id, timestamp)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deposits_withdrawals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          amount REAL NOT NULL,
          type TEXT NOT NULL,
          description TEXT,
          FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deposits_withdrawals_account_ts ON deposits_withdrawals(account_id, timestamp)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id TEXT,
          source_file TEXT,
          rows_processed INTEGER,
          rows_inserted INTEGER,
          hash_file TEXT,
          status TEXT,
          error_log TEXT,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_import_runs_account_started ON import_runs(account_id, started_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_trade_imports (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          import_run_id INTEGER,
          source_file TEXT NOT NULL,
          source_platform TEXT,
          account_key TEXT,
          account_id TEXT,
          row_number INTEGER NOT NULL,
          row_hash TEXT NOT NULL,
          raw_payload_json TEXT NOT NULL,
          normalized_status TEXT NOT NULL,
          error_message TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(import_run_id) REFERENCES import_runs(id),
          FOREIGN KEY(account_key) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trade_imports_run ON raw_trade_imports(import_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trade_imports_account ON raw_trade_imports(account_key, created_at)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_trade_imports_unique_row ON raw_trade_imports(import_run_id, row_number, row_hash)")
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_trade_imports_unique_source_row_hash ON raw_trade_imports(source_file, row_number, row_hash)")
    except sqlite3.IntegrityError:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_metric_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          strategy_id TEXT,
          instance_id TEXT,
          account_key TEXT,
          calculated_at TEXT NOT NULL,
          period_start TEXT,
          period_end TEXT,
          trades INTEGER,
          net_profit REAL,
          gross_profit REAL,
          gross_loss REAL,
          profit_factor REAL,
          max_drawdown REAL,
          win_rate REAL,
          expectancy REAL,
          avg_trade REAL,
          sharpe REAL,
          pips_avg REAL,
          classification TEXT,
          recommended_action TEXT,
          rules_version TEXT NOT NULL,
          payload_json TEXT,
          FOREIGN KEY(strategy_id) REFERENCES strategy_definitions(strategy_id),
          FOREIGN KEY(instance_id) REFERENCES strategy_instances(instance_id),
          FOREIGN KEY(account_key) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_metric_snapshots_strategy_ts ON strategy_metric_snapshots(strategy_id, calculated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_metric_snapshots_instance_ts ON strategy_metric_snapshots(instance_id, calculated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_metric_snapshots_account_ts ON strategy_metric_snapshots(account_key, calculated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_metric_snapshots_rules ON strategy_metric_snapshots(rules_version)")
    for column, definition in {
        "equity_current": "REAL",
        "equity_peak": "REAL",
        "return_pct": "REAL",
        "current_drawdown": "REAL",
        "current_drawdown_pct": "REAL",
        "max_drawdown_pct": "REAL",
        "pnl_curve_equity_current": "REAL",
        "pnl_curve_equity_peak": "REAL",
        "pnl_curve_current_drawdown": "REAL",
        "pnl_curve_current_drawdown_pct": "REAL",
        "pnl_curve_max_drawdown": "REAL",
        "pnl_curve_max_drawdown_pct": "REAL",
        "balance_drawdown_reliable": "INTEGER",
        "balance_equity_current": "REAL",
        "balance_equity_peak": "REAL",
        "balance_current_drawdown": "REAL",
        "balance_current_drawdown_pct": "REAL",
        "balance_max_drawdown": "REAL",
        "balance_max_drawdown_pct": "REAL",
        "drawdown_basis": "TEXT",
        "return_over_drawdown": "REAL",
        "recovery_factor": "REAL",
        "winning_trades": "INTEGER",
        "losing_trades": "INTEGER",
        "trades_last_20": "INTEGER",
        "trades_last_50": "INTEGER",
        "trades_last_100": "INTEGER",
        "current_winning_streak": "INTEGER",
        "current_losing_streak": "INTEGER",
        "longest_winning_streak": "INTEGER",
        "longest_losing_streak": "INTEGER",
        "average_winning_streak": "REAL",
        "average_losing_streak": "REAL",
        "average_win": "REAL",
        "average_loss": "REAL",
        "payoff_ratio": "REAL",
        "status": "TEXT",
        "health_score": "REAL",
        "decision_state": "TEXT",
        "expected_metrics_id": "INTEGER",
        "expected_metrics_source": "TEXT",
        "drawdown_ratio": "REAL",
        "losing_streak_ratio": "REAL",
        "profit_factor_ratio": "REAL",
        "expectancy_ratio": "REAL",
        "decision_reasons_json": "TEXT",
    }.items():
        _ensure_column(conn, "strategy_metric_snapshots", column, definition)
    _ensure_column(conn, "system_metrics", "account_composite_key", "TEXT")
    _ensure_column(conn, "system_metrics", "strategy_id", "TEXT")
    _ensure_column(conn, "system_metrics", "max_risk_per_trade", "REAL")
    _ensure_column(conn, "system_metrics", "exposure_per_symbol", "TEXT")
    _ensure_column(conn, "system_metrics", "average_holding_hours", "REAL")
    _ensure_column(conn, "system_metrics", "rules_version", "TEXT")
    _backfill_phase1_from_legacy_trades(conn)
    _backfill_strategy_links(conn)
    _backfill_closed_trade_traceability(conn)
    _backfill_trade_hash_v2(conn)
    migrate_performance_indexes(conn)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_audit_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_type TEXT NOT NULL,
          entity_type TEXT,
          entity_id TEXT,
          account_key TEXT,
          severity TEXT NOT NULL DEFAULT 'info',
          message TEXT,
          payload_json TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_type_created ON audit_events(event_type, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_entity ON audit_events(entity_type, entity_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_account_created ON audit_events(account_key, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_severity_created ON audit_events(severity, created_at)")


def migrate_performance_indexes(conn: sqlite3.Connection) -> None:
    """Create query-path indexes using the actual SQLite column names."""
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_closed_trades_account_close_time
        ON closed_trades(account_id, close_time)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategies_magic_account
        ON strategies(magic_number, account_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_closed_trades_strategy_time
        ON closed_trades(strategy_id, close_time)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_equity_points_account_timestamp
        ON equity_points(account_id, timestamp)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_open_positions_account_magic
        ON open_positions(account_id, magic_number)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_commands_account_status
        ON commands(account_composite_key, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_metric_snapshots_instance_time
        ON strategy_metric_snapshots(instance_id, calculated_at)
        """
    )


def migrate_trade_file_state_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_file_state (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_file TEXT NOT NULL UNIQUE,
          account_key TEXT,
          account_id TEXT,
          platform TEXT,
          last_modified_at TEXT,
          file_size INTEGER,
          last_row_count INTEGER,
          last_imported_hash TEXT,
          last_successful_import_at TEXT,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_file_state_account ON trade_file_state(account_key, updated_at)")


def migrate_discovered_accounts_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovered_accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id TEXT NOT NULL,
          account_key TEXT,
          platform TEXT,
          account_type TEXT,
          label TEXT,
          server TEXT,
          broker_name TEXT,
          source_file TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending_approval',
          approved_at TEXT,
          rejected_at TEXT,
          ignored_at TEXT,
          notes TEXT,
          payload_json TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(account_id, platform, server, account_type)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_accounts_status ON discovered_accounts(status, last_seen_at)")


def migrate_decision_journal_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_journal (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          account_key TEXT,
          account_id TEXT,
          platform TEXT,
          strategy_id TEXT,
          instance_id TEXT,
          magic INTEGER,
          symbol TEXT,
          decision_type TEXT NOT NULL,
          reason_category TEXT NOT NULL,
          human_note TEXT,
          ai_summary TEXT,
          metrics_snapshot_id INTEGER,
          previous_state TEXT,
          new_state TEXT,
          portfolio_context_json TEXT,
          review_after_days INTEGER,
          outcome_7d TEXT,
          outcome_30d TEXT,
          created_by TEXT,
          FOREIGN KEY(metrics_snapshot_id) REFERENCES strategy_metric_snapshots(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_journal_created ON decision_journal(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_journal_strategy_created ON decision_journal(strategy_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_journal_account_created ON decision_journal(account_key, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_journal_type_created ON decision_journal(decision_type, created_at)")


def migrate_strategy_identity_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          strategy_id TEXT NOT NULL,
          version_name TEXT NOT NULL,
          parameters_hash TEXT,
          parameters_json TEXT,
          source TEXT,
          backtest_ref TEXT,
          expected_profit_factor REAL,
          expected_max_drawdown REAL,
          expected_trades_per_month REAL,
          expected_losing_streak INTEGER,
          created_at TEXT NOT NULL,
          notes TEXT,
          FOREIGN KEY(strategy_id) REFERENCES strategy_definitions(strategy_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_versions_strategy ON strategy_versions(strategy_id, created_at)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_versions_unique ON strategy_versions(strategy_id, version_name, parameters_hash)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_expected_metrics (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          strategy_id TEXT,
          instance_id TEXT,
          strategy_version_id INTEGER,
          risk_profile_id INTEGER,
          source TEXT NOT NULL,
          source_name TEXT,
          sample_start TEXT,
          sample_end TEXT,
          expected_net_profit REAL,
          expected_return_pct REAL,
          expected_avg_monthly_return REAL,
          expected_profit_factor REAL,
          expected_expectancy REAL,
          expected_win_rate REAL,
          expected_average_win REAL,
          expected_average_loss REAL,
          expected_payoff_ratio REAL,
          expected_max_drawdown REAL,
          expected_max_drawdown_pct REAL,
          expected_longest_winning_streak INTEGER,
          expected_longest_losing_streak INTEGER,
          expected_average_losing_streak REAL,
          expected_trades INTEGER,
          expected_trades_per_month REAL,
          created_at TEXT NOT NULL,
          notes TEXT,
          FOREIGN KEY(strategy_id) REFERENCES strategy_definitions(strategy_id),
          FOREIGN KEY(instance_id) REFERENCES strategy_instances(instance_id),
          FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions(id),
          FOREIGN KEY(risk_profile_id) REFERENCES risk_profiles(id)
        )
        """
    )
    _ensure_column(conn, "strategy_expected_metrics", "expected_avg_monthly_return", "REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_expected_metrics_strategy ON strategy_expected_metrics(strategy_id, source, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_expected_metrics_instance ON strategy_expected_metrics(instance_id, source, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_expected_metrics_version ON strategy_expected_metrics(strategy_version_id, source, created_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_profiles (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          risk_type TEXT,
          risk_value REAL,
          max_lots REAL,
          max_daily_loss REAL,
          max_total_drawdown REAL,
          created_at TEXT NOT NULL,
          notes TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_risk_profiles_name ON risk_profiles(name)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deployment_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          strategy_id TEXT,
          strategy_version_id INTEGER,
          instance_id TEXT,
          account_key TEXT,
          account_id TEXT,
          platform TEXT,
          magic INTEGER,
          symbol TEXT,
          event_type TEXT NOT NULL,
          previous_state TEXT,
          new_state TEXT,
          risk_profile_id INTEGER,
          event_note TEXT,
          created_at TEXT NOT NULL,
          created_by TEXT,
          FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions(id),
          FOREIGN KEY(risk_profile_id) REFERENCES risk_profiles(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deployment_events_strategy_created ON deployment_events(strategy_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deployment_events_instance_created ON deployment_events(instance_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deployment_events_account_created ON deployment_events(account_key, created_at)")
    _ensure_column(conn, "strategy_instances", "strategy_version_id", "INTEGER")
    _ensure_column(conn, "strategy_instances", "risk_profile_id", "INTEGER")
    _ensure_column(conn, "strategy_instances", "deployment_state", "TEXT")
    _ensure_column(conn, "strategy_instances", "ea_name", "TEXT")
    _ensure_column(conn, "strategy_instances", "parametros_hash", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_canonical_identities (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          strategy_id TEXT,
          instance_id TEXT,
          account_key TEXT,
          ea_name TEXT,
          magic INTEGER,
          symbol TEXT,
          timeframe TEXT,
          version TEXT,
          parametros_hash TEXT,
          source TEXT,
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(account_key, ea_name, magic, symbol, timeframe, version, parametros_hash),
          FOREIGN KEY(strategy_id) REFERENCES strategy_definitions(strategy_id),
          FOREIGN KEY(instance_id) REFERENCES strategy_instances(instance_id),
          FOREIGN KEY(account_key) REFERENCES accounts(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_canonical_identity_lookup ON strategy_canonical_identities(ea_name, magic, symbol, timeframe, version, parametros_hash)")


ROLE_VALUES = ("historical_official", "incubation", "production", "replica", "ignored", "paper", "shadow")
CAPITAL_MODE_VALUES = ("real", "demo", "paper", "external")
STATE_VALUES = ("mining", "validation", "incubation", "production", "scaling", "reduced_risk", "paused", "retired", "archived")
ACTOR_TYPE_VALUES = ("human", "ai", "system_rule")


def migrate_governance_schema(conn: sqlite3.Connection) -> None:
    """Create the first append-only strategy governance layer."""
    migrate_strategy_identity_schema(conn)
    role_values = ", ".join(f"'{value}'" for value in ROLE_VALUES)
    capital_values = ", ".join(f"'{value}'" for value in CAPITAL_MODE_VALUES)
    state_values = ", ".join(f"'{value}'" for value in STATE_VALUES)
    actor_values = ", ".join(f"'{value}'" for value in ACTOR_TYPE_VALUES)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS strategy_sources (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          strategy_id TEXT NOT NULL,
          strategy_version_id INTEGER,
          source_type TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          is_official INTEGER NOT NULL DEFAULT 0 CHECK(is_official IN (0, 1)),
          valid_from TEXT NOT NULL,
          valid_to TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(strategy_id) REFERENCES strategy_definitions(strategy_id),
          FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_sources_strategy_active ON strategy_sources(strategy_id, strategy_version_id, valid_to)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_sources_one_official_active
        ON strategy_sources(strategy_id, COALESCE(strategy_version_id, -1))
        WHERE is_official = 1 AND valid_to IS NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_sources_unique_ref
        ON strategy_sources(strategy_id, COALESCE(strategy_version_id, -1), source_type, source_ref, valid_from)
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS strategy_deployments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          strategy_id TEXT NOT NULL,
          strategy_version_id INTEGER,
          instance_id TEXT NOT NULL,
          account_key TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ({role_values})),
          capital_mode TEXT NOT NULL CHECK(capital_mode IN ({capital_values})),
          state TEXT NOT NULL CHECK(state IN ({state_values})),
          risk_profile_id INTEGER,
          valid_from TEXT NOT NULL,
          valid_to TEXT,
          created_by TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(strategy_id) REFERENCES strategy_definitions(strategy_id),
          FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions(id),
          FOREIGN KEY(instance_id) REFERENCES strategy_instances(instance_id),
          FOREIGN KEY(account_key) REFERENCES accounts(id),
          FOREIGN KEY(risk_profile_id) REFERENCES risk_profiles(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_deployments_strategy_active ON strategy_deployments(strategy_id, state, valid_to)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_deployments_instance_active ON strategy_deployments(instance_id, valid_to)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_deployments_account_active ON strategy_deployments(account_key, valid_to)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_deployments_one_active_instance
        ON strategy_deployments(instance_id)
        WHERE valid_to IS NULL
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS governance_rule_sets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          version TEXT NOT NULL,
          scope TEXT NOT NULL,
          params_json TEXT NOT NULL,
          active_from TEXT NOT NULL,
          active_to TEXT
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_governance_rule_sets_unique ON governance_rule_sets(name, version, scope)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_governance_rule_sets_active ON governance_rule_sets(scope, active_from, active_to)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS governance_evaluations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          rule_set_id INTEGER,
          entity_type TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          snapshot_id INTEGER,
          evaluated_at TEXT NOT NULL,
          result_json TEXT NOT NULL,
          recommended_transition TEXT,
          severity TEXT NOT NULL,
          FOREIGN KEY(rule_set_id) REFERENCES governance_rule_sets(id),
          FOREIGN KEY(snapshot_id) REFERENCES strategy_metric_snapshots(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_governance_evaluations_entity_time ON governance_evaluations(entity_type, entity_id, evaluated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_governance_evaluations_rule_time ON governance_evaluations(rule_set_id, evaluated_at)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS decisions_append_only (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          created_by TEXT,
          actor_type TEXT NOT NULL CHECK(actor_type IN ({actor_values})),
          entity_type TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          decision_type TEXT NOT NULL,
          previous_state TEXT,
          new_state TEXT,
          reason TEXT NOT NULL,
          rule_set_id INTEGER,
          rule_evaluation_id INTEGER,
          evidence_json TEXT NOT NULL,
          metrics_snapshot_id INTEGER,
          source_snapshot_ids_json TEXT,
          supersedes_decision_id INTEGER,
          correlation_id TEXT,
          FOREIGN KEY(rule_set_id) REFERENCES governance_rule_sets(id),
          FOREIGN KEY(rule_evaluation_id) REFERENCES governance_evaluations(id),
          FOREIGN KEY(metrics_snapshot_id) REFERENCES strategy_metric_snapshots(id),
          FOREIGN KEY(supersedes_decision_id) REFERENCES decisions_append_only(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_append_only_entity_time ON decisions_append_only(entity_type, entity_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_append_only_type_time ON decisions_append_only(decision_type, created_at)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_append_only_correlation ON decisions_append_only(correlation_id) WHERE correlation_id IS NOT NULL")
    _seed_governance_rule_set(conn)
    _backfill_governance_sources(conn)
    _backfill_governance_deployments(conn)


def _seed_governance_rule_set(conn: sqlite3.Connection) -> int:
    now = _utc_now()
    params = {
        "min_closed_trades_for_production": 30,
        "negative_months_window": 3,
        "rolling_pf_90d_reduced_risk_below": 0.8,
        "production_snapshot_max_age_hours": 24,
        "stale_active_instance_days": 30,
        "mode": "recommend_only",
    }
    conn.execute(
        """
        INSERT OR IGNORE INTO governance_rule_sets (
          name, version, scope, params_json, active_from, active_to
        )
        VALUES (?, ?, ?, ?, ?, NULL)
        """,
        ("conservative_initial_governance", "v1.0", "strategy_instance", json.dumps(params, ensure_ascii=False, sort_keys=True), now),
    )
    row = conn.execute(
        """
        SELECT id FROM governance_rule_sets
         WHERE name = ? AND version = ? AND scope = ?
        """,
        ("conservative_initial_governance", "v1.0", "strategy_instance"),
    ).fetchone()
    return int(row["id"])


def _backfill_governance_sources(conn: sqlite3.Connection) -> None:
    now = _utc_now()
    rows = conn.execute(
        """
        SELECT sd.strategy_id, sd.source, sd.created_at
          FROM strategy_definitions sd
         WHERE NOT EXISTS (
               SELECT 1 FROM strategy_sources ss
                WHERE ss.strategy_id = sd.strategy_id
                  AND ss.strategy_version_id IS NULL
           )
        """
    ).fetchall()
    for row in rows:
        source = str(row["source"] or "derived_identity")
        source_ref = f"strategy_definitions.source:{source}"
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO strategy_sources (
              strategy_id, strategy_version_id, source_type, source_ref,
              is_official, valid_from, valid_to, created_at
            )
            VALUES (?, NULL, ?, ?, 0, ?, NULL, ?)
            """,
            (row["strategy_id"], source, source_ref, row["created_at"] or now, now),
        )
        source_id = cursor.lastrowid
        if source_id:
            _insert_decision_append_only(
                conn,
                {
                    "created_at": now,
                    "created_by": "migration",
                    "actor_type": "system_rule",
                    "entity_type": "strategy_source",
                    "entity_id": str(source_id),
                    "decision_type": "source_registered",
                    "previous_state": None,
                    "new_state": "unofficial",
                    "reason": "Conservative governance migration registered existing derived source as non-official.",
                    "evidence_json": {
                        "strategy_id": row["strategy_id"],
                        "source_type": source,
                        "source_ref": source_ref,
                        "is_official": 0,
                    },
                    "source_snapshot_ids_json": json.dumps([source_id], ensure_ascii=False),
                    "correlation_id": f"governance_migration_v1:source:{row['strategy_id']}",
                },
            )


def _map_deployment_state(previous_state: str | None, account_type: str | None, is_active: int | None) -> tuple[str, str]:
    normalized = str(previous_state or "").strip().lower()
    mapping = {
        "production": ("production", "production"),
        "active": ("shadow", "validation"),
        "incubation": ("incubation", "incubation"),
        "paused": ("shadow", "paused"),
        "reduced_risk": ("shadow", "reduced_risk"),
        "retired": ("shadow", "retired"),
        "archived": ("shadow", "archived"),
    }
    if normalized in mapping:
        return mapping[normalized]
    if str(account_type or "").lower() == "demo" and int(is_active or 0) == 1:
        return "incubation", "incubation"
    if int(is_active or 0) != 1:
        return "shadow", "retired"
    return "shadow", "validation"


def _capital_mode(account_type: str | None) -> str:
    normalized = str(account_type or "").strip().lower()
    if normalized in ("real", "demo"):
        return normalized
    if normalized in ("paper", "external"):
        return normalized
    return "external"


def _backfill_governance_deployments(conn: sqlite3.Connection) -> None:
    now = _utc_now()
    rows = conn.execute(
        """
        SELECT si.instance_id, si.strategy_id, si.strategy_version_id, si.account_key,
               si.risk_profile_id, si.deployment_state, si.started_at, si.created_at,
               si.is_active, a.account_type
          FROM strategy_instances si
          LEFT JOIN accounts a ON a.id = si.account_key
         WHERE NOT EXISTS (
               SELECT 1 FROM strategy_deployments sd
                WHERE sd.instance_id = si.instance_id
                  AND sd.valid_to IS NULL
           )
        """
    ).fetchall()
    for row in rows:
        role, state = _map_deployment_state(row["deployment_state"], row["account_type"], row["is_active"])
        valid_from = row["started_at"] or row["created_at"] or now
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO strategy_deployments (
              strategy_id, strategy_version_id, instance_id, account_key,
              role, capital_mode, state, risk_profile_id,
              valid_from, valid_to, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                row["strategy_id"],
                row["strategy_version_id"],
                row["instance_id"],
                row["account_key"],
                role,
                _capital_mode(row["account_type"]),
                state,
                row["risk_profile_id"],
                valid_from,
                "migration",
                now,
            ),
        )
        deployment_id = cursor.lastrowid
        if deployment_id:
            _insert_decision_append_only(
                conn,
                {
                    "created_at": now,
                    "created_by": "migration",
                    "actor_type": "system_rule",
                    "entity_type": "strategy_deployment",
                    "entity_id": str(deployment_id),
                    "decision_type": "deployment_backfilled",
                    "previous_state": row["deployment_state"],
                    "new_state": state,
                    "reason": "Conservative governance migration created an active deployment without marking production unless explicit evidence existed.",
                    "evidence_json": {
                        "strategy_id": row["strategy_id"],
                        "instance_id": row["instance_id"],
                        "account_key": row["account_key"],
                        "account_type": row["account_type"],
                        "previous_deployment_state": row["deployment_state"],
                        "role": role,
                        "capital_mode": _capital_mode(row["account_type"]),
                        "state": state,
                    },
                    "correlation_id": f"governance_migration_v1:deployment:{row['instance_id']}",
                },
            )


def _insert_audit_event(
    conn: sqlite3.Connection,
    event_type: str,
    entity_type: str | None = None,
    entity_id=None,
    account_key: str | None = None,
    severity: str = "info",
    message: str | None = None,
    payload=None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO audit_events (
          event_type, entity_type, entity_id, account_key, severity,
          message, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            account_key,
            severity or "info",
            message,
            encode_result(payload or {}),
            _utc_now(),
        ),
    )
    return int(cursor.lastrowid)


def log_audit_event(
    event_type: str,
    entity_type: str | None = None,
    entity_id=None,
    account_key: str | None = None,
    severity: str = "info",
    message: str | None = None,
    payload=None,
) -> int:
    with _connect() as conn:
        migrate_audit_schema(conn)
        return _insert_audit_event(
            conn,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            account_key=account_key,
            severity=severity,
            message=message,
            payload=payload,
        )


def get_audit_events(
    event_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    account_key: str | None = None,
    severity: str | None = None,
    limit: int = 500,
) -> list[dict]:
    query = "SELECT * FROM audit_events"
    clauses = []
    params = []
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(str(entity_id))
    if account_key:
        clauses.append("account_key = ?")
        params.append(account_key)
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _infer_account_type(label: str) -> str:
    text = str(label or "").lower()
    if "demo" in text:
        return "demo"
    if "real" in text:
        return "real"
    return "real"


def _strategy_id(account_key: str, magic) -> str:
    magic_text = str(magic if magic not in (None, "") else 0)
    return f"{account_key}__magic_{magic_text}"


def _stable_id(prefix: str, *parts) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.md5(raw.encode('utf-8')).hexdigest()}"


def strategy_identity_from_trade(trade: dict) -> dict:
    account_key = trade.get("account_key") or trade.get("account_id")
    platform = str(trade.get("platform", "MT4")).upper()
    magic = _to_int(trade.get("magic"), 0)
    symbol = str(trade.get("symbol", "") or "unknown")
    timeframe = str(trade.get("timeframe", "") or "unknown")
    comment = str(trade.get("comment", "") or "").strip()

    if magic > 0:
        logical_key = f"magic:{magic}"
        name = logical_key
        source = "magic"
    elif comment and comment.lower() not in ("0", "nan", "none"):
        logical_key = f"comment:{comment[:80]}|account:{account_key}"
        name = f"comment:{comment[:80]}"
        source = "comment_account_scoped"
    else:
        logical_key = f"symbol:{symbol}|account:{account_key}"
        name = f"symbol:{symbol}"
        source = "symbol_account_scoped"

    strategy_id = _stable_id("strat", logical_key, symbol, timeframe, "v1")
    instance_id = _stable_id("inst", account_key, platform, magic, symbol, timeframe, logical_key)
    return {
        "strategy_id": strategy_id,
        "instance_id": instance_id,
        "name": name,
        "description": "Provisional strategy definition generated from imported trades",
        "symbol": symbol,
        "timeframe": timeframe,
        "source": source,
        "version": "v1",
        "status": "active",
        "account_key": account_key,
        "platform": platform,
        "magic": magic if magic > 0 else None,
        "risk_profile": "unknown",
        "logical_key": logical_key,
    }


def ensure_strategy_definition_and_instance(conn: sqlite3.Connection, identity: dict, started_at=None, ended_at=None) -> tuple[str, str]:
    now = _utc_now()
    existing_unique_instance = conn.execute(
        """
        SELECT instance_id
          FROM strategy_instances
         WHERE account_key = ?
           AND COALESCE(platform, '') = COALESCE(?, '')
           AND COALESCE(magic, -9223372036854775808) = COALESCE(?, -9223372036854775808)
           AND COALESCE(symbol, '') = COALESCE(?, '')
           AND COALESCE(timeframe, '') = COALESCE(?, '')
         LIMIT 1
        """,
        (
            identity["account_key"],
            identity.get("platform"),
            identity.get("magic"),
            identity.get("symbol"),
            identity.get("timeframe"),
        ),
    ).fetchone()
    if existing_unique_instance:
        identity = dict(identity)
        identity["instance_id"] = existing_unique_instance["instance_id"]
    instance_exists = conn.execute(
        "SELECT 1 FROM strategy_instances WHERE instance_id = ?",
        (identity["instance_id"],),
    ).fetchone() is not None
    conn.execute(
        """
        INSERT INTO strategy_definitions (
          strategy_id, name, description, symbol, timeframe, source,
          version, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(strategy_id) DO UPDATE SET
          name=excluded.name,
          description=COALESCE(strategy_definitions.description, excluded.description),
          symbol=excluded.symbol,
          timeframe=excluded.timeframe,
          source=excluded.source,
          version=excluded.version,
          status=excluded.status,
          updated_at=excluded.updated_at
        """
        ,
        (
            identity["strategy_id"],
            identity["name"],
            identity.get("description"),
            identity.get("symbol"),
            identity.get("timeframe"),
            identity.get("source"),
            identity.get("version", "v1"),
            identity.get("status", "active"),
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO strategy_instances (
          instance_id, strategy_id, account_key, platform, magic, symbol,
          timeframe, risk_profile, started_at, ended_at, is_active,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(instance_id) DO UPDATE SET
          strategy_id=excluded.strategy_id,
          platform=excluded.platform,
          magic=excluded.magic,
          symbol=excluded.symbol,
          timeframe=excluded.timeframe,
          risk_profile=excluded.risk_profile,
          started_at=COALESCE(strategy_instances.started_at, excluded.started_at),
          ended_at=excluded.ended_at,
          is_active=CASE WHEN excluded.ended_at IS NULL THEN 1 ELSE strategy_instances.is_active END,
          updated_at=excluded.updated_at
        """
        ,
        (
            identity["instance_id"],
            identity["strategy_id"],
            identity["account_key"],
            identity.get("platform"),
            identity.get("magic"),
            identity.get("symbol"),
            identity.get("timeframe"),
            identity.get("risk_profile", "unknown"),
            _as_iso_text(started_at),
            _as_iso_text(ended_at),
            now,
            now,
        ),
    )
    if not instance_exists:
        _insert_audit_event(
            conn,
            event_type="strategy_instance_created",
            entity_type="strategy_instance",
            entity_id=identity["instance_id"],
            account_key=identity.get("account_key"),
            severity="info",
            message="Strategy instance created from imported trade identity",
            payload=identity,
        )
    return identity["strategy_id"], identity["instance_id"]


def _broker_id(server: str) -> str:
    clean_server = re.sub(r"[^A-Za-z0-9]+", "_", str(server or "unknown")).strip("_") or "unknown"
    return f"broker_{clean_server}"


def _backfill_phase1_from_legacy_trades(conn: sqlite3.Connection) -> None:
    try:
        normalized_rows = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM closed_trades
             WHERE source_file IS NOT NULL
               AND source_file <> 'legacy_trades_table'
            """
        ).fetchone()
        if normalized_rows and int(normalized_rows["n"] or 0) > 0:
            return
    except sqlite3.OperationalError:
        pass
    try:
        rows = conn.execute(
            """
            SELECT account_id, platform, magic, ticket, symbol, trade_type,
                   open_time, close_time, open_price, close_price, lots,
                   profit, pips, comment, label, imported_at
              FROM trades
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return

    now = _utc_now()
    for row in rows:
        login = str(row["account_id"] or "")
        platform = str(row["platform"] or "MT4").upper()
        label = str(row["label"] or login)
        account_type = _infer_account_type(label)
        server = "unknown"
        broker_id = _broker_id(server)
        account_key = get_account_key(platform, server, login, account_type)
        strategy_id = _strategy_id(account_key, row["magic"])
        identity = strategy_identity_from_trade({
            "account_key": account_key,
            "account_id": login,
            "platform": platform,
            "magic": row["magic"],
            "symbol": row["symbol"],
            "comment": row["comment"],
        })
        definition_id, instance_id = ensure_strategy_definition_and_instance(
            conn,
            identity,
            started_at=row["open_time"],
            ended_at=row["close_time"],
        )

        conn.execute(
            """
            INSERT OR IGNORE INTO brokers (
              id, name, server, timezone, commission_model, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (broker_id, server, server, "broker_local", "unknown", now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts (
              id, broker_id, login, platform, server, account_type,
              alias, currency, leverage, created_at, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (account_key, broker_id, login, platform, server, account_type, label, "", "", now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO strategies (
              id, account_id, magic_number, name, description, symbols,
              created_at, strategy_definition_id, strategy_instance_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                account_key,
                row["magic"],
                f"magic:{row['magic'] or 0}",
                "Migrated from legacy trades table",
                json.dumps([row["symbol"]], ensure_ascii=False) if row["symbol"] else "[]",
                now,
                definition_id,
                instance_id,
            ),
        )
        conn.execute(
            """
            UPDATE strategies
               SET strategy_definition_id = COALESCE(strategy_definition_id, ?),
                   strategy_instance_id = COALESCE(strategy_instance_id, ?)
             WHERE id = ?
            """,
            (definition_id, instance_id, strategy_id),
        )

        profit = row["profit"] or 0.0
        natural_key = trade_natural_key(platform, account_key, row["ticket"], row["magic"], row["symbol"], row["open_time"], row["close_time"])
        hash_uniq = trade_hash_v2(platform, account_key, row["ticket"], row["magic"], row["symbol"], row["open_time"], row["close_time"], profit, row["lots"])
        existing = conn.execute("SELECT id FROM closed_trades WHERE natural_key = ? LIMIT 1", (natural_key,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE closed_trades
                   SET last_seen_at = COALESCE(last_seen_at, ?),
                       hash_version = COALESCE(hash_version, 2)
                 WHERE id = ?
                """,
                (now, existing["id"]),
            )
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO closed_trades (
              account_id, strategy_id, legacy_account_id, ticket, symbol, action,
              volume, open_price, close_price, open_time, close_time, swap,
              commission, profit, balance_before, balance_after, hash_uniq, imported_at,
              natural_key, hash_version, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, 2, ?)
            """,
            (
                account_key,
                strategy_id,
                login,
                str(row["ticket"] or ""),
                str(row["symbol"] or ""),
                str(row["trade_type"] or "").lower(),
                row["lots"],
                row["open_price"],
                row["close_price"],
                _as_iso_text(row["open_time"]),
                _as_iso_text(row["close_time"]),
                0.0,
                0.0,
                profit,
                hash_uniq,
                row["imported_at"] or now,
                natural_key,
                now,
            ),
        )


def _backfill_strategy_links(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT s.id, s.account_id, s.magic_number, s.name, s.symbols,
               a.platform
          FROM strategies s
          JOIN accounts a ON a.id = s.account_id
         WHERE s.strategy_definition_id IS NULL
            OR s.strategy_instance_id IS NULL
        """
    ).fetchall()
    for row in rows:
        symbol = "unknown"
        try:
            symbols = json.loads(row["symbols"] or "[]")
            if symbols:
                symbol = str(symbols[0] or "unknown")
        except Exception:
            pass
        bounds = conn.execute(
            """
            SELECT MIN(open_time) AS started_at, MAX(close_time) AS ended_at
              FROM closed_trades
             WHERE strategy_id = ?
            """,
            (row["id"],),
        ).fetchone()
        identity = strategy_identity_from_trade({
            "account_key": row["account_id"],
            "platform": row["platform"],
            "magic": row["magic_number"],
            "symbol": symbol,
            "comment": row["name"],
        })
        definition_id, instance_id = ensure_strategy_definition_and_instance(
            conn,
            identity,
            started_at=bounds["started_at"] if bounds else None,
            ended_at=bounds["ended_at"] if bounds else None,
        )
        conn.execute(
            """
            UPDATE strategies
               SET strategy_definition_id = ?,
                   strategy_instance_id = ?
             WHERE id = ?
            """,
            (definition_id, instance_id, row["id"]),
        )


def _raw_trade_hash_from_payload(row: sqlite3.Row) -> str | None:
    try:
        payload = json.loads(row["raw_payload_json"] or "{}")
    except Exception:
        return None
    account_id = str(row["account_id"] or payload.get("account") or payload.get("Account") or "")
    ticket = payload.get("ticket", payload.get("Ticket", ""))
    symbol = payload.get("symbol", payload.get("Symbol", ""))
    open_time = payload.get("open_time", payload.get("OpenTime", ""))
    close_time = payload.get("close_time", payload.get("CloseTime", ""))
    profit = payload.get("net_profit", payload.get("NetProfit", payload.get("profit", payload.get("Profit", 0))))
    return trade_hash(account_id, ticket, symbol, open_time, close_time, profit)


def _backfill_closed_trade_traceability(conn: sqlite3.Connection) -> None:
    try:
        raw_rows = conn.execute(
            """
            SELECT id, import_run_id, source_file, account_key, account_id,
                   row_hash, raw_payload_json
              FROM raw_trade_imports
             WHERE normalized_status = 'ok'
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return

    now = _utc_now()
    for raw in raw_rows:
        hash_uniq = _raw_trade_hash_from_payload(raw)
        if not hash_uniq:
            continue
        conn.execute(
            """
            UPDATE closed_trades
               SET import_run_id = COALESCE(import_run_id, ?),
                   source_file = COALESCE(source_file, ?),
                   source_row_hash = COALESCE(source_row_hash, ?),
                   raw_import_id = COALESCE(raw_import_id, ?),
                   normalized_at = COALESCE(normalized_at, ?)
             WHERE hash_uniq = ?
               AND raw_import_id IS NULL
            """,
            (raw["import_run_id"], raw["source_file"], raw["row_hash"], raw["id"], now, hash_uniq),
        )

    missing = conn.execute(
        """
        SELECT id, account_id, legacy_account_id, ticket, symbol, action, volume,
               open_price, close_price, open_time, close_time, swap, commission,
               profit, hash_uniq
          FROM closed_trades
         WHERE raw_import_id IS NULL
        """
    ).fetchall()
    if not missing:
        return

    run_by_account: dict[str, int] = {}
    for trade in missing:
        account_key = trade["account_id"]
        if account_key not in run_by_account:
            cursor = conn.execute(
                """
                INSERT INTO import_runs (
                  account_id, source_file, rows_processed, rows_inserted,
                  hash_file, status, error_log, started_at, finished_at
                )
                VALUES (?, 'legacy_trades_table', 0, 0, NULL, 'backfill', '[]', ?, ?)
                """,
                (account_key, now, now),
            )
            run_by_account[account_key] = int(cursor.lastrowid)

        payload = {
            "ticket": trade["ticket"],
            "symbol": trade["symbol"],
            "type": trade["action"],
            "lots": trade["volume"],
            "open_price": trade["open_price"],
            "close_price": trade["close_price"],
            "open_time": trade["open_time"],
            "close_time": trade["close_time"],
            "swap": trade["swap"],
            "commission": trade["commission"],
            "profit": trade["profit"],
            "account": trade["legacy_account_id"],
            "account_key": account_key,
            "legacy_backfill": True,
        }
        raw_json = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True)
        row_hash = hashlib.md5(raw_json.encode("utf-8")).hexdigest()
        cursor = conn.execute(
            """
            INSERT INTO raw_trade_imports (
              import_run_id, source_file, source_platform, account_key, account_id,
              row_number, row_hash, raw_payload_json, normalized_status,
              error_message, created_at
            )
            VALUES (?, 'legacy_trades_table', NULL, ?, ?, ?, ?, ?, 'ok', NULL, ?)
            """,
            (
                run_by_account[account_key],
                account_key,
                trade["legacy_account_id"],
                int(trade["id"]),
                row_hash,
                raw_json,
                now,
            ),
        )
        raw_id = int(cursor.lastrowid)
        conn.execute(
            """
            UPDATE closed_trades
               SET import_run_id = ?,
                   source_file = 'legacy_trades_table',
                   source_row_hash = ?,
                   raw_import_id = ?,
                   normalized_at = COALESCE(normalized_at, ?)
             WHERE id = ?
            """,
            (run_by_account[account_key], row_hash, raw_id, now, int(trade["id"])),
        )

    for account_key, run_id in run_by_account.items():
        counts = conn.execute(
            "SELECT COUNT(*) FROM raw_trade_imports WHERE import_run_id = ?",
            (run_id,),
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE import_runs
               SET rows_processed = ?, rows_inserted = ?
             WHERE id = ?
            """,
            (counts, counts, run_id),
        )


def _backfill_trade_hash_v2(conn: sqlite3.Connection) -> None:
    existing_keys = {
        row["natural_key"]: row["id"]
        for row in conn.execute(
            "SELECT id, natural_key FROM closed_trades WHERE natural_key IS NOT NULL ORDER BY id ASC"
        ).fetchall()
        if row["natural_key"]
    }
    rows = conn.execute(
        """
        SELECT ct.id, ct.account_id, ct.ticket, ct.symbol, ct.open_time, ct.close_time,
               ct.profit, ct.volume, ct.hash_uniq, ct.natural_key, ct.raw_import_id,
               ct.import_run_id, s.magic_number,
               a.platform
          FROM closed_trades ct
          JOIN accounts a ON a.id = ct.account_id
          LEFT JOIN strategies s ON s.id = ct.strategy_id
         WHERE ct.hash_version IS NULL OR ct.hash_version < 2 OR ct.natural_key IS NULL
        """
    ).fetchall()
    now = _utc_now()
    for row in rows:
        natural_key = trade_natural_key(
            row["platform"],
            row["account_id"],
            row["ticket"],
            row["magic_number"],
            row["symbol"],
            row["open_time"],
            row["close_time"],
        )
        hash_v2 = trade_hash_v2(
            row["platform"],
            row["account_id"],
            row["ticket"],
            row["magic_number"],
            row["symbol"],
            row["open_time"],
            row["close_time"],
            row["profit"],
            row["volume"],
        )
        existing_id = existing_keys.get(natural_key)
        if existing_id and existing_id != row["id"]:
            existing = conn.execute("SELECT * FROM closed_trades WHERE id = ?", (existing_id,)).fetchone()
            _log_trade_conflict(
                conn,
                import_run_id=row["import_run_id"],
                raw_import_id=row["raw_import_id"],
                existing_trade_id=existing_id,
                account_key=row["account_id"],
                natural_key=natural_key,
                existing_hash=existing["hash_uniq"] if existing else None,
                incoming_hash=hash_v2,
                conflict_type="duplicate_natural_key_removed",
                existing_payload=dict(existing) if existing else {},
                incoming_payload=dict(row),
            )
            conn.execute("DELETE FROM closed_trades WHERE id = ?", (int(row["id"]),))
            continue
        existing_keys[natural_key] = row["id"]
        try:
            conn.execute(
                """
                UPDATE closed_trades
                   SET natural_key = ?,
                       hash_uniq = ?,
                       hash_version = 2,
                       last_seen_at = COALESCE(last_seen_at, ?)
                 WHERE id = ?
                """,
                (natural_key, hash_v2, now, int(row["id"])),
            )
        except sqlite3.IntegrityError:
            _log_trade_conflict(
                conn,
                import_run_id=None,
                raw_import_id=None,
                existing_trade_id=int(row["id"]),
                account_key=row["account_id"],
                natural_key=natural_key,
                existing_hash=row["hash_uniq"],
                incoming_hash=hash_v2,
                conflict_type="legacy_hash_collision",
                existing_payload={},
                incoming_payload=dict(row),
            )


def _log_trade_conflict(
    conn: sqlite3.Connection,
    import_run_id,
    raw_import_id,
    existing_trade_id,
    account_key,
    natural_key,
    existing_hash,
    incoming_hash,
    conflict_type,
    existing_payload,
    incoming_payload,
) -> None:
    cursor = conn.execute(
        """
        INSERT INTO trade_import_conflicts (
          import_run_id, raw_import_id, existing_trade_id, account_key,
          natural_key, existing_hash, incoming_hash, conflict_type,
          existing_payload_json, incoming_payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_run_id,
            raw_import_id,
            existing_trade_id,
            account_key,
            natural_key,
            existing_hash,
            incoming_hash,
            conflict_type,
            encode_result(existing_payload or {}),
            encode_result(incoming_payload or {}),
            _utc_now(),
        ),
    )
    _insert_audit_event(
        conn,
        event_type="duplicate_trade_detected",
        entity_type="closed_trade",
        entity_id=existing_trade_id,
        account_key=account_key,
        severity="warning",
        message=f"Trade import conflict detected: {conflict_type}",
        payload={
            "conflict_id": cursor.lastrowid,
            "import_run_id": import_run_id,
            "raw_import_id": raw_import_id,
            "existing_trade_id": existing_trade_id,
            "natural_key": natural_key,
            "existing_hash": existing_hash,
            "incoming_hash": incoming_hash,
            "conflict_type": conflict_type,
        },
    )


def migrate_commands_schema(conn: sqlite3.Connection) -> None:
    """Create the durable command queue used by Commander EAs."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commands (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_composite_key TEXT NOT NULL,
          account_id TEXT,
          account_label TEXT,
          platform TEXT,
          system TEXT,
          magic INTEGER,
          action TEXT NOT NULL,
          parameters TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL,
          sent_to_ea_at TEXT,
          acknowledged_at TEXT,
          executed_at TEXT,
          response TEXT,
          error_message TEXT,
          processed_flag INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status, processed_flag, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_commands_account ON commands(account_composite_key, id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS command_executions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          command_id INTEGER NOT NULL,
          account_id TEXT,
          status_before TEXT,
          status_after TEXT,
          response_data TEXT,
          executed_at TEXT NOT NULL,
          FOREIGN KEY(command_id) REFERENCES commands(id)
        )
        """
    )


def upsert_trades(trades: list[dict], account_id: str, platform: str) -> int:
    """Insert new normalized trades and ignore duplicates."""
    if not trades:
        return 0

    rows = []
    imported_at = _utc_now()
    for trade in trades:
        rows.append(
            (
                str(account_id),
                str(platform),
                trade.get("magic"),
                str(trade.get("ticket", "")),
                str(trade.get("symbol", "")),
                str(trade.get("type", "")),
                _as_iso_text(trade.get("open_time")),
                _as_iso_text(trade.get("close_time")),
                trade.get("open_price"),
                trade.get("close_price"),
                trade.get("lots"),
                trade.get("net_profit", trade.get("profit")),
                trade.get("pips"),
                str(trade.get("comment", "")),
                str(trade.get("account_label", trade.get("label", ""))),
                imported_at,
            )
        )

    with _connect() as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO trades (
              account_id, platform, magic, ticket, symbol, trade_type,
              open_time, close_time, open_price, close_price, lots,
              profit, pips, comment, label, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        _upsert_closed_trades(conn, trades)
        return conn.total_changes - before


def upsert_closed_trades_only(trades: list[dict]) -> int:
    """Upsert normalized closed trades and return inserted/updated row count."""
    if not trades:
        return 0
    with _connect() as conn:
        return _upsert_closed_trades(conn, trades)


def replace_trades(trades: list[dict]) -> int:
    """Replace the trades table with the current normalized CSV snapshot."""
    rows = []
    imported_at = _utc_now()
    for trade in trades:
        rows.append(
            (
                str(trade.get("account_id", "")),
                str(trade.get("platform", "")),
                trade.get("magic"),
                str(trade.get("ticket", "")),
                str(trade.get("symbol", "")),
                str(trade.get("type", "")),
                _as_iso_text(trade.get("open_time")),
                _as_iso_text(trade.get("close_time")),
                trade.get("open_price"),
                trade.get("close_price"),
                trade.get("lots"),
                trade.get("net_profit", trade.get("profit")),
                trade.get("pips"),
                str(trade.get("comment", "")),
                str(trade.get("account_label", trade.get("label", ""))),
                imported_at,
            )
        )

    with _connect() as conn:
        conn.execute("DELETE FROM trades")
        if rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO trades (
                  account_id, platform, magic, ticket, symbol, trade_type,
                  open_time, close_time, open_price, close_price, lots,
                  profit, pips, comment, label, imported_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        _upsert_closed_trades(conn, trades)
        return len(rows)


def get_trades(account_id: str = None, magic: int = None) -> list[dict]:
    try:
        rows = _get_closed_trades(account_id=account_id, magic=magic)
        if rows:
            return rows
    except Exception:
        pass

    query = "SELECT * FROM trades"
    clauses = []
    params = []
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(str(account_id))
    if magic is not None:
        clauses.append("magic = ?")
        params.append(int(magic))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY close_time ASC, id ASC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    trades = []
    for row in rows:
        trades.append(
            {
                "ticket": row["ticket"] or "",
                "symbol": row["symbol"] or "",
                "type": row["trade_type"] or "",
                "lots": row["lots"] or 0.0,
                "open_price": row["open_price"] or 0.0,
                "close_price": row["close_price"] or 0.0,
                "open_time": row["open_time"],
                "close_time": row["close_time"],
                "profit": row["profit"] or 0.0,
                "swap": 0.0,
                "commission": 0.0,
                "net_profit": row["profit"] or 0.0,
                "pips": row["pips"] or 0.0,
                "magic": row["magic"] or 0,
                "comment": row["comment"] or "",
                "account_id": row["account_id"],
                "account_label": row["label"] or row["account_id"],
                "platform": row["platform"],
                "source_file": "qa_portfolio.db",
            }
        )
    return trades


def _get_closed_trades(account_id: str = None, magic: int = None) -> list[dict]:
    query = """
        SELECT ct.*, a.login, a.platform, a.alias, a.account_type, a.currency,
               s.magic_number, s.strategy_definition_id, s.strategy_instance_id
          FROM closed_trades ct
          JOIN accounts a ON a.id = ct.account_id
          LEFT JOIN strategies s ON s.id = ct.strategy_id
    """
    clauses = []
    params = []
    if account_id is not None:
        clauses.append("(ct.account_id = ? OR ct.legacy_account_id = ? OR a.login = ?)")
        params.extend([str(account_id), str(account_id), str(account_id)])
    if magic is not None:
        clauses.append("s.magic_number = ?")
        params.append(int(magic))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY ct.close_time ASC, ct.id ASC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    trades = []
    for row in rows:
        trades.append(
            {
                "ticket": row["ticket"] or "",
                "symbol": row["symbol"] or "",
                "type": str(row["action"] or "").upper(),
                "lots": row["volume"] or 0.0,
                "open_price": row["open_price"] or 0.0,
                "close_price": row["close_price"] or 0.0,
                "open_time": row["open_time"],
                "close_time": row["close_time"],
                "profit": row["profit"] or row["net_profit"] or 0.0,
                "gross_profit": row["gross_profit"] or 0.0,
                "gross_loss": row["gross_loss"] or 0.0,
                "swap": row["swap"] or 0.0,
                "commission": row["commission"] or 0.0,
                "fees": row["fees"] or 0.0,
                "spread_cost_estimated": row["spread_cost_estimated"] or 0.0,
                "slippage_estimated": row["slippage_estimated"] or 0.0,
                "net_profit": row["net_profit"] if row["net_profit"] is not None else (row["profit"] or 0.0),
                "balance_before": row["balance_before"],
                "balance_after": row["balance_after"],
                "pips": 0.0,
                "magic": row["magic_number"] or 0,
                "strategy_id": row["strategy_definition_id"] or row["strategy_id"],
                "instance_id": row["strategy_instance_id"],
                "comment": "",
                "account_id": row["legacy_account_id"] or row["login"],
                "account_key": row["account_id"],
                "account_label": row["alias"] or row["login"],
                "account_type": str(row["account_type"] or "real").upper(),
                "platform": row["platform"],
                "currency": row["currency"] or "",
                "source_file": "closed_trades",
            }
        )
    return trades


def _upsert_closed_trades(conn: sqlite3.Connection, trades: list[dict]) -> int:
    if not trades:
        return 0
    now = _utc_now()
    inserted = 0
    for trade in trades:
        login = str(trade.get("account_id", ""))
        platform = str(trade.get("platform", "MT4")).upper()
        label = str(trade.get("account_label", trade.get("label", login)))
        account_type = str(trade.get("account_type") or _infer_account_type(label)).lower()
        server = str(trade.get("server", "unknown") or "unknown")
        account_key = trade.get("account_key") or get_account_key(platform, server, login, account_type)
        broker_id = _broker_id(server)
        magic = trade.get("magic", 0)
        strategy_id = _strategy_id(account_key, magic)
        symbol = str(trade.get("symbol", ""))
        identity = strategy_identity_from_trade({
            **trade,
            "account_key": account_key,
            "platform": platform,
            "symbol": symbol,
            "magic": magic,
        })
        definition_id, instance_id = ensure_strategy_definition_and_instance(
            conn,
            identity,
            started_at=trade.get("open_time"),
            ended_at=trade.get("close_time"),
        )
        raw_profit = _to_float(trade.get("profit"), 0.0)
        swap = _to_float(trade.get("swap"), 0.0)
        commission = _to_float(trade.get("commission"), 0.0)
        fees = _to_float(trade.get("fees"), 0.0)
        spread_cost_estimated = _to_float(trade.get("spread_cost_estimated"), 0.0)
        slippage_estimated = _to_float(trade.get("slippage_estimated"), 0.0)
        net_profit = _to_float(
            trade.get("net_profit"),
            raw_profit + swap + commission + fees - spread_cost_estimated - slippage_estimated,
        )
        gross_profit = _to_float(trade.get("gross_profit"), max(raw_profit, 0.0))
        gross_loss = _to_float(trade.get("gross_loss"), abs(min(raw_profit, 0.0)))
        volume = trade.get("lots", trade.get("volume"))
        natural_key = trade_natural_key(platform, account_key, trade.get("ticket"), magic, symbol, trade.get("open_time"), trade.get("close_time"))
        hash_uniq = trade_hash_v2(platform, account_key, trade.get("ticket"), magic, symbol, trade.get("open_time"), trade.get("close_time"), net_profit, volume)
        import_run_id = trade.get("import_run_id")
        source_file = trade.get("source_file")
        source_row_hash = trade.get("source_row_hash")
        raw_import_id = trade.get("raw_import_id")
        normalized_at = trade.get("normalized_at") or now
        balance_before = trade.get("balance_before")
        balance_after = trade.get("balance_after")
        balance_reconstructed = 1 if trade.get("balance_reconstructed") else 0

        conn.execute(
            """
            INSERT OR IGNORE INTO brokers (
              id, name, server, timezone, commission_model, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (broker_id, server, server, "broker_local", "unknown", now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts (
              id, broker_id, login, platform, server, account_type,
              alias, currency, leverage, created_at, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                account_key,
                broker_id,
                login,
                platform,
                server,
                account_type,
                label,
                str(trade.get("currency", "")),
                "",
                now,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO strategies (
              id, account_id, magic_number, name, description, symbols,
              created_at, strategy_definition_id, strategy_instance_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                account_key,
                magic,
                f"magic:{magic or 0}",
                "Imported from normalized CSV",
                json.dumps([symbol], ensure_ascii=False) if symbol else "[]",
                now,
                definition_id,
                instance_id,
            ),
        )
        existing = conn.execute(
            """
            SELECT id, hash_uniq, natural_key, account_id, ticket, symbol, action, volume,
                   open_price, close_price, open_time, close_time, profit, net_profit,
                   import_run_id, source_file, source_row_hash, raw_import_id
              FROM closed_trades
             WHERE natural_key = ?
                OR (
                    account_id = ?
                    AND COALESCE(ticket, '') = COALESCE(?, '')
                    AND COALESCE(symbol, '') = COALESCE(?, '')
                    AND COALESCE(open_time, '') = COALESCE(?, '')
                    AND COALESCE(close_time, '') = COALESCE(?, '')
                )
             ORDER BY CASE WHEN natural_key = ? THEN 0 ELSE 1 END, id ASC
             LIMIT 1
            """,
            (
                natural_key,
                account_key,
                str(trade.get("ticket", "")),
                symbol,
                _as_iso_text(trade.get("open_time")),
                _as_iso_text(trade.get("close_time")),
                natural_key,
            ),
        ).fetchone()
        if existing:
            if existing["hash_uniq"] != hash_uniq and existing["natural_key"] == natural_key:
                _log_trade_conflict(
                    conn,
                    import_run_id,
                    raw_import_id,
                    existing["id"],
                    account_key,
                    natural_key,
                    existing["hash_uniq"],
                    hash_uniq,
                    "changed_trade_same_natural_key",
                    dict(existing),
                    trade,
                )
            else:
                before = conn.total_changes
                conn.execute(
                    """
                    UPDATE closed_trades
                       SET import_run_id = COALESCE(import_run_id, ?),
                           source_file = COALESCE(source_file, ?),
                           source_row_hash = COALESCE(source_row_hash, ?),
                           raw_import_id = COALESCE(raw_import_id, ?),
                           normalized_at = COALESCE(normalized_at, ?),
                           natural_key = ?,
                           hash_uniq = ?,
                           hash_version = 2,
                           last_seen_at = ?,
                           gross_profit = COALESCE(gross_profit, ?),
                           gross_loss = COALESCE(gross_loss, ?),
                           fees = COALESCE(fees, ?),
                           spread_cost_estimated = COALESCE(spread_cost_estimated, ?),
                           slippage_estimated = COALESCE(slippage_estimated, ?),
                           net_profit = COALESCE(net_profit, ?),
                           balance_before = COALESCE(balance_before, ?),
                           balance_after = COALESCE(balance_after, ?),
                           balance_reconstructed = MAX(COALESCE(balance_reconstructed, 0), ?)
                     WHERE id = ?
                    """,
                    (
                        import_run_id,
                        source_file,
                        source_row_hash,
                        raw_import_id,
                        normalized_at,
                        natural_key,
                        hash_uniq,
                        now,
                        gross_profit,
                        gross_loss,
                        fees,
                        spread_cost_estimated,
                        slippage_estimated,
                        net_profit,
                        balance_before,
                        balance_after,
                        balance_reconstructed,
                        existing["id"],
                    ),
                )
                inserted += conn.total_changes - before
            continue

        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO closed_trades (
              account_id, strategy_id, legacy_account_id, ticket, symbol, action,
              volume, open_price, close_price, open_time, close_time, swap,
              commission, profit, balance_before, balance_after, hash_uniq, imported_at,
              import_run_id, source_file, source_row_hash, raw_import_id, normalized_at,
              natural_key, hash_version, last_seen_at, gross_profit, gross_loss, fees,
              spread_cost_estimated, slippage_estimated, net_profit, balance_reconstructed
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_key,
                strategy_id,
                login,
                str(trade.get("ticket", "")),
                symbol,
                str(trade.get("type", "")).lower(),
                volume,
                trade.get("open_price"),
                trade.get("close_price"),
                _as_iso_text(trade.get("open_time")),
                _as_iso_text(trade.get("close_time")),
                swap,
                commission,
                net_profit,
                balance_before,
                balance_after,
                hash_uniq,
                now,
                import_run_id,
                source_file,
                source_row_hash,
                raw_import_id,
                normalized_at,
                natural_key,
                now,
                gross_profit,
                gross_loss,
                fees,
                spread_cost_estimated,
                slippage_estimated,
                net_profit,
                balance_reconstructed,
            ),
        )
        if conn.total_changes == before:
            conn.execute(
                """
                UPDATE closed_trades
                   SET import_run_id = COALESCE(import_run_id, ?),
                       source_file = COALESCE(source_file, ?),
                       source_row_hash = COALESCE(source_row_hash, ?),
                       raw_import_id = COALESCE(raw_import_id, ?),
                       normalized_at = COALESCE(normalized_at, ?),
                       natural_key = COALESCE(natural_key, ?),
                       hash_version = COALESCE(hash_version, 2),
                       last_seen_at = ?,
                       gross_profit = COALESCE(gross_profit, ?),
                       gross_loss = COALESCE(gross_loss, ?),
                       fees = COALESCE(fees, ?),
                       spread_cost_estimated = COALESCE(spread_cost_estimated, ?),
                       slippage_estimated = COALESCE(slippage_estimated, ?),
                       net_profit = COALESCE(net_profit, ?),
                       balance_before = COALESCE(balance_before, ?),
                       balance_after = COALESCE(balance_after, ?),
                       balance_reconstructed = MAX(COALESCE(balance_reconstructed, 0), ?)
                 WHERE hash_uniq = ?
                """,
                (
                    import_run_id,
                    source_file,
                    source_row_hash,
                    raw_import_id,
                    normalized_at,
                    natural_key,
                    now,
                    gross_profit,
                    gross_loss,
                    fees,
                    spread_cost_estimated,
                    slippage_estimated,
                    net_profit,
                    balance_before,
                    balance_after,
                    balance_reconstructed,
                    hash_uniq,
                ),
            )
        inserted += conn.total_changes - before
    return inserted


def save_system_metrics(metrics: list[dict]) -> None:
    calculated_at = _utc_now()
    rows = []
    for item in metrics:
        metric = item.get("metrics", item)
        rows.append(
            (
                item.get("magic"),
                item.get("account_id"),
                metric.get("trades", metric.get("trades_count", 0)),
                metric.get("net_profit", 0.0),
                metric.get("profit_factor", 0.0),
                metric.get("max_drawdown", metric.get("max_drawdown_pct", 0.0)),
                metric.get("avg_pips", 0.0),
                metric.get("sharpe", 0.0),
                metric.get("win_rate", 0.0),
                metric.get("first_trade", "n/a"),
                metric.get("last_trade", "n/a"),
                item.get("action"),
                calculated_at,
                item.get("rules_version"),
            )
        )

    with _connect() as conn:
        conn.execute("DELETE FROM system_metrics")
        if rows:
            conn.executemany(
                """
                INSERT INTO system_metrics (
                  magic, account_id, trades_count, net_profit, profit_factor,
                  max_drawdown, avg_pips, sharpe, win_rate, first_trade,
                  last_trade, action, calculated_at, rules_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def save_strategy_metric_snapshots(snapshots: list[dict]) -> int:
    if not snapshots:
        return 0

    calculated_at = _utc_now()
    rows = []
    for item in snapshots:
        metric = dict(item.get("metrics", item))
        expected_metrics = get_latest_strategy_expected_metrics(
            strategy_id=item.get("strategy_id") or metric.get("strategy_id"),
            instance_id=item.get("instance_id") or metric.get("instance_id"),
            strategy_version_id=item.get("strategy_version_id") or metric.get("strategy_version_id"),
        )
        if expected_metrics:
            from services.performance_metrics_service import compare_against_expected

            decision = compare_against_expected(metric, expected_metrics)
            metric.update({
                "status": decision.get("status"),
                "health_score": decision.get("health_score"),
                "decision_state": decision.get("decision_state"),
                "drawdown_ratio": decision.get("drawdown_ratio"),
                "losing_streak_ratio": decision.get("losing_streak_ratio"),
                "profit_factor_ratio": decision.get("profit_factor_ratio"),
                "expectancy_ratio": decision.get("expectancy_ratio"),
                "decision_reasons": decision.get("reasons", []),
                "expected_metrics_id": expected_metrics.get("id"),
                "expected_metrics_source": expected_metrics.get("source"),
            })
        action = item.get("recommended_action") or item.get("classification") or item.get("action")
        action = metric.get("decision_state") or action
        payload = item.get("payload_json")
        if payload is None:
            payload = json.dumps(_json_safe(item.get("payload", metric)), ensure_ascii=False, sort_keys=True)
        elif not isinstance(payload, str):
            payload = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True)

        rows.append(
            (
                item.get("strategy_id"),
                item.get("instance_id"),
                item.get("account_key"),
                item.get("calculated_at") or calculated_at,
                metric.get("first_trade") if metric.get("first_trade") != "n/a" else item.get("period_start"),
                metric.get("last_trade") if metric.get("last_trade") != "n/a" else item.get("period_end"),
                int(metric.get("trades", metric.get("trades_count", 0)) or 0),
                metric.get("net_profit", 0.0),
                metric.get("gross_profit", 0.0),
                metric.get("gross_loss", 0.0),
                metric.get("profit_factor", 0.0),
                metric.get("max_drawdown", metric.get("max_drawdown_pct", 0.0)),
                metric.get("win_rate", 0.0),
                metric.get("expectancy", metric.get("avg_trade", 0.0)),
                metric.get("avg_trade", 0.0),
                metric.get("sharpe", 0.0),
                metric.get("pips_avg", metric.get("avg_pips", 0.0)),
                item.get("classification") or action,
                action,
                item.get("rules_version") or "v1.0",
                payload,
                metric.get("equity_current"),
                metric.get("equity_peak"),
                metric.get("return_pct"),
                metric.get("current_drawdown"),
                metric.get("current_drawdown_pct"),
                metric.get("max_drawdown_pct", metric.get("max_drawdown")),
                metric.get("pnl_curve_equity_current"),
                metric.get("pnl_curve_equity_peak"),
                metric.get("pnl_curve_current_drawdown"),
                metric.get("pnl_curve_current_drawdown_pct"),
                metric.get("pnl_curve_max_drawdown"),
                metric.get("pnl_curve_max_drawdown_pct"),
                1 if metric.get("balance_drawdown_reliable") is True else 0 if metric.get("balance_drawdown_reliable") is False else None,
                metric.get("balance_equity_current"),
                metric.get("balance_equity_peak"),
                metric.get("balance_current_drawdown"),
                metric.get("balance_current_drawdown_pct"),
                metric.get("balance_max_drawdown"),
                metric.get("balance_max_drawdown_pct"),
                metric.get("drawdown_basis"),
                metric.get("return_over_drawdown"),
                metric.get("recovery_factor"),
                metric.get("winning_trades"),
                metric.get("losing_trades"),
                metric.get("trades_last_20"),
                metric.get("trades_last_50"),
                metric.get("trades_last_100"),
                metric.get("current_winning_streak"),
                metric.get("current_losing_streak"),
                metric.get("longest_winning_streak"),
                metric.get("longest_losing_streak"),
                metric.get("average_winning_streak"),
                metric.get("average_losing_streak"),
                metric.get("average_win"),
                metric.get("average_loss"),
                metric.get("payoff_ratio"),
                metric.get("status", item.get("status") or item.get("classification") or action),
                metric.get("health_score", item.get("health_score")),
                metric.get("decision_state", item.get("decision_state") or action),
                metric.get("expected_metrics_id"),
                metric.get("expected_metrics_source"),
                metric.get("drawdown_ratio"),
                metric.get("losing_streak_ratio"),
                metric.get("profit_factor_ratio"),
                metric.get("expectancy_ratio"),
                json.dumps(_json_safe(metric.get("decision_reasons", [])), ensure_ascii=False, sort_keys=True),
            )
        )

    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        for row in rows:
            cursor = conn.execute(
                """
                INSERT INTO strategy_metric_snapshots (
                  strategy_id, instance_id, account_key, calculated_at,
                  period_start, period_end, trades, net_profit, gross_profit,
                  gross_loss, profit_factor, max_drawdown, win_rate, expectancy,
                  avg_trade, sharpe, pips_avg, classification, recommended_action,
                  rules_version, payload_json, equity_current, equity_peak,
                  return_pct, current_drawdown, current_drawdown_pct,
                  max_drawdown_pct, pnl_curve_equity_current, pnl_curve_equity_peak,
                  pnl_curve_current_drawdown, pnl_curve_current_drawdown_pct,
                  pnl_curve_max_drawdown, pnl_curve_max_drawdown_pct,
                  balance_drawdown_reliable, balance_equity_current,
                  balance_equity_peak, balance_current_drawdown,
                  balance_current_drawdown_pct, balance_max_drawdown,
                  balance_max_drawdown_pct, drawdown_basis,
                  return_over_drawdown, recovery_factor,
                  winning_trades, losing_trades, trades_last_20, trades_last_50,
                  trades_last_100, current_winning_streak, current_losing_streak,
                  longest_winning_streak, longest_losing_streak,
                  average_winning_streak, average_losing_streak, average_win,
                  average_loss, payoff_ratio, status, health_score, decision_state,
                  expected_metrics_id, expected_metrics_source, drawdown_ratio,
                  losing_streak_ratio, profit_factor_ratio, expectancy_ratio,
                  decision_reasons_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            _insert_audit_event(
                conn,
                event_type="metric_snapshot_created",
                entity_type="strategy_metric_snapshot",
                entity_id=cursor.lastrowid,
                account_key=row[2],
                severity="info",
                message="Strategy metric snapshot created",
                payload={
                    "snapshot_id": cursor.lastrowid,
                    "strategy_id": row[0],
                    "instance_id": row[1],
                    "account_key": row[2],
                    "trades": row[6],
                    "net_profit": row[7],
                    "classification": row[17],
                    "recommended_action": row[18],
                    "rules_version": row[19],
                },
            )
        return len(rows)


def get_latest_strategy_expected_metrics(
    strategy_id: str | None = None,
    instance_id: str | None = None,
    strategy_version_id: int | None = None,
) -> dict | None:
    clauses = []
    params = []
    if strategy_id:
        clauses.append("strategy_id = ?")
        params.append(strategy_id)
    if strategy_version_id not in (None, ""):
        clauses.append("strategy_version_id = ?")
        params.append(int(strategy_version_id))
    if instance_id:
        clauses.append("(instance_id = ? OR instance_id IS NULL OR instance_id = '')")
        params.append(instance_id)
    if not clauses:
        return None

    query = "SELECT * FROM strategy_expected_metrics WHERE " + " AND ".join(clauses)
    if instance_id:
        query += " ORDER BY CASE WHEN instance_id = ? THEN 0 ELSE 1 END, created_at DESC, id DESC LIMIT 1"
        params.append(instance_id)
    else:
        query += " ORDER BY created_at DESC, id DESC LIMIT 1"
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def get_strategy_metric_snapshots(
    strategy_id: str | None = None,
    instance_id: str | None = None,
    account_key: str | None = None,
    limit: int = 500,
) -> list[dict]:
    query = "SELECT * FROM strategy_metric_snapshots"
    clauses = []
    params = []
    if strategy_id:
        clauses.append("strategy_id = ?")
        params.append(strategy_id)
    if instance_id:
        clauses.append("instance_id = ?")
        params.append(instance_id)
    if account_key:
        clauses.append("account_key = ?")
        params.append(account_key)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY calculated_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def strategy_metric_snapshot_exists(snapshot_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM strategy_metric_snapshots WHERE id = ?",
            (int(snapshot_id),),
        ).fetchone()
    return row is not None


def create_decision_journal_entry(entry: dict) -> dict:
    now = entry.get("created_at") or _utc_now()
    portfolio_context = entry.get("portfolio_context_json")
    if portfolio_context is None:
        portfolio_context = encode_result(entry.get("portfolio_context") or {})
    elif not isinstance(portfolio_context, str):
        portfolio_context = encode_result(portfolio_context)

    with _connect() as conn:
        migrate_decision_journal_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO decision_journal (
              created_at, account_key, account_id, platform, strategy_id,
              instance_id, magic, symbol, decision_type, reason_category,
              human_note, ai_summary, metrics_snapshot_id, previous_state,
              new_state, portfolio_context_json, review_after_days,
              outcome_7d, outcome_30d, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                entry.get("account_key"),
                entry.get("account_id"),
                entry.get("platform"),
                entry.get("strategy_id"),
                entry.get("instance_id"),
                _to_int(entry.get("magic"), None) if entry.get("magic") not in (None, "") else None,
                entry.get("symbol"),
                entry.get("decision_type"),
                entry.get("reason_category"),
                entry.get("human_note"),
                entry.get("ai_summary"),
                _to_int(entry.get("metrics_snapshot_id"), None) if entry.get("metrics_snapshot_id") not in (None, "") else None,
                entry.get("previous_state"),
                entry.get("new_state"),
                portfolio_context,
                _to_int(entry.get("review_after_days"), None) if entry.get("review_after_days") not in (None, "") else None,
                entry.get("outcome_7d"),
                entry.get("outcome_30d"),
                entry.get("created_by") or "dashboard",
            ),
        )
        decision_id = int(cursor.lastrowid)
        _insert_audit_event(
            conn,
            event_type="decision_created",
            entity_type="decision_journal",
            entity_id=decision_id,
            account_key=entry.get("account_key"),
            severity="info",
            message="Decision journal entry created",
            payload={"decision_id": decision_id, "strategy_id": entry.get("strategy_id"), "decision_type": entry.get("decision_type")},
        )
        return dict(conn.execute("SELECT * FROM decision_journal WHERE id = ?", (decision_id,)).fetchone())


def get_decision_journal_entry(decision_id: int) -> dict | None:
    with _connect() as conn:
        migrate_decision_journal_schema(conn)
        row = conn.execute("SELECT * FROM decision_journal WHERE id = ?", (int(decision_id),)).fetchone()
    return dict(row) if row else None


def get_decision_journal_entries(
    strategy_id: str | None = None,
    account_key: str | None = None,
    account_id: str | None = None,
    decision_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    query = "SELECT * FROM decision_journal"
    clauses = []
    params = []
    if strategy_id:
        clauses.append("strategy_id = ?")
        params.append(strategy_id)
    if account_key:
        clauses.append("account_key = ?")
        params.append(account_key)
    if account_id:
        clauses.append("account_id = ?")
        params.append(account_id)
    if decision_type:
        clauses.append("decision_type = ?")
        params.append(decision_type)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit or 100))
    with _connect() as conn:
        migrate_decision_journal_schema(conn)
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def update_decision_journal_outcome(decision_id: int, outcome_7d=None, outcome_30d=None) -> dict | None:
    with _connect() as conn:
        migrate_decision_journal_schema(conn)
        current = conn.execute("SELECT * FROM decision_journal WHERE id = ?", (int(decision_id),)).fetchone()
        if not current:
            return None
        conn.execute(
            """
            UPDATE decision_journal
               SET outcome_7d = COALESCE(?, outcome_7d),
                   outcome_30d = COALESCE(?, outcome_30d)
             WHERE id = ?
            """,
            (outcome_7d, outcome_30d, int(decision_id)),
        )
        updated = dict(conn.execute("SELECT * FROM decision_journal WHERE id = ?", (int(decision_id),)).fetchone())
        _insert_audit_event(
            conn,
            event_type="decision_outcome_updated",
            entity_type="decision_journal",
            entity_id=decision_id,
            account_key=updated.get("account_key"),
            severity="info",
            message="Decision journal outcome updated",
            payload={
                "decision_id": int(decision_id),
                "outcome_7d": updated.get("outcome_7d"),
                "outcome_30d": updated.get("outcome_30d"),
            },
        )
        return updated


def _normalize_json_text(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    return encode_result(value)


def _parameters_hash(parameters_json: str | None) -> str | None:
    if not parameters_json:
        return None
    try:
        payload = json.loads(parameters_json)
        raw = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True)
    except Exception:
        raw = str(parameters_json)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_strategy_version(data: dict) -> dict:
    parameters_json = _normalize_json_text(data.get("parameters_json", data.get("parameters")))
    parameters_hash = data.get("parameters_hash") or _parameters_hash(parameters_json)
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO strategy_versions (
              strategy_id, version_name, parameters_hash, parameters_json,
              source, backtest_ref, expected_profit_factor, expected_max_drawdown,
              expected_trades_per_month, expected_losing_streak, created_at, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("strategy_id"),
                data.get("version_name") or "v1",
                parameters_hash,
                parameters_json,
                data.get("source"),
                data.get("backtest_ref"),
                _to_float(data.get("expected_profit_factor")),
                _to_float(data.get("expected_max_drawdown")),
                _to_float(data.get("expected_trades_per_month")),
                _to_int(data.get("expected_losing_streak"), None) if data.get("expected_losing_streak") not in (None, "") else None,
                data.get("created_at") or _utc_now(),
                data.get("notes"),
            ),
        )
        version_id = int(cursor.lastrowid)
        _insert_audit_event(
            conn,
            event_type="strategy_version_created",
            entity_type="strategy_version",
            entity_id=version_id,
            severity="info",
            message="Strategy version created",
            payload={"strategy_version_id": version_id, "strategy_id": data.get("strategy_id"), "version_name": data.get("version_name") or "v1"},
        )
        return dict(conn.execute("SELECT * FROM strategy_versions WHERE id = ?", (version_id,)).fetchone())


def get_strategy_versions(strategy_id: str | None = None, limit: int = 500) -> list[dict]:
    query = "SELECT * FROM strategy_versions"
    params = []
    if strategy_id:
        query += " WHERE strategy_id = ?"
        params.append(strategy_id)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit or 500))
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def strategy_version_exists(version_id: int) -> bool:
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        row = conn.execute("SELECT id FROM strategy_versions WHERE id = ?", (int(version_id),)).fetchone()
    return row is not None


def create_risk_profile(data: dict) -> dict:
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO risk_profiles (
              name, risk_type, risk_value, max_lots, max_daily_loss,
              max_total_drawdown, created_at, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("name"),
                data.get("risk_type"),
                _to_float(data.get("risk_value")),
                _to_float(data.get("max_lots")),
                _to_float(data.get("max_daily_loss")),
                _to_float(data.get("max_total_drawdown")),
                data.get("created_at") or _utc_now(),
                data.get("notes"),
            ),
        )
        profile_id = int(cursor.lastrowid)
        _insert_audit_event(
            conn,
            event_type="risk_profile_created",
            entity_type="risk_profile",
            entity_id=profile_id,
            severity="info",
            message="Risk profile created",
            payload={"risk_profile_id": profile_id, "name": data.get("name"), "risk_type": data.get("risk_type")},
        )
        return dict(conn.execute("SELECT * FROM risk_profiles WHERE id = ?", (profile_id,)).fetchone())


def get_risk_profiles(limit: int = 500) -> list[dict]:
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        rows = conn.execute(
            "SELECT * FROM risk_profiles ORDER BY name, id LIMIT ?",
            (int(limit or 500),),
        ).fetchall()
    return [dict(row) for row in rows]


def risk_profile_exists(profile_id: int) -> bool:
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        row = conn.execute("SELECT id FROM risk_profiles WHERE id = ?", (int(profile_id),)).fetchone()
    return row is not None


def create_deployment_event(data: dict) -> dict:
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO deployment_events (
              strategy_id, strategy_version_id, instance_id, account_key, account_id,
              platform, magic, symbol, event_type, previous_state, new_state,
              risk_profile_id, event_note, created_at, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("strategy_id"),
                _to_int(data.get("strategy_version_id"), None) if data.get("strategy_version_id") not in (None, "") else None,
                data.get("instance_id"),
                data.get("account_key"),
                data.get("account_id"),
                data.get("platform"),
                _to_int(data.get("magic"), None) if data.get("magic") not in (None, "") else None,
                data.get("symbol"),
                data.get("event_type"),
                data.get("previous_state"),
                data.get("new_state"),
                _to_int(data.get("risk_profile_id"), None) if data.get("risk_profile_id") not in (None, "") else None,
                data.get("event_note"),
                data.get("created_at") or _utc_now(),
                data.get("created_by") or "dashboard",
            ),
        )
        event_id = int(cursor.lastrowid)
        _insert_audit_event(
            conn,
            event_type="deployment_event_created",
            entity_type="deployment_event",
            entity_id=event_id,
            account_key=data.get("account_key"),
            severity="info",
            message="Deployment event created",
            payload={"deployment_event_id": event_id, "strategy_id": data.get("strategy_id"), "event_type": data.get("event_type")},
        )
        return dict(conn.execute("SELECT * FROM deployment_events WHERE id = ?", (event_id,)).fetchone())


def get_deployment_events(
    strategy_id: str | None = None,
    instance_id: str | None = None,
    account_key: str | None = None,
    limit: int = 500,
) -> list[dict]:
    query = "SELECT * FROM deployment_events"
    clauses = []
    params = []
    if strategy_id:
        clauses.append("strategy_id = ?")
        params.append(strategy_id)
    if instance_id:
        clauses.append("instance_id = ?")
        params.append(instance_id)
    if account_key:
        clauses.append("account_key = ?")
        params.append(account_key)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit or 500))
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def link_strategy_instance_version(instance_id: str, strategy_version_id: int) -> dict | None:
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        row = conn.execute("SELECT * FROM strategy_instances WHERE instance_id = ?", (instance_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE strategy_instances SET strategy_version_id = ?, updated_at = ? WHERE instance_id = ?",
            (int(strategy_version_id), _utc_now(), instance_id),
        )
        updated = dict(conn.execute("SELECT * FROM strategy_instances WHERE instance_id = ?", (instance_id,)).fetchone())
        _insert_audit_event(
            conn,
            event_type="strategy_instance_version_linked",
            entity_type="strategy_instance",
            entity_id=instance_id,
            account_key=updated.get("account_key"),
            severity="info",
            message="Strategy instance linked to version",
            payload={"instance_id": instance_id, "strategy_version_id": int(strategy_version_id)},
        )
        return updated


def link_strategy_instance_risk_profile(instance_id: str, risk_profile_id: int, deployment_state: str | None = None) -> dict | None:
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        row = conn.execute("SELECT * FROM strategy_instances WHERE instance_id = ?", (instance_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            """
            UPDATE strategy_instances
               SET risk_profile_id = ?,
                   deployment_state = COALESCE(?, deployment_state),
                   updated_at = ?
             WHERE instance_id = ?
            """,
            (int(risk_profile_id), deployment_state, _utc_now(), instance_id),
        )
        updated = dict(conn.execute("SELECT * FROM strategy_instances WHERE instance_id = ?", (instance_id,)).fetchone())
        _insert_audit_event(
            conn,
            event_type="strategy_instance_risk_profile_linked",
            entity_type="strategy_instance",
            entity_id=instance_id,
            account_key=updated.get("account_key"),
            severity="info",
            message="Strategy instance linked to risk profile",
            payload={"instance_id": instance_id, "risk_profile_id": int(risk_profile_id), "deployment_state": deployment_state},
        )
        return updated


def get_strategy_identity_map(instance_ids: list[str] | None = None) -> dict[str, dict]:
    query = """
        SELECT si.instance_id, si.strategy_version_id, sv.version_name,
               sv.parameters_hash, si.risk_profile_id, rp.name AS risk_profile_name,
               si.deployment_state
          FROM strategy_instances si
          LEFT JOIN strategy_versions sv ON sv.id = si.strategy_version_id
          LEFT JOIN risk_profiles rp ON rp.id = si.risk_profile_id
    """
    params = []
    if instance_ids:
        placeholders = ",".join("?" for _ in instance_ids)
        query += f" WHERE si.instance_id IN ({placeholders})"
        params.extend(instance_ids)
    with _connect() as conn:
        migrate_strategy_identity_schema(conn)
        rows = conn.execute(query, params).fetchall()
    return {row["instance_id"]: dict(row) for row in rows}


def _json_text(value) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)


def _insert_decision_append_only(conn: sqlite3.Connection, data: dict) -> int:
    evidence = data.get("evidence_json")
    if evidence in (None, "", {}):
        raise ValueError("evidence_json is required for append-only decisions")
    actor_type = data.get("actor_type") or "system_rule"
    if actor_type not in ACTOR_TYPE_VALUES:
        raise ValueError(f"invalid actor_type: {actor_type}")
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO decisions_append_only (
          created_at, created_by, actor_type, entity_type, entity_id,
          decision_type, previous_state, new_state, reason,
          rule_set_id, rule_evaluation_id, evidence_json,
          metrics_snapshot_id, source_snapshot_ids_json,
          supersedes_decision_id, correlation_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.get("created_at") or _utc_now(),
            data.get("created_by"),
            actor_type,
            data.get("entity_type"),
            str(data.get("entity_id")),
            data.get("decision_type"),
            data.get("previous_state"),
            data.get("new_state"),
            data.get("reason") or "Governance decision",
            _to_int(data.get("rule_set_id"), None) if data.get("rule_set_id") not in (None, "") else None,
            _to_int(data.get("rule_evaluation_id"), None) if data.get("rule_evaluation_id") not in (None, "") else None,
            _json_text(evidence),
            _to_int(data.get("metrics_snapshot_id"), None) if data.get("metrics_snapshot_id") not in (None, "") else None,
            _json_text(data.get("source_snapshot_ids_json")) if not isinstance(data.get("source_snapshot_ids_json"), str) else data.get("source_snapshot_ids_json"),
            _to_int(data.get("supersedes_decision_id"), None) if data.get("supersedes_decision_id") not in (None, "") else None,
            data.get("correlation_id"),
        ),
    )
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    if data.get("correlation_id"):
        row = conn.execute(
            "SELECT id FROM decisions_append_only WHERE correlation_id = ?",
            (data.get("correlation_id"),),
        ).fetchone()
        if row:
            return int(row["id"])
    return 0


def create_decision_append_only(data: dict) -> dict:
    with _connect() as conn:
        migrate_governance_schema(conn)
        decision_id = _insert_decision_append_only(conn, data)
        row = conn.execute("SELECT * FROM decisions_append_only WHERE id = ?", (decision_id,)).fetchone()
        return dict(row) if row else {}


def get_decisions_append_only(entity_type: str | None = None, entity_id: str | None = None, limit: int = 500) -> list[dict]:
    query = "SELECT * FROM decisions_append_only"
    clauses = []
    params = []
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(str(entity_id))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit or 500))
    with _connect() as conn:
        migrate_governance_schema(conn)
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def create_strategy_source(data: dict) -> dict:
    with _connect() as conn:
        migrate_governance_schema(conn)
        now = _utc_now()
        cursor = conn.execute(
            """
            INSERT INTO strategy_sources (
              strategy_id, strategy_version_id, source_type, source_ref,
              is_official, valid_from, valid_to, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("strategy_id"),
                _to_int(data.get("strategy_version_id"), None) if data.get("strategy_version_id") not in (None, "") else None,
                data.get("source_type") or "manual",
                data.get("source_ref") or "manual",
                1 if data.get("is_official") else 0,
                data.get("valid_from") or now,
                data.get("valid_to"),
                data.get("created_at") or now,
            ),
        )
        source_id = int(cursor.lastrowid)
        _insert_decision_append_only(
            conn,
            {
                "created_at": now,
                "created_by": data.get("created_by") or "system",
                "actor_type": data.get("actor_type") or "system_rule",
                "entity_type": "strategy_source",
                "entity_id": str(source_id),
                "decision_type": "source_registered",
                "new_state": "official" if data.get("is_official") else "unofficial",
                "reason": data.get("reason") or "Strategy source registered.",
                "evidence_json": {"strategy_source": data, "source_id": source_id},
                "source_snapshot_ids_json": json.dumps([source_id], ensure_ascii=False),
                "correlation_id": data.get("correlation_id"),
            },
        )
        return dict(conn.execute("SELECT * FROM strategy_sources WHERE id = ?", (source_id,)).fetchone())


def get_official_strategy_source(strategy_id: str, strategy_version_id: int | None = None) -> dict | None:
    query = """
        SELECT * FROM strategy_sources
         WHERE strategy_id = ?
           AND is_official = 1
           AND valid_to IS NULL
    """
    params = [strategy_id]
    if strategy_version_id is None:
        query += " AND strategy_version_id IS NULL"
    else:
        query += " AND strategy_version_id = ?"
        params.append(int(strategy_version_id))
    query += " ORDER BY valid_from DESC, id DESC LIMIT 1"
    with _connect() as conn:
        migrate_governance_schema(conn)
        row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def get_active_strategy_deployments(
    strategy_id: str | None = None,
    instance_id: str | None = None,
    role: str | None = None,
    include_ignored: bool = True,
) -> list[dict]:
    query = """
        SELECT sd.*, si.magic, si.symbol, si.timeframe, a.account_type, a.platform
          FROM strategy_deployments sd
          LEFT JOIN strategy_instances si ON si.instance_id = sd.instance_id
          LEFT JOIN accounts a ON a.id = sd.account_key
         WHERE sd.valid_to IS NULL
    """
    params = []
    if strategy_id:
        query += " AND sd.strategy_id = ?"
        params.append(strategy_id)
    if instance_id:
        query += " AND sd.instance_id = ?"
        params.append(instance_id)
    if role:
        query += " AND sd.role = ?"
        params.append(role)
    if not include_ignored:
        query += " AND sd.role <> 'ignored'"
    query += " ORDER BY sd.strategy_id, sd.instance_id, sd.valid_from"
    with _connect() as conn:
        migrate_governance_schema(conn)
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _latest_metric_snapshot(conn: sqlite3.Connection, strategy_id: str | None = None, instance_id: str | None = None) -> dict | None:
    query = "SELECT * FROM strategy_metric_snapshots WHERE 1 = 1"
    params = []
    if instance_id:
        query += " AND instance_id = ?"
        params.append(instance_id)
    elif strategy_id:
        query += " AND strategy_id = ?"
        params.append(strategy_id)
    query += " ORDER BY calculated_at DESC, id DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def _active_official_source_ids(conn: sqlite3.Connection, strategy_id: str, strategy_version_id: int | None = None) -> list[int]:
    query = """
        SELECT id FROM strategy_sources
         WHERE strategy_id = ?
           AND is_official = 1
           AND valid_to IS NULL
    """
    params = [strategy_id]
    if strategy_version_id is None:
        query += " AND strategy_version_id IS NULL"
    else:
        query += " AND strategy_version_id = ?"
        params.append(int(strategy_version_id))
    return [int(row["id"]) for row in conn.execute(query, params).fetchall()]


def change_strategy_deployment_state(
    deployment_id: int,
    new_state: str,
    *,
    role: str | None = None,
    capital_mode: str | None = None,
    created_by: str = "system",
    actor_type: str = "system_rule",
    reason: str = "Governance state transition",
    evidence: dict | None = None,
) -> dict:
    if new_state not in STATE_VALUES:
        raise ValueError(f"invalid state: {new_state}")
    if role is not None and role not in ROLE_VALUES:
        raise ValueError(f"invalid role: {role}")
    if capital_mode is not None and capital_mode not in CAPITAL_MODE_VALUES:
        raise ValueError(f"invalid capital_mode: {capital_mode}")
    with _connect() as conn:
        migrate_governance_schema(conn)
        current = conn.execute("SELECT * FROM strategy_deployments WHERE id = ? AND valid_to IS NULL", (int(deployment_id),)).fetchone()
        if not current:
            raise ValueError("active deployment not found")
        current = dict(current)
        latest_snapshot = _latest_metric_snapshot(conn, strategy_id=current["strategy_id"], instance_id=current["instance_id"])
        official_source_ids = _active_official_source_ids(conn, current["strategy_id"], current.get("strategy_version_id"))
        if new_state == "production":
            if not official_source_ids:
                raise ValueError("production requires an active official source")
            if not latest_snapshot:
                raise ValueError("production requires a recent metric snapshot")
        now = _utc_now()
        conn.execute("UPDATE strategy_deployments SET valid_to = ? WHERE id = ?", (now, int(deployment_id)))
        cursor = conn.execute(
            """
            INSERT INTO strategy_deployments (
              strategy_id, strategy_version_id, instance_id, account_key,
              role, capital_mode, state, risk_profile_id,
              valid_from, valid_to, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                current["strategy_id"],
                current["strategy_version_id"],
                current["instance_id"],
                current["account_key"],
                role or current["role"],
                capital_mode or current["capital_mode"],
                new_state,
                current["risk_profile_id"],
                now,
                created_by,
                now,
            ),
        )
        new_id = int(cursor.lastrowid)
        decision_id = _insert_decision_append_only(
            conn,
            {
                "created_at": now,
                "created_by": created_by,
                "actor_type": actor_type,
                "entity_type": "strategy_deployment",
                "entity_id": str(new_id),
                "decision_type": "state_changed",
                "previous_state": current["state"],
                "new_state": new_state,
                "reason": reason,
                "evidence_json": {
                    **(evidence or {}),
                    "closed_deployment_id": current["id"],
                    "new_deployment_id": new_id,
                    "official_source_ids": official_source_ids,
                    "latest_snapshot_id": latest_snapshot.get("id") if latest_snapshot else None,
                },
                "metrics_snapshot_id": latest_snapshot.get("id") if latest_snapshot else None,
                "source_snapshot_ids_json": json.dumps(official_source_ids, ensure_ascii=False),
                "correlation_id": f"deployment_transition:{current['id']}:{new_id}",
            },
        )
        row = dict(conn.execute("SELECT * FROM strategy_deployments WHERE id = ?", (new_id,)).fetchone())
        row["decision_id"] = decision_id
        return row


def list_current_governance_state(include_ignored: bool = True) -> list[dict]:
    with _connect() as conn:
        migrate_governance_schema(conn)
        query = """
            SELECT sd.id, sd.strategy_id, sdef.name AS strategy_name, sd.strategy_version_id,
                   sd.instance_id, sd.account_key, sd.role, sd.capital_mode,
                   sd.state, sd.risk_profile_id, sd.valid_from, sd.valid_to,
                   si.magic, si.symbol, si.timeframe,
                   CASE WHEN sd.role <> 'ignored' THEN 1 ELSE 0 END AS included_in_official_performance,
                   (
                     SELECT ss.id FROM strategy_sources ss
                      WHERE ss.strategy_id = sd.strategy_id
                        AND COALESCE(ss.strategy_version_id, -1) = COALESCE(sd.strategy_version_id, -1)
                        AND ss.is_official = 1
                        AND ss.valid_to IS NULL
                      ORDER BY ss.valid_from DESC, ss.id DESC
                      LIMIT 1
                   ) AS official_source_id
              FROM strategy_deployments sd
              JOIN strategy_definitions sdef ON sdef.strategy_id = sd.strategy_id
              LEFT JOIN strategy_instances si ON si.instance_id = sd.instance_id
             WHERE sd.valid_to IS NULL
        """
        if not include_ignored:
            query += " AND sd.role <> 'ignored'"
        query += " ORDER BY sdef.name, sd.instance_id"
        rows = conn.execute(query).fetchall()
    return [dict(row) for row in rows]


GOVERNANCE_VALIDATION_QUERIES = {
    "systems_without_deployment": """
        SELECT sd.strategy_id, sd.name
          FROM strategy_definitions sd
         WHERE NOT EXISTS (
               SELECT 1 FROM strategy_deployments dep
                WHERE dep.strategy_id = sd.strategy_id
                  AND dep.valid_to IS NULL
         )
    """,
    "instances_without_strategy": """
        SELECT si.instance_id, si.strategy_id
          FROM strategy_instances si
          LEFT JOIN strategy_definitions sd ON sd.strategy_id = si.strategy_id
         WHERE sd.strategy_id IS NULL
    """,
    "systems_with_multiple_active_official_sources": """
        SELECT strategy_id, COALESCE(strategy_version_id, -1) AS strategy_version_key, COUNT(*) AS active_official_sources
          FROM strategy_sources
         WHERE is_official = 1 AND valid_to IS NULL
         GROUP BY strategy_id, COALESCE(strategy_version_id, -1)
        HAVING COUNT(*) > 1
    """,
    "duplicate_active_deployments": """
        SELECT instance_id, COUNT(*) AS active_deployments
          FROM strategy_deployments
         WHERE valid_to IS NULL
         GROUP BY instance_id
        HAVING COUNT(*) > 1
    """,
    "active_instances_without_role": """
        SELECT dep.id, dep.instance_id, dep.strategy_id
          FROM strategy_deployments dep
         WHERE dep.valid_to IS NULL
           AND (dep.role IS NULL OR dep.role = '')
    """,
    "production_without_official_source": """
        SELECT dep.id, dep.strategy_id, dep.instance_id
          FROM strategy_deployments dep
         WHERE dep.valid_to IS NULL
           AND dep.state = 'production'
           AND NOT EXISTS (
               SELECT 1 FROM strategy_sources ss
                WHERE ss.strategy_id = dep.strategy_id
                  AND COALESCE(ss.strategy_version_id, -1) = COALESCE(dep.strategy_version_id, -1)
                  AND ss.is_official = 1
                  AND ss.valid_to IS NULL
           )
    """,
    "production_without_recent_snapshot": """
        SELECT dep.id, dep.strategy_id, dep.instance_id
          FROM strategy_deployments dep
         WHERE dep.valid_to IS NULL
           AND dep.state = 'production'
           AND NOT EXISTS (
               SELECT 1 FROM strategy_metric_snapshots sms
                WHERE sms.instance_id = dep.instance_id
                  AND sms.calculated_at >= datetime('now', '-24 hours')
           )
    """,
    "decisions_without_evidence": """
        SELECT id, entity_type, entity_id, decision_type
          FROM decisions_append_only
         WHERE evidence_json IS NULL
            OR evidence_json = ''
            OR evidence_json = '{}'
    """,
    "expired_sources_still_active": """
        SELECT id, strategy_id, strategy_version_id, valid_from, valid_to
          FROM strategy_sources
         WHERE valid_to IS NOT NULL
           AND valid_to <= datetime('now')
           AND is_official = 1
    """,
}


def get_governance_validation_queries() -> dict[str, str]:
    return dict(GOVERNANCE_VALIDATION_QUERIES)


def run_governance_validation_queries() -> dict[str, list[dict]]:
    with _connect() as conn:
        migrate_governance_schema(conn)
        return {
            name: [dict(row) for row in conn.execute(sql).fetchall()]
            for name, sql in GOVERNANCE_VALIDATION_QUERIES.items()
        }


def evaluate_basic_governance_rules(entity_type: str = "strategy_instance", entity_id: str | None = None) -> list[dict]:
    with _connect() as conn:
        migrate_governance_schema(conn)
        rule_set_id = _seed_governance_rule_set(conn)
        rule_set = conn.execute("SELECT * FROM governance_rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
        params = json.loads(rule_set["params_json"])
        deployments_query = "SELECT * FROM strategy_deployments WHERE valid_to IS NULL"
        deployment_params = []
        if entity_type == "strategy_instance" and entity_id:
            deployments_query += " AND instance_id = ?"
            deployment_params.append(entity_id)
        deployments = [dict(row) for row in conn.execute(deployments_query, deployment_params).fetchall()]
        created = []
        now = _utc_now()
        for dep in deployments:
            snapshot = _latest_metric_snapshot(conn, strategy_id=dep["strategy_id"], instance_id=dep["instance_id"])
            issues = []
            severity = "info"
            recommended = None
            official_source_ids = _active_official_source_ids(conn, dep["strategy_id"], dep.get("strategy_version_id"))
            if len(official_source_ids) > 1:
                issues.append("multiple_active_official_sources")
                severity = "critical"
                recommended = "revision_manual"
            if not snapshot:
                issues.append("missing_metric_snapshot")
                severity = "warning" if severity != "critical" else severity
                if dep["state"] == "production":
                    recommended = "revision_manual"
            else:
                trades = int(snapshot.get("trades") or 0)
                pf = float(snapshot.get("profit_factor") or 0.0)
                if trades < int(params["min_closed_trades_for_production"]) and dep["state"] == "production":
                    issues.append("insufficient_trades_for_production")
                    severity = "warning" if severity != "critical" else severity
                    recommended = "revision_manual"
                if pf < float(params["rolling_pf_90d_reduced_risk_below"]) and trades >= int(params["min_closed_trades_for_production"]):
                    issues.append("profit_factor_below_reduced_risk_threshold")
                    severity = "warning" if severity != "critical" else severity
                    recommended = "reduced_risk"
            if dep["state"] == "production" and not official_source_ids:
                issues.append("production_without_official_source")
                severity = "critical"
                recommended = "revision_manual"
            if dep["role"] == "ignored":
                recommended = None
                severity = "info"
            result = {
                "deployment_id": dep["id"],
                "strategy_id": dep["strategy_id"],
                "instance_id": dep["instance_id"],
                "state": dep["state"],
                "role": dep["role"],
                "issues": issues,
                "official_source_ids": official_source_ids,
                "snapshot_id": snapshot.get("id") if snapshot else None,
            }
            cursor = conn.execute(
                """
                INSERT INTO governance_evaluations (
                  rule_set_id, entity_type, entity_id, snapshot_id,
                  evaluated_at, result_json, recommended_transition, severity
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_set_id,
                    "strategy_instance",
                    dep["instance_id"],
                    snapshot.get("id") if snapshot else None,
                    now,
                    json.dumps(_json_safe(result), ensure_ascii=False, sort_keys=True),
                    recommended,
                    severity,
                ),
            )
            evaluation_id = int(cursor.lastrowid)
            result["evaluation_id"] = evaluation_id
            result["recommended_transition"] = recommended
            result["severity"] = severity
            if issues:
                _insert_decision_append_only(
                    conn,
                    {
                        "created_at": now,
                        "created_by": "governance_rules",
                        "actor_type": "system_rule",
                        "entity_type": "strategy_deployment",
                        "entity_id": str(dep["id"]),
                        "decision_type": "state_recommended",
                        "previous_state": dep["state"],
                        "new_state": recommended,
                        "reason": "Conservative governance rules produced a recommendation only.",
                        "rule_set_id": rule_set_id,
                        "rule_evaluation_id": evaluation_id,
                        "evidence_json": result,
                        "metrics_snapshot_id": snapshot.get("id") if snapshot else None,
                        "source_snapshot_ids_json": json.dumps(official_source_ids, ensure_ascii=False),
                        "correlation_id": f"governance_eval:{evaluation_id}",
                    },
                )
            created.append(result)
        return created


def log_command(command: dict) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO command_log (
              system, magic, action, account_id, platform, sent_at, result, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                command.get("system"),
                command.get("magic"),
                command.get("action"),
                command.get("account_id"),
                command.get("platform"),
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def create_command(command: dict) -> int:
    params = command.get("parameters", {})
    parameters = params if isinstance(params, str) else json.dumps(params, ensure_ascii=False)
    with _connect() as conn:
        account_key = command.get("account_composite_key") or command.get("account_id")
        cursor = conn.execute(
            """
            INSERT INTO commands (
              account_composite_key, account_id, account_label, platform,
              system, magic, action, parameters, status, created_at,
              sent_to_ea_at, acknowledged_at, executed_at, response,
              error_message, processed_flag
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, NULL, NULL, NULL, 0)
            """,
            (
                account_key,
                command.get("account_id"),
                command.get("account_label"),
                command.get("platform"),
                command.get("system"),
                command.get("magic"),
                command.get("action"),
                parameters,
                _utc_now(),
            ),
        )
        command_id = int(cursor.lastrowid)
        _insert_audit_event(
            conn,
            event_type="command_created",
            entity_type="command",
            entity_id=command_id,
            account_key=account_key,
            severity="info",
            message=f"Command created: {command.get('action')}",
            payload={**command, "command_id": command_id, "parameters": parameters},
        )
        return command_id


def get_commands(limit: int = 100, active_only: bool = False) -> list[dict]:
    where = ""
    if active_only:
        where = "WHERE status IN ('pending', 'sent', 'ack') AND processed_flag = 0"
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, account_composite_key, account_id, account_label, platform,
                   system, magic, action, parameters, status, created_at,
                   sent_to_ea_at, acknowledged_at, executed_at, response,
                   error_message, processed_flag
              FROM commands
              {where}
             ORDER BY id DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [_decode_command_row(row) for row in rows]


def mark_commands_sent(command_ids: list[int]) -> None:
    if not command_ids:
        return
    sent_at = _utc_now()
    placeholders = ",".join("?" for _ in command_ids)
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE commands
               SET status = CASE WHEN status = 'pending' THEN 'sent' ELSE status END,
                   sent_to_ea_at = COALESCE(sent_to_ea_at, ?)
             WHERE id IN ({placeholders})
               AND processed_flag = 0
            """,
            [sent_at, *[int(cid) for cid in command_ids]],
        )


def acknowledge_command(command_id: int, response: dict | str | None = None) -> bool:
    response_text = encode_result(response or {"ack": True})
    with _connect() as conn:
        row = conn.execute("SELECT status, account_composite_key FROM commands WHERE id = ?", (int(command_id),)).fetchone()
        if not row:
            return False
        before = row["status"]
        conn.execute(
            """
            UPDATE commands
               SET status = CASE WHEN status IN ('pending', 'sent') THEN 'ack' ELSE status END,
                   acknowledged_at = COALESCE(acknowledged_at, ?),
                   response = COALESCE(response, ?)
             WHERE id = ?
            """,
            (_utc_now(), response_text, int(command_id)),
        )
        _insert_command_execution(conn, command_id, None, before, "ack", response_text)
        _insert_audit_event(
            conn,
            event_type="command_acknowledged",
            entity_type="command",
            entity_id=command_id,
            account_key=row["account_composite_key"],
            severity="info",
            message="Command acknowledged by EA",
            payload={"status_before": before, "status_after": "ack", "response": response or {"ack": True}},
        )
        return True


def complete_command(command_id: int, status: str, response=None, error_message: str | None = None) -> bool:
    final_status = status if status in ("executed", "failed", "expired") else "failed"
    response_text = encode_result(response or {})
    with _connect() as conn:
        row = conn.execute(
            "SELECT status, account_id, account_composite_key FROM commands WHERE id = ?",
            (int(command_id),),
        ).fetchone()
        if not row:
            return False
        before = row["status"]
        conn.execute(
            """
            UPDATE commands
               SET status = ?,
                   executed_at = COALESCE(executed_at, ?),
                   response = ?,
                   error_message = ?,
                   processed_flag = 1
             WHERE id = ?
            """,
            (final_status, _utc_now(), response_text, error_message, int(command_id)),
        )
        _insert_command_execution(conn, command_id, row["account_id"], before, final_status, response_text)
        _insert_audit_event(
            conn,
            event_type="command_executed" if final_status == "executed" else "command_failed",
            entity_type="command",
            entity_id=command_id,
            account_key=row["account_composite_key"],
            severity="info" if final_status == "executed" else "error",
            message=f"Command completed with status {final_status}",
            payload={
                "status_before": before,
                "status_after": final_status,
                "response": response or {},
                "error_message": error_message,
            },
        )
        return True


def clear_active_commands() -> int:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, account_composite_key, status
              FROM commands
             WHERE status IN ('pending', 'sent', 'ack')
               AND processed_flag = 0
            """
        ).fetchall()
        cursor = conn.execute(
            """
            UPDATE commands
               SET status = 'expired',
                   executed_at = COALESCE(executed_at, ?),
                   processed_flag = 1,
                   error_message = COALESCE(error_message, 'cleared by dashboard')
             WHERE status IN ('pending', 'sent', 'ack')
               AND processed_flag = 0
            """,
            (_utc_now(),),
        )
        for row in rows:
            _insert_audit_event(
                conn,
                event_type="command_failed",
                entity_type="command",
                entity_id=row["id"],
                account_key=row["account_composite_key"],
                severity="warning",
                message="Command expired by dashboard clear action",
                payload={"status_before": row["status"], "status_after": "expired"},
            )
        return int(cursor.rowcount)


def sync_accounts_from_config(accounts: list[dict]) -> int:
    now = _utc_now()
    changed = 0
    with _connect() as conn:
        for account in accounts or []:
            login = str(account.get("id") or account.get("login") or "")
            if not login:
                continue
            platform = str(account.get("platform", "MT4")).upper()
            server = str(account.get("server", "unknown") or "unknown")
            account_type = str(account.get("type") or account.get("account_type") or "real").lower()
            alias = str(account.get("label") or account.get("alias") or login)
            currency = str(account.get("currency", ""))
            leverage = str(account.get("leverage", ""))
            broker_id = _broker_id(server)
            account_key = account.get("account_key") or get_account_key(platform, server, login, account_type)
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO brokers (
                  id, name, server, timezone, commission_model, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (broker_id, server, server, "broker_local", "unknown", now),
            )
            conn.execute(
                """
                INSERT INTO accounts (
                  id, broker_id, login, platform, server, account_type,
                  alias, currency, leverage, created_at, is_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                  broker_id=excluded.broker_id,
                  login=excluded.login,
                  platform=excluded.platform,
                  server=excluded.server,
                  account_type=excluded.account_type,
                  alias=excluded.alias,
                  currency=excluded.currency,
                  leverage=excluded.leverage,
                  is_active=1
                """
                ,
                (
                    account_key,
                    broker_id,
                    login,
                    platform,
                    server,
                    account_type,
                    alias,
                    currency,
                    leverage,
                    now,
                ),
            )
            changed += conn.total_changes - before
    return changed


def upsert_account(account: dict) -> dict:
    sync_accounts_from_config([account])
    login = str(account.get("id") or account.get("login") or "")
    platform = str(account.get("platform", "MT4")).upper()
    server = str(account.get("server", "unknown") or "unknown")
    account_type = str(account.get("type") or account.get("account_type") or "real").lower()
    account_key = account.get("account_key") or get_account_key(platform, server, login, account_type)
    with _connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_key,)).fetchone()
    return dict(row) if row else {}


def get_accounts(active_only: bool = True) -> list[dict]:
    query = """
        SELECT a.id, a.broker_id, a.login, a.platform, a.server, a.account_type,
               a.alias, a.currency, a.leverage, a.created_at, a.is_active,
               b.name AS broker_name
          FROM accounts a
          LEFT JOIN brokers b ON b.id = a.broker_id
    """
    params = []
    if active_only:
        query += " WHERE a.is_active = ?"
        params.append(1)
    query += " ORDER BY a.platform, a.server, a.login, a.account_type"
    with _connect() as conn:
        migrate_phase1_schema(conn)
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def resolve_account_key(identifier: str, platform: str | None = None, server: str | None = None, account_type: str | None = None, alias: str | None = None, currency: str | None = None) -> str:
    raw = str(identifier or "").strip()
    if not raw:
        raise ValueError("identifier requerido")
    with _connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE id = ?", (raw,)).fetchone()
        if row:
            return row["id"]

        clauses = ["login = ?"]
        params = [raw]
        if platform:
            clauses.append("platform = ?")
            params.append(str(platform).upper())
        if server:
            clauses.append("server = ?")
            params.append(str(server))
        if account_type:
            clauses.append("account_type = ?")
            params.append(str(account_type).lower())
        row = conn.execute(
            f"SELECT id FROM accounts WHERE {' AND '.join(clauses)} ORDER BY is_active DESC, created_at DESC LIMIT 1",
            params,
        ).fetchone()
        if row:
            return row["id"]

    account = {
        "login": raw,
        "platform": platform or "MT4",
        "server": server or "unknown",
        "account_type": account_type or "real",
        "alias": alias or raw,
        "currency": currency or "",
    }
    return upsert_account(account).get("id")


def upsert_open_positions(account_key: str, positions: list[dict], source_file: str | None = None) -> int:
    now = _utc_now()
    seen = set()
    changed = 0
    with _connect() as conn:
        for pos in positions:
            ticket = str(pos.get("ticket", "")).strip()
            if not ticket:
                continue
            seen.add(ticket)
            magic = pos.get("magic", pos.get("magic_number"))
            strategy_id = None
            if magic not in (None, "", 0, "0"):
                strategy_id = _strategy_id(account_key, magic)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO strategies (
                      id, account_id, magic_number, name, description, symbols, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_id,
                        account_key,
                        int(float(magic)),
                        f"magic:{magic}",
                        "Discovered from open position",
                        json.dumps([pos.get("symbol")], ensure_ascii=False) if pos.get("symbol") else "[]",
                        now,
                    ),
                )
            before = conn.total_changes
            conn.execute(
                """
                INSERT INTO open_positions (
                  account_id, ticket, symbol, action, volume, open_price,
                  sl, tp, open_time, swap, unrealized_profit, last_updated_at,
                  strategy_id, magic_number
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, ticket) DO UPDATE SET
                  symbol=excluded.symbol,
                  action=excluded.action,
                  volume=excluded.volume,
                  open_price=excluded.open_price,
                  sl=excluded.sl,
                  tp=excluded.tp,
                  open_time=excluded.open_time,
                  swap=excluded.swap,
                  unrealized_profit=excluded.unrealized_profit,
                  last_updated_at=excluded.last_updated_at,
                  strategy_id=excluded.strategy_id,
                  magic_number=excluded.magic_number
                """,
                (
                    account_key,
                    ticket,
                    str(pos.get("symbol", "")),
                    str(pos.get("type", pos.get("action", ""))).lower(),
                    _to_float(pos.get("lots", pos.get("volume"))),
                    _to_float(pos.get("open_price")),
                    _to_float(pos.get("sl")),
                    _to_float(pos.get("tp")),
                    _as_iso_text(pos.get("open_time")),
                    _to_float(pos.get("swap")),
                    _to_float(pos.get("profit_float", pos.get("unrealized_profit"))),
                    now,
                    strategy_id,
                    int(float(magic)) if magic not in (None, "", 0, "0") else None,
                ),
            )
            changed += conn.total_changes - before
        if seen:
            placeholders = ",".join("?" for _ in seen)
            conn.execute(
                f"DELETE FROM open_positions WHERE account_id = ? AND ticket NOT IN ({placeholders})",
                [account_key, *seen],
            )
        else:
            conn.execute("DELETE FROM open_positions WHERE account_id = ?", (account_key,))
    return changed


def save_account_snapshot(account_key: str, snapshot: dict) -> bool:
    timestamp = _as_iso_text(snapshot.get("timestamp")) or _utc_now()
    balance = _to_float(snapshot.get("balance"))
    equity = _to_float(snapshot.get("equity"))
    margin = _to_float(snapshot.get("margin"))
    free_margin = _to_float(snapshot.get("free_margin", snapshot.get("margin_free")))
    margin_level = _to_float(snapshot.get("margin_level_pct", snapshot.get("margin_level")))
    drawdown = None
    if balance and equity is not None:
        drawdown = max(0.0, (balance - equity) / abs(balance) * 100.0)
    unrealized = None if balance is None or equity is None else equity - balance
    with _connect() as conn:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO account_snapshots (
              account_id, timestamp, balance, equity, margin, free_margin, margin_level, drawdown_percent
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_key, timestamp, balance, equity, margin, free_margin, margin_level, drawdown),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO equity_points (
              account_id, timestamp, equity, balance, unrealized_pnl
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (account_key, timestamp, equity, balance, unrealized),
        )
        return conn.total_changes > before


def upsert_deposit_withdrawal(account_key: str, movement: dict) -> bool:
    timestamp = _as_iso_text(movement.get("timestamp")) or _utc_now()
    amount = _to_float(movement.get("amount")) or 0.0
    kind = str(movement.get("type") or ("deposit" if amount >= 0 else "withdrawal")).lower()
    description = str(movement.get("description", ""))
    with _connect() as conn:
        before = conn.total_changes
        conn.execute(
            """
            INSERT INTO deposits_withdrawals (
              account_id, timestamp, amount, type, description
            )
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
              SELECT 1 FROM deposits_withdrawals
               WHERE account_id = ? AND timestamp = ? AND amount = ? AND description = ?
            )
            """,
            (account_key, timestamp, amount, kind, description, account_key, timestamp, amount, description),
        )
        return conn.total_changes > before


def log_import_run(
    account_key: str | None,
    source_file: str,
    rows_processed: int,
    rows_inserted: int,
    status: str,
    error_log=None,
    hash_file: str | None = None,
    started_at: str | None = None,
) -> int:
    with _connect() as conn:
        migrate_phase1_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO import_runs (
              account_id, source_file, rows_processed, rows_inserted,
              hash_file, status, error_log, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_key,
                source_file,
                int(rows_processed or 0),
                int(rows_inserted or 0),
                hash_file,
                status,
                encode_result(error_log or []),
                started_at or _utc_now(),
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def start_import_run(account_key: str | None, source_file: str, hash_file: str | None = None) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO import_runs (
              account_id, source_file, rows_processed, rows_inserted,
              hash_file, status, error_log, started_at, finished_at
            )
            VALUES (?, ?, 0, 0, ?, 'running', '[]', ?, NULL)
            """,
            (account_key, source_file, hash_file, _utc_now()),
        )
        import_run_id = int(cursor.lastrowid)
        _insert_audit_event(
            conn,
            event_type="import_started",
            entity_type="import_run",
            entity_id=import_run_id,
            account_key=account_key,
            severity="info",
            message=f"Import started: {source_file}",
            payload={"source_file": source_file, "hash_file": hash_file},
        )
        return import_run_id


def finish_import_run(import_run_id: int, rows_processed: int, rows_inserted: int, status: str, error_log=None) -> None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT account_id, source_file FROM import_runs WHERE id = ?",
            (int(import_run_id),),
        ).fetchone()
        conn.execute(
            """
            UPDATE import_runs
               SET rows_processed = ?,
                   rows_inserted = ?,
                   status = ?,
                   error_log = ?,
                   finished_at = ?
             WHERE id = ?
            """,
            (
                int(rows_processed or 0),
                int(rows_inserted or 0),
                status,
                encode_result(error_log or []),
                _utc_now(),
                int(import_run_id),
            ),
        )
        event_type = "import_completed" if status in ("ok", "completed", "success") else "import_error"
        _insert_audit_event(
            conn,
            event_type=event_type,
            entity_type="import_run",
            entity_id=import_run_id,
            account_key=row["account_id"] if row else None,
            severity="info" if event_type == "import_completed" else "warning",
            message=f"Import finished with status {status}",
            payload={
                "source_file": row["source_file"] if row else None,
                "rows_processed": rows_processed,
                "rows_inserted": rows_inserted,
                "status": status,
                "error_log": error_log or [],
            },
        )


def record_raw_trade_import(
    import_run_id: int | None,
    source_file: str,
    source_platform: str,
    account_key: str | None,
    account_id: str | None,
    row_number: int,
    raw_payload: dict,
    normalized_status: str,
    error_message: str | None = None,
) -> int:
    raw_json = json.dumps(_json_safe(raw_payload), ensure_ascii=False, sort_keys=True)
    row_hash = raw_payload_hash(raw_payload)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM raw_trade_imports
             WHERE source_file = ? AND row_number = ? AND row_hash = ?
             ORDER BY id ASC LIMIT 1
            """,
            (source_file, int(row_number), row_hash),
        ).fetchone()
        if row:
            return int(row["id"])
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO raw_trade_imports (
              import_run_id, source_file, source_platform, account_key,
              account_id, row_number, row_hash, raw_payload_json,
              normalized_status, error_message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_run_id,
                source_file,
                source_platform,
                account_key,
                account_id,
                int(row_number),
                row_hash,
                raw_json,
                normalized_status,
                error_message,
                _utc_now(),
            ),
        )
        if cursor.lastrowid:
            raw_id = int(cursor.lastrowid)
            if normalized_status == "error":
                _insert_audit_event(
                    conn,
                    event_type="data_quality_warning",
                    entity_type="raw_trade_import",
                    entity_id=raw_id,
                    account_key=account_key,
                    severity="warning",
                    message=f"Raw trade row normalization failed: {error_message}",
                    payload={
                        "import_run_id": import_run_id,
                        "source_file": source_file,
                        "source_platform": source_platform,
                        "row_number": row_number,
                        "row_hash": row_hash,
                        "error_message": error_message,
                    },
                )
            return raw_id
        row = conn.execute(
            """
            SELECT id FROM raw_trade_imports
             WHERE import_run_id IS ? AND row_number = ? AND row_hash = ?
             ORDER BY id DESC LIMIT 1
            """,
            (import_run_id, int(row_number), row_hash),
        ).fetchone()
        return int(row["id"]) if row else 0


def get_raw_trade_imports(import_run_id: int | None = None, limit: int = 500) -> list[dict]:
    query = "SELECT * FROM raw_trade_imports"
    params = []
    if import_run_id is not None:
        query += " WHERE import_run_id = ?"
        params.append(int(import_run_id))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_trade_import_conflicts(limit: int = 500) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM trade_import_conflicts
             ORDER BY id DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_trade_file_state(source_file: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM trade_file_state WHERE source_file = ?",
            (str(source_file),),
        ).fetchone()
    return dict(row) if row else None


def upsert_trade_file_state(
    source_file: str,
    account_key: str | None = None,
    account_id: str | None = None,
    platform: str | None = None,
    last_modified_at: str | None = None,
    file_size: int | None = None,
    last_row_count: int | None = None,
    last_imported_hash: str | None = None,
    last_successful_import_at: str | None = None,
    last_error: str | None = None,
) -> None:
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO trade_file_state (
              source_file, account_key, account_id, platform, last_modified_at,
              file_size, last_row_count, last_imported_hash,
              last_successful_import_at, last_error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
              account_key=excluded.account_key,
              account_id=excluded.account_id,
              platform=excluded.platform,
              last_modified_at=excluded.last_modified_at,
              file_size=excluded.file_size,
              last_row_count=excluded.last_row_count,
              last_imported_hash=excluded.last_imported_hash,
              last_successful_import_at=excluded.last_successful_import_at,
              last_error=excluded.last_error,
              updated_at=excluded.updated_at
            """,
            (
                str(source_file),
                account_key,
                account_id,
                platform,
                last_modified_at,
                file_size,
                last_row_count,
                last_imported_hash,
                last_successful_import_at,
                last_error,
                now,
                now,
            ),
        )


def get_trade_file_states(limit: int = 1000) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM trade_file_state
             ORDER BY updated_at DESC, source_file
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_discovered_account(account: dict) -> dict:
    now = _utc_now()
    account_id = str(account.get("account_id") or account.get("login") or "").strip()
    if not account_id:
        raise ValueError("account_id requerido")
    platform = str(account.get("platform") or "MT4").upper()
    account_type = str(account.get("account_type") or account.get("type") or "real").lower()
    server = str(account.get("server") or "unknown")
    account_key = account.get("account_key") or get_account_key(platform, server, account_id, account_type)
    payload = account.get("payload") or {}
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO discovered_accounts (
              account_id, account_key, platform, account_type, label, server,
              broker_name, source_file, first_seen_at, last_seen_at, status,
              notes, payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?, ?)
            ON CONFLICT(account_id, platform, server, account_type) DO UPDATE SET
              account_key=excluded.account_key,
              label=COALESCE(excluded.label, discovered_accounts.label),
              broker_name=COALESCE(excluded.broker_name, discovered_accounts.broker_name),
              source_file=excluded.source_file,
              last_seen_at=excluded.last_seen_at,
              payload_json=excluded.payload_json,
              updated_at=excluded.updated_at
            """,
            (
                account_id,
                account_key,
                platform,
                account_type,
                account.get("label"),
                server,
                account.get("broker_name") or account.get("broker"),
                account.get("source_file"),
                now,
                now,
                account.get("notes"),
                encode_result(_json_safe(payload)),
                now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM discovered_accounts
             WHERE account_id = ? AND platform = ? AND server = ? AND account_type = ?
             LIMIT 1
            """,
            (account_id, platform, server, account_type),
        ).fetchone()
    return dict(row) if row else {}


def get_discovered_accounts(status: str | None = None, limit: int = 1000) -> list[dict]:
    query = "SELECT * FROM discovered_accounts"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(str(status))
    query += " ORDER BY status, last_seen_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_discovered_account(discovered_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM discovered_accounts WHERE id = ?", (int(discovered_id),)).fetchone()
    return dict(row) if row else None


def find_discovered_account(account_id: str, platform: str, server: str, account_type: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM discovered_accounts
             WHERE account_id = ? AND platform = ? AND server = ? AND account_type = ?
             LIMIT 1
            """,
            (str(account_id), str(platform).upper(), str(server or "unknown"), str(account_type or "real").lower()),
        ).fetchone()
    return dict(row) if row else None


def update_discovered_account_status(discovered_id: int, status: str, notes: str | None = None) -> dict:
    if status not in {"approved", "rejected", "ignored"}:
        raise ValueError("status invalido")
    now = _utc_now()
    timestamp_column = {"approved": "approved_at", "rejected": "rejected_at", "ignored": "ignored_at"}[status]
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE discovered_accounts
               SET status = ?,
                   {timestamp_column} = ?,
                   notes = COALESCE(?, notes),
                   updated_at = ?
             WHERE id = ?
            """,
            (status, now, notes, now, int(discovered_id)),
        )
        row = conn.execute("SELECT * FROM discovered_accounts WHERE id = ?", (int(discovered_id),)).fetchone()
    return dict(row) if row else {}


def count_closed_trades() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM closed_trades").fetchone()
    return int(row["n"] if row else 0)


def get_strategy_definitions(limit: int = 1000) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM strategy_definitions
             ORDER BY name, version, strategy_id
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_strategy_instances(strategy_id: str | None = None, limit: int = 2000) -> list[dict]:
    query = """
        SELECT si.*, sd.name AS strategy_name, sd.version AS strategy_version,
               sv.version_name, sv.parameters_hash,
               rp.name AS risk_profile_name
          FROM strategy_instances si
          JOIN strategy_definitions sd ON sd.strategy_id = si.strategy_id
          LEFT JOIN strategy_versions sv ON sv.id = si.strategy_version_id
          LEFT JOIN risk_profiles rp ON rp.id = si.risk_profile_id
    """
    params = []
    if strategy_id:
        query += " WHERE si.strategy_id = ?"
        params.append(strategy_id)
    query += " ORDER BY si.account_key, si.platform, si.magic, si.symbol LIMIT ?"
    params.append(int(limit))
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_open_positions(account_key: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM open_positions WHERE account_id = ? ORDER BY symbol, ticket",
            (account_key,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_account_snapshots(account_key: str, limit: int = 500) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM account_snapshots
             WHERE account_id = ?
             ORDER BY timestamp DESC
             LIMIT ?
            """,
            (account_key, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def get_equity_points(account_key: str, limit: int = 2000) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM equity_points
             WHERE account_id = ?
             ORDER BY timestamp DESC
             LIMIT ?
            """,
            (account_key, int(limit)),
        ).fetchall()
    return list(reversed([dict(row) for row in rows]))


def find_account_twin_with_data(account_key: str, data_table: str) -> dict | None:
    table_map = {
        "closed_trades": "closed_trades",
        "equity_points": "equity_points",
        "account_snapshots": "account_snapshots",
        "open_positions": "open_positions",
    }
    table = table_map.get(data_table)
    if not table:
        raise ValueError("data_table invalida")
    with _connect() as conn:
        account = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_key,)).fetchone()
        if not account:
            return None
        rows = conn.execute(
            f"""
            SELECT a.*,
                   (SELECT COUNT(*) FROM {table} d WHERE d.account_id = a.id) AS data_rows
              FROM accounts a
             WHERE a.id <> ?
               AND a.login = ?
               AND a.platform = ?
             ORDER BY data_rows DESC,
                      CASE WHEN a.server = 'unknown' THEN 0 ELSE 1 END,
                      a.id
             LIMIT 1
            """,
            (account_key, account["login"], account["platform"]),
        ).fetchall()
    for row in rows:
        if int(row["data_rows"] or 0) > 0:
            return dict(row)
    return None


def get_data_quality_checks(
    stale_positions_hours: int = 24,
    old_commands_minutes: int = 30,
    equity_gap_minutes: int = 180,
) -> list[dict]:
    checks = []

    def add(name: str, count: int, ok_message: str, warn_message: str, critical: bool = False, payload=None) -> None:
        checks.append(
            {
                "name": name,
                "status": "critical" if critical and count else ("warning" if count else "ok"),
                "count": int(count or 0),
                "message": ok_message if not count else warn_message,
                "payload": payload or {},
            }
        )

    with _connect() as conn:
        duplicate_rows = conn.execute(
            """
            SELECT natural_key, COUNT(*) AS c
              FROM closed_trades
             WHERE natural_key IS NOT NULL
             GROUP BY natural_key
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        add(
            "duplicate_trades",
            len(duplicate_rows),
            "No duplicate trades detected",
            "Duplicate closed trades detected by natural_key",
            critical=True,
            payload={"examples": [dict(row) for row in duplicate_rows[:20]]},
        )

        unknown_accounts = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM (
                SELECT ct.account_id
                  FROM closed_trades ct
                  LEFT JOIN accounts a ON a.id = ct.account_id
                 WHERE a.id IS NULL
                UNION ALL
                SELECT op.account_id
                  FROM open_positions op
                  LEFT JOIN accounts a ON a.id = op.account_id
                 WHERE a.id IS NULL
                UNION ALL
                SELECT ri.account_key
                  FROM raw_trade_imports ri
                  LEFT JOIN accounts a ON a.id = ri.account_key
                 WHERE ri.account_key IS NOT NULL AND a.id IS NULL
              )
            """
        ).fetchone()["c"]
        audit_unknown = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_events WHERE event_type = 'unknown_account_detected'"
        ).fetchone()["c"]
        add(
            "unknown_accounts",
            int(unknown_accounts or 0) + int(audit_unknown or 0),
            "No unknown accounts detected",
            "Unknown account references detected",
            critical=True,
        )

        magic_without_strategy = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM closed_trades ct
              LEFT JOIN strategies s ON s.id = ct.strategy_id
             WHERE COALESCE(s.magic_number, 0) > 0
               AND (ct.strategy_id IS NULL OR s.strategy_definition_id IS NULL OR s.strategy_instance_id IS NULL)
            """
        ).fetchone()["c"]
        add(
            "magic_without_strategy",
            magic_without_strategy,
            "All magic numbers are linked to strategy definitions and instances",
            "Magic numbers without complete strategy mapping detected",
        )

        stale_positions = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM open_positions
             WHERE datetime(last_updated_at) < datetime('now', ?)
            """,
            (f"-{int(stale_positions_hours)} hours",),
        ).fetchone()["c"]
        add(
            "stale_open_positions",
            stale_positions,
            "No stale open positions detected",
            f"Open positions older than {stale_positions_hours} hours detected",
        )

        invalid_equity = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM equity_points ep
              LEFT JOIN accounts a ON a.id = ep.account_id
             WHERE a.id IS NULL
            """
        ).fetchone()["c"]
        add(
            "equity_points_without_valid_account",
            invalid_equity,
            "All equity points reference valid accounts",
            "Equity points without valid account detected",
            critical=True,
        )

        gap_rows = conn.execute(
            """
            WITH ordered AS (
              SELECT account_id,
                     timestamp,
                     LAG(timestamp) OVER (PARTITION BY account_id ORDER BY timestamp) AS prev_ts
                FROM equity_points
            )
            SELECT account_id,
                   COUNT(*) AS gaps,
                   MAX((julianday(timestamp) - julianday(prev_ts)) * 24 * 60) AS max_gap_minutes
              FROM ordered
             WHERE prev_ts IS NOT NULL
               AND (julianday(timestamp) - julianday(prev_ts)) * 24 * 60 > ?
             GROUP BY account_id
            """,
            (float(equity_gap_minutes),),
        ).fetchall()
        add(
            "equity_gaps",
            sum(int(row["gaps"] or 0) for row in gap_rows),
            "No equity gaps above threshold detected",
            f"Equity gaps above {equity_gap_minutes} minutes detected",
            payload={"accounts": [dict(row) for row in gap_rows]},
        )

        null_snapshots = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM account_snapshots
             WHERE equity IS NULL OR balance IS NULL
            """
        ).fetchone()["c"]
        add(
            "snapshots_with_null_equity_balance",
            null_snapshots,
            "No snapshots with null equity/balance detected",
            "Snapshots with null equity or balance detected",
        )

        old_commands = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM commands
             WHERE status IN ('pending', 'sent', 'ack')
               AND processed_flag = 0
               AND datetime(created_at) < datetime('now', ?)
            """,
            (f"-{int(old_commands_minutes)} minutes",),
        ).fetchone()["c"]
        add(
            "old_pending_commands",
            old_commands,
            "No old pending commands detected",
            f"Commands pending longer than {old_commands_minutes} minutes detected",
            critical=True,
        )

        failed_imports = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM import_runs
             WHERE COALESCE(status, '') NOT IN ('ok', 'completed', 'success', 'running')
            """
        ).fetchone()["c"]
        add(
            "failed_imports",
            failed_imports,
            "No failed or partial imports detected",
            "Failed or partial imports detected",
        )

        stale_running_imports = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM import_runs
             WHERE COALESCE(status, '') = 'running'
               AND datetime(started_at) < datetime('now', '-30 minutes')
            """
        ).fetchone()["c"]
        add(
            "stale_running_imports",
            stale_running_imports,
            "No stale running imports detected",
            "Running imports older than 30 minutes detected",
            critical=True,
        )

    return checks


def get_temporal_performance_breakdown() -> dict:
    query = """
        SELECT ct.id,
               ct.account_id,
               a.platform,
               a.server,
               a.account_type,
               ct.symbol,
               ct.close_time,
               COALESCE(ct.net_profit, ct.profit, 0) AS net_profit,
               CASE WHEN COALESCE(ct.profit, ct.net_profit, 0) > 0 THEN COALESCE(ct.profit, ct.net_profit, 0) ELSE 0.0 END AS gross_profit,
               CASE WHEN COALESCE(ct.profit, ct.net_profit, 0) < 0 THEN ABS(COALESCE(ct.profit, ct.net_profit, 0)) ELSE 0.0 END AS gross_loss,
               COALESCE(ct.commission, 0) AS commission,
               COALESCE(ct.swap, 0) AS swap,
               COALESCE(ct.fees, 0) AS fees,
               COALESCE(ct.spread_cost_estimated, 0) AS spread_cost_estimated,
               COALESCE(ct.slippage_estimated, 0) AS slippage_estimated,
               s.magic_number,
               s.strategy_definition_id,
               s.strategy_instance_id
          FROM closed_trades ct
          JOIN accounts a ON a.id = ct.account_id
          LEFT JOIN strategies s ON s.id = ct.strategy_id
         WHERE ct.close_time IS NOT NULL
    """

    def empty_bucket() -> dict:
        return {
            "trades": 0,
            "net_profit": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "commission": 0.0,
            "swap": 0.0,
            "fees": 0.0,
            "spread_cost_estimated": 0.0,
            "slippage_estimated": 0.0,
        }

    def add_bucket(groups: dict, key, row) -> None:
        bucket = groups.setdefault(str(key), empty_bucket())
        bucket["trades"] += 1
        for field in (
            "net_profit",
            "gross_profit",
            "gross_loss",
            "commission",
            "swap",
            "fees",
            "spread_cost_estimated",
            "slippage_estimated",
        ):
            bucket[field] = round(bucket[field] + _to_float(row[field], 0.0), 4)

    by_weekday = {}
    by_hour = {}
    by_session = {}
    by_month = {}
    by_symbol = {}
    by_account = {}
    by_strategy = {}
    by_instance = {}

    with _connect() as conn:
        rows = conn.execute(query).fetchall()

    for row in rows:
        ts = _parse_datetime(row["close_time"])
        if not ts:
            continue
        hour = ts.hour
        if 0 <= hour < 7:
            session = "asia"
        elif 7 <= hour < 13:
            session = "london"
        elif 13 <= hour < 21:
            session = "new_york"
        else:
            session = "rollover"
        add_bucket(by_weekday, ts.strftime("%A"), row)
        add_bucket(by_hour, f"{hour:02d}:00", row)
        add_bucket(by_session, session, row)
        add_bucket(by_month, ts.strftime("%Y-%m"), row)
        add_bucket(by_symbol, row["symbol"] or "unknown", row)
        add_bucket(by_account, row["account_id"], row)
        add_bucket(by_strategy, row["strategy_definition_id"] or f"magic:{row['magic_number'] or 0}", row)
        add_bucket(by_instance, row["strategy_instance_id"] or "unknown", row)

    return {
        "timezone": "UTC",
        "timestamp_source": "close_time",
        "metrics_use": "net_profit",
        "by_weekday": by_weekday,
        "by_hour": by_hour,
        "by_session": by_session,
        "by_month": by_month,
        "by_symbol": by_symbol,
        "by_account": by_account,
        "by_strategy": by_strategy,
        "by_instance": by_instance,
    }


def get_pnl_cost_breakdown() -> dict:
    query = """
        SELECT ct.account_id,
               a.server AS broker,
               ct.symbol,
               s.strategy_definition_id,
               s.strategy_instance_id,
               COALESCE(ct.net_profit, ct.profit, 0) AS net_profit,
               CASE WHEN COALESCE(ct.profit, ct.net_profit, 0) > 0 THEN COALESCE(ct.profit, ct.net_profit, 0) ELSE 0.0 END AS gross_profit,
               CASE WHEN COALESCE(ct.profit, ct.net_profit, 0) < 0 THEN ABS(COALESCE(ct.profit, ct.net_profit, 0)) ELSE 0.0 END AS gross_loss,
               COALESCE(ct.commission, 0) AS commission,
               COALESCE(ct.swap, 0) AS swap,
               COALESCE(ct.fees, 0) AS fees,
               COALESCE(ct.spread_cost_estimated, 0) AS spread_cost_estimated,
               COALESCE(ct.slippage_estimated, 0) AS slippage_estimated
          FROM closed_trades ct
          JOIN accounts a ON a.id = ct.account_id
          LEFT JOIN strategies s ON s.id = ct.strategy_id
    """

    def bucket() -> dict:
        return {
            "trades": 0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "commission": 0.0,
            "swap": 0.0,
            "fees": 0.0,
            "spread_cost_estimated": 0.0,
            "slippage_estimated": 0.0,
            "net_profit": 0.0,
            "total_costs": 0.0,
        }

    def add(groups: dict, key, row) -> None:
        item = groups.setdefault(str(key or "unknown"), bucket())
        item["trades"] += 1
        for field in (
            "gross_profit",
            "gross_loss",
            "commission",
            "swap",
            "fees",
            "spread_cost_estimated",
            "slippage_estimated",
            "net_profit",
        ):
            item[field] = round(item[field] + _to_float(row[field], 0.0), 4)
        item["total_costs"] = round(
            item["commission"]
            + item["swap"]
            + item["fees"]
            + item["spread_cost_estimated"]
            + item["slippage_estimated"],
            4,
        )

    by_account = {}
    by_broker = {}
    by_symbol = {}
    by_strategy = {}
    by_instance = {}
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
    for row in rows:
        add(by_account, row["account_id"], row)
        add(by_broker, row["broker"], row)
        add(by_symbol, row["symbol"], row)
        add(by_strategy, row["strategy_definition_id"], row)
        add(by_instance, row["strategy_instance_id"], row)
    return {
        "metrics_use": "net_profit",
        "cost_fields": ["commission", "swap", "fees", "spread_cost_estimated", "slippage_estimated"],
        "by_account": by_account,
        "by_broker": by_broker,
        "by_symbol": by_symbol,
        "by_strategy": by_strategy,
        "by_instance": by_instance,
    }


def _parse_datetime(value):
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value), fmt)
            except Exception:
                continue
    return None


def _to_float(value, default=None):
    try:
        if value is None or str(value).lower() in ("", "nan", "none", "nat"):
            return default
        return float(value)
    except Exception:
        return default


def _to_int(value, default=0):
    try:
        if value is None or str(value).lower() in ("", "nan", "none", "nat"):
            return default
        return int(float(value))
    except Exception:
        return default


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        if value != value:
            return None
    except Exception:
        pass
    if str(value).lower() in ("nan", "nat"):
        return None
    return value


def _insert_command_execution(
    conn: sqlite3.Connection,
    command_id: int,
    account_id: str | None,
    status_before: str | None,
    status_after: str,
    response_data: str,
) -> None:
    conn.execute(
        """
        INSERT INTO command_executions (
          command_id, account_id, status_before, status_after, response_data, executed_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (int(command_id), account_id, status_before, status_after, response_data, _utc_now()),
    )


def _decode_command_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    try:
        item["parameters"] = json.loads(item.get("parameters") or "{}")
    except Exception:
        item["parameters"] = {}
    item["processed_flag"] = bool(item.get("processed_flag"))
    item["command_id"] = item["id"]
    item["ts"] = item.get("created_at")
    return item


def resolve_command(command_id: int, result: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE command_log
               SET result = ?, resolved_at = ?
             WHERE id = ?
            """,
            (result, _utc_now(), int(command_id)),
        )


def get_command_log(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, system, magic, action, account_id, platform, sent_at, result, resolved_at
              FROM command_log
             ORDER BY sent_at DESC, id DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def db_has_data() -> bool:
    with _connect() as conn:
        migrate_phase1_schema(conn)
        row = conn.execute(
            """
            SELECT 1
             WHERE EXISTS (SELECT 1 FROM trades LIMIT 1)
                OR EXISTS (SELECT 1 FROM closed_trades LIMIT 1)
            """
        ).fetchone()
    return row is not None


def _as_iso_text(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value)
    if text.lower() in ("nat", "nan", "none"):
        return None
    return text


def encode_result(result) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)
