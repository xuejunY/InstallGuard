@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole --name InstallGuard --icon=assets\guard.ico --add-data "assets\guard.ico;assets" --hidden-import=win32com.client --hidden-import=pythoncom --hidden-import=win32api --hidden-import=win32con --hidden-import=win32gui main.py
echo.
if exist "dist\InstallGuard.exe" echo Build OK: dist\InstallGuard.exe
