@echo off
REM Stops the running agent, including one started hidden by autostart.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*telegram-agent*start.bat*' -or ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*run.py*' -and $_.ExecutablePath -like '*telegram-agent*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Agent stopped.
