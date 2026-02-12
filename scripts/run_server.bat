@echo off
setlocal
cd /d "%~dp0"
cd ..

echo [FinanceBot] Iniciando em modo servidor...
python main.py --server --no-browser

endlocal
