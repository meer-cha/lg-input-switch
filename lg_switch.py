#!/usr/bin/env python3
"""
lg-switch — LG 45GX950A-B input switcher for Windows
"""

import argparse
import ctypes
import ctypes.wintypes
import json
import msvcrt
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

import pystray
from PIL import Image

# ---------------------------------------------------------------------------
# Input source values (LG-specific, sent via proprietary VCP code 0xF4)
# ---------------------------------------------------------------------------
INPUTS = {
    "dp":    (0xD0, "DisplayPort"),
    "hdmi1": (0x90, "HDMI 1"),
    "hdmi2": (0x91, "HDMI 2"),
    "usbc":  (0xD1, "USB-C / Thunderbolt"),
}

VCP_CODE        = 0xF4    # LG proprietary input-select code
DDC_DEVICE_ADDR = 0x6E    # 0x37 << 1  (DDC/CI destination address)
NVAPI_OK        = 0
NVAPI_MAX_GPUS  = 64

CONFIG_PATH = (
    Path(sys.executable).parent / "config.json"
    if getattr(sys, "frozen", False)          # running as PyInstaller .exe
    else Path(__file__).parent / "config.json"
)

_verbose = False

# ---------------------------------------------------------------------------
# Hotkey parsing (Win32 RegisterHotKey)
# ---------------------------------------------------------------------------
MODIFIERS: dict[str, int] = {
    "ctrl":    0x0002,
    "control": 0x0002,
    "alt":     0x0001,
    "shift":   0x0004,
    "win":     0x0008,
}
MOD_NOREPEAT = 0x4000

VK_CODES: dict[str, int] = {
    **{chr(c): 0x41 + i for i, c in enumerate(range(ord("a"), ord("z") + 1))},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
    "space":    0x20,
    "enter":    0x0D,
    "esc":      0x1B,
    "escape":   0x1B,
    "tab":      0x09,
    "insert":   0x2D,
    "delete":   0x2E,
    "home":     0x24,
    "end":      0x23,
    "pageup":   0x21,
    "pagedown": 0x22,
    "left":     0x25,
    "right":    0x27,
    "up":       0x26,
    "down":     0x28,
    **{f"numpad{d}": 0x60 + d for d in range(10)},
    "numpad*": 0x6A, "numpadmultiply": 0x6A,
    "numpad+": 0x6B, "numpadadd":      0x6B,
    "numpad-": 0x6D, "numpadsubtract": 0x6D,
    "numpad.": 0x6E, "numpaddecimal":  0x6E,
    "numpad/": 0x6F, "numpaddivide":   0x6F,
    # OEM symbols (US layout)
    ";":  0xBA, ":":  0xBA,
    "=":  0xBB, "+":  0xBB,
    ",":  0xBC, "<":  0xBC,
    "-":  0xBD, "_":  0xBD,
    ".":  0xBE, ">":  0xBE,
    "/":  0xBF, "?":  0xBF,
    "`":  0xC0, "~":  0xC0,
    "[":  0xDB, "{":  0xDB,
    "\\": 0xDC, "|":  0xDC,
    "]":  0xDD, "}":  0xDD,
    "'":  0xDE, "\"": 0xDE,
}


def parse_hotkey(hotkey: str) -> tuple[int, int]:
    """Parse 'ctrl+shift+d' into (modifier_flags, vk_code). Raises ValueError on bad input.

    Handles '+' as the key itself: 'ctrl++' or 'ctrl+shift++' — consecutive '+' signs
    (which produce empty tokens after split) are collapsed into a single '+' token.
    """
    raw_tokens = hotkey.split("+")
    # Collapse runs of empty strings (produced by ++ in input) into a single "+" token
    tokens = []
    i = 0
    while i < len(raw_tokens):
        if raw_tokens[i] == "":
            tokens.append("+")
            while i < len(raw_tokens) and raw_tokens[i] == "":
                i += 1
        else:
            tokens.append(raw_tokens[i].strip().lower())
            i += 1
    mods = 0
    vk   = None
    for token in tokens:
        if token in MODIFIERS:
            mods |= MODIFIERS[token]
        elif token in VK_CODES:
            if vk is not None:
                raise ValueError(f"multiple non-modifier keys in hotkey: '{hotkey}'")
            vk = VK_CODES[token]
        else:
            raise ValueError(f"unrecognised hotkey token: '{token}'")
    if vk is None:
        raise ValueError(f"hotkey has no non-modifier key: '{hotkey}'")

    _FKEY_VKS = frozenset(range(0x70, 0x7C))  # VK_F1–VK_F12

    if mods == 0 and vk == VK_CODES["esc"]:
        raise ValueError(
            "esc alone cannot be used as a hotkey — it is reserved for reconfiguring"
        )
    if mods == 0 and vk not in _FKEY_VKS:
        raise ValueError(
            "hotkey must include at least one modifier (ctrl, shift, alt, or win) "
            "to avoid intercepting regular typing"
        )

    # Block shift-only with any key that produces a typed character when shifted
    # (shift+/ = ?, shift+a = A, shift+1 = !, etc.)
    _SHIFTABLE_VKS = (
        frozenset(range(0x41, 0x5B)) |          # a–z
        frozenset(range(0x30, 0x3A)) |          # 0–9
        {0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF,   # OEM symbols (; = , - . /)
         0xC0, 0xDB, 0xDC, 0xDD, 0xDE}         # OEM symbols (` [ \ ] ')
    )
    if mods == MODIFIERS["shift"] and vk in _SHIFTABLE_VKS:
        raise ValueError(
            "shift alone with a character key would intercept normal typing "
            "(e.g. shift+/ types ?) — add another modifier like ctrl or alt"
        )

    # Block well-known shortcuts that would silently break normal workflow
    _BLOCKED: dict[tuple[int, int], str] = {
        (MODIFIERS["ctrl"], VK_CODES["c"]): "ctrl+c is reserved for stopping the daemon",
        (MODIFIERS["ctrl"], VK_CODES["v"]): "ctrl+v is a common paste shortcut",
        (MODIFIERS["ctrl"], VK_CODES["x"]): "ctrl+x is a common cut shortcut",
        (MODIFIERS["ctrl"], VK_CODES["z"]): "ctrl+z is a common undo shortcut",
        (MODIFIERS["ctrl"], VK_CODES["a"]): "ctrl+a is a common select all shortcut",
        (MODIFIERS["ctrl"], VK_CODES["s"]): "ctrl+s is a common save shortcut",
    }
    reason = _BLOCKED.get((mods, vk))
    if reason:
        raise ValueError(f"{reason} — choose a different combination")

    return mods, vk


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------
def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(
            f"error: config.json not found — run 'python lg_switch.py configure' first"
        )
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as exc:
        sys.exit(f"error: config.json is not valid JSON: {exc}")

    for key in ("hotkey", "inputs"):
        if key not in cfg:
            sys.exit(f"error: config.json is missing '{key}' — re-run configure")
    if not isinstance(cfg["inputs"], list) or len(cfg["inputs"]) != 2:
        sys.exit("error: config.json 'inputs' must be a list of exactly two input names")
    for inp in cfg["inputs"]:
        if inp not in INPUTS:
            sys.exit(f"error: config.json contains unknown input '{inp}'")
    try:
        parse_hotkey(cfg["hotkey"])
    except ValueError as exc:
        sys.exit(f"error: config.json hotkey invalid: {exc}")

    return cfg


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Windows startup (HKCU registry — no admin rights required)
# ---------------------------------------------------------------------------
_STARTUP_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_REG_NAME = "lg-input-switch"


def _get_startup() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY) as key:
            winreg.QueryValueEx(key, _STARTUP_REG_NAME)
            return True
    except FileNotFoundError:
        return False


def _set_startup(enabled: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _STARTUP_REG_NAME, 0, winreg.REG_SZ, sys.executable)
        else:
            try:
                winreg.DeleteValue(key, _STARTUP_REG_NAME)
            except FileNotFoundError:
                pass


def log(msg: str) -> None:
    if _verbose:
        print(msg)


# ---------------------------------------------------------------------------
# DDC/CI SetVCP packet
#
# LG 45GX950A-B requires source address 0x50 in the DDC/CI packet.
# The Windows DDC/CI API hardcodes 0x51, which the monitor silently ignores.
# We construct the packet manually and inject it via NVAPI raw I2C to
# bypass the Windows stack entirely.
#
# Packet layout:  [src_addr, length, opcode, vcp_code, value_hi, value_lo, checksum]
# Checksum:       XOR of DDC_DEVICE_ADDR and all preceding payload bytes.
# ---------------------------------------------------------------------------
def _build_setvcp(vcp_code: int, value: int) -> list[int]:
    vh  = (value >> 8) & 0xFF
    vl  = value & 0xFF
    pkt = [0x50, 0x84, 0x03, vcp_code, vh, vl]
    checksum = DDC_DEVICE_ADDR
    for b in pkt:
        checksum ^= b
    pkt.append(checksum)
    return pkt


# ---------------------------------------------------------------------------
# NV_I2C_INFO_V3 ctypes struct
#
# The layout must match the NVIDIA SDK header exactly. On 64-bit Windows,
# two consecutive uint8 fields at offsets 8–9 are followed by 6 bytes of
# implicit compiler padding before the first pointer at offset 16.
# We model this explicitly to avoid ctypes alignment surprises.
# ---------------------------------------------------------------------------
class _NV_I2C_INFO(ctypes.Structure):
    _fields_ = [
        ("version",          ctypes.c_uint32),
        ("displayMask",      ctypes.c_uint32),
        ("bIsDDCPort",       ctypes.c_uint8),
        ("i2cDevAddress",    ctypes.c_uint8),
        ("_pad",             ctypes.c_uint8 * 6),
        ("pbI2cRegAddress",  ctypes.c_void_p),
        ("regAddrSize",      ctypes.c_uint32),
        ("_pad2",            ctypes.c_uint32),
        ("pbData",           ctypes.c_void_p),
        ("cbSize",           ctypes.c_uint32),
        ("i2cSpeed",         ctypes.c_uint32),
        ("i2cSpeedKhz",      ctypes.c_uint32),
        ("portId",           ctypes.c_uint8),
        ("_pad3",            ctypes.c_uint8 * 3),
        ("bIsPortIdSet",     ctypes.c_uint32),
    ]

_NV_I2C_VER3 = (3 << 16) | ctypes.sizeof(_NV_I2C_INFO)


# ---------------------------------------------------------------------------
# NVAPI bootstrap helpers
# ---------------------------------------------------------------------------
def _k32() -> ctypes.WinDLL:
    k = ctypes.WinDLL("kernel32")
    k.GetProcAddress.restype  = ctypes.c_void_p
    k.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.GetModuleFileNameW.restype  = ctypes.c_uint32
    k.GetModuleFileNameW.argtypes = [ctypes.c_void_p,
                                      ctypes.c_wchar_p, ctypes.c_uint32]
    return k


def _load_nvapi() -> ctypes.CDLL:
    try:
        lib = ctypes.CDLL("nvapi64.dll")
    except OSError:
        sys.exit("error: nvapi64.dll not found — NVIDIA drivers required")

    if _verbose:
        k   = _k32()
        buf = ctypes.create_unicode_buffer(512)
        k.GetModuleFileNameW(ctypes.c_void_p(lib._handle), buf, 512)
        log(f"[debug] nvapi64.dll path : {buf.value}")
        log(f"[debug] NV_I2C_INFO size : {ctypes.sizeof(_NV_I2C_INFO)} bytes")
        log(f"[debug] version field    : 0x{_NV_I2C_VER3:08X}")

    return lib


def _resolve(lib: ctypes.CDLL, func_id: int):
    """Resolve an NVAPI function pointer via nvapi_QueryInterface."""
    k      = _k32()
    handle = ctypes.c_void_p(lib._handle)

    qi_addr = None
    for name in (b"nvapi_QueryInterface", b"nvapi64_QueryInterface"):
        addr = k.GetProcAddress(
            handle, ctypes.cast(ctypes.c_char_p(name), ctypes.c_void_p).value
        )
        if addr:
            qi_addr = addr
            break

    if not qi_addr:
        sys.exit("error: cannot find nvapi_QueryInterface in nvapi64.dll")

    qi  = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint32)(qi_addr)
    ptr = qi(func_id)
    if not ptr:
        raise RuntimeError(f"QueryInterface returned NULL for 0x{func_id:08X}")
    return ptr


# ---------------------------------------------------------------------------
# High-level NVAPI operations
# ---------------------------------------------------------------------------
def _nvapi_setup(lib: ctypes.CDLL):
    """Initialise NVAPI, return (gpu_handle, display_mask)."""
    NvAPI_Init = ctypes.CFUNCTYPE(ctypes.c_int)(_resolve(lib, 0x0150E828))
    if NvAPI_Init() != NVAPI_OK:
        sys.exit("error: NvAPI_Initialize failed")
    log("[debug] NvAPI initialised")

    NvAPI_EnumGPUs = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
    )(_resolve(lib, 0xE5AC921F))

    gpu_arr   = (ctypes.c_void_p * NVAPI_MAX_GPUS)()
    gpu_count = ctypes.c_uint32(0)
    if NvAPI_EnumGPUs(gpu_arr, ctypes.byref(gpu_count)) != NVAPI_OK or gpu_count.value == 0:
        sys.exit("error: no NVIDIA GPUs found")
    log(f"[debug] {gpu_count.value} GPU(s) — using GPU 0")
    gpu = gpu_arr[0]

    NvAPI_GetOutputs = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
    )(_resolve(lib, 0x1730BFC9))

    mask_val = ctypes.c_uint32(0)
    ret = NvAPI_GetOutputs(gpu, ctypes.byref(mask_val))
    if ret != NVAPI_OK or mask_val.value == 0:
        log(f"[debug] GetConnectedOutputs returned 0x{mask_val.value:08X} (ret={ret}), using fallback masks")
        masks = [1 << i for i in range(8)]
    else:
        masks = [1 << i for i in range(32) if mask_val.value & (1 << i)]
        log(f"[debug] connected output mask = 0x{mask_val.value:08X}  bits: {[hex(m) for m in masks]}")

    return gpu, masks


def _i2c_write(lib: ctypes.CDLL, gpu, masks: list[int], packet: list[int]) -> bool:
    """Send a raw DDC/CI packet via NVAPI I2C. Returns True on success."""
    NvAPI_I2CWrite = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_NV_I2C_INFO)
    )(_resolve(lib, 0xE812EB07))

    data_buf = (ctypes.c_uint8 * len(packet))(*packet)
    success = False

    for mask in masks:
        for port_id, port_set in [(0, 0), (1, 1), (2, 1), (3, 1),
                                   (4, 1), (5, 1), (6, 1), (7, 1)]:
            info = _NV_I2C_INFO()
            info.version         = _NV_I2C_VER3
            info.displayMask     = mask
            info.bIsDDCPort      = 1
            info.i2cDevAddress   = DDC_DEVICE_ADDR
            info.pbI2cRegAddress = None
            info.regAddrSize     = 0
            info.pbData          = ctypes.cast(data_buf, ctypes.c_void_p).value
            info.cbSize          = len(packet)
            info.i2cSpeed        = 0xFFFF
            info.i2cSpeedKhz     = 0
            info.portId          = port_id
            info.bIsPortIdSet    = port_set

            ret = NvAPI_I2CWrite(gpu, ctypes.byref(info))
            log(f"[debug]   mask=0x{mask:04X} port={port_id}(set={port_set}) -> "
                f"{'OK' if ret == NVAPI_OK else f'err {ret}'}")

            if ret == NVAPI_OK:
                success = True

    return success


# ---------------------------------------------------------------------------
# configure / daemon commands
# ---------------------------------------------------------------------------
_RESET   = "\033[0m"
_BOLD    = "\033[1m"
_DIM     = "\033[2m"
_RED     = "\033[91m"
_GREEN   = "\033[92m"
_YELLOW  = "\033[93m"
_CYAN    = "\033[96m"
_MAGENTA = "\033[95m"


def _clear() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _fmt_hotkey(raw: str) -> str:
    return f"{_MAGENTA}{raw}{_RESET}"


def _fmt_input(key: str) -> str:
    return f"{_CYAN}{_BOLD}{key}{_RESET}"


def _show_context(ctx: dict) -> None:
    for label, value in ctx.items():
        print(f"  {_DIM}{label}:{_RESET} {value}")
    print()


def _pick_input(prompt: str, exclude: str | None = None,
                ctx: dict | None = None, allow_back: bool = True) -> str | None:
    """Arrow-key selector. Returns chosen key, or None if ESC pressed."""
    options = [k for k in INPUTS if k != exclude]
    idx     = 0
    n_lines = len(options) + 1   # option rows + hints row

    def render(first: bool = False) -> None:
        if not first:
            sys.stdout.write(f"\033[{n_lines}A")
        for i, key in enumerate(options):
            label = INPUTS[key][1]
            if i == idx:
                line = f"  {_GREEN}>{_RESET} {_CYAN}{_BOLD}{key:<8}{_RESET} {_CYAN}{label}{_RESET}"
            else:
                line = f"    {_DIM}{key:<8} {label}{_RESET}"
            sys.stdout.write(f"\033[2K{line}\n")
        esc_desc = "exit" if not allow_back else "go back"
        hints = (
            f"  {_YELLOW}↑ ↓{_RESET}{_DIM} navigate{_RESET}"
            f"   {_YELLOW}Enter{_RESET}{_DIM} select{_RESET}"
            f"   {_YELLOW}ESC{_RESET}{_DIM} {esc_desc}{_RESET}"
        )
        sys.stdout.write(f"\033[2K{hints}\n")
        sys.stdout.flush()

    _clear()
    if ctx:
        _show_context(ctx)
    print(f"{_BOLD}{prompt}{_RESET}\n")
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    render(first=True)

    try:
        while True:
            ch = msvcrt.getch()
            if ch in (b"\xe0", b"\x00"):
                ch2 = msvcrt.getch()
                if ch2 == b"H":
                    idx = (idx - 1) % len(options)
                    render()
                elif ch2 == b"P":
                    idx = (idx + 1) % len(options)
                    render()
            elif ch == b"\r":
                return options[idx]
            elif ch == b"\x1b":
                return None
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def _prompt_hotkey(ctx: dict | None = None, error: str | None = None) -> str | None:
    """Read a hotkey string character by character. Returns raw string or None if ESC pressed."""
    _clear()
    if ctx:
        _show_context(ctx)
    _example = "ctrl+shift+d"
    _tokens  = _example.split("+")
    _parts   = [f"{_RESET}{_MAGENTA}{t}{_RESET}" for t in _tokens]
    _listed  = (
        f"{_DIM}, {_RESET}".join(_parts[:-1]) + f"{_DIM} and {_RESET}" + _parts[-1]
    )
    print(f"{_BOLD}Hotkey — type it as text, e.g. {_fmt_hotkey(_example)}{_BOLD}:{_RESET}")
    print(f"  {_DIM}the toggle will trigger when you press {_RESET}{_listed}{_DIM} together{_RESET}")
    hints = (
        f"  {_YELLOW}Enter{_RESET}{_DIM} confirm{_RESET}"
        f"   {_YELLOW}ESC{_RESET}{_DIM} go back{_RESET}"
    )
    print(hints)
    if error:
        print(f"\n  {_YELLOW}{error}{_RESET}")
    sys.stdout.write(f"\n  {_MAGENTA}")
    sys.stdout.flush()

    chars: list[str] = []
    try:
        while True:
            ch = msvcrt.getch()
            if ch == b"\x1b":
                return None
            elif ch == b"\r":
                sys.stdout.write(_RESET + "\n")
                sys.stdout.flush()
                return "".join(chars)
            elif ch in (b"\xe0", b"\x00"):
                msvcrt.getch()  # discard arrow key second byte
            elif ch == b"\x08":  # backspace
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            elif 0x20 <= ch[0] <= 0x7E:
                c = ch.decode("latin-1")
                chars.append(c)
                sys.stdout.write(c)
                sys.stdout.flush()
    finally:
        sys.stdout.write(_RESET)
        sys.stdout.flush()


def cmd_configure() -> None:
    """Interactive setup — writes config.json."""
    current      = None
    target       = None
    raw          = None
    step         = 0
    hotkey_error = None

    while True:
        if step == 0:
            result = _pick_input("Which input are you currently on?", allow_back=False)
            if result is None:   # ESC on first step = exit
                _clear()
                print("Exiting setup.")
                sys.exit(0)
            current      = result
            hotkey_error = None
            step         = 1

        elif step == 1:
            result = _pick_input(
                "Which input do you want to toggle to?",
                exclude=current,
                ctx={"Currently on": _fmt_input(current)},
            )
            if result is None:   # ESC = go back
                step = 0
            else:
                target       = result
                hotkey_error = None
                step         = 2

        elif step == 2:
            result = _prompt_hotkey(
                ctx={
                    "Currently on": _fmt_input(current),
                    "Toggles to":   _fmt_input(target),
                },
                error=hotkey_error,
            )
            if result is None:   # ESC = go back
                hotkey_error = None
                step         = 1
                continue
            raw = result.strip()
            if not raw:
                hotkey_error = "Hotkey cannot be empty."
                continue
            try:
                parse_hotkey(raw)
                step = 3
            except ValueError as exc:
                hotkey_error = str(exc)

        elif step == 3:
            yn_options = ["Yes", "No"]
            yn_idx     = 0 if _get_startup() else 1
            n_lines    = len(yn_options) + 1

            def render_yn(first: bool = False) -> None:
                if not first:
                    sys.stdout.write(f"\033[{n_lines}A")
                for i, opt in enumerate(yn_options):
                    if i == yn_idx:
                        line = f"  {_GREEN}>{_RESET} {_CYAN}{_BOLD}{opt}{_RESET}"
                    else:
                        line = f"    {_DIM}{opt}{_RESET}"
                    sys.stdout.write(f"\033[2K{line}\n")
                hints = (
                    f"  {_YELLOW}↑ ↓{_RESET}{_DIM} navigate{_RESET}"
                    f"   {_YELLOW}Enter{_RESET}{_DIM} select{_RESET}"
                    f"   {_YELLOW}ESC{_RESET}{_DIM} go back{_RESET}"
                )
                sys.stdout.write(f"\033[2K{hints}\n")
                sys.stdout.flush()

            _clear()
            _show_context({
                "Currently on": _fmt_input(current),
                "Toggles to":   _fmt_input(target),
                "Hotkey":        _fmt_hotkey(raw),
            })
            print(f"{_BOLD}Start with Windows?{_RESET}\n")
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()
            render_yn(first=True)

            try:
                while True:
                    ch = msvcrt.getch()
                    if ch in (b"\xe0", b"\x00"):
                        ch2 = msvcrt.getch()
                        if ch2 == b"H":
                            yn_idx = (yn_idx - 1) % len(yn_options)
                            render_yn()
                        elif ch2 == b"P":
                            yn_idx = (yn_idx + 1) % len(yn_options)
                            render_yn()
                    elif ch == b"\r":
                        _set_startup(yn_idx == 0)
                        break
                    elif ch == b"\x1b":
                        step = 2
                        break
            finally:
                sys.stdout.write("\033[?25h")
                sys.stdout.flush()

            if step == 2:
                continue
            break

    cfg = {"hotkey": raw, "inputs": [current, target], "last_input": current}
    _save_config(cfg)
    _clear()
    print(f"  {_DIM}hotkey :{_RESET} {_fmt_hotkey(raw)}")
    print(f"  {_DIM}toggle :{_RESET} {_fmt_input(current)} {_DIM}↔{_RESET} {_fmt_input(target)}")
    print()


def _create_icon_image() -> Image.Image:
    """Load the icon.png file from disk or PyInstaller bundle."""
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    icon_path = exe_dir / "icon.png"
    if icon_path.exists():
        return Image.open(icon_path).convert("RGBA")
    if getattr(sys, "frozen", False):
        return Image.open(Path(sys._MEIPASS) / "icon.png").convert("RGBA")
    return Image.open(icon_path).convert("RGBA")


def cmd_daemon() -> None:
    """Listen for the configured hotkey and operate from the system tray."""
    cfg      = _load_config()
    mods, vk = parse_hotkey(cfg["hotkey"])
    inputs   = cfg["inputs"]

    _stop_event = threading.Event()

    def hotkey_listener():
        lib        = _load_nvapi()
        gpu, masks = _nvapi_setup(lib)

        user32    = ctypes.WinDLL("user32")
        WM_HOTKEY = 0x0312
        HOTKEY_ID = 1

        if not user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
            print(f"error: RegisterHotKey failed for '{cfg['hotkey']}'")
            import os
            os._exit(1)

        PM_REMOVE = 0x0001
        msg = ctypes.wintypes.MSG()

        while not _stop_event.is_set():
            if not user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                time.sleep(0.05)
                continue
            if msg.message == 0x0012:  # WM_QUIT
                break
            if msg.message != WM_HOTKEY:
                continue

            last   = cfg.get("last_input")
            target = inputs[1] if last == inputs[0] else inputs[0]

            value, label = INPUTS[target]
            packet = _build_setvcp(VCP_CODE, value)
            log(f"[debug] packet: {[f'0x{b:02X}' for b in packet]}")

            if _i2c_write(lib, gpu, masks, packet):
                log(f"[info] switched to {target} ({label})")
                cfg["last_input"] = target
                _save_config(cfg)
            else:
                log(f"[error] failed to switch to {label}")

        user32.UnregisterHotKey(None, HOTKEY_ID)

    t = threading.Thread(target=hotkey_listener, daemon=True)
    t.start()

    def on_quit(icon, item):
        _stop_event.set()
        icon.stop()

    def on_configure(icon, item):
        # Fire up a new command prompt to run the configure wizard
        if getattr(sys, "frozen", False):
            args = [sys.executable, "configure"]
        else:
            py_exe = str(Path(sys.executable).with_name("python.exe"))
            args = [py_exe, __file__, "configure"]
        
        subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE)

    icon = pystray.Icon(
        "lg-switch", 
        _create_icon_image(), 
        "LG Input Switch\nHotkey active", 
        menu=pystray.Menu(
            pystray.MenuItem(
                "Start with Windows",
                lambda icon, item: _set_startup(not _get_startup()),
                checked=lambda item: _get_startup()
            ),
            pystray.MenuItem("Configure", on_configure),
            pystray.MenuItem("Quit", on_quit)
        )
    )

    print(f"Running as daemon in system tray. Listening for {cfg['hotkey']}...")
    try:
        icon.run()
    except KeyboardInterrupt:
        on_quit(icon, None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lg-switch",
        description=(
            "Switch the active input on an LG 45GX950A-B monitor.\n\n"
            "Uses NVAPI raw I2C to send DDC/CI commands with source address 0x50,\n"
            "bypassing the Windows DDC/CI API which the LG silently ignores."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join([
            "inputs:",
            *[f"  {k:<12} {desc}" for k, (_, desc) in INPUTS.items()],
            "",
            "commands:",
            "  scan         verify monitor is detected on I2C bus",
            "  configure    interactive setup: choose two inputs and a hotkey",
            "  daemon       listen for configured hotkey and toggle inputs",
            "",
            "examples:",
            "  lg-switch dp",
            "  lg-switch usbc",
            "  lg-switch --verbose hdmi1",
            "  lg-switch scan",
            "  lg-switch configure",
            "  lg-switch daemon",
        ]),
    )
    parser.add_argument(
        "input",
        choices=[*INPUTS.keys(), "scan", "configure", "daemon"],
        metavar="input",
        help=f"target input or command: {{{', '.join(INPUTS)}, scan, configure, daemon}}",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="print NVAPI debug info and per-attempt results",
    )
    return parser


def main() -> None:
    global _verbose

    parser = _build_parser()
    args   = parser.parse_args()
    _verbose = args.verbose

    if args.input == "configure":
        cmd_configure()
        return

    if args.input == "daemon":
        cmd_daemon()
        return

    lib        = _load_nvapi()
    gpu, masks = _nvapi_setup(lib)

    if args.input == "scan":
        print(f"connected output mask: 0x{sum(masks):08X}")
        print(f"output bit(s):         {[hex(m) for m in masks]}")
        return

    value, label = INPUTS[args.input]
    packet = _build_setvcp(VCP_CODE, value)
    log(f"[debug] packet: {[f'0x{b:02X}' for b in packet]}")

    if _i2c_write(lib, gpu, masks, packet):
        print(f"switched to {label}")
    else:
        sys.exit(f"error: failed to switch to {label} — run with --verbose for details")


if __name__ == "__main__":
    main()
