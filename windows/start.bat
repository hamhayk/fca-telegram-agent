@echo off
REM Runs the agent and restarts it automatically if it stops. Close this window to stop it.
title Fca Soft Telegram Agent
cd /d "%~dp0.."
if not exist data mkdir data
:loop
.venv\Scripts\python.exe run.py >> data\agent.log 2>&1
echo %date% %time% Agent stopped, restarting in 10 seconds... >> data\agent.log
timeout /t 10 /nobreak >nul
goto loop
