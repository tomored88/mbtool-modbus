@echo off
rem Run from anywhere. Builds, in the repo root, dist\mbtool\ (mbtool.exe GUI + mbtool-cli.exe) and dist\mbtool-setup.exe (GUI installer).
rem Needs: pip install pyinstaller pillow pyserial mcp
cd /d "%~dp0.."
if not exist src\mbtool.ico python packaging\make_icon.py
python -m PyInstaller --noconfirm --distpath dist --workpath build\work packaging\mbtool.spec
if errorlevel 1 goto fail
python packaging\make_payload.py
if errorlevel 1 goto fail
python -m PyInstaller --noconfirm --distpath dist --workpath build\work_setup packaging\mbtool-setup.spec
if errorlevel 1 goto fail
python packaging\make_portable.py
if errorlevel 1 goto fail
echo.
echo Done:  %CD%\dist\mbtool-setup.exe   (installer)
echo        %CD%\dist\mbtool-portable.zip  (portable, no install)
exit /b 0
:fail
echo BUILD FAILED
pause
exit /b 1
