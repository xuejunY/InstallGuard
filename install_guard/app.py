"""主程序编排：监控、弹窗决策、规则记忆、托盘与开机自启。"""
from __future__ import annotations

import atexit
import os
import sys
import threading
import winsound

from . import config, debuglog, detector, manager, monitor, processctl, storage, tray, ui


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    entry = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py"))
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    return '"%s" "%s"' % (pythonw, entry)


class App:
    def __init__(self) -> None:
        self.monitor: monitor.Monitor | None = None
        self.icon = None
        self._dialog_lock = threading.Lock()

    def on_candidate(self, detection: detector.Detection) -> str:
        """由监控线程调用；阻塞直到用户选择。"""
        with self._dialog_lock:
            cfg = storage.load_config()
            if not cfg.get("enabled", True):
                return "allow"
            if cfg.get("sound", True):
                try:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass

            decision = ui.show_confirm(detection)
            if decision == "allow_always":
                self._remember(detection)

            storage.append_history(
                {
                    "name": detection.name,
                    "path": detection.exe_path,
                    "publisher": detection.publisher,
                    "parent": detection.parent_name,
                    "level": detection.level,
                    "score": detection.score,
                    "signed": detection.signed,
                    "decision": decision,
                    "pid": detection.pid,
                }
            )
            return decision

    @staticmethod
    def _remember(detection: detector.Detection) -> None:
        if detection.exe_hash:
            rule = {"type": "hash", "value": detection.exe_hash, "note": detection.name}
        elif detection.publisher and "未知" not in detection.publisher:
            rule = {"type": "publisher", "value": detection.publisher, "note": detection.name}
        elif detection.exe_path:
            rule = {"type": "path", "value": detection.exe_path, "note": detection.name}
        else:
            return
        storage.add_allow(rule)

    def toggle_autostart(self) -> None:
        enabling = not storage.is_autostart_enabled()
        storage.set_autostart(enabling, _autostart_command() if enabling else "")

    def open_manager(self) -> None:
        manager.open_manager(self)

    def start(self) -> None:
        storage.ensure_dirs()
        atexit.register(processctl.release_all)
        # 单文件 exe 可被随意移动：每次启动把自启项纠正到当前实际位置。
        # 仅在打包运行时执行，避免开发/自检改写用户机器的注册表。
        if getattr(sys, "frozen", False):
            storage.sync_autostart(_autostart_command())
        self.monitor = monitor.Monitor(self.on_candidate)
        self.monitor.start()
        self.icon = tray.create_icon(self)
        self.icon.run()

    def stop(self) -> None:
        if self.monitor:
            self.monitor.stop()
        processctl.release_all()
        if self.icon:
            self.icon.stop()


def main() -> None:
    App().start()
