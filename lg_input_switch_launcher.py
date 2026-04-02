"""
Single entry point for the lg-input-switch executable.
Runs configure if no valid config exists, then starts the daemon.
"""
import sys
import ctypes

def check_and_alloc_console():
    """Allocate a console if one does not exist (for --noconsole builds)."""
    if ctypes.windll.kernel32.GetConsoleWindow() == 0:
        ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8")
        sys.stderr = open("CONOUT$", "w", buffering=1, encoding="utf-8")
        
        # Enable VT100 ANSI processing for Windows consoles
        STD_OUTPUT_HANDLE = -11
        handle = ctypes.windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint32()
        ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)

def main() -> None:
    # If the user passed arguments, allocate a console if needed, run CLI, then exit.
    if len(sys.argv) > 1:
        check_and_alloc_console()
        import lg_switch
        lg_switch.main()
        return

    from lg_switch import (
        CONFIG_PATH, _load_config, cmd_configure, cmd_daemon,
        _clear, _get_startup, _GREEN, _DIM, _RESET
    )

    valid = False
    if CONFIG_PATH.exists():
        try:
            _load_config()
            valid = True
        except SystemExit:
            pass  # config exists but is invalid — re-run configure

    while True:
        if not valid:
            check_and_alloc_console()
            cmd_configure()
            _clear()
            print("Setup complete! The tool is now hiding in your System Tray.\n")
            valid = True

        # If we had allocated a console for setup, we can detach/free it now so the daemon runs silently.
        if ctypes.windll.kernel32.GetConsoleWindow() != 0:
            ctypes.windll.kernel32.FreeConsole()

        cmd_daemon()
        break


if __name__ == "__main__":
    main()
