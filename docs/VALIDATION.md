# Validacion Post-Refactor

Este documento cubre la Fase 1.5 de seguridad posterior al refactor modular. El objetivo es confirmar que la modularizacion no cambio el comportamiento funcional.

## Pruebas Automatizadas

Ejecutar desde la raiz del proyecto:

```bash
python tests/validate_modular_refactor.py
```

La suite usa una base SQLite temporal y el test client de Flask. No modifica `qa_portfolio.db` real ni los CSV reales configurados.

Para validar si la base de datos ya puede responder las preguntas objetivo del dashboard
por estrategia, cuenta y portafolio:

```bash
python tests/test_database_goal_queries.py
```

Esta prueba es un contrato de meta. Valida que existan columnas consultables
para drawdown actual, rachas, equity, comparacion contra esperado, estado
operativo y accion recomendada. No valida datos reales; valida que el esquema
pueda sostener esas consultas sin depender de JSON opaco.

Para validar el criterio operativo del motor de decision:

```bash
python tests/test_strategy_decision_engine.py
```

Esta prueba cubre decisiones puras con metricas live contra esperadas: mantener
si el DD real sobre balance/equity esta dentro de rango, reducir riesgo sobre
130% del DD esperado, pausar sobre 150%, marcar critica una racha perdedora
sobre 150%, impedir decisiones fuertes cuando no hay muestra suficiente y
forzar `revision_manual` cuando no existe DD real confiable.

Para validar que la decision queda persistida y auditable en snapshots:

```bash
python tests/test_metric_snapshot_decision_persistence.py
```

Esta prueba inserta metricas esperadas, guarda un snapshot live y confirma que
`strategy_metric_snapshots` persiste `status`, `health_score`,
`decision_state`, ratios contra esperado y razones en JSON consultable. Esto
no dispara comandos ni modifica `commands.json`; la capa solo recomienda.

Para validar trazabilidad entre recomendacion del motor, decision humana y
outcome:

```bash
python tests/test_decision_recommendation_audit_flow.py
```

Esta prueba confirma que un snapshot puede recomendar `reducir_riesgo`, que el
usuario puede registrar una decision humana distinta vinculada al snapshot, que
los outcomes 7d/30d quedan guardados y que nada de ese flujo crea comandos ni
modifica `commands.json`.

Para iniciar validacion manual con datos reales:

```bash
python generate_real_system_validation_report.py --limit 10
```

Este reporte abre `qa_portfolio.db` en modo solo lectura desde `config.json` y
genera `docs/real_system_validation_report.md`. Sirve para revisar 5 a 10
sistemas reales con net profit, DD real actual, DD real esperado, racha perdedora,
recomendacion del motor, decision humana y outcomes pendientes. Si los campos
esperados aparecen como `pendiente`, primero hay que cargar
`strategy_expected_metrics` antes de calibrar recomendaciones.

Cobertura automatizada:

- Contrato JSON minimo de `/api/portfolio`.
- Contrato de fuente de verdad: `/api/portfolio` debe responder `summary.source = sqlite`; CSV solo se usa como ingesta.
- Contrato JSON minimo de `/api/systems`.
- Contrato JSON minimo de `/api/data-quality`.
- Contrato JSON minimo de `/api/sync/trades`.
- Contrato JSON minimo de `/api/sync/broker`.
- Contrato JSON minimo de `/api/command`.
- Contrato JSON minimo de `/api/commands/history`.
- Idempotencia de importacion incremental: dos ejecuciones no duplican `closed_trades`.
- Seguridad de callbacks de comandos: `/api/command/ack` y `/api/command/result` rechazan payloads sin `command_id`.
- Seguridad de comandos: rechaza magic inexistente.
- Seguridad de comandos: rechaza plataforma incorrecta.
- Seguridad de comandos: rechaza `increase_lots_25` en sistema en cuarentena.
- Consistencia de metricas: mismos trades producen el mismo resultado.
- Consistencia de metricas objetivo: `compute_metrics()` separa drawdown de
  curva PnL (`pnl_curve_*`) y drawdown real de capital (`balance_*`), ademas de
  rachas, average win/loss y payoff ratio.
- Motor de decision: `compare_against_expected()` clasifica sistemas como
  `normal`, `warning`, `critical` o `insufficient_data` y emite una accion
  operativa usando DD real cuando `balance_drawdown_reliable=true`.
- Persistencia de decision: los snapshots guardan la recomendacion del motor,
  ratios contra expected metrics y razones auditables sin ejecutar comandos.
- Trazabilidad humana: una decision manual puede diferir de la recomendacion
  del motor, quedar vinculada al snapshot y registrar outcomes sin crear
  comandos.
- Consistencia de calidad de riesgo: si `risk_metrics_reliable=false`, la accion debe ser `revision_manual` y Sharpe/Drawdown deben ser `null`.
- Consistencia de breakdowns: `gross_profit` y `gross_loss` se reconstruyen desde `profit`, no desde columnas crudas contaminadas.
- Consistencia de holding period: `average_holding_hours` excluye trades con `close_time <= open_time`.
- Governance: crea una decision valida en `decision_journal`.
- Governance: rechaza `decision_type` invalido.
- Governance: rechaza `reason_category` invalido.
- Governance: rechaza `metrics_snapshot_id` inexistente.
- Governance: acepta `metrics_snapshot_id` existente en `strategy_metric_snapshots`.
- Governance: lista decisiones filtradas por `strategy_id`.
- Governance: actualiza `outcome_7d` y `outcome_30d`.
- Governance: confirma `audit_events` para `decision_created` y `decision_outcome_updated`.
- Dashboard workflow: `GET /api/decisions` devuelve decisiones creadas.
- Dashboard workflow: la pagina principal renderiza el shell de decisiones sin romper.
- Dashboard workflow: el endpoint de outcome funciona para actualizaciones hechas desde UI.
- Dashboard hardening: el HTML contiene pestaña `Decisiones`, formulario, panel de pendientes y controles de outcome.
- Dashboard hardening: crear una decision no crea `command_id`, no inserta comandos y no escribe `commands.json`.
- Strategy identity: crea `strategy_version` valida.
- Strategy identity: crea `risk_profile` valido.
- Strategy identity: crea `deployment_event` valido.
- Strategy identity: vincula `strategy_instance` a `strategy_version`.
- Strategy identity: vincula `strategy_instance` a `risk_profile`.
- Strategy identity: `/api/systems` sigue respondiendo e incluye metadata opcional cuando existe.
- Strategy identity: `/api/strategy-instances` incluye campos nuevos sin romper contrato.
- Strategy identity: migracion idempotente.
- Strategy identity: crea `audit_events` para version, perfil, deployment y enlaces.
- Strategy identity: `magic` sigue funcionando como fallback sin version explicita.

## Checklist Manual

Estas validaciones dependen de MetaTrader/EA real y no se automatizan en esta fase:

- Dashboard detalle de estrategia:
  - abrir una fila de sistema en la pestaña `Sistemas`;
  - confirmar que aparece un panel desplegable con KPIs extendidos, curva de P&L acumulado, tabla por cuenta y tabla por simbolo;
  - confirmar que la curva se recalcula al cambiar filtros de plataforma, tipo de cuenta o cuentas seleccionadas;
  - confirmar en Network que abrir/cerrar el detalle no llama a `/api/command`, no crea `command_id` y no modifica `commands.json`;
  - confirmar que los botones operativos dentro de la fila siguen aislados del clic de apertura del detalle.

- Regla de arquitectura: las recomendaciones del motor nunca ejecutan acciones
  directamente. Toda accion operativa debe pasar por decision humana registrada.

- Confirmar en MT4/MT5 que `QA_Commander` sigue leyendo `commands.json` generado por el dashboard.
- Confirmar que un `result.json` real con `command_id` actualiza el comando correspondiente.
- Confirmar que `QA_AccountMonitor` real sigue exportando `open_trades`, `account_history` y `trades_*.csv` con los nombres esperados.
- Ejecutar `/api/sync/broker` contra una carpeta real de MetaTrader y verificar que posiciones/snapshots se actualizan en el dashboard.
- Ejecutar `POST /api/sync/trades` y confirmar que importa hacia SQLite; luego `GET /api/portfolio` debe seguir mostrando `summary.source = sqlite`.
- Confirmar que presionar `Sync MT4/MT5` no cambia el dashboard a `source=csv`.
- Confirmar que sistemas con `DD n/a` y `Sharpe n/a` aparecen como `revision_manual`, no como `cuarentena`.
- Confirmar que sistemas sin `balance_drawdown_reliable=true` aparecen como `revision_manual`, aunque tengan DD de curva PnL.
- Confirmar que `pnl_curve_max_drawdown_pct` no se usa como riesgo real de cuenta ni como base para comandos.
- Confirmar que `strategy_expected_metrics.expected_max_drawdown_pct` esta expresado en DD real sobre `balance_after` o equity confiable para fuentes `live_history`.
- Confirmar que los comandos quedan bloqueados para sistemas con `risk_metrics_reliable=false`.
- Confirmar que `/api/data-quality` reporta `stale_running_imports` si existe un `import_runs.status='running'` antiguo.
- Revisar manualmente que las decisiones registradas en `/api/decisions` no disparan comandos ni modifican `commands.json`.
- En el dashboard, abrir un sistema, usar `Registrar decision`, confirmar que aparece como ultima decision y en la pestaña `Decisiones`.
- En la pestaña `Decisiones`, completar `outcome_7d` y `outcome_30d` y confirmar que la decision deja de aparecer como pendiente.

## Checklist Manual De Navegador

### Detalle De Estrategia

Antes de validar decisiones, abrir una fila de sistema desde `Sistemas` y confirmar que aparece el bloque `Curva P&L acumulado de la estrategia`.

Confirmar que el detalle muestra KPIs extendidos, tabla por cuenta y tabla por simbolo. Aplicar un filtro de cuenta o plataforma y confirmar que la curva y tablas se recalculan con el subconjunto visible.

En la pestaña Network, confirmar que abrir/cerrar el detalle no llama a `/api/command` ni modifica `commands.json`. Los botones operativos dentro de la fila deben seguir siendo las unicas acciones que crean comandos.

Usar este checklist si `node --check` no esta disponible en el entorno local.

Antes de validar decisiones, abrir Network y confirmar que `/api/portfolio` responde `summary.source = sqlite`. Si hay metricas de riesgo no confiables, confirmar que el estado visible sea `revision_manual` y no `cuarentena`.

1. Abrir `http://localhost:5000/?v=validation`.
2. Abrir DevTools del navegador y confirmar que la consola no muestra errores JavaScript al cargar.
3. Confirmar que existe la pestaña `Decisiones`.
4. Abrir un sistema y presionar `Registrar decision`.
5. Guardar una decision valida y confirmar que aparece como ultima decision del sistema.
6. Ir a la pestaña `Decisiones` y confirmar que aparece en pendientes si falta `outcome_7d` u `outcome_30d`.
7. Actualizar ambos outcomes y confirmar que aparece mensaje visual de exito.
8. Revisar la pestaña Network y confirmar que registrar decision llama solo a `/api/decisions`, no a `/api/command`.
9. Confirmar que `commands.json` no cambia luego de registrar una decision.

## Checklist Manual Strategy Identity

1. Crear una version con `POST /api/strategy-versions` para un `strategy_id` existente.
2. Crear un perfil con `POST /api/risk-profiles`.
3. Crear un deployment con `POST /api/deployment-events` incluyendo `instance_id`, `strategy_version_id` y `risk_profile_id`.
4. Consultar `/api/strategy-instances` y confirmar `strategy_version_id`, `version_name`, `parameters_hash`, `risk_profile_id`, `risk_profile_name` y `deployment_state`.
5. Consultar `/api/systems` y confirmar que el sistema sigue apareciendo aunque no tenga version vinculada.
6. Confirmar en `/api/audit-events` los eventos `strategy_version_created`, `risk_profile_created`, `deployment_event_created`, `strategy_instance_version_linked` y `strategy_instance_risk_profile_linked`.
7. Confirmar que crear versiones/perfiles/deployments no llama a `/api/command` y no modifica `commands.json`.

## Criterio De Aprobacion

La fase se considera aprobada si:

- `python tests/validate_modular_refactor.py` termina en `OK`.
- No hay cambios en rutas publicas existentes.
- El checklist manual no muestra diferencias frente al comportamiento anterior.
