# PyInstaller spec for the GUI installer: one file, mbtool-setup.exe, with build\payload.zip inside.
import os

root = os.path.dirname(os.path.abspath(SPECPATH))  # repo root
src = os.path.join(root, "src")
a = Analysis([os.path.join(src, "setup_app.py")], pathex=[src],
             datas=[(os.path.join(root, "build", "payload.zip"), "."), (os.path.join(src, "mbtool.ico"), ".")],
             hiddenimports=["appinfo", "winreg"],
             excludes=["serial", "mcp", "uvicorn", "starlette", "PIL", "numpy", "pymodbus"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="mbtool-setup", console=False,
          icon=os.path.join(src, "mbtool.ico"), upx=False,
          version=os.path.join(root, "build", "version_info.txt"))
