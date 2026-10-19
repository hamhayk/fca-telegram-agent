@echo off
REM Starts the agent automatically every time you sign in to Windows (hidden window).
cd /d "%~dp0"
schtasks /create /f /tn "FcaSoftTelegramAgent" /sc onlogon /rl limited /tr "conhost.exe --headless \"%~dp0start.bat\"" && echo Autostart is on. It starts at your next sign-in; run start.bat to start it now.
pause
