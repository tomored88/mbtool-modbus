"""Zip dist/mbtool (the PyInstaller output) into build/payload.zip for the setup exe."""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "dist", "mbtool")
OUT = os.path.join(HERE, "build", "payload.zip")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            full = os.path.join(root, name)
            z.write(full, os.path.relpath(full, SRC))
print(f"{OUT}  {os.path.getsize(OUT) / 1e6:.1f} MB")
