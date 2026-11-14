"""Application identity and file locations, shared by the GUI, CLI and MCP server.

Run from source, everything stays next to the scripts (as before). Installed/frozen (PyInstaller),
the program folder is treated as read-only: settings, presets you edit, logs and the MCP audit log live in
%APPDATA%\\mbtool (override with the MBTOOL_HOME environment variable).
"""
import os
import shutil
import sys
import threading
import time
import traceback

APP_NAME = "mbtool"
APP_TITLE = "mbtool - Modbus master & simulator"
VERSION = "1.1.4"
FROZEN = bool(getattr(sys, "frozen", False))
# Portable build: a file named "portable.txt" next to mbtool.exe keeps settings, presets and logs in .\data
PORTABLE = FROZEN and os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "portable.txt"))


def resource_dir():
    """Folder holding the bundled read-only files (help/, presets/)."""
    if FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root: src/.. holds help/, presets/


def icon_path():
    """mbtool.ico: next to the bundled files in the exe, next to this module (src/) in a source checkout."""
    for d in (resource_dir(), os.path.dirname(os.path.abspath(__file__))):
        p = os.path.join(d, "mbtool.ico")
        if os.path.isfile(p):
            return p
    return None


def data_dir():
    """Writable per-user folder (created on demand)."""
    env = os.environ.get("MBTOOL_HOME")
    if env:
        path = env
    elif PORTABLE:
        path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data")
    elif FROZEN:
        path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
    else:
        return resource_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def settings_file():
    return os.environ.get("MBTOOL_SETTINGS") or os.path.join(data_dir(), "mbtool_settings.json")


def preset_dir():
    """Presets folder the user can edit. Frozen builds seed it from the bundled library (see seed_presets)."""
    if FROZEN or os.environ.get("MBTOOL_HOME"):
        return os.path.join(data_dir(), "presets")
    return os.path.join(resource_dir(), "presets")


def log_dir():
    path = os.path.join(data_dir(), "logs") if (FROZEN or os.environ.get("MBTOOL_HOME")) \
        else os.path.join(os.path.expanduser("~"), ".mbtool")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def audit_log():
    return os.path.join(log_dir(), "mcp_audit.log")


def seed_presets():
    """Copy the bundled presets into the user's folder: the shipped library is refreshed whenever the program
    version changes, files you added or edited outside presets/library are never touched."""
    src, dst = os.path.join(resource_dir(), "presets"), preset_dir()
    if os.path.abspath(src) == os.path.abspath(dst) or not os.path.isdir(src):
        return dst
    stamp = os.path.join(dst, ".seeded")
    try:
        with open(stamp, encoding="utf-8") as f:
            if f.read().strip() == VERSION:
                return dst
    except OSError:
        pass
    try:
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            out = dst if rel == "." else os.path.join(dst, rel)
            os.makedirs(out, exist_ok=True)
            for name in files:
                target = os.path.join(out, name)
                inside_library = rel.split(os.sep)[0] == "library"
                if inside_library or not os.path.exists(target):
                    shutil.copy2(os.path.join(root, name), target)
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(VERSION)
    except OSError:
        pass  # read-only profile: the program still works, it just lists fewer presets
    return dst


# ---------------- crash reporting ----------------
_crash_lock = threading.Lock()


def crash_log_path():
    return os.path.join(log_dir(), "crash.log")


def write_crash(exc_type, exc, tb, where="main"):
    """Append a traceback to crash.log; returns the path (or None when the folder is not writable)."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    path = crash_log_path()
    try:
        with _crash_lock, open(path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  {APP_NAME} {VERSION}  ({where}) ===\n{text}")
        return path
    except OSError:
        return None


def install_crash_handlers(root=None):
    """Log uncaught errors from the main thread, worker threads and Tk callbacks instead of losing them
    (a windowed .exe has no console). With a Tk root the user also sees a short dialog."""
    def show(path, exc):
        if root is None:
            return
        try:
            from tkinter import messagebox
            messagebox.showerror(
                APP_TITLE, f"Unexpected error: {exc}\n\nThe details were saved to:\n{path or '(could not write the log)'}\n\n"
                "The program keeps running; save your work if something looks wrong.", parent=root)
        except Exception:
            pass

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return sys.__excepthook__(exc_type, exc, tb)
        show(write_crash(exc_type, exc, tb), exc)
        if not FROZEN:
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook
    threading.excepthook = lambda a: write_crash(a.exc_type, a.exc_value, a.exc_traceback,
                                                 where=f"thread {getattr(a.thread, 'name', '?')}")
    if root is not None:
        root.report_callback_exception = lambda t, e, tb: show(write_crash(t, e, tb, where="tk"), e)


def about_text():
    import platform
    return (f"{APP_TITLE}\nVersion {VERSION}{' (portable)' if PORTABLE else ''}\n\n"
            "Modbus master (TCP / RTU / ASCII), slave simulator, device presets, scanner,\n"
            "trend / alerts / logging and an MCP server for AI assistants.\n"
            "Protocol implemented from the public Modbus specification.\n\n"
            f"Python {platform.python_version()} on {platform.system()} {platform.release()}\n"
            f"Settings:  {settings_file()}\nPresets:   {preset_dir()}\nLogs:      {log_dir()}")
