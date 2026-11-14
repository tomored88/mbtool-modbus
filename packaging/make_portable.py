"""Build dist/mbtool-portable.zip: dist/mbtool plus portable.txt, so settings/presets/logs stay in the folder
(.\\data) and nothing is written to AppData or the registry. Unzip anywhere (USB stick, network share) and run mbtool.exe."""
import os
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
SRC = os.path.join(HERE, "dist", "mbtool")
OUT = os.path.join(HERE, "dist", "mbtool-portable.zip")
NOTE = ("mbtool portable mode\r\n\r\nThis file makes mbtool keep its settings, presets and logs in the 'data' folder\r\n"
        "next to mbtool.exe instead of %APPDATA%. Delete this file to use %APPDATA% again.\r\n"
        "Run mbtool.exe for the GUI; mbtool-cli.exe is the console version (mbtool-cli.exe mcp = MCP server).\r\n")

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            full = os.path.join(root, name)
            z.write(full, os.path.join("mbtool-portable", os.path.relpath(full, SRC)))
    z.writestr("mbtool-portable/portable.txt", NOTE)
print(f"{OUT}  {os.path.getsize(OUT) / 1e6:.1f} MB")
