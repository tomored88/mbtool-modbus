"""Demo devices: start several simulated Modbus devices with moving values so every feature (device tabs, PLC cycle,
bus calculator, bus map, trend, alerts) can be tried without any real hardware.

  python demo.py             start the demo devices and keep them running (Ctrl+C stops)
  python demo.py --tcp       one TCP simulator per device instead of one emulated RS-485 line

In the GUI: Devices > "Demo devices..." starts the same thing and adds the devices (one tab each).
"""
import os
import sys
import time

import appinfo
from modbus_core import SerialSlave, SimSlave, VirtualCable
from preset import load_preset
from signals import SignalEngine

# (device name, Slave ID, preset under presets/, response delay in ms)
DEMO_SET = [
    ("Meter 1", 1, "library/eastron_sdm230.json", 12),
    ("Drive 1", 2, "library/schneider_atv320.json", 25),
    ("Level 1", 3, "library/vega_vegapuls_c21_modbus.json", 18),
    ("Flow 1", 4, "library/endress_hauser_promag_100_modbus.json", 20),
    ("Pressure 1", 5, "library/keller_series_30_40_modbus.json", 8),
]


DEMO_PORT = 7100  # emulated line = socket://127.0.0.1:7100, TCP devices 7101...


class Demo:
    """Simulated devices with moving values. line='rtu': all devices share one emulated RS-485 line (a virtual
    cable that really takes bytes x bits / baud seconds), so the cycle time you measure behaves like a real bus;
    line='tcp': one TCP simulator per device on 127.0.0.1."""

    def __init__(self, count=None, line="rtu", baud=19200, parity="E", mode="profile", preset_dir=None, speed=1.0,
                 base_port=0):
        if line not in ("rtu", "tcp"):
            raise ValueError("line must be rtu or tcp")
        self.line, self.baud, self.parity, self.mode, self.speed = line, int(baud), parity, mode, speed
        self.preset_dir = preset_dir or appinfo.preset_dir()
        self.spec = DEMO_SET[: (count or len(DEMO_SET))]
        self.base_port = base_port  # fixed ports (cable = base_port, TCP devices base_port+1...) so a saved workspace
        #                             keeps working between runs; 0 = let the system choose
        self.cable = None
        self.slaves, self.serials, self.engines, self.devices = [], [], [], []
        self.running = False

    def start(self):
        if self.running:
            return self.devices
        try:
            bits = 1 + 8 + (0 if self.parity == "N" else 1) + 1
            if self.line == "rtu":
                self.cable = self._cable(bits)
            for i, (name, unit, rel, delay) in enumerate(self.spec):
                path = os.path.join(self.preset_dir, *rel.split("/"))
                if not os.path.isfile(path):  # bundled copy (portable builds seed the user folder lazily)
                    path = os.path.join(appinfo.resource_dir(), "presets", *rel.split("/"))
                pr = load_preset(path)
                store = self._store(i, unit)
                pr.fill_simulator(store)
                store.response_delay = delay / 1000.0
                eng = SignalEngine(pr, store, mode=self.mode, speed=self.speed)
                eng.tick(0.0)
                eng.start()
                self.engines.append(eng)
                self.slaves.append(store)
                if self.line == "rtu":
                    self.serials.append(SerialSlave(self.cable.url, unit, self.baud, self.parity, store=store))
                    conn = {"mode": "RTU", "com": self.cable.url, "baud": str(self.baud), "parity": self.parity,
                            "stop": "1", "data": "8", "timeout": "1000"}
                else:
                    conn = {"mode": "TCP", "host": "127.0.0.1", "port": str(store.port), "timeout": "1000"}
                self.devices.append({"name": name, "conn": conn, "unit": str(unit), "scan": "1000", "enabled": True,
                                     "preset": os.path.abspath(path), "fc": "3", "addr": "0", "qty": "10",
                                     "fmt": "Unsigned", "response_delay_ms": delay})
            self.running = True
        except Exception:
            self.stop()
            raise
        return self.devices

    def _cable(self, bits):
        try:
            return VirtualCable(port=self.base_port, baud=self.baud, bits_per_char=bits)
        except OSError:  # fixed port busy (a second demo?): take any free one
            return VirtualCable(baud=self.baud, bits_per_char=bits)

    def _store(self, i, unit):
        if self.line != "tcp":
            return SimSlave(listen=False)
        try:
            return SimSlave("127.0.0.1", self.base_port + 1 + i if self.base_port else 0, unit=unit)
        except OSError:
            return SimSlave("127.0.0.1", 0, unit=unit)

    def set_delay(self, name, ms):
        """Change how long one demo device 'thinks' before answering (ms)."""
        for (n, *_), s in zip(self.spec, self.slaves):
            if n == name:
                s.response_delay = max(0.0, ms) / 1000.0
                return True
        return False

    def stop(self):
        for e in self.engines:
            e.stop()
        for s in self.serials:
            try:
                s.stop()
            except Exception:
                pass
        for s in self.slaves:
            try:
                s.stop()
            except Exception:
                pass
        if self.cable:
            self.cable.close()
        self.engines, self.serials, self.slaves, self.devices, self.cable = [], [], [], [], None
        self.running = False


def workspace_file(path, line="rtu"):
    """Write a workspace (.mbw) that lists the demo devices on the fixed demo ports. Open it in the GUI (Ctrl+O)
    while `python demo.py` (or the packaged demo) is running and every device gets its own tab."""
    import monitor as mon
    d = Demo(line=line, base_port=DEMO_PORT)
    ws = mon.new_workspace()
    for i, (name, unit, rel, delay) in enumerate(d.spec):
        conn = ({"mode": "RTU", "com": f"socket://127.0.0.1:{DEMO_PORT}", "baud": str(d.baud), "parity": d.parity,
                 "stop": "1", "data": "8", "timeout": "1000"} if line == "rtu" else
                {"mode": "TCP", "host": "127.0.0.1", "port": str(DEMO_PORT + 1 + i), "timeout": "1000"})
        ws["devices"].append(mon.norm_device({"name": name, "conn": conn, "unit": str(unit), "scan": "1000",
                                              "enabled": True, "preset": os.path.join(appinfo.preset_dir(), *rel.split("/")),
                                              "fc": "3", "addr": "0", "qty": "10"}))
    mon.save_workspace(path, ws)
    return path


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if "--workspace" in a:  # python demo.py --workspace demo.mbw [--tcp]
        out = a[a.index("--workspace") + 1]
        print("written:", workspace_file(out, "tcp" if "--tcp" in a else "rtu"))
        return 0
    d = Demo(line="tcp" if "--tcp" in a else "rtu", base_port=DEMO_PORT)
    devs = d.start()
    print("Demo devices running (values move; Ctrl+C to stop):")
    for dv in devs:
        c = dv["conn"]
        where = c.get("com") if c["mode"] == "RTU" else f"{c['host']}:{c['port']}"
        print(f"  {dv['name']:<12} ID {dv['unit']:<3} {c['mode']} {where}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        d.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
