@echo off
setlocal
cd /d "%~dp0"
where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
    start "N1MM PingPong" pythonw.exe n1mm_watch.py %*
) else (
    start "N1MM PingPong" python.exe n1mm_watch.py %*
)