"""`mic-monitor-tray`: a system tray / menu bar icon that toggles monitoring.

Green disc with a white mic = on, grey disc with a red slash = off. Left
click toggles. The right-click menu shows the devices and has Start/Stop,
Input device, Output device, Volume and Latency submenus (a change is saved
and a running monitor restarts with it), Start at login, Open log, and Exit.
Exit stops monitoring but remembers it was on, so a tray started at login
(--login) turns it back on. The icon polls every 3 s so it stays correct
when monitoring is toggled from the command line or devices come and go.

On Windows the menus follow the light/dark app setting (wintheme) and the
device lists come from Core Audio (winaudio), so they are always current.

Installed as a GUI script: on Windows it runs under pythonw, which has no
console and where sys.stdout is None, so this module never prints. Errors go
to tray.log in the state directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback

from . import autostart, cli, config

POLL_SECONDS = 3
SIZE = 64
GAINS = (0.5, 0.75, 1.0, 1.5, 2.0)
BLOCKSIZES = (
    (32, "Lowest (may crackle)"),
    (64, "Low (default)"),
    (128, "Medium"),
    (256, "Safe"),
)


def make_icon(on: bool):
    """Bold, high-contrast disc. Tray icons are tiny, so no fine detail."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = (30, 160, 60, 255) if on else (70, 70, 70, 255)
    d.ellipse((0, 0, SIZE - 1, SIZE - 1), fill=bg)
    white = (255, 255, 255, 255)
    d.rounded_rectangle((24, 12, 40, 36), radius=8, fill=white)  # capsule
    d.arc((18, 22, 46, 44), start=0, end=180, fill=white, width=4)  # cradle
    d.line((32, 44, 32, 52), fill=white, width=4)  # stem
    d.line((22, 52, 42, 52), fill=white, width=4)  # base
    if not on:
        d.line((12, 52, 52, 12), fill=(220, 40, 40, 255), width=7)
    return img


def device_choices(saved, names: list[str]) -> list[tuple[str, str | None, bool]]:
    """(label, value, checked) rows for a device submenu. Value None = system
    default. A saved name is checked on the exact device, else on the first
    device containing it (how the worker matches); one that matches nothing
    right now is kept as a "(not connected)" row so the menu never hides the
    real setting."""
    names = list(dict.fromkeys(names))  # identical names are indistinguishable
    rows: list[tuple[str, str | None, bool]] = [("System default", None, not saved)]
    hit = None
    if saved:
        if saved in names:
            hit = saved
        else:
            hit = next((n for n in names if saved.lower() in n.lower()), None)
    rows += [(n, n, n == hit) for n in names]
    if saved and hit is None:
        rows.append((f"{saved} (not connected)", saved, True))
    return rows


def list_devices(kind: str) -> list[str]:
    """Device names for the menus: Core Audio on Windows (always current),
    PortAudio elsewhere (as of this process's start)."""
    if sys.platform == "win32":
        from . import winaudio

        return winaudio.active_endpoint_names(kind)
    import sounddevice as sd

    key = "max_input_channels" if kind == "input" else "max_output_channels"
    return sorted({d["name"] for d in sd.query_devices() if d[key] > 0}, key=str.lower)


def open_path(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class TrayApp:
    def __init__(self):
        import pystray

        self.pystray = pystray
        self.on = False
        self.devices = ""
        self.inputs: list[str] = []
        self.outputs: list[str] = []
        self.dark = None
        self._stop_poll = threading.Event()
        if sys.platform == "win32":
            from . import wintheme

            self.dark = wintheme.apply()
        self._load_devices()
        item, menu = pystray.MenuItem, pystray.Menu
        self.icon = pystray.Icon(
            "mic-monitor",
            icon=make_icon(False),
            title="Mic monitor: OFF",
            menu=menu(
                item(lambda i: self.status_text(), None, enabled=False),
                item(lambda i: self.toggle_text(), self.toggle, default=True),
                menu.SEPARATOR,
                item("Input device", menu(lambda: self._device_items("in", self.inputs))),
                item("Output device", menu(lambda: self._device_items("out", self.outputs))),
                item("Volume", menu(self._gain_items)),
                item("Latency", menu(self._blocksize_items)),
                menu.SEPARATOR,
                item("Start at login", self.toggle_autostart,
                     checked=lambda i: autostart.is_enabled()),
                item("Open log", self.open_log),
                menu.SEPARATOR,
                item("Exit" if sys.platform == "win32" else "Quit", self.quit),
            ),
        )

    # --- menu text and items ---

    def status_text(self) -> str:
        if self.on:
            return f"Monitoring: ON  ({self.devices})" if self.devices else "Monitoring: ON"
        return "Monitoring: OFF"

    def toggle_text(self) -> str:
        return "Stop monitoring" if self.on else "Start monitoring"

    def _radio(self, label: str, key: str, value, checked: bool):
        def act(icon, item):
            self.set_setting(key, value)

        return self.pystray.MenuItem(label, act, checked=lambda i: checked, radio=True)

    def _device_items(self, key: str, names: list[str]):
        rows = device_choices(config.load().get(key), names)
        label, value, checked = rows[0]
        items = [self._radio(label, key, value, checked), self.pystray.Menu.SEPARATOR]
        items += [self._radio(label, key, value, checked) for label, value, checked in rows[1:]]
        return items

    def _gain_items(self):
        current = float(config.load().get("gain") or 1.0)
        return [self._radio(f"{int(g * 100)}%", "gain", g, g == current) for g in GAINS]

    def _blocksize_items(self):
        current = int(config.load().get("blocksize") or 64)
        return [
            self._radio(f"{label}  ({bs})", "blocksize", bs, bs == current)
            for bs, label in BLOCKSIZES
        ]

    # --- actions ---

    def set_setting(self, key: str, value) -> None:
        """Save one setting; a running monitor restarts with it."""
        config.save({key: value})
        if cli.is_running():
            cli.stop(remember=False)
            cli.start(remember=False)
        self.refresh(force=True)

    def toggle(self, icon=None, item=None) -> None:
        cli.toggle()
        self.refresh()

    def toggle_autostart(self, icon=None, item=None) -> None:
        autostart.set_enabled(not autostart.is_enabled())
        self.icon.update_menu()

    def open_log(self, icon=None, item=None) -> None:
        log = config.log_path()
        open_path(log if log.exists() else config.state_dir())

    def quit(self, icon=None, item=None) -> None:
        self._stop_poll.set()
        cli.stop(remember=False)  # keep "on" so the next login restores it
        self.icon.stop()

    # --- polling ---

    def _load_devices(self) -> bool:
        """Refresh the device lists; True when they changed."""
        try:
            inputs, outputs = list_devices("input"), list_devices("output")
        except Exception:  # no audio stack right now: keep the old lists
            _log_exception()
            return False
        changed = (inputs, outputs) != (self.inputs, self.outputs)
        self.inputs, self.outputs = inputs, outputs
        return changed

    def refresh(self, force: bool = False) -> None:
        on = cli.is_running()
        devices = ""
        if on:
            st = cli.read_status()
            if st and st.get("waiting"):
                devices = "waiting for device"
            elif st:
                devices = f"{st['in']} -> {st['out']}"
        rebuild = force
        if on != self.on or devices != self.devices:
            self.on, self.devices = on, devices
            self.icon.icon = make_icon(on)
            self.icon.title = "Mic monitor: ON" if on else "Mic monitor: OFF"
            rebuild = True
        if sys.platform == "win32":
            from . import wintheme

            if wintheme.apps_use_dark() != self.dark:
                self.dark = wintheme.apply()
                rebuild = True
            rebuild = self._load_devices() or rebuild
        if rebuild:
            self.icon.update_menu()

    def _poll(self) -> None:
        while not self._stop_poll.wait(POLL_SECONDS):
            try:
                self.refresh()
            except Exception:  # keep polling; a dead poll thread = stale icon forever
                _log_exception()

    def _setup(self, icon) -> None:
        icon.visible = True
        self.refresh(force=True)
        threading.Thread(target=self._poll, daemon=True).start()

    def run(self) -> None:
        self.icon.run(setup=self._setup)


def _log_exception() -> None:
    try:
        p = config.tray_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
    except OSError:
        pass


_instance_handle = None


def claim_single_instance(name: str = "Local\\mic-monitor-tray") -> bool:
    """False if another tray already holds the named mutex (Windows only).

    The handle stays open for the life of the process; Windows releases it
    when the process exits, including when it is killed.
    """
    global _instance_handle
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _instance_handle = handle
    return True


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not claim_single_instance():
        return 0
    try:
        if autostart.LOGIN_FLAG in argv and config.last_state_on() and not cli.is_running():
            cli.start(remember=False)
        TrayApp().run()
    except Exception:
        _log_exception()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
