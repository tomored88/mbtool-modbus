@echo off
rem Build dist\mbtool\ (mbtool.exe GUI + mbtool-cli.exe) and dist\mbtool-setup.exe (GUI installer).
rem Needs: pip install pyinstaller pillow pyserial mcp
cd /d "%~dp0"
if not exist mbtool.ico python make_icon.py
python -m PyInstaller --noconfirm --distpath dist --workpath build\work mbtool.spec
if errorlevel 1 goto fail
python make_payload.py
if errorlevel 1 goto fail
python -m PyInstaller --noconfirm --distpath dist --workpath build\work_setup mbtool-setup.spec
if errorlevel 1 goto fail
python make_portable.py
if errorlevel 1 goto fail
echo.
echo Done:  %~dp0dist\mbtool-setup.exe   (installer)
echo        %~dp0dist\mbtool-portable.zip  (portable, no install)
exit /b 0
:fail
echo BUILD FAILED
pause
exit /b 1
