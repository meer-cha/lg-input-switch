@echo off
pip install pyinstaller
pyinstaller --onefile --console --name lg-input-switch lg_input_switch_launcher.py
echo.
echo Done. Executable is in the dist\ folder.
