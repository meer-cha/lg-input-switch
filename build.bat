@echo off
pip install -r requirements.txt
python -m PyInstaller --onefile --noconsole --name lg-input-switch --add-data "icon.png;." lg_input_switch_launcher.py
echo.
echo Done. Executable is in the dist\ folder.
