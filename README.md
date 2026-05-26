# QA Portfolio Commander

Dashboard local para analizar cuentas de trading en MetaTrader 4 y MetaTrader 5, clasificar sistemas por rendimiento y enviar comandos de gestion de riesgo hacia MetaTrader.

El sistema usa archivos CSV/JSON en la carpeta comun de MetaTrader. No requiere modificar los EAs de trading existentes.

## Flujo De Trabajo

```text
MT4 terminal
  QA_TradeExporter.mq4   -> trades_<label>_<account_id>.csv
  QA_AccountMonitor.mq4  -> running_eas, account_history, open_trades
  QA_Commander.mq4       <- commands.json
                          -> result.json

MT5 terminal
  QA_TradeExporter.mq5   -> trades_<label>_<account_id>.csv
  QA_AccountMonitor.mq5  -> running_eas.json, account_history.json, open_trades.json
  QA_Commander.mq5       <- commands.json
                          -> result.json

Python Flask
  app.py                 -> lee trades_<label>_<account_id>.csv
                          -> normaliza MT4/MT5
                          -> calcula metricas y alertas
                          -> sirve el dashboard
                          -> escribe commands.json
                          -> expone chat IA con DeepSeek
```

## Componentes Activos

- `app.py`: servidor Flask local y API del dashboard.
- `templates/index.html`: interfaz web.
- `config.json`: ruta de datos, cuentas, plataformas, umbrales, filtros y puerto.
- `requirements.txt`: dependencias Python.
- `start.bat`: inicio rapido en Windows; instala dependencias locales si faltan.

## Componentes MT4

- `mql4/Scripts/QA_TradeExporter.mq4`: exporta operaciones cerradas a CSV.
- `mql4/Experts/QA_AccountMonitor.mq4`: exporta estado de cuenta, trades abiertos y EAs activos.
- `mql4/Experts/QA_Commander.mq4`: lee `commands.json`, ejecuta o simula comandos y escribe `result.json`.

## Componentes MT5

- `mql5/Scripts/QA_TradeExporter.mq5`: usa `HistorySelect()` y `HistoryDealGetTicket()` para exportar historial cerrado al mismo esquema CSV interno.
- `mql5/Experts/QA_AccountMonitor.mq5`: usa posiciones MT5 y exporta `open_trades.json`, `account_history.json` y `running_eas.json` con `"platform": "MT5"`.
- `mql5/Experts/QA_Commander.mq5`: lee `commands.json`, ignora comandos de otra plataforma, ejecuta acciones por magic y escribe `result.json`.

## Carpeta Compartida

La configuracion actual usa:

```text
C:\Users\marin\AppData\Roaming\MetaQuotes\Terminal\Common\Files\QuantAnalyzer
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
3. Ejecuta `QA_TradeExporter` y configura `AccountLabel` como `REAL` o `DEMO`.
4. Adjunta `QA_AccountMonitor` en cada terminal monitoreado.
5. Adjunta `QA_Commander` solo donde quieras permitir comandos. Mantener `AutoExecute=false` para pruebas.

## Instalacion MT5

1. Copia los archivos:

```text
mql5/Scripts/QA_TradeExporter.mq5 -> MQL5/Scripts
mql5/Experts/QA_AccountMonitor.mq5 -> MQL5/Experts
mql5/Experts/QA_Commander.mq5 -> MQL5/Experts
```

2. Reinicia MT5 o compila los archivos en MetaEditor.
3. Ejecuta `QA_TradeExporter` y configura `AccountLabel` como `REAL` o `DEMO`.
4. Adjunta `QA_AccountMonitor` en un grafico del terminal MT5 que quieras monitorear.
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

Los CSV se asocian automaticamente por nombre: `trades_REAL_123456.csv` o `trades_DEMO_789012.csv`. Si aparece un CSV cuya cuenta no esta en `config.json`, el backend lo carga con metadata provisional y emite warning.

El parser tambien acepta etiquetas distintas a `REAL`/`DEMO` y cuentas negativas, por ejemplo:

```text
trades_TLXQ_-294886245.csv
```

La etiqueta del archivo ayuda a inferir metadata, pero la configuracion de `accounts` manda cuando existe una cuenta con el mismo `id`.

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

Luego clasifica cada sistema como:

- `cuarentena`
- `reducir_riesgo`
- `aumentar_riesgo`
- `mantener`
- `insuficiente_data`

Los umbrales se configuran en `config.json` bajo `risk_thresholds`.

## Comandos

Desde el dashboard se pueden generar comandos:

- `close_by_magic`
- `reduce_lots`
- `increase_lots_25`
- `increase_lots_50`
- `set_max_lots`

`app.py` escribe esos comandos en `commands.json` e incluye:

```json
{
  "system": "magic:12345",
  "magic": 12345,
  "action": "close_by_magic",
  "account_id": "789012",
  "platform": "MT5"
}
```

Cada Commander ignora comandos que no correspondan a su `platform` o a su numero de cuenta.

## API Multi-Cuenta

- `GET /api/portfolio`: portfolio completo; acepta `?reload=true`.
- `GET /api/portfolio/summary` o `GET /api/summary`: resumen consolidado.
- `GET /api/accounts`: cuentas con P&L, equity y sistemas activos.
- `GET /api/accounts/<id>/systems`: sistemas presentes en una cuenta.
- `GET /api/systems`: sistemas globales con metricas consolidadas. Acepta filtros `account_id`, `account_type` y `action`.
- `GET /api/systems/<magic>`: detalle de sistema con breakdown por cuenta.
- `POST /api/command`: crea un comando en `commands.json`.
- `GET /api/commands`: lista comandos pendientes/registrados en memoria.
- `POST /api/commands/clear`: limpia el registro de comandos y reescribe `commands.json`.
- `GET /api/result`: lee `result.json` generado por `QA_Commander`.
- `GET /api/config`: configuracion activa sin claves sensibles.
- `POST /api/config`: actualiza `risk_thresholds`.
- `POST /api/chat`: streaming SSE del chat IA con contexto del portfolio.
- `GET /api/debug/trades`: diagnostico de CSV cargados, warnings y errores.
- `GET /api/debug/accounts`: diagnostico de archivos de estado por cuenta.

## Validacion Recomendada

1. Revisa los CSV de ejemplo en `test_data/QuantAnalyzer`: contienen el mismo `magic:12345` en tres cuentas.
2. Verifica que el dashboard muestra un solo sistema global con tres instancias.
3. Confirma que `conflicto` aparece cuando una instancia esta en `cuarentena` pero el global esta en `mantener`.
4. En MT5, adjunta `QA_AccountMonitor.mq5` y ejecuta `QA_TradeExporter.mq5`.
5. Adjunta `QA_Commander.mq5` con `AutoExecute=false`.
6. Envia `close_by_magic` desde la instancia de una cuenta especifica y revisa que `commands.json` incluya `account_id`.
7. Cambia `AutoExecute=true` solo despues de confirmar el flujo completo.

## Notas De Seguridad

- Ejecuta `QA_Commander` con `AutoExecute=false` hasta validar el flujo completo.
- `reduce_lots` cierra y reabre con otro lote si `AutoExecute=true`.
- `increase_lots_25`, `increase_lots_50` y `set_max_lots` tambien cierran y reabren posiciones con volumen ajustado.
- La ejecucion por `magic` es mas segura que por comentario.
