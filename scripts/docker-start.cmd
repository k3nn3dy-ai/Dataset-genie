@echo off
rem Double-click helper for Windows: runs docker-start.ps1 without changing the execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0docker-start.ps1"
pause
