@echo off
rem Launcher for Steam Metadata Editor.
rem Works from any folder / PC: it always uses its own location.
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw src\main.py
    exit /b
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python src\main.py
    exit /b
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" py src\main.py
    exit /b
)

echo Python was not found on this PC.
echo Install it from https://www.python.org/downloads/
echo and make sure "Add python.exe to PATH" is checked during setup.
pause
