@echo off
chcp 65001 >nul
title Reiniciar Antigravity (1-Clique)
echo ========================================================
echo   Reiniciando Google Antigravity com a Conta Ativa...
echo ========================================================
echo.
echo Fechando processos antigos e reabrindo Antigravity...
powershell -NoProfile -Command "Get-Process Antigravity, language_server -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Milliseconds 800; Start-Process (Join-Path $env:LOCALAPPDATA 'Programs\antigravity\Antigravity.exe')"
echo.
echo Antigravity reiniciado com sucesso!
timeout /t 3 >nul
