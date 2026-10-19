@echo off
REM One-time setup on Windows: creates the Python environment and the .env file.
cd /d "%~dp0.."
where python >nul 2>nul || (echo Python is not installed. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || (pause & exit /b 1)
if not exist .env (
  copy .env.example .env >nul
  echo Fill in BOT_TOKEN and ADMIN_IDS, save, and close Notepad.
  notepad .env
)
echo.
echo Setup done. Start the agent with windows\start.bat
pause
