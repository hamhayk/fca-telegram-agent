@echo off
schtasks /delete /f /tn "FcaSoftTelegramAgent" && echo Autostart is off.
call "%~dp0stop.bat"
pause
