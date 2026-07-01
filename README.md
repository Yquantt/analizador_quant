# QA Portfolio Commander

Base central local para analizar cuentas de trading en MetaTrader 4 y MetaTrader 5, persistir historial/estado vivo en SQLite, clasificar sistemas por rendimiento y enviar comandos de gestion de riesgo hacia MetaTrader.

El sistema mantiene compatibilidad con archivos CSV/JSON en la carpeta comun de MetaTrader, pero `qa_portfolio.db` es la fuente de verdad progresiva para historial, cuentas, posiciones vivas, snapshots, equity, comandos y auditoria.

## Cambios De Esta Version

- Importacion incremental automatica de trades cerrados desde la carpeta comun de MetaTrader usando archivos `trades_<label>_<account_id>.csv`.
- Exportacion automatica de historial cerrado desde `QA_AccountMonitor` en MT4 y MT5; `QA_TradeExporter` queda como respaldo manual.
- Soporte para MT4 y MT5 en el mismo dashboard, normalizando columnas y metricas en el backend Flask.
- Deteccion multi-cuenta por `account_id`, `label`, `type` y `platform`, con agrupacion global de sistemas por magic number.
- Seleccion automatica del CSV mas relevante cuando hay mas de un archivo para la misma cuenta.
- Lectura de estado de cuenta desde `account_history*.json/csv`, `open_trades*.json` y `running_eas*.json` generados por los monitores.
- Comandos de riesgo multi-cuenta con `account_id` y `platform` para evitar ejecutar acciones en la cuenta equivocada.
- Chat IA opcional usando API compatible con OpenAI, tomando la clave desde variables de entorno o configuracion local.
- Limpieza para GitHub: `config.json`, CSV reales, logs, entornos virtuales, caches y binarios compilados MetaTrader quedan fuera del repositorio.
- SQLite central con tablas `accounts`, `brokers`, `strategy_definitions`, `strategy_instances`, `strategies`, `closed_trades`, `raw_trade_imports`, `open_positions`, `account_snapshots`, `equity_points`, `strategy_metric_snapshots`, `commands`, `command_executions`, `import_runs`, `trade_file_state`, `discovered_accounts`, `trade_import_conflicts` y `audit_events`.
- Identidad interna por `account_key` compuesto: `platform + server + login + account_type`.
- Control de comandos con `command_id`, `sent_to_ea_at`, acuse, resultado y deduplicacion local en los Commander.
- Sincronizacion broker-local mediante `sync_broker.py` para persistir posiciones abiertas, snapshots y puntos de equity.
- Scheduler incremental: detecta cambios reales en `trades_*.csv`, evita duplicados y refresca el dashboard sin `reload=true`.
- Auto-descubrimiento seguro de cuentas nuevas: quedan pendientes hasta aprobacion manual desde API/dashboard.
- Dashboard con SQLite como unica fuente de lectura: CSV/JSON quedan como capa de ingesta hacia `qa_portfolio.db`, no como fuente alternativa de presentacion.
- KPIs de riesgo bloqueados cuando la calidad no permite usarlos: si trades y equity no estan reconciliados, el sistema pasa a `revision_manual` y no genera comandos automaticos de riesgo.
- Regla fundacional de YQuant: nunca usar DD porcentual sobre PnL acumulado como riesgo real de cuenta; el riesgo operativo usa DD real sobre `balance_after` o equity confiable.
- Detalle desplegable por estrategia en el dashboard: al abrir una fila de sistema muestra KPIs extendidos, curva de P&L acumulado, desglose por cuenta y desglose por simbolo respetando filtros activos.

## Flujo De Trabajo

```text
MT4 terminal
  QA_AccountMonitor.mq4  -> trades_<label>_<account_id>.csv
                          -> running_eas, account_history, open_trades
  QA_TradeExporter.mq4   -> respaldo manual para historial cerrado
  QA_Commander.mq4       <- commands.json
                          -> result.json

MT5 terminal
  QA_AccountMonitor.mq5  -> trades_<label>_<account_id>.csv
                          -> running_eas.json, account_history.json, open_trades.json
  QA_TradeExporter.mq5   -> respaldo manual para historial cerrado
  QA_Commander.mq5       <- commands.json
                          -> result.json

Python Flask
  app.py                 -> revisa incrementalmente trades_<label>_<account_id>.csv
                          -> controla estado en trade_file_state
                          -> registra raw_trade_imports
                          -> normaliza hacia closed_trades
                          -> normaliza MT4/MT5
                          -> calcula metricas
                          -> persiste strategy_metric_snapshots
                          -> sirve el dashboard solo desde SQLite/cache
                          -> crea comandos en SQLite y exporta commands.json como transporte
                          -> sincroniza open_positions, account_snapshots y equity_points
                          -> registra audit_events y data quality
                          -> expone chat IA con DeepSeek
```

## Fuente De Verdad Del Dashboard

`qa_portfolio.db` es la unica fuente de lectura del dashboard. Los archivos `trades_*.csv`, `account_history*`, `open_trades*` y `running_eas*` son entradas de ingesta generadas por MetaTrader; el backend las normaliza, deduplica y persiste en SQLite antes de que la UI consuma datos.

Reglas actuales:

- `GET /api/portfolio` responde desde cache/SQLite.
- `GET /api/portfolio?reload=true` invalida/recalcula cache, pero no fuerza lectura CSV.
- `POST /api/sync/trades` importa incrementalmente `trades_*.csv` hacia SQLite y despues recarga el dashboard desde SQLite.
- `POST /api/sync/broker` sincroniza estado vivo, snapshots, equity y trades cerrados hacia SQLite.
- No debe existir logica de decision que lea CSV como segunda fuente de verdad.

Esto evita inconsistencias entre la capa cruda de MetaTrader y la capa normalizada/auditada.

## Componentes Activos

- `app.py`: servidor Flask local y API del dashboard.
- `database.py`: esquema SQLite, migraciones idempotentes y CRUD de trading/comandos.
- `repositories/`: capa de acceso a datos sobre SQLite. Los modulos `accounts_repo.py`, `trades_repo.py`, `metrics_repo.py`, `commands_repo.py` y `audit_repo.py` delegan en `database.py` y definen el limite estable para futuras migraciones de persistencia.
- `services/`: capa de casos de uso. Contiene `import_service.py`, `broker_sync_service.py`, `metrics_service.py`, `command_service.py`, `audit_service.py` y `data_quality_service.py`.
- `routes/`: punto de registro de blueprints por dominio. En esta fase mantiene blueprints preparados y los endpoints publicos siguen registrados en `app.py` para conservar compatibilidad durante la extraccion progresiva.
- `migrate_db.py`: aplica migraciones sobre `qa_portfolio.db`.
- `sync_broker.py`: sincroniza datos vivos desde archivos broker-locales de MetaTrader hacia SQLite.
- `templates/index.html`: interfaz web.
- `config.example.json`: plantilla segura para crear el `config.json` local.
- `config.json`: archivo local ignorado por Git con ruta de datos, cuentas, plataformas, umbrales, filtros y puerto.
- `requirements.txt`: dependencias Python.
- `start.bat`: inicio rapido en Windows; instala dependencias locales si faltan.

## Arquitectura Modular

El proyecto esta migrando a un monolito modular. La prioridad de esta etapa es separar responsabilidades sin redisenar comportamiento, mantener SQLite y no romper endpoints existentes.

```text
app.py
  carga configuracion
  inicializa Flask/CORS
  registra blueprints de routes/
  mantiene endpoints publicos compatibles
  inicia scheduler

routes/
  puntos de registro por dominio para mover endpoints progresivamente

services/
  casos de uso de aplicacion:
  importacion incremental, sync broker-local, metricas, comandos, auditoria y data quality

repositories/
  fachada de persistencia:
  accounts, trades, metrics, commands y audit

database.py
  esquema SQLite, migraciones y SQL existente
```

Regla de evolucion: cualquier logica nueva debe entrar en `services/` y acceder a SQLite mediante `repositories/`. `database.py` permanece como implementacion SQLite actual, pero no debe ser el punto de dependencia principal para nuevas rutas o servicios.

Validacion basica del refactor:

```bash
python tests/validate_modular_refactor.py
```

Ese script usa una base SQLite temporal y confirma contratos JSON de endpoints criticos, importacion incremental sin duplicados, seguridad de comandos, creacion de `command_id`, respuesta del dashboard, `/api/data-quality` y consistencia de metricas.

La validacion manual dependiente de MetaTrader real esta documentada en `docs/VALIDATION.md`.

## Governance De Decisiones

La Fase 2 agrega `decision_journal`, un diario operativo para registrar decisiones humanas o del sistema sin ejecutar acciones automaticamente. Esta capa solo guarda trazabilidad y consulta; no dispara comandos hacia MetaTrader.

Tabla SQLite:

- `decision_journal`: registra cuenta, plataforma, estrategia/instancia, magic, simbolo, tipo de decision, categoria de razon, notas humanas, resumen IA, snapshot de metricas asociado, estado anterior/nuevo, contexto de portafolio, revision y outcomes.

Endpoints nuevos:

- `GET /api/decisions`: lista decisiones. Filtros soportados: `strategy_id`, `account_key`, `account_id`, `decision_type`, `limit`.
- `POST /api/decisions`: crea una decision auditada.
- `GET /api/decisions/<id>`: obtiene una decision puntual.
- `POST /api/decisions/<id>/outcome`: actualiza `outcome_7d` y/o `outcome_30d`.

Valores validos para `decision_type`:

```text
mantener, observar, reducir_riesgo, pausar, desactivar, reactivar,
aumentar_riesgo, cerrar_posiciones
```

Valores validos para `reason_category`:

```text
drawdown, racha_perdedora, degradacion_metricas, baja_data,
error_ejecucion, evento_macro, regla_del_sistema,
decision_emocional, manual_override
```

Cada creacion registra `audit_events.event_type = decision_created`. Cada actualizacion de outcome registra `decision_outcome_updated`.

### Flujo En Dashboard

El dashboard muestra una pestaña `Decisiones` con:

- decisiones pendientes de revision, cuando falta `outcome_7d` u `outcome_30d`;
- diario de decisiones recientes;
- actualizacion de outcomes mediante `POST /api/decisions/<id>/outcome`.

En las filas de sistemas e instancias se muestra la ultima decision registrada cuando existe, incluyendo `decision_type`, `reason_category`, `created_at`, `review_after_days` y estado de outcome. El boton `Registrar decision` abre un formulario que envia a `POST /api/decisions`.

El formulario pide `decision_type`, `reason_category`, `human_note` y `review_after_days`. Cuando el contexto esta disponible, el dashboard adjunta automaticamente cuenta, plataforma, estrategia, instancia, magic, simbolo, snapshot de metricas, estado anterior/nuevo y contexto de portafolio.

Las decisiones no ejecutan comandos ni modifican `commands.json`.

### Detalle De Estrategia En Dashboard

En la pestaña `Sistemas`, cada fila de estrategia se puede abrir con un clic sobre la tarjeta/fila. El detalle se renderiza en el frontend desde el payload de `/api/portfolio`, sin crear comandos ni modificar datos.

El panel desplegable muestra:

- KPIs extendidos de la estrategia filtrada: P&L global, expectancy, average trade, payoff ratio, average win, average loss, racha perdedora maxima, P&L de los ultimos 20 trades, P&L de los ultimos 50 trades y fecha del ultimo trade.
- Curva de P&L acumulado de la estrategia usando trades cerrados (`net_profit`/`profit`) ordenados por `close_time`.
- Tabla por cuenta visible con PF, P&L, DD real y accion por instancia.
- Tabla por simbolo con cantidad de trades, win rate y P&L.

El detalle respeta los filtros activos de plataforma (`MT4`/`MT5`), tipo de cuenta (`REAL`/`DEMO`) y cuentas seleccionadas. La curva de P&L acumulado es una visualizacion analitica de performance cerrada; no debe usarse como riesgo real de cuenta. Para decisiones operativas se mantiene la regla de usar `balance_*` cuando `balance_drawdown_reliable=true`.

Los botones operativos (`Detener`, `Reducir 50%`, `Aumentar 25%`, `Aumentar 50%`) estan aislados del clic de apertura del detalle. Usar esos botones sigue generando comandos mediante `/api/command`.

## Strategy Identity & Versioning

La Fase 4 separa la identidad operativa de un sistema en capas para evitar mezclar estrategias, versiones, parametros, cuentas o perfiles de riesgo distintos. El agrupamiento por `magic` sigue siendo compatible y se mantiene como fallback cuando no hay version/perfil explicito.

- `magic`: identificador operativo de MetaTrader. Sigue siendo compatible para agrupar y comandar sistemas, pero no describe version, parametros ni riesgo.
- `strategy_definition`: definicion logica de una estrategia o EA. Representa el sistema base normalizado desde trades/instancias.
- `strategy_version`: version concreta de una estrategia, con `parameters_hash`, parametros JSON, referencia de backtest y expectativas de rendimiento.
- `strategy_instance`: ejecucion de una estrategia en una cuenta/plataforma/magic/simbolo. Ahora puede enlazarse opcionalmente a `strategy_version_id`, `risk_profile_id` y `deployment_state`.
- `risk_profile`: configuracion de riesgo esperada para una instancia, como tipo/valor de riesgo, max lots, perdida diaria maxima y drawdown maximo.
- `deployment_event`: evento de gobierno para registrar despliegues, cambios de estado, vinculacion de version/perfil y notas operativas.

Endpoints nuevos:

- `GET /api/strategy-versions`
- `POST /api/strategy-versions`
- `GET /api/risk-profiles`
- `POST /api/risk-profiles`
- `GET /api/deployment-events`
- `POST /api/deployment-events`

Cuando existe metadata de identidad, `/api/systems` y `/api/strategy-instances` incluyen:

```text
strategy_version_id, version_name, parameters_hash,
risk_profile_id, risk_profile_name, deployment_state
```

Crear versiones, perfiles y eventos registra auditoria en `audit_events`. Esta capa no ejecuta comandos ni modifica `commands.json`.

## Componentes MT4

- `mql4/Scripts/QA_TradeExporter.mq4`: respaldo manual para exportar operaciones cerradas a CSV.
- `mql4/Experts/QA_AccountMonitor.mq4`: exporta estado de cuenta, trades abiertos, EAs activos e historial cerrado.
- `mql4/Experts/QA_Commander.mq4`: lee `commands.json`, ejecuta o simula comandos y escribe `result.json`.

## Componentes MT5

- `mql5/Scripts/QA_TradeExporter.mq5`: respaldo manual para exportar historial cerrado al mismo esquema CSV interno.
- `mql5/Experts/QA_AccountMonitor.mq5`: usa posiciones MT5 y exporta historial cerrado CSV, `open_trades.json`, `account_history.json` y `running_eas.json` con `"platform": "MT5"`.
- `mql5/Experts/QA_Commander.mq5`: lee `commands.json`, ignora comandos de otra plataforma, ejecuta acciones por magic y escribe `result.json`.

## Carpeta Compartida

La configuracion actual usa:

```text
%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\QuantAnalyzer
```

Los componentes MQL deben usar:

```text
OutputFolder = QuantAnalyzer
UseCommonPath = true
```

`QA_Commander` debe usar:

```text
CommandsFolder = QuantAnalyzer
UseCommonPath = true
AutoExecute = false
```

## Instalacion MT4

1. Copia los archivos:

```text
mql4/Scripts/QA_TradeExporter.mq4 -> MQL4/Scripts
mql4/Experts/QA_AccountMonitor.mq4 -> MQL4/Experts
mql4/Experts/QA_Commander.mq4 -> MQL4/Experts
```

2. Reinicia MT4 o compila los archivos en MetaEditor.
3. Adjunta `QA_AccountMonitor` en cada terminal monitoreado y configura `AccountLabel` como `REAL` o `DEMO`.
4. Deja `ExportClosedTrades=true`; `ClosedTradesDaysBack=365` exporta el ultimo ano y `0` exporta todo el historial.
5. Adjunta `QA_Commander` solo donde quieras permitir comandos. Mantener `AutoExecute=false` para pruebas.

## Instalacion MT5

1. Copia los archivos:

```text
mql5/Scripts/QA_TradeExporter.mq5 -> MQL5/Scripts
mql5/Experts/QA_AccountMonitor.mq5 -> MQL5/Experts
mql5/Experts/QA_Commander.mq5 -> MQL5/Experts
```

2. Reinicia MT5 o compila los archivos en MetaEditor.
3. Adjunta `QA_AccountMonitor` en un grafico del terminal MT5 que quieras monitorear y configura `AccountLabel` como `REAL` o `DEMO`.
4. Deja `ExportClosedTrades=true`; `ClosedTradesDaysBack=365` exporta el ultimo ano y `0` exporta todo el historial.
5. Adjunta `QA_Commander` en el terminal MT5 donde quieras probar comandos.
6. Valida primero con `AutoExecute=false`; el EA escribira `result.json` sin ejecutar operaciones reales.

## Configuracion De Cuentas Multi-Cuenta

`config.json` define cada cuenta como entidad independiente. `id` puede ser positivo o negativo segun lo reporte MetaTrader. `type` acepta solo `REAL` o `DEMO`; `label` es el nombre visible en UI y `color` se usa en tarjetas y curvas comparativas:

```json
"accounts": [
  {
    "id": "123456",
    "type": "REAL",
    "label": "Broker A Principal",
    "platform": "MT4",
    "color": "#4CAF50"
  },
  {
    "id": "789012",
    "type": "DEMO",
    "label": "Pruebas Agresivas",
    "platform": "MT5",
    "color": "#2196F3"
  }
]
```

Los CSV se asocian automaticamente por nombre: `trades_REAL_123456.csv` o `trades_DEMO_789012.csv`. Para importacion automatica, la cuenta debe estar permitida por `config.json`, existir activa en la tabla `accounts` o haber sido aprobada desde `discovered_accounts`. Si aparece un CSV de una cuenta nueva, el backend no lo importa al portfolio principal: lo registra como `pending_approval`, emite warning/auditoria y lo expone en diagnosticos.

El parser tambien acepta etiquetas distintas a `REAL`/`DEMO` y cuentas negativas, por ejemplo:

```text
trades_TLXQ_-294886245.csv
```

La etiqueta del archivo ayuda a inferir metadata, pero la configuracion de `accounts` manda cuando existe una cuenta con el mismo `id`.

## Importacion Incremental De Trades Cerrados

Al iniciar el dashboard y en cada ciclo del scheduler, `app.py` lee `data_folder` desde `config.json` y busca archivos que cumplan el patron:

```text
trades_<label>_<account_id>.csv
```

El flujo normal es incremental:

- El scheduler corre cada `broker_sync_seconds` segundos, por defecto `60`.
- Primero sincroniza estado vivo con `sync_broker.py`.
- Luego ejecuta importacion incremental de `trades_*.csv`.
- Cada archivo se compara contra `trade_file_state` usando fecha de modificacion, tamano y hash de archivo.
- Si el archivo no cambio, no se reprocesa.
- Si el archivo cambio pero el hash real es igual, solo se actualiza el estado y no se reimporta.
- Si hay filas nuevas o modificadas, se normalizan, se registran en `raw_trade_imports` y se insertan en `closed_trades` con deduplicacion por `natural_key`/`hash_uniq`.
- Si se insertaron trades nuevos, se invalida/recalcula la cache del portfolio para que el dashboard los muestre sin recarga manual.

Comportamiento de API:

- `GET /api/portfolio` usa cache/SQLite para responder rapido.
- `GET /api/portfolio?reload=true` se mantiene como recarga manual de cache desde SQLite; no reconstruye desde CSV.
- Si hay varios CSV para la misma cuenta, el backend elige el mejor candidato segun coincidencia con `label`, `type`, `csv_pattern` y fecha de modificacion.
- Si un CSV pertenece a una cuenta no permitida, no se importa automaticamente y queda informado en `discovered_accounts`, `trade_file_state`, `/api/debug/trades`, `/api/data-quality` y `audit_events`.

## Auto-descubrimiento De Cuentas

El flujo seguro admite dos formas de autorizar cuentas:

1. Config manual via `config.json` y `python migrate_db.py`.
2. Auto-descubrimiento desde archivos MetaTrader y aprobacion manual desde dashboard/API.

Cuando el sistema detecta `trades_*.csv`, `account_history_*`, `open_trades_*` o `running_eas_*` de una cuenta no autorizada:

- crea o actualiza una fila en `discovered_accounts`;
- deja la cuenta en `pending_approval`;
- bloquea la importacion de sus trades;
- no mezcla esa cuenta con metricas globales;
- la muestra en `/api/debug/trades`, `/api/data-quality` y en la tarjeta "Cuentas detectadas pendientes" del dashboard.

Estados posibles:

- `pending_approval`: detectada y esperando decision.
- `approved`: aprobada; se crea/activa en SQLite `accounts` y el scheduler puede importarla.
- `rejected`: bloqueada de forma explicita.
- `ignored`: ignorada como archivo temporal o prueba.

Las cuentas desconocidas nunca se importan automaticamente al portfolio principal. Primero deben ser aprobadas.

Los archivos CSV no se versionan en GitHub porque pueden contener datos reales de trading.

## Ejecutar Dashboard

Instala dependencias:

```powershell
pip install -r requirements.txt
```

Inicia el servidor:

```powershell
python app.py
```

O usa:

```powershell
.\start.bat
```

Luego abre:

```text
http://localhost:5000
```

## Migracion SQLite

La base central vive en:

```text
%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\QuantAnalyzer\qa_portfolio.db
```

Ejecuta migraciones idempotentes:

```powershell
python migrate_db.py --db-path "%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\QuantAnalyzer\qa_portfolio.db"
```

Las migraciones no borran tablas legacy. `trades` se conserva y se migra hacia `closed_trades`. La deduplicacion usa `natural_key` y `hash_uniq` v2. Cada registro de `closed_trades` conserva trazabilidad hacia `raw_trade_imports` mediante `raw_import_id`, `import_run_id`, `source_file`, `source_row_hash` y `normalized_at`.

`hash_uniq` v2 considera:

- `platform`
- `account_key`
- `ticket/order/deal id`
- `magic`
- `symbol`
- `open_time`
- `close_time`
- `profit`
- `volume/lots`

Si una operacion reimportada tiene la misma `natural_key` pero cambia el hash v2, se registra en `trade_import_conflicts` y no se duplica el trade.

Tablas principales:

- `brokers`, `accounts`, `strategy_definitions`, `strategy_instances`, `strategies`
- `raw_trade_imports`: cada fila cruda importada desde CSV/JSON antes de normalizar.
- `closed_trades`: operaciones cerradas normalizadas, deduplicadas y trazables a raw/import.
- `open_positions`: estado vivo por cuenta/ticket/magic.
- `account_snapshots`, `equity_points`, `deposits_withdrawals`
- `strategy_metric_snapshots`: historico append-only de metricas, clasificacion y `rules_version`.
- `commands`, `command_executions`
- `import_runs`, `trade_file_state`, `trade_import_conflicts`, `audit_events`

Flujo persistente:

```text
CSV/JSON MetaTrader
  -> trade_file_state
  -> raw_trade_imports
  -> closed_trades / open_positions / account_snapshots / equity_points
  -> strategy_definitions + strategy_instances
  -> strategy_metric_snapshots
  -> /api/systems + dashboard
  -> commands + command_executions + audit_events
```

## Sincronizacion De Datos Vivos

`sync_broker.py` lee archivos generados por los monitores en la carpeta comun:

- `open_trades*.json/csv`
- `account_history*.json/csv`
- `trades_*.csv` para detectar movimientos no-trade cuando existan

El scheduler interno de `app.py` ejecuta sincronizacion cada `broker_sync_seconds` segundos, con valor por defecto `60`. En cada ciclo:

1. Sincroniza estado vivo con `sync_broker.sync_all()`.
2. Importa incrementalmente trades cerrados con `sync_closed_trades_incremental()`.
3. Si entraron trades nuevos, recalcula la cache del portfolio.

Persiste:

- posiciones vivas en `open_positions`
- snapshots de balance/equity/margen en `account_snapshots`
- curva granular en `equity_points`
- estado de archivos de trades en `trade_file_state`
- trades cerrados nuevos en `closed_trades`
- auditoria de cada corrida en `import_runs`

Puedes forzar una sincronizacion:

```powershell
curl -X POST http://localhost:5000/api/sync/broker
```

Ese endpoint tambien ejecuta la importacion incremental de trades cerrados y devuelve el bloque `closed_trade_sync` con archivos revisados, archivos cambiados, nuevos trades insertados, duplicados ignorados y errores.

Si defines `QA_API_KEY` o `api_key` en configuracion, los endpoints POST protegidos requieren header:

```text
X-API-Key: <clave>
```

### Chat IA

El panel de chat usa la API compatible con OpenAI de DeepSeek. Para habilitarlo, puedes configurar la clave como variable de entorno:

```powershell
$env:DEEPSEEK_API_KEY = "tu_api_key"
python app.py
```

En Windows tambien puedes dejar `DEEPSEEK_API_KEY` configurada como variable de entorno del sistema. Si no esta definida, el dashboard carga igual, pero `/api/chat` devolvera error de API key.

Tambien puedes dejarla en `config.json`:

```json
"deepseek_api_key": "tu_api_key",
"deepseek_base_url": "https://api.deepseek.com",
"deepseek_model": "deepseek-chat"
```

El backend prioriza `DEEPSEEK_API_KEY` y luego `deepseek_api_key`. Las claves no se exponen desde `GET /api/config`.

## Como Identifica Sistemas Globales

Un sistema/EA ya no pertenece a una sola cuenta. El dashboard lo identifica globalmente asi:

1. `magic` si es mayor que cero: `magic:12345`, agrupado entre todas las cuentas.
2. `comment` si no hay magic valido: `comment:nombre|account:<id>`, aislado por cuenta para evitar colisiones.
3. `symbol` como fallback, tambien aislado por cuenta.

Cada sistema contiene `instances`, una por cuenta donde aparezca ese EA, mas `metrics_global` y `metrics_per_account`.

Internamente, la base separa:

- `strategy_definitions`: estrategia logica, versionable e independiente de la cuenta.
- `strategy_instances`: ejecucion concreta de una estrategia en una cuenta, broker, plataforma, symbol, timeframe y perfil de riesgo.
- `strategies`: tabla legacy de compatibilidad, enlazada a `strategy_definition_id` y `strategy_instance_id`.

Reglas de migracion inicial:

- Si `magic > 0`, se crea/busca instancia por `account_key + platform + magic + symbol + timeframe`.
- Si no hay magic valido, se usa `comment` aislado por cuenta.
- Si no hay comment, se usa `symbol` aislado por cuenta.
- La agrupacion global por `magic` se mantiene para compatibilidad del dashboard.

## Metricas Y Acciones

El dashboard normaliza filas MT4 y MT5 con `normalize_trade(row, platform)` y calcula:

- cantidad de trades
- beneficio neto
- profit factor
- drawdown maximo
- pips promedio
- Sharpe simplificado
- win rate
- primera y ultima operacion
- expectancy, average trade, average win/loss, payoff ratio, rachas y ventanas recientes usadas por el detalle desplegable de estrategia

Luego clasifica cada sistema como:

- `revision_manual`
- `cuarentena`
- `reducir_riesgo`
- `aumentar_riesgo`
- `mantener`
- `insuficiente_data`

Los umbrales se configuran en `config.json` bajo `risk_thresholds`.

`revision_manual` se usa cuando la rentabilidad cerrada es visible, pero las metricas de riesgo no son confiables. El caso principal es identidad partida entre trades y equity/snapshots, por ejemplo trades bajo `server=unknown` y curva de equity bajo `server=Darwinex-Demo`. En ese estado, `max_drawdown_pct` y `sharpe` se devuelven como `null`, `risk_metrics_reliable=false` y el dashboard bloquea comandos de riesgo.

Reglas actuales de KPIs:

- `profit`/`net_profit` son la fuente confiable para P&L cerrado, win rate, expectancy y profit factor.
- Los breakdowns reconstruyen `gross_profit` y `gross_loss` desde `profit`, no desde las columnas crudas de `closed_trades`, porque esas columnas pueden contener `0.0` importado.
- `average_holding_hours` excluye operaciones con `close_time <= open_time`.
- `pnl_curve_*` conserva el drawdown porcentual de la curva aislada de PnL del sistema. Es analitico, no riesgo real de cuenta.
- La curva visible del detalle de estrategia se construye desde trades cerrados y P&L acumulado. Sirve para inspeccionar trayectoria, recuperaciones y deterioro reciente, pero no reemplaza `balance_*`.
- `balance_*` conserva el drawdown real de capital usando `balance_after` o equity confiable. Es la base para decisiones operativas.
- `compare_against_expected()` compara DD real (`balance_current_drawdown_pct` / `balance_max_drawdown_pct`) contra `strategy_expected_metrics.expected_max_drawdown_pct`.
- Si no existe DD real confiable (`balance_drawdown_reliable != true`), el sistema queda en `revision_manual` y no se deben exportar comandos de riesgo.
- Drawdown operativo y Sharpe son informativos solo cuando las metricas de riesgo son confiables; si no, se muestran como `n/a` o `revision_manual`.

## Comandos

Desde el dashboard se pueden generar comandos:

- `close_by_magic`
- `reduce_lots`
- `increase_lots_25`
- `increase_lots_50`
- `set_max_lots`

`app.py` crea cada comando primero en SQLite (`commands`) y luego exporta `commands.json` solo como transporte compatible con MetaTrader. Cada comando incluye `command_id`, `sent_to_ea_at`, `account_key/account_id` y estado durable:

```json
{
  "command_id": 123,
  "system": "magic:12345",
  "magic": 12345,
  "action": "close_by_magic",
  "account_composite_key": "MT5_Darwinex_Demo_789012_demo",
  "account_id": "789012",
  "platform": "MT5",
  "status": "sent",
  "sent_to_ea_at": "2026-06-22T01:00:00+00:00"
}
```

Cada Commander ignora comandos que no correspondan a su `platform` o a su numero de cuenta. Tambien ignora comandos sin `command_id` y guarda los IDs procesados en `processed_MT4_<login>.txt` o `processed_MT5_<login>.txt` para impedir ejecucion repetida aunque el JSON no cambie.

Los resultados se escriben como `result_<login>.json` y el backend los sincroniza hacia `commands` y `command_executions`.

Antes de exportar un comando a `commands.json`, el backend valida:

- la cuenta existe en SQLite;
- la plataforma solicitada coincide con la instancia de la cuenta;
- el `magic` existe en esa cuenta;
- hay posiciones abiertas para ese `magic`;
- no existe un comando activo equivalente para la misma cuenta, sistema y accion;
- las acciones `increase_lots_25`, `increase_lots_50` y `set_max_lots` requieren `confirm=true` en cuentas REAL;
- no se permite aumentar riesgo si la instancia o el sistema global esta en `cuarentena`.
- no se exportan comandos si el sistema esta en `revision_manual` o si `risk_metrics_reliable=false`.
- no se toman decisiones de riesgo por DD si `drawdown_basis` no es `balance_after` o equity real confiable.

Todo comando rechazado queda registrado en `audit_events` como evento de comando fallido con `reason` en el payload. Para cuentas reales, mantÃ©n `AutoExecute=false` hasta validar manualmente el circuito completo de `commands.json`, `processed_<platform>_<login>.txt` y `result_<login>.json`.

## API Multi-Cuenta

- `GET /api/portfolio`: portfolio completo desde cache/SQLite; acepta `?reload=true` para recargar cache desde SQLite. No renderiza desde CSV.
- `GET /api/portfolio/summary` o `GET /api/summary`: resumen consolidado.
- `GET /api/accounts`: cuentas con P&L, equity y sistemas activos.
- `POST /api/accounts`: registra/actualiza cuenta en SQLite usando API key.
- `GET /api/accounts/discovered`: lista cuentas auto-detectadas; acepta `status=pending_approval|approved|rejected|ignored`.
- `POST /api/accounts/discovered/<id>/approve`: aprueba una cuenta descubierta, crea/activa `accounts` e intenta importar sus archivos.
- `POST /api/accounts/discovered/<id>/reject`: rechaza una cuenta descubierta para que no se importe automaticamente.
- `POST /api/accounts/discovered/<id>/ignore`: ignora una cuenta descubierta sin marcarla como rechazo definitivo.
- `GET /api/accounts/<id>/systems`: sistemas presentes en una cuenta.
- `GET /api/accounts/<id>/equity`: puntos de equity persistidos en SQLite.
- `GET /api/accounts/<id>/snapshots`: snapshots historicos de cuenta.
- `GET /api/accounts/<id>/open-positions`: posiciones abiertas actuales.
- `GET /api/systems`: sistemas globales con metricas consolidadas. Acepta filtros `account_id`, `account_key`, `account_type` y `action`.
- `POST /api/sync/trades`: importa incrementalmente `trades_*.csv` hacia SQLite y recarga el dashboard desde SQLite.
- `GET /api/systems/<magic>`: detalle de sistema con breakdown por cuenta.
- `GET /api/strategies`: definiciones logicas de estrategias.
- `GET /api/strategy-instances`: instancias operativas; acepta `strategy_id`.
- `GET /api/strategy-metrics`: snapshots historicos de metricas y decisiones; acepta `strategy_id`, `instance_id`, `account_key`, `account_id` y `limit`; requiere `X-API-Key` si `QA_API_KEY` o `api_key` esta configurado.
- `POST /api/command`: valida reglas operacionales, crea un comando durable en SQLite y actualiza `commands.json`; requiere `X-API-Key` si `QA_API_KEY` o `api_key` esta configurado.
- `POST /api/command/ack`: acuse de comando; requiere API key si `QA_API_KEY` esta configurado.
- `POST /api/command/result`: resultado final de comando; requiere API key si `QA_API_KEY` esta configurado.
- `GET /api/commands`: lista comandos activos desde SQLite.
- `GET /api/commands/history`: historial de comandos desde SQLite.
- `POST /api/commands/clear`: expira comandos activos y reescribe `commands.json`.
- `GET /api/result`: lee `result.json` generado por `QA_Commander`.
- `POST /api/sync/broker`: ejecuta sincronizacion broker-local de posiciones, snapshots, equity e importacion incremental de trades cerrados; devuelve `closed_trade_sync`.
- `GET /api/config`: configuracion activa sin claves sensibles.
- `POST /api/config`: actualiza `risk_thresholds`.
- `POST /api/chat`: streaming SSE del chat IA con contexto del portfolio.
- `GET /api/debug/trades`: diagnostico de CSV/SQLite, ultimo resumen incremental, `trade_file_state`, `discovered_accounts`, archivos bloqueados, warnings y errores.
- `GET /api/debug/accounts`: diagnostico de archivos de estado por cuenta.
- `GET /api/debug/raw-imports`: auditoria de filas crudas importadas; acepta `import_run_id` y `limit`.
- `GET /api/debug/trade-conflicts`: conflictos de deduplicacion detectados durante importaciones.
- `GET /api/audit-events`: auditoria generica; acepta `event_type`, `entity_type`, `entity_id`, `account_key`, `severity` y `limit`; requiere `X-API-Key` si `QA_API_KEY` o `api_key` esta configurado.
- `GET /api/data-quality`: diagnostico de salud de datos; revisa duplicados, cuentas desconocidas, cuentas descubiertas pendientes/rechazadas/ignoradas, magics sin estrategia, posiciones antiguas, CSVs viejos, estado incremental de archivos, gaps de equity, snapshots nulos, comandos antiguos e imports fallidos; requiere API key si esta configurada.
- `GET /api/pnl-breakdown`: desglose de P&L y costos por cuenta, broker, simbolo, estrategia e instancia; requiere API key si esta configurada.
- `GET /api/temporal-analysis`: rendimiento por dia, hora, sesion, mes, simbolo, cuenta, estrategia e instancia; requiere API key si esta configurada.

## Validacion Recomendada

1. Crea `config.json` desde `config.example.json` y apunta `data_folder` a la carpeta comun de MetaTrader.
2. Adjunta `QA_AccountMonitor` en MT4/MT5 y confirma que genera `trades_<label>_<account_id>.csv`.
3. Ejecuta `python migrate_db.py --db-path "<ruta>\qa_portfolio.db"` y confirma que las migraciones son idempotentes.
4. Abre `http://localhost:5000` y verifica que el dashboard carga desde SQLite/cache.
5. Fuerza `POST /api/sync/broker` o espera el ciclo del scheduler; revisa `closed_trade_sync` en la respuesta o `GET /api/debug/trades`.
6. Confirma que `trade_file_state` registra los `trades_*.csv` y que `closed_trades` queda poblada sin duplicados.
7. Revisa `GET /api/accounts/<account_key>/equity`, `/snapshots` y `/open-positions`.
8. Verifica que el dashboard agrupa como un solo sistema global los trades con el mismo magic number en distintas cuentas.
9. Confirma que `conflicto` aparece cuando una instancia esta en `cuarentena` pero el global esta en `mantener`.
10. Adjunta `QA_Commander` con `AutoExecute=false`.
11. Envia `close_by_magic` desde la instancia de una cuenta especifica y revisa que SQLite cree un comando con `command_id`.
12. Confirma que `commands.json` incluye `command_id` y `sent_to_ea_at`, y que el EA crea `processed_<platform>_<login>.txt`.
13. Cambia `AutoExecute=true` solo despues de confirmar el flujo completo.

## Guia De Migracion Desde Version Anterior

1. Haz respaldo de la carpeta comun de MetaTrader, especialmente `trades_*.csv`, `account_history*`, `open_trades*`, `commands.json` y `qa_portfolio.db` si ya existe.
2. Instala Python 3.10+ desde `python.org`, con `Add Python to PATH`, y verifica `python --version`.
3. Instala dependencias con `pip install -r requirements.txt`.
4. Ejecuta:

```powershell
python migrate_db.py --db-path "%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\QuantAnalyzer\qa_portfolio.db"
```

5. Inicia `python app.py` y espera el ciclo del scheduler, o ejecuta `POST /api/sync/broker` para disparar sincronizacion inmediata.
6. Revisa `GET /api/debug/trades` y confirma `trade_file_state`, archivos revisados y `new_trades_inserted`.
7. Usa `POST /api/sync/trades` para importar CSV hacia SQLite. Usa `GET /api/portfolio?reload=true` solo para recargar cache desde SQLite.
8. Revisa `GET /api/data-quality` y corrige primero duplicados, cuentas desconocidas, archivos de trades ignorados, imports fallidos y CSVs antiguos.
9. Revisa `GET /api/audit-events?limit=50` para confirmar imports, snapshots y comandos.
10. MantÃ©n los CSV/JSON existentes como capa de ingesta. El dashboard lee desde SQLite como fuente unica.
11. En cuentas REAL, deja `QA_Commander` con `AutoExecute=false` hasta verificar que los comandos rechazados y aceptados quedan auditados correctamente.

## Notas De Seguridad

- Ejecuta `QA_Commander` con `AutoExecute=false` hasta validar el flujo completo.
- No se ejecutan comandos sin `command_id`.
- Un mismo `command_id` no se ejecuta dos veces porque cada Commander mantiene registro local de procesados.
- `reduce_lots` cierra y reabre con otro lote si `AutoExecute=true`.
- `increase_lots_25`, `increase_lots_50` y `set_max_lots` tambien cierran y reabren posiciones con volumen ajustado.
- La ejecucion por `magic` es mas segura que por comentario.
- En cuentas REAL, los aumentos de riesgo requieren confirmacion explicita y nunca se exportan si el sistema esta en `cuarentena`.

## Metricas y Reglas

Cada recalc del portfolio guarda un snapshot append-only en `strategy_metric_snapshots` para conservar la decision historica del sistema o instancia. La version actual del motor de clasificacion es `v1.0` y se devuelve en `/api/systems` como `rules_version`.

Campos principales persistidos:

- `strategy_id`, `instance_id`, `account_key`
- `trades`, `net_profit`, `gross_profit`, `gross_loss`, `profit_factor`
- `max_drawdown`, `max_drawdown_pct`, `win_rate`, `expectancy`, `avg_trade`, `sharpe`, `pips_avg`
- `pnl_curve_*`: curva de PnL aislada; no usar como riesgo real.
- `balance_*`, `balance_drawdown_reliable`, `drawdown_basis`: DD real de capital para decisiones operativas.
- `classification`, `recommended_action`, `rules_version`, `payload_json`

Regla operativa de DD:

- `max_drawdown_pct` y `current_drawdown_pct` deben representar DD real cuando existe `balance_after`/equity confiable.
- El DD porcentual sobre PnL acumulado vive en `pnl_curve_max_drawdown_pct` y `pnl_curve_current_drawdown_pct`.
- `strategy_expected_metrics.expected_max_drawdown_pct` debe estar en la misma base real de capital si se usa para decisiones de riesgo.

## Auditoria

`audit_events` guarda eventos transversales del sistema con `event_type`, entidad afectada, cuenta, severidad, mensaje, payload JSON y timestamp. Actualmente se registran imports, warnings de calidad de datos, conflictos de deduplicacion, creacion de instancias, snapshots de metricas, comandos y cambios de configuracion.

Eventos principales:

- `import_started`, `import_completed`, `import_error`
- `duplicate_trade_detected`, `unknown_account_detected`, `trade_file_ignored`, `data_quality_warning`
- `strategy_instance_created`, `metric_snapshot_created`
- `command_created`, `command_acknowledged`, `command_executed`, `command_failed`
- `config_updated`

## Calidad de Datos y Analisis Financiero

`/api/data-quality` devuelve:

```json
{
  "status": "ok|warning|critical",
  "checks": [
    {"name": "duplicate_trades", "status": "ok", "count": 0, "message": "No duplicate trades detected"}
  ]
}
```

Tambien incluye `trade_file_state`, que advierte si un archivo de `trades_*.csv` fue ignorado por cuenta no permitida o fallo durante la importacion incremental, y `stale_running_imports`, que marca como criticas las importaciones en estado `running` por mas de 30 minutos.

`closed_trades` separa componentes de P&L: `gross_profit`, `gross_loss`, `commission`, `swap`, `fees`, `spread_cost_estimated`, `slippage_estimated` y `net_profit`. Las metricas principales usan `net_profit`/`profit` para mantener consistencia con resultados reales despues de costos.

Los endpoints de desglose no confian en `closed_trades.gross_profit` ni `closed_trades.gross_loss` cuando calculan P&L agregado. Reconstruyen esos campos desde `profit` para evitar que valores `0.0` importados contaminen profit factor, average win/loss o desgloses por simbolo:

```sql
CASE WHEN profit > 0 THEN profit ELSE 0.0 END
CASE WHEN profit < 0 THEN ABS(profit) ELSE 0.0 END
```

`/api/temporal-analysis` normaliza el analisis sobre `close_time` en UTC y agrupa por `by_weekday`, `by_hour`, `by_session`, `by_month`, `by_symbol`, `by_account`, `by_strategy` y `by_instance`.

`/api/pnl-breakdown` usa la misma reconstruccion de gross profit/loss y agrupa costos por cuenta, broker, simbolo, estrategia e instancia.

## Indices de Performance

La migracion crea indices idempotentes para consultas frecuentes:

- `closed_trades(account_id, close_time)`
- `strategies(magic_number, account_id)`
- `closed_trades(strategy_id, close_time)`
- `equity_points(account_id, timestamp)`
- `open_positions(account_id, magic_number)`
- `commands(account_composite_key, status)`
- `strategy_metric_snapshots(instance_id, calculated_at)`

