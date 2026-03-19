# lg-switch

Command-line input switcher for the **LG 45GX950A-B** monitor on Windows for nvidia GPUs.

## Background

The LG 45GX950A-B ignores standard DDC/CI input-switching commands sent via the Windows DDC/CI API. The root cause is that LG's firmware requires the DDC/CI source address `0x50` in the I2C packet, while Windows hardcodes `0x51` with no way to override it. Tools like ControlMyMonitor and Twinkle Tray both hit this limitation silently.

The fix is to bypass the Windows DDC/CI stack entirely and write the raw I2C packet directly via **NVAPI** (`NvAPI_I2CWrite`), which exposes the physical I2C bus on NVIDIA GPUs.

## Requirements

- Windows 10/11
- Python 3.10+
- NVIDIA GPU with up-to-date drivers (RTX series confirmed working)
- LG 45GX950A-B connected via DisplayPort or HDMI (USB-C confirmed working too)

No third-party Python packages required — only the standard library.

## Installation

```
git clone https://github.com/meer-cha/lg-input-switch.git
cd lg-input-switch
```

## Usage

```
python lg_switch.py <input> [-v]
```

### Inputs

| Argument | Input |
|----------|-------|
| `dp`     | DisplayPort |
| `hdmi1`  | HDMI 1 |
| `hdmi2`  | HDMI 2 |
| `usbc`   | USB-C / Thunderbolt |
| `scan`   | Detect connected outputs (no switch) |

### Options

| Flag | Description |
|------|-------------|
| `-v`, `--verbose` | Print NVAPI debug info and per-attempt I2C results |
| `-h`, `--help`    | Show help and exit |

### Examples

```
python lg_switch.py dp
python lg_switch.py usbc
python lg_switch.py --verbose hdmi1
python lg_switch.py scan
```

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

## Credits

Input values taken from [ddcutil wiki here](https://github.com/rockowitz/ddcutil/wiki/Switching-input-source-on-LG-monitors) and VCP code discovered from [community research](https://www.reddit.com/r/ultrawidemasterrace/comments/1kephki/comment/nrc6riz/) on ddcutil with `--i2c-source-addr=0x50` on Linux, and [BetterDisplay on MacOS](https://www.reddit.com/r/ultrawidemasterrace/comments/1kephki/comment/mr17it9/) using `--ddcAlt`.
