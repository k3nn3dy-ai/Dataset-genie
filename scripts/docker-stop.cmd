@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0docker-stop.ps1" %*
pause
