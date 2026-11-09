@echo off
rem Double-click: open the drag-and-drop app.
rem Drop a .blend file on this icon: film it with default settings.
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo First run: setting up...
  python -m venv "%ROOT%.venv" || goto :fail
  "%ROOT%.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%ROOT%requirements.txt" || goto :fail
)
cd /d "%ROOT%"
"%ROOT%.venv\Scripts\python.exe" -m mousestorm %*
if errorlevel 1 pause
exit /b

:fail
echo Setup failed. Python 3.10+ must be on your PATH.
pause
exit /b 1
