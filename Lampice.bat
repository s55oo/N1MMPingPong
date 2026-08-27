@echo off
setlocal
cd /d "%~dp0"
where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
    start "PingPong lučke" pythonw.exe n1mm_lamps.py %*
) else (
    start "PingPong lučke" python.exe n1mm_lamps.py %*
)