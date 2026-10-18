@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "windows_startup.py" check
) else (
  where python >nul 2>nul || (echo Python is not installed or is not on PATH. & exit /b 1)
  python "windows_startup.py" check
)
exit /b %errorlevel%
