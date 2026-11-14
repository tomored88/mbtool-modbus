"""Windows installer / uninstaller GUI for mbtool (per-user, no admin needed).

Built into mbtool-setup.exe (with the program folder as payload.zip) by build_setup.bat.
The same module backs `mbtool.exe --uninstall`.

  python setup_app.py --payload build\\payload.zip     run the installer from source
"""
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk

import appinfo

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\mbtool"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def default_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Programs", "mbtool")


def previous_install():
    """InstallLocation of an existing install (HKCU), or None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            path = winreg.QueryValueEx(k, "InstallLocation")[0]
        return path if os.path.isfile(os.path.join(path, "mbtool.exe")) else None
    except (OSError, ImportError):
        return None


def check_target(path):
    """(final_path, message) - message is '' when fine, else a warning to show; raises ValueError when unusable."""
    path = os.path.normpath(os.path.expandvars(path.strip().strip('"')))
    if not path or not os.path.isabs(path):
        raise ValueError("Enter a full folder path, e.g. C:\\Tools\\mbtool")
    if os.path.splitdrive(path)[1] in ("", "\\"):
        raise ValueError("Pick a folder, not a whole drive.")
    note = ""
    if os.path.isdir(path) and os.listdir(path) and not os.path.isfile(os.path.join(path, "mbtool.exe")):
        path = os.path.join(path, "mbtool")
        note = f"That folder already has other files, so mbtool will go in:\n{path}"
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
    except OSError as e:
        raise ValueError(f"Cannot write to this folder ({e.strerror or e}).\n"
                         "Pick a folder you own (Program Files needs administrator rights).")
    return path, note


def free_mb(path):
    p = path
    while p and not os.path.isdir(p):
        p = os.path.dirname(p)
    try:
        return shutil.disk_usage(p or "C:\\").free // (1024 * 1024)
    except OSError:
        return 0


def _ps(script):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   creationflags=NO_WINDOW, check=False, capture_output=True)


def make_shortcuts(target, desktop, start_menu):
    exe = os.path.join(target, "mbtool.exe")
    lines = ["$ws = New-Object -ComObject WScript.Shell",
             "function S($p){ $s=$ws.CreateShortcut($p); $s.TargetPath='%s'; $s.WorkingDirectory='%s'; "
             "$s.IconLocation='%s,0'; $s.Description='mbtool - Modbus master & simulator'; $s.Save() }" % (exe, target, exe)]
    if start_menu:
        lines.append("S (Join-Path ([Environment]::GetFolderPath('Programs')) 'mbtool.lnk')")
    if desktop:
        lines.append("S (Join-Path ([Environment]::GetFolderPath('Desktop')) 'mbtool.lnk')")
    _ps("\n".join(lines))


def register(target, size_kb):
    import winreg
    exe = os.path.join(target, "mbtool.exe")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in (("DisplayName", appinfo.APP_TITLE), ("DisplayVersion", appinfo.VERSION),
                          ("Publisher", "mbtool"), ("DisplayIcon", exe), ("InstallLocation", target),
                          ("UninstallString", f'"{exe}" --uninstall')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))


def extract(payload, target, progress):
    """Unzip payload into target, calling progress(done, total). Replaces an older install of mbtool."""
    with zipfile.ZipFile(payload) as z:
        infos = z.infolist()
        total = sum(i.file_size for i in infos) or 1
        if os.path.isfile(os.path.join(target, "mbtool.exe")):
            for name in os.listdir(target):  # old program files go, never the folder itself
                p = os.path.join(target, name)
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        done = 0
        for info in infos:
            z.extract(info, target)
            done += info.file_size
            progress(done, total)
    return total // 1024


class Installer:
    def __init__(self, root, payload):
        self.root, self.payload = root, payload
        self.q = queue.Queue()
        self.s = root.winfo_fpixels("1i") / 96.0
        s = self.s
        root.title(f"Install {appinfo.APP_TITLE} {appinfo.VERSION}")
        root.resizable(False, False)
        ico = appinfo.icon_path()
        if os.path.isfile(ico):
            try:
                root.iconbitmap(ico)
            except tk.TclError:
                pass
        old = previous_install()
        self.v_dir = tk.StringVar(value=old or default_dir())
        self.v_desktop = tk.BooleanVar(value=True)
        self.v_menu = tk.BooleanVar(value=True)
        self.v_run = tk.BooleanVar(value=True)
        pad = int(14 * s)
        f = ttk.Frame(root, padding=pad)
        f.grid()
        ttk.Label(f, text=f"{appinfo.APP_TITLE}  {appinfo.VERSION}", font=("Segoe UI", int(13), "bold")).grid(
            sticky="w")
        ttk.Label(f, text="Modbus master, slave simulator, device presets and AI (MCP) tools\n"
                          "ตัวอ่านเขียน Modbus พร้อมตัวจำลอง พรีเซ็ตอุปกรณ์ และเครื่องมือสำหรับ AI",
                  justify="left").grid(sticky="w", pady=(2, int(10 * s)))
        ttk.Label(f, text=("Update existing installation in:" if old else "Install to this folder:") +
                          "   (โฟลเดอร์ที่จะติดตั้ง)").grid(sticky="w")
        row = ttk.Frame(f)
        row.grid(sticky="we", pady=(2, 0))
        self.e_dir = ttk.Entry(row, textvariable=self.v_dir, width=58)
        self.e_dir.grid(row=0, column=0, sticky="we")
        self.b_browse = ttk.Button(row, text="Browse...", command=self.browse)
        self.b_browse.grid(row=0, column=1, padx=(6, 0))
        self.l_space = ttk.Label(f, foreground="#59606b")
        self.l_space.grid(sticky="w", pady=(2, int(8 * s)))
        self.v_dir.trace_add("write", lambda *_: self.update_space())
        self.checks = [ttk.Checkbutton(f, text="Desktop shortcut  (ไอคอนบน Desktop)", variable=self.v_desktop),
                       ttk.Checkbutton(f, text="Start Menu shortcut  (เมนู Start)", variable=self.v_menu),
                       ttk.Checkbutton(f, text="Start mbtool when finished  (เปิดโปรแกรมเมื่อเสร็จ)",
                                       variable=self.v_run)]
        for c in self.checks:
            c.grid(sticky="w")
        ttk.Label(f, text="Settings and your presets are stored in %APPDATA%\\mbtool and are kept on update.",
                  foreground="#59606b").grid(sticky="w", pady=(int(8 * s), 0))
        self.bar = ttk.Progressbar(f, length=int(560 * s), mode="determinate")
        self.bar.grid(sticky="we", pady=(int(10 * s), 0))
        self.l_stat = ttk.Label(f, text="")
        self.l_stat.grid(sticky="w")
        btns = ttk.Frame(f)
        btns.grid(sticky="e", pady=(int(10 * s), 0))
        self.b_install = ttk.Button(btns, text="Install", command=self.start, default="active")
        self.b_install.grid(row=0, column=0, padx=(0, 6))
        self.b_cancel = ttk.Button(btns, text="Cancel", command=root.destroy)
        self.b_cancel.grid(row=0, column=1)
        root.bind("<Return>", lambda e: self.b_install.invoke() if str(self.b_install["state"]) == "normal" else None)
        root.bind("<Escape>", lambda e: root.destroy() if str(self.b_cancel["state"]) == "normal" else None)
        self.update_space()
        self.center()
        self.target = None

    def center(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"+{x}+{y}")

    def browse(self):
        d = filedialog.askdirectory(parent=self.root, title="Choose install folder",
                                    initialdir=os.path.dirname(self.v_dir.get()) or None, mustexist=False)
        if d:
            d = os.path.normpath(d)
            self.v_dir.set(d if os.path.basename(d).lower() == "mbtool" else os.path.join(d, "mbtool"))

    def update_space(self):
        try:
            need = sum(i.file_size for i in zipfile.ZipFile(self.payload).infolist()) // (1024 * 1024)
        except (OSError, zipfile.BadZipFile):
            need = 0
        p = os.path.expandvars(self.v_dir.get().strip())
        self.l_space.config(text=f"Needs about {need} MB, {free_mb(p):,} MB free on this drive.")

    def start(self):
        try:
            self.target, note = check_target(self.v_dir.get())
        except ValueError as e:
            messagebox.showwarning("Install folder", str(e), parent=self.root)
            return
        if note and not messagebox.askokcancel("Install folder", note, parent=self.root):
            return
        self.v_dir.set(self.target)
        for w in [self.e_dir, self.b_browse, self.b_install, self.b_cancel, *self.checks]:
            w.state(["disabled"])
        self.l_stat.config(text="Installing...")
        threading.Thread(target=self.work, daemon=True).start()
        self.root.after(80, self.pump)

    def work(self):
        try:
            size = extract(self.payload, self.target, lambda d, t: self.q.put(("p", d / t)))
            self.q.put(("s", "Creating shortcuts..."))
            make_shortcuts(self.target, self.v_desktop.get(), self.v_menu.get())
            register(self.target, size)
            self.q.put(("done", None))
        except Exception as e:  # shown to the user, details in crash.log
            appinfo.write_crash(type(e), e, e.__traceback__, where="installer")
            self.q.put(("err", f"{type(e).__name__}: {e}"))

    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "p":
                    self.bar["value"] = val * 100
                elif kind == "s":
                    self.l_stat.config(text=val)
                elif kind == "done":
                    return self.finish()
                elif kind == "err":
                    self.l_stat.config(text="Failed")
                    messagebox.showerror("Install failed", f"{val}\n\nClose mbtool if it is running and try again.",
                                         parent=self.root)
                    for w in [self.e_dir, self.b_browse, self.b_install, self.b_cancel, *self.checks]:
                        w.state(["!disabled"])
                    return
        except queue.Empty:
            pass
        self.root.after(80, self.pump)

    def finish(self):
        self.bar["value"] = 100
        self.l_stat.config(text=f"Installed in {self.target}")
        if self.v_run.get():
            subprocess.Popen([os.path.join(self.target, "mbtool.exe")], cwd=self.target)
        messagebox.showinfo("Done", f"mbtool is installed.\n\n{self.target}\n\n"
                                    "For AI tools (MCP) use:\n"
                                    f"{os.path.join(self.target, 'mbtool-cli.exe')} mcp", parent=self.root)
        self.root.destroy()


def uninstall_gui():
    """Ask, then remove shortcuts/registry entry and schedule deletion of this install folder."""
    root = tk.Tk()
    root.withdraw()
    target = os.path.dirname(os.path.abspath(sys.executable)) if appinfo.FROZEN else previous_install()
    if not target or not os.path.isfile(os.path.join(target, "mbtool.exe")):
        messagebox.showerror("Uninstall mbtool", "No installed copy of mbtool was found.")
        return 1
    if not messagebox.askyesno("Uninstall mbtool", f"Remove mbtool from\n{target} ?"):
        return 0
    wipe = messagebox.askyesno("Uninstall mbtool", "Also delete your settings, edited presets and logs?\n"
                                                   f"({appinfo.data_dir()})\n\nChoose No to keep them.")
    _ps("Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'mbtool.lnk') -Force -ErrorAction SilentlyContinue;"
        "Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'mbtool.lnk') -Force -ErrorAction SilentlyContinue")
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    if wipe:
        shutil.rmtree(appinfo.data_dir(), ignore_errors=True)
    # this exe is inside the folder being removed: finish from a detached shell once we have exited
    subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 4 >nul & rmdir /s /q "{target}"', creationflags=NO_WINDOW, shell=False)
    messagebox.showinfo("Uninstall mbtool", "mbtool has been removed.")
    return 0


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if "--uninstall" in a:
        return uninstall_gui()
    payload = os.path.join(appinfo.resource_dir(), "payload.zip")
    if "--payload" in a:
        payload = a[a.index("--payload") + 1]
    if not os.path.isfile(payload):
        ctypes.windll.user32.MessageBoxW(0, f"Installer data not found:\n{payload}", "mbtool setup", 0x10)
        return 1
    if "--silent" in a:  # scripted install: setup.exe --silent [--dir PATH] [--no-desktop] [--no-menu]
        target, _ = check_target(a[a.index("--dir") + 1] if "--dir" in a else (previous_install() or default_dir()))
        size = extract(payload, target, lambda d, t: None)
        make_shortcuts(target, "--no-desktop" not in a, "--no-menu" not in a)
        register(target, size)
        return 0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    appinfo.install_crash_handlers(root)
    Installer(root, payload)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
