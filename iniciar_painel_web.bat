@echo off
cd /d "%~dp0"
title Central de Hot-Swap de Contas - Antigravity (Web)
cls
echo ====================================================================
echo      Central de Hot-Swap de Contas - Antigravity
echo ====================================================================
echo.
echo Iniciando servidor local na porta 5055...
echo.
start "" http://127.0.0.1:5055
python "web\server.py"
pause
