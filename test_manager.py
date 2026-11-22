"""管理界面冒烟测试：打开窗口、切换标签、关闭。"""
from __future__ import annotations

import threading
import time

import win32api
import win32con
import win32gui

from install_guard import manager

TITLE = "安装守卫 · 管理中心"


class FakeApp:
    def toggle_autostart(self):
        pass


def driver() -> None:
    deadline = time.monotonic() + 8
    hwnd = 0
    while time.monotonic() < deadline:
        hwnd = win32gui.FindWindow(None, TITLE)
        if hwnd:
            break
        time.sleep(0.1)
    print("window opened:", bool(hwnd), flush=True)
    if not hwnd:
        return
    time.sleep(0.8)
    for control_id in (3002, 3003, 3001):
        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, win32api.MAKELONG(control_id, 0), 0)
        time.sleep(0.5)
    print("tabs switched", flush=True)
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    print("close sent", flush=True)


threading.Thread(target=driver, daemon=True).start()
manager.open_manager(FakeApp())
time.sleep(4)
still_open = bool(win32gui.FindWindow(None, TITLE))
print("still open:", still_open, flush=True)
assert not still_open, "管理窗口未关闭"
print("MANAGER TEST PASSED")
