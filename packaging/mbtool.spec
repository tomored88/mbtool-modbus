# PyInstaller spec: one folder holding mbtool.exe (windowed GUI) and mbtool-cli.exe (console: CLI, sim, MCP server).
# Build with packaging\build_exe.bat (or, from the repo root: python -m PyInstaller --noconfirm packaging\mbtool.spec)
import os
import re

root = os.path.dirname(os.path.abspath(SPECPATH))  # repo root
src = os.path.join(root, "src")
ver = re.search(r'VERSION = "([\d.]+)"', open(os.path.join(src, "appinfo.py"), encoding="utf-8").read()).group(1)
parts = (ver.split(".") + ["0", "0", "0"])[:4]
vtuple = ", ".join(parts)

version_file = os.path.join(root, "build", "version_info.txt")
os.makedirs(os.path.dirname(version_file), exist_ok=True)
with open(version_file, "w", encoding="utf-8") as f:
    f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({vtuple}), prodvers=({vtuple}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'mbtool'),
    StringStruct('FileDescription', 'mbtool - Modbus master & simulator'),
    StringStruct('FileVersion', '{ver}'),
    StringStruct('InternalName', 'mbtool'),
    StringStruct('OriginalFilename', 'mbtool.exe'),
    StringStruct('ProductName', 'mbtool'),
    StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""")

datas = [(os.path.join(root, "help"), "help"), (os.path.join(root, "presets"), "presets"), (os.path.join(src, "mbtool.ico"), ".")]
hidden = ["mbtool_mcp", "scanner", "testcenter", "interpret", "ai_report", "datalog", "monitor", "preset",
          "helpdocs", "appinfo", "signals", "buscalc", "busmap", "bushealth", "commission", "export", "demo", "selftest", "setup_app", "winreg", "serial.tools.list_ports",
          # pyserial loads URL handlers (socket:// for the emulated line / vcom, loop://, rfc2217://) by name at run time
          "serial.urlhandler.protocol_socket", "serial.urlhandler.protocol_loop", "serial.urlhandler.protocol_rfc2217",
          "serial.urlhandler.protocol_hwgrep", "serial.urlhandler.protocol_spy", "serial.urlhandler.protocol_alt"]
excludes = ["pymodbus", "pytest", "numpy", "matplotlib", "PIL", "scipy", "pandas"]


def analysis():
    return Analysis([os.path.join(src, "mbtool.py")], pathex=[src], datas=datas, hiddenimports=hidden,
                    excludes=excludes, noarchive=False)


a_gui = analysis()
a_cli = analysis()
pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)
exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="mbtool", console=False,
              icon=os.path.join(src, "mbtool.ico"), version=version_file)
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="mbtool-cli", console=True,
              icon=os.path.join(src, "mbtool.ico"), version=version_file)
coll = COLLECT(exe_gui, a_gui.binaries, a_gui.datas, exe_cli, a_cli.binaries, a_cli.datas,
               name="mbtool", strip=False, upx=False)
