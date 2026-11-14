"""mbtool - a Modbus master (GUI + CLI) with a built-in simulator slave.

  python mbtool.py                      GUI  (F3 setup, F4 value inspector, F5 connect/disconnect, F6 write,
                                              F7 presets, F8 traffic monitor, F9 slave sim, F10 trend,
                                              F11 devices, F12 AI assist, Ctrl+L log, Ctrl+O / Ctrl+S workspace)
  python mbtool.py --workspace plant.mbw   GUI with a workspace
  python mbtool.py sim [--port 5020]    run a Modbus/TCP simulator slave
  python mbtool.py read tcp://127.0.0.1:5020 -u 1 -f 3 -a 0 -n 10 [--fmt Float] [--scan 1000] [--log out.csv]
  python mbtool.py write tcp://127.0.0.1:5020 -u 1 -f 6 -a 5 123
  python mbtool.py read serial:COM3?baud=19200&parity=E&mode=rtu -u 1 -f 4 -a 0 -n 4
"""
import sys
import types

if sys.version_info < (3, 11):  # 3.10's time.sleep on Windows is ~15 ms coarse: RTU gaps / PLC cycles go wrong
    sys.exit(f"mbtool needs Python 3.11 or newer (this is {sys.version.split()[0]}). "
             "Install a newer Python from https://www.python.org/downloads/ or use the Windows installer.")

from mbui import (ai_ui, app, busmap_ui, cli, common, devices, dialogs, export_ui, grids, help_ui,  # noqa: F401
                  inspector, polltabs, presets_ui, simwin, traffic, trend)
from mbui.cli import main

# Every name of the package is also available as mbtool.<name> (mbtool.MasterApp, mbtool.FUNCS, mbtool.connect ...),
# as it was before the GUI was split into src/mbui/, so scripts and tests keep working.
for _mod in (common, grids, dialogs, presets_ui, simwin, traffic, inspector, trend, devices, polltabs, busmap_ui,
             export_ui, ai_ui, help_ui, app, cli):
    for _k, _v in vars(_mod).items():
        if not _k.startswith("__"):
            globals().setdefault(_k, _v)
del _mod, _k, _v


class _Facade(types.ModuleType):
    """mbtool.SETTINGS_FILE / HOME_SETTINGS = path also changes where the GUI saves its settings."""

    def __setattr__(self, name, value):
        if name in ("SETTINGS_FILE", "HOME_SETTINGS"):
            setattr(common, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Facade


if __name__ == "__main__":
    main()
