@echo off
rem Create a private virtual environment next to the program and install the dependencies.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m venv .venv || goto :fail
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
echo.
echo Done. Start the program with run.bat
echo Optional check:  .venv\Scripts\python.exe src\mbtool.py selftest
pause
exit /b 0
:fail
echo Installation failed. Python 3.10 or newer is required (https://www.python.org/downloads/).
pause
exit /b 1
