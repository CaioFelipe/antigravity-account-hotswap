@echo off
chcp 65001 >nul
title Recarregar Janela do Antigravity
echo ========================================================
echo   Recarregando Janela do Google Antigravity...
echo ========================================================
python "%~dp0core\ide_reloader.py"
echo.
echo Dica: Você também pode pressionar F1 no Antigravity e digitar "Reload Window".
echo.
timeout /t 3 >nul
