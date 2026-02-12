@echo off
setlocal
cd /d "%~dp0"
cd ..

echo [FinanceBot] Iniciando em modo servidor...
set "PY_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"
echo [FinanceBot] Python: %PY_EXE%
"%PY_EXE%" main.py --server --no-browser
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [FinanceBot] Falha ao iniciar. Codigo: %RC%
  echo [FinanceBot] Verifique os erros exibidos acima.
  pause
)

endlocal
exit /b %RC%
