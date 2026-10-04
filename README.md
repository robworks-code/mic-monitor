# mic-monitor

Hear your own microphone in your headphones with low latency.

Useful when you record or stream with a USB microphone and closed headphones
and want to hear yourself (sidetone) without the delay that Windows' "Listen
to this device" adds. It streams an input device straight to an output device
through PortAudio with a small buffer, and it survives a wireless headset
going to sleep and waking up.

- **CLI**: `mic-monitor` toggles monitoring on and off in the background.
- **Tray icon**: `mic-monitor-tray` gives you a click-to-toggle icon, with a
  menu to pick devices and start at login.
- **Any devices**: defaults to the system default input and output, and on
  Windows follows them when you change the default in Sound settings. Pick
  others by a part of their name.
- **Recovery**: reopens the stream by itself when a headset sleeps and wakes.

Works on Windows and macOS, where it started. Linux is supported by the code
but not yet tested, see [Platform notes](#platform-notes).

## Install

One line. It installs Python if you do not have it, sets mic-monitor up in
its own private environment, puts the commands on your PATH and starts the
tray icon. Run the same line again to upgrade.

Windows, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/robworks-code/mic-monitor/main/install.ps1 | iex
```

macOS or Linux, in Terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/robworks-code/mic-monitor/main/install.sh | sh
```

Where things go: Windows keeps the environment in `%LOCALAPPDATA%\mic-monitor`
and adds a Start Menu shortcut "Mic Monitor"; macOS and Linux keep it in
`~/.local/share/mic-monitor` with links in `~/.local/bin`. Nothing is
installed system-wide and nothing runs at login unless you add it.

Uninstall (your settings and logs are kept, see [Files](#files)):

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/robworks-code/mic-monitor/main/install.ps1))) -Uninstall
```

```sh
curl -fsSL https://raw.githubusercontent.com/robworks-code/mic-monitor/main/install.sh | sh -s -- --uninstall
```

Prefer to manage it yourself? It is a normal Python package (3.10 or newer):

```sh
pipx install https://github.com/robworks-code/mic-monitor/archive/refs/heads/main.zip
```

Linux also needs the PortAudio library, for example `sudo apt install libportaudio2`.

## Use

```sh
mic-monitor              # toggle: start if stopped, stop if running
mic-monitor start        # start in the background
mic-monitor stop
mic-monitor status       # Running (pid 1234): <input>  ->  <output>
mic-monitor run          # foreground, Ctrl+C to stop (handy for trying settings)
mic-monitor list         # show audio devices
```

Tray icon:

```sh
mic-monitor-tray
```

Green disc with a white mic = on, grey disc with a red slash = off. Left-click
toggles. Right-click opens the menu:

- **Start / Stop monitoring**, with the devices in use shown above it.
- **Input device** and **Output device**: pick one, or "System default" to
  follow whatever the system default is.
- **Volume** (50% to 200%) and **Latency** (the blocksize).
- **Start at login**: puts the tray icon in your login items. At login,
  monitoring comes back on only if it was on when you last logged off or
  exited.
- **Open log** and **Exit** (Quit on macOS). Exit stops monitoring.

A change in the menu is saved like `mic-monitor config` does, and monitoring
that is running restarts with it. The icon polls every 3 seconds, so it stays
correct when you toggle from a terminal or plug in a device. On Windows the
menu follows the light or dark app mode.

## Choose devices and settings

With no settings, monitoring goes from the system default input to the system
default output. To pick devices, use a distinctive part of their names as
shown by `mic-monitor list`:

```sh
mic-monitor start --in Yeti --out CORSAIR
```

Save settings so every start and the tray icon use them:

```sh
mic-monitor config --in Yeti --out CORSAIR     # save
mic-monitor config                              # show
mic-monitor config --in ""                      # input back to the system default
mic-monitor config --reset                      # back to defaults
```

| Setting | Default | Meaning |
|---|---|---|
| `--in` | system default | input device name substring (case-insensitive) |
| `--out` | system default | output device name substring |
| `--gain` | 1.0 | output volume multiplier |
| `--blocksize` | 64 | frames per buffer; lower = less latency, more risk of dropouts |
| `--samplerate` | input device's rate | only change it if you know the device accepts it |

Precedence: a command-line flag beats the saved config, which beats the default.

A device left at the system default follows the default: on Windows, when you
pick a different default input or output in Sound settings, monitoring moves
to it within about 2 seconds. A device you named stays put. If the two
devices run at different sample rates (a 44.1 kHz interface into a 48 kHz
headset), the Windows audio engine converts, and a mono microphone plays in
both ears.

On Windows a device appears once per host API (MME, DirectSound, WASAPI,
WDM-KS). mic-monitor always prefers the WASAPI entry, which is the low-latency
one (MME buffers far more), so you never need to pick it by index.

## Latency

What you hear is delayed by mic-monitor's own buffer plus everything the
operating system and the devices add. mic-monitor's part is the smallest:
at the default blocksize of 64 frames it is about 1.3 ms at 48 kHz. Windows'
shared-mode audio engine adds roughly 20 ms on each side, and a wireless
headset adds its own radio buffering on top.

Measured on the author's setup (Blue Yeti into a Corsair Virtuoso SE on its
2.4 GHz wireless link), with a beep played through the headset held against
the microphone: about 130 ms from mic to ear. Most of that is outside
mic-monitor. Lowering `--blocksize` from 256 to 64 saved 4 ms of it.

To get it lower:

- **Use a wired path to your ears.** A wireless link is usually the biggest
  single delay. A USB or 3.5 mm cable mode on the headset avoids it.
- **Use the microphone's own headphone jack if it has one.** Many USB
  microphones and audio interfaces (the Yeti, most interfaces with a "direct
  monitor" knob) mix the mic into their headphone output in hardware, with no
  delay at all. mic-monitor is for when that is not an option.
- `--blocksize 32` trims about 1 ms more, at a higher risk of crackles.

## How recovery works

A wireless headset that sleeps and wakes gets its audio endpoint re-created by
the operating system. A stream that was open on the old endpoint keeps
running into nothing: the process looks fine, you hear silence. mic-monitor
reopens its stream when:

- the stream goes inactive on its own (any platform), or
- on Windows, the Plug and Play arrival timestamp of the input or output
  endpoint changes (checked every 2 seconds through cfgmgr32, no extra
  packages), or
- on Windows, the system default input or output changes and that device is
  left at the system default (checked every 2 seconds through Core Audio).

While the device is away the log shows `waiting for device: ...` with a retry
every 2 seconds, and `mic-monitor status` and the tray say it is waiting.
Then the log shows `Reopened.` The reopen happens inside the same process,
so the PID, the CLI and the tray icon are unaffected.

## Files

| What | Windows | macOS / Linux |
|---|---|---|
| Saved config | `%APPDATA%\mic-monitor\config.json` | `~/.config/mic-monitor/config.json` |
| Worker log | `%LOCALAPPDATA%\mic-monitor\worker.log` | `~/.local/state/mic-monitor/worker.log` |
| PID and status | same directory as the log | same directory as the log |
| Tray errors | `%LOCALAPPDATA%\mic-monitor\tray.log` | `~/.local/state/mic-monitor/tray.log` |
| Installer's environment | `%LOCALAPPDATA%\mic-monitor\venv` and `bin` | `~/.local/share/mic-monitor/venv` |

`XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `XDG_DATA_HOME` are honoured.

## Platform notes

- **Windows**: tested. The background worker runs with no console window and
  stopping it is a hard kill, which is fine for a pass-through stream.
- **macOS**: where mic-monitor was first built and used. The tray icon uses
  pystray, which pulls in pyobjc; this packaged tray has had less use on macOS
  than on Windows. The worker code path is the same as on Windows minus the
  PnP watch and following a changed default device (restart monitoring after
  changing the default). macOS asks once for microphone access the first time monitoring
  starts. Reports welcome.
- **Linux**: needs `libportaudio2` and an X11 or AppIndicator-capable tray for
  the icon. Not yet tested by the author.

## Development

```sh
git clone https://github.com/robworks-code/mic-monitor
cd mic-monitor
pip install -e .[dev]
python check.py          # ruff + tests, no hardware needed, a few seconds
python check.py --hw     # also the hardware tests (opens your real devices)
```

The hardware tests use your saved config or the system defaults. One of them
simulates a headset re-arrival, another a changed default input, and both
assert the stream reopens.

## License

MIT, see [LICENSE](LICENSE).
