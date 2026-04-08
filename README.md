# lg-input-switch

Command-line input switcher for the **LG 45GX950A-B** monitor on Windows for nvidia GPUs.

## Background

The LG 45GX950A-B ignores standard DDC/CI input-switching commands sent via the Windows DDC/CI API. The root cause is that LG's firmware requires the DDC/CI source address `0x50` in the I2C packet, while Windows hardcodes `0x51` with no way to override it. Tools like ControlMyMonitor and Twinkle Tray both hit this limitation silently.

The fix is to bypass the Windows DDC/CI stack entirely and write the raw I2C packet directly via **NVAPI** (`NvAPI_I2CWrite`), which exposes the physical I2C bus on NVIDIA GPUs.

## Requirements

- Windows 10/11
- NVIDIA GPU with up-to-date drivers (RTX series confirmed working)
- LG 45GX950A-B connected via DisplayPort or HDMI (USB-C confirmed working too)

## Installation

### Option A — pre-built executables (no Python required)

1. Go to the [Releases](https://github.com/meer-cha/lg-input-switch/releases) page and download the `.zip` from the latest release (not the "Source code (zip)")
2. Create a new folder anywhere (e.g. `C:\Users\You\LG Input Switch`)
3. Extract the contents of the zip into that folder
4. Run `lg-input-switch.exe` — it will guide you through setup on the first run, then hide seamlessly into your system tray and start listening for your hotkey.

**System Tray:** The executable runs entirely in the background. Look for the `LG Input Switch` icon in your Windows System Tray to configure your hotkeys, exit the app, or toggle "Start with Windows".

![tray-example.png](tray-example.png)

### Option B — run from source

Requires Python 3.10+. No third-party packages needed.

```
git clone https://github.com/meer-cha/lg-input-switch.git
cd lg-input-switch
python lg_switch.py configure
python lg_switch.py daemon
```

### Option C — build the executables yourself

Requires Python 3.10+ and pip.

```
git clone https://github.com/meer-cha/lg-input-switch.git
cd lg-input-switch
build.bat
```

The executables will be in the `dist\` folder.

## Usage

### Standalone executable

```
lg-input-switch.exe
```

On the first run it will allocate a temporary console to walk you through choosing your two inputs and hotkey, then immediately vanish into your System Tray and start listening. On every subsequent run it goes straight to the System Tray.

- **Configure:** Right click the tray icon and select **Configure** to rebind your inputs or hotkey at any time.
- **Start with Windows:** Right click the tray icon and toggle **Start with Windows** on or off. This writes a single value to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` — no admin rights required.
- **Quit:** Right click the tray icon and select **Quit** to exit.
- The last active input is remembered so it always picks up where it left off!

### From source

```
python lg_switch.py <input|command> [-v]
```

#### Inputs

| Argument | Input |
|----------|-------|
| `dp`     | DisplayPort |
| `hdmi1`  | HDMI 1 |
| `hdmi2`  | HDMI 2 |
| `usbc`   | USB-C / Thunderbolt |
| `scan`   | Detect connected outputs (no switch) |

#### Options

| Flag | Description |
|------|-------------|
| `-v`, `--verbose` | Print NVAPI debug info and per-attempt I2C results |
| `-h`, `--help`    | Show help and exit |

#### Examples

```
python lg_switch.py dp
python lg_switch.py usbc
python lg_switch.py --verbose hdmi1
python lg_switch.py scan
python lg_switch.py configure
python lg_switch.py daemon
```

### Hotkey format

Type the hotkey as `+`-separated tokens — do **not** press the keys, type the names:

| Token type | Examples |
|------------|---------|
| Modifiers  | `ctrl`, `shift`, `alt`, `win` |
| Letters    | `a`–`z` |
| Digits     | `0`–`9` |
| F-keys     | `f1`–`f12` |
| Navigation | `insert`, `delete`, `home`, `end`, `pageup`, `pagedown`, `left`, `right`, `up`, `down` |
| Numpad digits | `numpad0`–`numpad9` |
| Numpad operators | `numpad+`, `numpad-`, `numpad*`, `numpad/`, `numpad.` |
| Symbols    | `;` `:` `=` `,` `-` `_` `.` `/` `` ` `` `[` `]` `\` `'` and their shifted variants |
| Space/Enter/Esc/Tab | `space`, `enter`, `esc`, `tab` |

Examples: `ctrl+shift+d`, `alt+f1`, `ctrl+numpad1`, `ctrl+shift+;`

#### Hotkey restrictions

- At least one modifier (`ctrl`, `shift`, `alt`, or `win`) is required — except F-keys (`f1`–`f12`) which may be used alone
- `shift` alone with a typeable character is blocked (e.g. `shift+/` just types `?`)
- The following are reserved and cannot be used: `esc` (reconfigure), `ctrl+c` (exit)
- Common Windows shortcuts are blocked: `ctrl+v`, `ctrl+x`, `ctrl+z`, `ctrl+a`, `ctrl+s`

## How it works

DDC/CI uses I2C to send monitor control commands. A `SetVCP` packet to switch inputs looks like this:

```
[src, length, opcode, vcp_code, value_hi, value_lo, checksum]
 0x50  0x84    0x03    0xF4      0x00      0xD0      <xor>
```

The key byte is `src = 0x50`. Windows sends `0x51` — the LG silently drops it.

NVAPI exposes `NvAPI_I2CWrite`, which writes raw bytes to the physical I2C bus with no OS-level DDC/CI wrapping. The script constructs the packet manually with `0x50` as the source address and sends it directly.

The LG also uses a **proprietary VCP code `0xF4`** for input selection rather than the standard `0x60`. The input values are:

| Value  | Input |
|--------|-------|
| `0x90` | HDMI 1 |
| `0x91` | HDMI 2 |
| `0xD0` | DisplayPort |
| `0xD1` | USB-C / Thunderbolt |

## Troubleshooting

**Nothing happens, no error** — check that DDC/CI is enabled in the monitor OSD.

**`nvapi64.dll` not found** — NVIDIA drivers are not installed or not on the system PATH.

**All attempts fail with `err -1`** — run `python lg_switch.py scan` and check the output mask. If it returns `0x00000000`, the GPU is not detecting the monitor on its I2C bus (try a different cable or port). Run with `--verbose` to see per-attempt results.

**Daemon hotkey does nothing** — another application may have registered the same hotkey. Try a different combination.

**Want to change inputs or hotkey** — Right-click the System Tray icon and select **Configure**.

## Credits

Input values taken from [ddcutil wiki here](https://github.com/rockowitz/ddcutil/wiki/Switching-input-source-on-LG-monitors) and VCP code discovered from [community research](https://www.reddit.com/r/ultrawidemasterrace/comments/1kephki/comment/nrc6riz/) on ddcutil with `--i2c-source-addr=0x50` on Linux, and [BetterDisplay on MacOS](https://www.reddit.com/r/ultrawidemasterrace/comments/1kephki/comment/mr17it9/) using `--ddcAlt`.
