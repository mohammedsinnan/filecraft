@echo off
setlocal
cd /d "%~dp0"
set "APP_PYTHON=.venv\Scripts\python.exe"
if exist "%APP_PYTHON%" goto dependencies

py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
    if errorlevel 1 goto missing_python
    python -m venv .venv
)
if errorlevel 1 goto failed

:dependencies
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>&1
if not errorlevel 1 goto run
echo Setting up Filecraft. The first launch needs an internet connection.
"%APP_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto failed
copy /y requirements.txt .venv\installed-requirements.txt >nul

:run
"%APP_PYTHON%" -m filecraft %*
if errorlevel 1 goto failed
exit /b 0

:missing_python
echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/ and launch again.
pause
exit /b 1

:failed
echo Filecraft could not start. See the message above.
pause
exit /b 1
