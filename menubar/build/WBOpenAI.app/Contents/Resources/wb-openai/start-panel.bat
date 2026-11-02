@echo off
rem wb-openai control panel one-click launcher (Windows)
rem Double-click this file: prepares dependencies, starts panel, opens browser.
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py
if "%PY%"=="" (where python >nul 2>nul && set PY=python)

if "%PY%"=="" (
  echo Python not found. Please install Python 3.8+ first:
  echo   https://www.python.org/downloads/
  echo Remember to check "Add Python to PATH" during install.
  pause
  exit /b 1
)

%PY% bootstrap.py wb_panel.py %*
pause
