"""
Single entry point for the lg-input-switch executable.
Runs configure if no valid config exists, then starts the daemon.
"""
import sys
from lg_switch import (
    CONFIG_PATH, _load_config, cmd_configure, cmd_daemon,
    _clear, _prompt_startup, _get_startup,
    _GREEN, _DIM, _RESET, _YELLOW,
)


def main() -> None:
    valid = False
    if CONFIG_PATH.exists():
        try:
            _load_config()
            valid = True
        except SystemExit:
            pass  # config exists but is invalid — re-run configure

    while True:
        if not valid:
            cmd_configure()
            _clear()
            print("Setup complete!\n")
            while _prompt_startup():  # ESC = go back to configure
                cmd_configure()
                _clear()
                print("Setup complete!\n")
            print()
            valid = True

        status = f"{_GREEN}enabled{_RESET}" if _get_startup() else f"{_DIM}disabled{_RESET}"
        print(f"  {_DIM}Start with Windows:{_RESET} {status}")
        print()

        if not cmd_daemon():
            break          # Ctrl+C — exit for real

        # ESC pressed in daemon — reconfigure
        valid = False


if __name__ == "__main__":
    main()
