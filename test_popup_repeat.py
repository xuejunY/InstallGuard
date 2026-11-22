"""回归测试：连续两次弹窗，验证第二次不会因 WM_QUIT 残留一闪而过。"""
from __future__ import annotations

import threading
import time

import win32api
import win32con
import win32gui

from install_guard.detector import Detection
from install_guard.ui import show_confirm

detection = Detection(
    suspicious=True, pid=4242, name="测试软件 setup.exe",
    exe_path=r"C:\Users\admin\Downloads\setup.exe",
    publisher="未知", parent_name="chrome.exe",
    score=90, level="high", reasons=["测试"],
)


def clicker(last_hwnd: list, stop_event: threading.Event) -> None:
    """等待出现一个不同于上轮的新窗口，再发送点击。"""
    deadline = time.monotonic() + 8
    while not stop_event.is_set() and time.monotonic() < deadline:
        hwnd = win32gui.FindWindow(None, "安装守卫 · 请确认")
        if hwnd and hwnd != last_hwnd[0]:
            time.sleep(1.2)  # 等过最短显示时间
            if win32gui.IsWindow(hwnd):
                win32gui.PostMessage(hwnd, win32con.WM_COMMAND, win32api.MAKELONG(1003, 0), 0)
            return
        time.sleep(0.05)


last_hwnd = [0]
for round_no in (1, 2):
    stop = threading.Event()
    threading.Thread(target=clicker, args=(last_hwnd, stop), daemon=True).start()
    started = time.monotonic()
    decision = show_confirm(detection)
    elapsed = time.monotonic() - started
    stop.set()
    last_hwnd[0] = win32gui.FindWindow(None, "安装守卫 · 请确认")
    print("round %d decision=%s elapsed=%.2fs" % (round_no, decision, elapsed))
    assert decision == "block"
    assert elapsed > 1.0, "弹窗过早消失，WM_QUIT 残留问题未修复"

print("REPEAT POPUP TEST PASSED")
