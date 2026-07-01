# Reglas Operativas De YQuant

Este documento define reglas obligatorias para usar YQuant en decisiones de riesgo sobre cuentas MT4/MT5.

## Drawdown Operativo

Regla fundacional:

**Nunca usar drawdown porcentual sobre PnL acumulado como riesgo real de cuenta.**

Para riesgo operativo, el drawdown porcentual debe calcularse contra capital real:

```text
drawdown_real_pct = abs(balance_or_equity_current - balance_or_equity_peak) / balance_or_equity_peak * 100
```

Fuente preferida:

- `balance_after` por operacion cerrada, cuando esta disponible y reconciliado.
- Equity real de cuenta, cuando exista una curva confiable de snapshots.

Fuente no valida para riesgo real:

- PnL acumulado desde cero dividido por el pico de PnL.

Ese calculo se conserva solo como metrica analitica de curva del sistema y debe tratarse como:

```text
pnl_curve_drawdown_pct
```

El dashboard puede mostrar una curva visual de P&L acumulado dentro del detalle desplegable de cada estrategia. Esa curva se calcula desde trades cerrados y ayuda a inspeccionar trayectoria, recuperacion, deterioro reciente y concentracion por cuenta/simbolo. No convierte el P&L acumulado en riesgo real de cuenta.

## Separacion De Metricas

YQuant debe separar dos conceptos:

- `pnl_curve_*`: drawdown de la curva aislada de PnL del sistema.
- `balance_*`: drawdown real de capital de cuenta.

Para decisiones operativas (`mantener`, `reducir_riesgo`, `pausar`, `aumentar_riesgo`), el motor debe usar `balance_*` cuando `balance_drawdown_reliable = true`.

## Regla De Revision Manual

Si no existe drawdown real confiable:

- el sistema debe quedar en `revision_manual`;
- no se deben emitir recomendaciones fuertes basadas en DD;
- no se deben exportar comandos de riesgo;
- el DD de curva PnL y la curva visual de P&L acumulado pueden mostrarse solo como datos informativos.

Esta regla evita pausar, reducir o aumentar riesgo por una ilusion matematica causada por un denominador incorrecto.

## Expected Metrics

Para filas `strategy_expected_metrics`:

- `expected_max_drawdown_pct` debe representar DD real sobre balance/equity si la fuente es `live_history`.
- Si la fuente es backtest/OOS/WF y no trae capital base comparable, debe documentarse el denominador usado.
- Si el expected DD viene de una curva de PnL aislada, no debe usarse para decisiones operativas hasta convertirlo a una base de capital comparable.

## Comparacion Contra Esperado

`compare_against_expected()` debe comparar:

```text
balance_current_drawdown_pct o balance_max_drawdown_pct
vs
expected_max_drawdown_pct
```

No debe comparar `pnl_curve_*_drawdown_pct` contra `expected_max_drawdown_pct` para decisiones de riesgo real.

## Estados

- `normal` / `mantener`: permitido solo si la muestra es suficiente y el DD real es confiable.
- `warning` / `reducir_riesgo`: permitido cuando el DD real supera umbrales contra esperado.
- `critical` / `pausar`: permitido por DD real extremo, PF bajo, expectancy negativa o perdida con expectancy negativa.
- `revision_manual`: obligatorio cuando falta DD real confiable.
- `insufficient_data`: obligatorio cuando no se alcanza la muestra minima.

## Revision Humana

Antes de actuar en cuenta REAL:

- confirmar que `drawdown_basis` sea `balance_after` o equity real confiable;
- confirmar que `balance_drawdown_reliable = true`;
- revisar PF, expectancy, net profit, racha perdedora y el detalle desplegable de estrategia para ver curva de P&L acumulado, ultimos 20/50 trades, desglose por cuenta y desglose por simbolo;
- registrar la decision en `decision_journal`;
- mantener `QA_Commander.AutoExecute=false` hasta validar el circuito completo.
