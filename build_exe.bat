@echo off
rem Build n1mm_lamps.exe from n1mm_lamps.py (double-click this file).
cd /d "%~dp0"

echo Checking PyInstaller...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto :error
)

echo Building n1mm_lamps.exe ...
python -m PyInstaller --noconfirm --onefile --windowed --noupx --name n1mm_lamps --manifest manifest.xml --icon pingpong.ico n1mm_lamps.py
if errorlevel 1 goto :error

copy /Y dist\n1mm_lamps.exe n1mm_lamps.exe >nul
if errorlevel 1 (
    echo.
    echo Could not replace n1mm_lamps.exe - is the lamp still running? Close it and try again.
    goto :end
)

echo.
echo Done: n1mm_lamps.exe has been updated.
goto :end

:error
echo.
echo Build FAILED - see the messages above.

:end
pause
