"""全局配置与路径定义。"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "InstallGuard"
APP_TITLE = "安装守卫"
TRAY_TITLE = "安装守卫 · 运行中"

APPDATA_DIR = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
BASE_DIR = APPDATA_DIR / APP_NAME
RULES_PATH = BASE_DIR / "rules.json"
HISTORY_PATH = BASE_DIR / "history.jsonl"
CONFIG_PATH = BASE_DIR / "config.json"

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = APP_NAME

DEFAULT_CONFIG = {
    "enabled": True,
    "poll_interval": 0.3,
    "sound": True,
    "autostart": False,
}

DEFAULT_RULES = {
    "allow": [],
    "block": [],
}

# 这些进程永不挂起，避免把系统搞崩。
PROTECTED_PROCESSES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
    "explorer.exe", "dwm.exe", "fontdrvhost.exe", "sihost.exe",
    "ctfmon.exe", "spoolsv.exe", "audiodg.exe", "conhost.exe",
    "taskmgr.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "runtimebroker.exe", "applicationframehost.exe",
    "python.exe", "pythonw.exe", "install_guard.exe", "pyinstaller.exe",
}
