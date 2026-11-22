"""轻量调试日志：记录监控与弹窗生命周期，便于排查一闪而过等问题。"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from . import config

_LOCK = threading.RLock()
_PATH = config.BASE_DIR / "debug.log"
_ENABLED = True


def log(tag: str, message: str) -> None:
    if not _ENABLED:
        return
    try:
        with _LOCK:
            config.BASE_DIR.mkdir(parents=True, exist_ok=True)
            line = "%s [%s] %s\n" % (
                datetime.now().strftime("%H:%M:%S.%f")[:-3],
                tag,
                message,
            )
            with open(_PATH, "a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError:
        pass


def path() -> Path:
    return _PATH
