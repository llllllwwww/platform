@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Open index.html directly, or install Python 3.
  pause
  exit /b 1
)
python "启动平台.py"
if errorlevel 1 pause
