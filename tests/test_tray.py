"""Tray menu logic, remembered state and start-at-login, without a tray."""

import os
import sys

import pytest

from mic_monitor import autostart, cli, config
from mic_monitor.tray import device_choices

YETI = "Microphone (Yeti Stereo Microphone)"
LINE = "Line 1/2 (M-Audio AIR 192 4)"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    for var in ("LOCALAPPDATA", "XDG_STATE_HOME", "APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def checked(rows):
    return [label for label, _, on in rows if on]


def test_unset_device_checks_system_default():
    rows = device_choices(None, [YETI, LINE])
    assert rows[0] == ("System default", None, True)
    assert checked(rows) == ["System default"]


def test_exact_name_and_substring_both_check_the_device():
    assert checked(device_choices(YETI, [LINE, YETI])) == [YETI]
    assert checked(device_choices("yeti", [LINE, YETI])) == [YETI]


def test_missing_device_stays_visible_and_checked():
    rows = device_choices("CORSAIR", [YETI])
    assert checked(rows) == ["CORSAIR (not connected)"]
    assert rows[-1][1] == "CORSAIR"


def test_duplicate_names_listed_once():
    assert [r[0] for r in device_choices(None, [YETI, YETI])] == ["System default", YETI]


def test_remembered_state_follows_start_and_stop_but_not_exit():
    assert not config.last_state_on()  # nothing recorded = off
    config.remember_state(True)
    assert config.last_state_on()
    cli.stop(remember=False)  # the tray's Exit
    assert config.last_state_on()
    cli.main(["stop", "--keep-state"])  # the installers, before an upgrade
    assert config.last_state_on()
    cli.stop()  # an explicit stop
    assert not config.last_state_on()


def test_worker_runs_windowless_on_windows():
    exe = cli.worker_python()
    if sys.platform == "win32" and os.path.exists(
        os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    ):
        assert os.path.basename(exe).lower() == "pythonw.exe"
    else:
        assert exe == sys.executable


def test_tray_command_ends_with_login_flag():
    cmd = autostart.tray_command()
    assert cmd[-1] == autostart.LOGIN_FLAG and os.path.isabs(cmd[0])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Run key")
def test_windows_autostart_round_trip(monkeypatch):
    import winreg

    base = r"Software\mic-monitor-test"
    monkeypatch.setattr(autostart, "_RUN", base + r"\Run")
    monkeypatch.setattr(autostart, "_APPROVED", base + r"\Approved")
    try:
        assert not autostart.is_enabled()
        autostart.set_enabled(True)
        assert autostart.is_enabled()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart._RUN) as k:
            assert winreg.QueryValueEx(k, autostart.NAME)[0].endswith(autostart.LOGIN_FLAG)
        # Disabled in Task Manager's Startup tab reads as off.
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, autostart._APPROVED) as k:
            winreg.SetValueEx(k, autostart.NAME, 0, winreg.REG_BINARY, b"\x03" + b"\0" * 11)
        assert not autostart.is_enabled()
        autostart.set_enabled(True)  # re-enabling clears that mark
        assert autostart.is_enabled()
        autostart.set_enabled(False)
        assert not autostart.is_enabled()
    finally:
        for sub in (autostart._RUN, autostart._APPROVED, base):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass


@pytest.mark.skipif(sys.platform == "win32", reason="file-based autostart")
def test_posix_autostart_round_trip():
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    text = autostart._file_path().read_text(encoding="utf-8")
    assert autostart.LOGIN_FLAG in text
    autostart.set_enabled(False)
    assert not autostart.is_enabled()


@pytest.mark.skipif(sys.platform != "win32", reason="named mutex")
def test_second_tray_instance_is_refused():
    import subprocess

    from mic_monitor import tray

    name = f"Local\\mic-monitor-test-{os.getpid()}"
    code = (
        "import sys; from mic_monitor import tray; "
        f"print(tray.claim_single_instance({name!r})); sys.stdout.flush(); sys.stdin.read()"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "True"
        assert tray.claim_single_instance(name) is False
    finally:
        holder.communicate("")
    # Released when the holder exits.
    assert tray.claim_single_instance(name) is True
