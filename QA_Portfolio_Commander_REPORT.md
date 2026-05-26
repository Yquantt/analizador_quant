# QA Portfolio Commander - Reporte de Preparacion GitHub

Fecha: 2026-05-26

## Estado general

El proyecto quedo preparado para un primer commit seguro en GitHub desde el punto de vista de archivos sensibles, configuracion local y datos generados.

## Cambios realizados

- Se creo `.gitignore` en la raiz del proyecto.
- Se agrego `config.json` al `.gitignore` para evitar subir rutas locales y datos reales de cuentas.
- Se creo `config.example.json` como plantilla segura para el repositorio.
- Se excluyeron datos MetaTrader, binarios compilados, logs, caches, entornos locales y archivos runtime.

## Archivos que si deberian subir

### Codigo Python

- `app.py`

### Templates HTML

- `templates/index.html`

### Archivos MQL4

- `mql4/Experts/QA_AccountMonitor.mq4`
- `mql4/Experts/QA_Commander.mq4`
- `mql4/Scripts/QA_TradeExporter.mq4`

### Archivos MQL5

- `mql5/Experts/QA_AccountMonitor.mq5`
- `mql5/Experts/QA_Commander.mq5`
- `mql5/Scripts/QA_TradeExporter.mq5`

### Configuracion de ejemplo

- `config.example.json`

### Documentacion y soporte

- `README.md`
- `requirements.txt`
- `start.bat`
- `.gitignore`
- `QA_Portfolio_Commander_REPORT.md`

## Archivos y carpetas excluidos

- `config.json`
- `*.csv`
- `*.ex4`
- `*.ex5`
- `*.log`
- `test_data/`
- `_archivo/`
- `.venv/`
- `.python_packages/`
- `.pip-cache/`
- `.tmp/`
- `__pycache__/`
- `commands.json`
- `result.json`
- `account_history*.json`
- `open_trades*.json`
- `running_eas*.json`

## Verificacion de seguridad

- `config.json` no debe subir al repo.
- `config.example.json` contiene placeholders y si debe subir.
- Los CSV de operaciones reales/demo estan ignorados.
- Los binarios compilados MetaTrader `.ex4` y `.ex5` estan ignorados.
- Los logs y archivos runtime estan ignorados.

## Bloqueos para subir a GitHub

Durante la preparacion se detectaron dos bloqueos locales:

- `git` no esta disponible en el PATH de Windows.
- `gh` esta instalado, pero la sesion activa tiene token invalido.

Comando recomendado para reautenticar GitHub CLI:

```powershell
gh auth login -h github.com
```

Tambien se debe instalar Git o agregarlo al PATH antes de ejecutar el flujo normal de `git init`, `git add`, `git commit` y `git push`.

## Repositorios disponibles detectados

El conector GitHub detecto acceso a:

- `Yquantt/auditor_trading`
- `Yquantt/formulario-taller`
- `Yquantt/mi-proyecto`

## Proximo paso recomendado

Una vez disponible `git` y reautenticado `gh`, ejecutar el commit inicial con los archivos permitidos por `.gitignore` y subirlo al repositorio GitHub elegido.
