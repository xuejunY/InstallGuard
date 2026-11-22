"""弹窗交互冒烟测试：自动发送「阻止」点击，验证窗口与消息循环。"""
from __future__ import annotations

import threading
import time

import win32api
import win32con
import win32gui

from install_guard.detector import Detection
from install_guard.ui import show_confirm

detection = Detection(
    suspicious=True,
    pid=4242,
    name="全能PDF转换器 setup.exe",
    exe_path=r"C:\Users\admin\Downloads\setup.exe",
    publisher="未知（无签名）",
    parent_name="chrome.exe",
    score=95,
    level="high",
    reasons=["没有有效的数字签名", "来自下载或临时文件夹", "由浏览器直接启动"],
)


def auto_click() -> None:
    time.sleep(1.5)
    hwnd = win32gui.FindWindow(None, "安装守卫 · 请确认")
    print("window found:", bool(hwnd), "title:", win32gui.GetWindowText(hwnd) if hwnd else "-")
    if hwnd:
        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, win32api.MAKELONG(1003, 0), 0)


threading.Thread(target=auto_click, daemon=True).start()
decision = show_confirm(detection)
print("decision:", decision)
assert decision == "block", "弹窗未正确返回阻止决策"
print("POPUP TEST PASSED")
