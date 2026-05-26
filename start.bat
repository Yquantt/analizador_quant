@echo off
setlocal
title QA Portfolio Commander

cd /d "%~dp0"

echo.
echo  ========================================
echo   QA Portfolio Commander - Iniciando...
echo  ========================================
echo.

set "PYTHON_CMD="
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
        set "PYTHON_CMD=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    )
)

if not defined PYTHON_CMD (
    echo ERROR: Python no encontrado. Instala Python 3.10+ desde python.org
    pause
    exit /b 1
)

echo Python:
%PYTHON_CMD% --version
echo.

if not defined DEEPSEEK_API_KEY (
    echo AVISO: DEEPSEEK_API_KEY no esta configurada.
    echo        El dashboard funcionara, pero el chat IA devolvera error hasta configurarla.
    echo        Configurala en variables de entorno de Windows como DEEPSEEK_API_KEY.
    echo.
)

echo Verificando dependencias...
if not exist ".python_packages" mkdir ".python_packages"
set "PYTHONPATH=%CD%\.python_packages;%PYTHONPATH%"

%PYTHON_CMD% -c "import flask, pandas, flask_cors, openai" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias locales...
    %PYTHON_CMD% -m pip install -q -r requirements.txt --target ".python_packages"
    if errorlevel 1 (
        echo.
        echo ERROR: No se pudieron instalar las dependencias.
        pause
        exit /b 1
    )
)

echo.
echo  Servidor iniciado en: http://localhost:5000
echo  Presiona Ctrl+C para detener
echo.

start "" /B cmd /C "timeout /T 2 /NOBREAK >nul && start http://localhost:5000"

%PYTHON_CMD% app.py

echo.
echo Servidor detenido.
pause
