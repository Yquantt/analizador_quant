"""Pure performance metric calculations for strategy snapshots."""


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _ratio(numerator, denominator) -> float | None:
    den = abs(_safe_float(denominator))
    if den == 0:
        return None
    return abs(_safe_float(numerator)) / den


def calculate_quality_metrics(profits: list[float]) -> dict:
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value < 0]
    average_win = sum(wins) / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0
    payoff_ratio = average_win / abs(average_loss) if average_loss else 0.0
    return {
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "average_win": round(average_win, 4),
        "average_loss": round(average_loss, 4),
        "payoff_ratio": round(payoff_ratio, 4),
    }


def calculate_streak_metrics(profits: list[float]) -> dict:
    winning_streaks = []
    losing_streaks = []
    current_kind = None
    current_len = 0

    for profit in profits:
        kind = "win" if profit > 0 else "loss" if profit < 0 else "flat"
        if kind == current_kind and kind != "flat":
            current_len += 1
            continue

        if current_kind == "win":
            winning_streaks.append(current_len)
        elif current_kind == "loss":
            losing_streaks.append(current_len)

        current_kind = kind
        current_len = 1 if kind != "flat" else 0

    if current_kind == "win":
        winning_streaks.append(current_len)
    elif current_kind == "loss":
        losing_streaks.append(current_len)

    return {
        "current_winning_streak": current_len if current_kind == "win" else 0,
        "current_losing_streak": current_len if current_kind == "loss" else 0,
        "longest_winning_streak": max(winning_streaks or [0]),
        "longest_losing_streak": max(losing_streaks or [0]),
        "average_winning_streak": round(sum(winning_streaks) / len(winning_streaks), 4) if winning_streaks else 0.0,
        "average_losing_streak": round(sum(losing_streaks) / len(losing_streaks), 4) if losing_streaks else 0.0,
    }


def calculate_equity_metrics(profits: list[float]) -> dict:
    if not profits:
        return {
            "equity_current": 0.0,
            "equity_peak": 0.0,
            "return_pct": 0.0,
            "current_drawdown": 0.0,
            "current_drawdown_pct": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "return_over_drawdown": 0.0,
            "recovery_factor": 0.0,
        }

    equity = []
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    for profit in profits:
        running += float(profit)
        equity.append(running)
        peak = max(peak, running)
        drawdown = running - peak
        drawdown_pct = abs(drawdown) / abs(peak) * 100 if peak else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_pct = drawdown_pct

    equity_current = equity[-1]
    equity_peak = max(equity)
    current_drawdown = equity_current - equity_peak
    current_drawdown_pct = abs(current_drawdown) / abs(equity_peak) * 100 if equity_peak else 0.0
    max_drawdown_abs = abs(max_drawdown)
    net_profit = sum(profits)
    return {
        "equity_current": round(equity_current, 4),
        "equity_peak": round(equity_peak, 4),
        "return_pct": 0.0,
        "current_drawdown": round(current_drawdown, 4),
        "current_drawdown_pct": round(current_drawdown_pct, 4),
        "max_drawdown": round(max_drawdown_abs, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "return_over_drawdown": round(net_profit / max_drawdown_abs, 4) if max_drawdown_abs else 0.0,
        "recovery_factor": round(net_profit / max_drawdown_abs, 4) if max_drawdown_abs else 0.0,
    }


def calculate_balance_drawdown_metrics(balances: list[float | None]) -> dict:
    clean_balances = []
    for value in balances:
        try:
            if value in (None, ""):
                continue
            clean_balances.append(float(value))
        except (TypeError, ValueError):
            continue

    if len(clean_balances) < 2:
        return {
            "balance_drawdown_reliable": False,
            "balance_equity_current": None,
            "balance_equity_peak": None,
            "balance_current_drawdown": None,
            "balance_current_drawdown_pct": None,
            "balance_max_drawdown": None,
            "balance_max_drawdown_pct": None,
            "drawdown_basis": "unavailable",
        }

    peak = clean_balances[0]
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    current_drawdown = 0.0
    current_drawdown_pct = 0.0

    for balance in clean_balances:
        peak = max(peak, balance)
        drawdown = balance - peak
        drawdown_pct = abs(drawdown) / abs(peak) * 100 if peak else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_pct = drawdown_pct
        current_drawdown = drawdown
        current_drawdown_pct = drawdown_pct

    max_drawdown_abs = abs(max_drawdown)
    return {
        "balance_drawdown_reliable": True,
        "balance_equity_current": round(clean_balances[-1], 4),
        "balance_equity_peak": round(peak, 4),
        "balance_current_drawdown": round(current_drawdown, 4),
        "balance_current_drawdown_pct": round(current_drawdown_pct, 4),
        "balance_max_drawdown": round(max_drawdown_abs, 4),
        "balance_max_drawdown_pct": round(max_drawdown_pct, 4),
        "drawdown_basis": "balance_after",
    }


def calculate_recent_trade_counts(profits: list[float]) -> dict:
    total = len(profits)
    return {
        "trades_last_20": min(total, 20),
        "trades_last_50": min(total, 50),
        "trades_last_100": min(total, 100),
    }


def compare_against_expected(metrics: dict, expected: dict | None, min_trades: int = 30) -> dict:
    """Return operational status from live metrics and expected references."""
    expected = expected or {}
    trades = _safe_int(metrics.get("trades"))
    net_profit = _safe_float(metrics.get("net_profit"))
    expectancy = _safe_float(metrics.get("expectancy"))
    profit_factor = _safe_float(metrics.get("profit_factor"))
    current_losing_streak = _safe_int(metrics.get("current_losing_streak"))
    drawdown_pct = metrics.get("balance_current_drawdown_pct")
    if drawdown_pct in (None, ""):
        drawdown_pct = metrics.get("balance_max_drawdown_pct")
    real_drawdown_reliable = metrics.get("balance_drawdown_reliable") is True

    dd_ratio = _ratio(drawdown_pct, expected.get("expected_max_drawdown_pct")) if real_drawdown_reliable else None
    losing_streak_ratio = _ratio(current_losing_streak, expected.get("expected_longest_losing_streak"))
    profit_factor_ratio = _ratio(profit_factor, expected.get("expected_profit_factor"))
    expectancy_ratio = _ratio(expectancy, expected.get("expected_expectancy"))

    result = {
        "status": "normal",
        "health_score": 100.0,
        "decision_state": "mantener",
        "drawdown_ratio": round(dd_ratio, 4) if dd_ratio is not None else None,
        "losing_streak_ratio": round(losing_streak_ratio, 4) if losing_streak_ratio is not None else None,
        "profit_factor_ratio": round(profit_factor_ratio, 4) if profit_factor_ratio is not None else None,
        "expectancy_ratio": round(expectancy_ratio, 4) if expectancy_ratio is not None else None,
        "reasons": [],
    }

    if trades < min_trades:
        result.update({
            "status": "insufficient_data",
            "health_score": 50.0,
            "decision_state": "observar",
            "reasons": ["min_trades_not_reached"],
        })
        return result

    if not real_drawdown_reliable:
        result.update({
            "status": "revision_manual",
            "health_score": 50.0,
            "decision_state": "revision_manual",
            "reasons": ["real_drawdown_unavailable"],
        })
        return result

    if dd_ratio is not None:
        if dd_ratio > 1.5:
            result["reasons"].append("drawdown_above_150pct_expected")
        elif dd_ratio > 1.3:
            result["reasons"].append("drawdown_above_130pct_expected")
        elif dd_ratio > 1.0:
            result["reasons"].append("drawdown_above_expected")

    if losing_streak_ratio is not None:
        if losing_streak_ratio > 1.5:
            result["reasons"].append("losing_streak_above_150pct_expected")
        elif losing_streak_ratio > 1.25:
            result["reasons"].append("losing_streak_above_125pct_expected")
        elif losing_streak_ratio > 1.0:
            result["reasons"].append("losing_streak_above_expected")

    if expectancy < 0:
        result["reasons"].append("negative_expectancy")
    if profit_factor < 1:
        result["reasons"].append("profit_factor_below_1")
    if net_profit < 0 and expectancy < 0:
        result["reasons"].append("losing_capital_with_negative_expectancy")

    critical_reasons = {
        "drawdown_above_150pct_expected",
        "losing_streak_above_150pct_expected",
        "negative_expectancy",
        "profit_factor_below_1",
        "losing_capital_with_negative_expectancy",
    }
    reduce_reasons = {"drawdown_above_130pct_expected"}

    if any(reason in critical_reasons for reason in result["reasons"]):
        result["status"] = "critical"
        result["decision_state"] = "pausar"
        result["health_score"] = 20.0
    elif any(reason in reduce_reasons for reason in result["reasons"]):
        result["status"] = "warning"
        result["decision_state"] = "reducir_riesgo"
        result["health_score"] = 60.0
    elif result["reasons"]:
        result["status"] = "warning"
        result["decision_state"] = "observar"
        result["health_score"] = 75.0

    return result
