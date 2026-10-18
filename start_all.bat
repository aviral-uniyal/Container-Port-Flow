@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "windows_startup.py" start
) else (
  where python >nul 2>nul || (echo Python is not installed or is not on PATH. & exit /b 1)
  python "windows_startup.py" start
)
if errorlevel 1 (
  echo Startup stopped. See the message above.
  if not defined CPF_NO_PAUSE pause
  exit /b 1
)
exit /b 0
