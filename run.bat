@echo off
rem Start the GUI. Extra arguments are passed on, e.g.  run.bat sim --port 5020
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" mbtool.py %*) else (where py >nul 2>nul && (py -3 mbtool.py %*) || (python mbtool.py %*))
if errorlevel 1 pause
