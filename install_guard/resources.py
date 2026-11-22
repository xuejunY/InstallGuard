"""资源路径解析：开发态从工程目录读取，打包后从 PyInstaller 临时目录读取。"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    return Path(__file__).resolve().parent.parent / relative


def icon_path() -> Path:
    return resource_path("assets") / "guard.ico"
