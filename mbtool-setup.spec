# PyInstaller spec for the GUI installer: one file, mbtool-setup.exe, with build\payload.zip inside.
import os

here = os.path.abspath(SPECPATH)
a = Analysis([os.path.join(here, "setup_app.py")], pathex=[here],
             datas=[(os.path.join(here, "build", "payload.zip"), "."), (os.path.join(here, "mbtool.ico"), ".")],
             hiddenimports=["appinfo", "winreg"],
             excludes=["serial", "mcp", "uvicorn", "starlette", "PIL", "numpy", "pymodbus"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="mbtool-setup", console=False,
          icon=os.path.join(here, "mbtool.ico"), upx=False,
          version=os.path.join(here, "build", "version_info.txt"))
