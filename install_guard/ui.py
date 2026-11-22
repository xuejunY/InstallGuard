"""原生 Win32 确认弹窗：置顶、默认焦点在「阻止」、无第三方 GUI 依赖。

注意：消息循环使用 PumpWaitingMessages + 完成标志，绝不使用 PostQuitMessage。
PostQuitMessage 投递的 WM_QUIT 会残留在线程队列，导致同一线程后续弹窗一闪而过。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui

from . import debuglog, icons
from .detector import Detection

_ID_ALWAYS = 1001
_ID_ALLOW = 1002
_ID_BLOCK = 1003

_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR,
]
_gdi32.CreateFontW.restype = wintypes.HANDLE

_UI_LOCK = threading.Lock()
_MIN_DISPLAY_SECONDS = 0.8


def _create_font(height: int, weight: int):
    return _gdi32.CreateFontW(height, 0, 0, 0, weight, 0, 0, 0, "Microsoft YaHei")


_WIDTH = 500
_HEIGHT = 348

_LEVEL_COLOR = {
    "high": (163, 45, 45),
    "medium": (186, 117, 23),
    "low": (24, 95, 165),
}
_LEVEL_ADVICE = {
    "high": "建议阻止。没有可信签名且来源可疑，流氓软件通常有这些特征。",
    "medium": "请确认这是你主动下载的软件，再决定是否允许。",
    "low": "看起来是正常安装，但仍请确认是你本人主动操作的。",
}


def _clip(text: str, limit: int = 62) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def show_confirm(detection: Detection) -> str:
    """显示弹窗并阻塞到用户做出选择，返回 allow / allow_always / block。"""
    with _UI_LOCK:
        debuglog.log("POPUP", "显示弹窗 pid=%s name=%s level=%s" % (detection.pid, detection.name, detection.level))
        return _show_confirm_locked(detection)


def _show_confirm_locked(detection: Detection) -> str:
    state = {
        "value": "block",
        "done": False,
        "shown_at": time.monotonic(),
        "hwnd": 0,
    }
    risk_hwnd = [0]

    def decide(value: str) -> None:
        elapsed = time.monotonic() - state["shown_at"]
        if elapsed < _MIN_DISPLAY_SECONDS:
            debuglog.log("POPUP", "忽略过早操作 %s (%.2fs)" % (value, elapsed))
            return
        if state["done"]:
            return
        state["value"] = value
        state["done"] = True
        debuglog.log("POPUP", "用户决策: %s" % value)

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == win32con.WM_COMMAND:
            control_id = win32api.LOWORD(wparam)
            if control_id == _ID_ALWAYS:
                decide("allow_always")
            elif control_id == _ID_ALLOW:
                decide("allow")
            elif control_id == _ID_BLOCK:
                decide("block")
            else:
                return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
            return 0
        if msg == win32con.WM_CLOSE:
            decide("block")
            return 0
        if msg == win32con.WM_CTLCOLORSTATIC:
            win32gui.SetBkMode(wparam, win32con.TRANSPARENT)
            color = _LEVEL_COLOR.get(detection.level, _LEVEL_COLOR["low"])
            if risk_hwnd[0] and lparam == risk_hwnd[0]:
                win32gui.SetTextColor(wparam, win32api.RGB(*color))
            return win32gui.GetSysColorBrush(win32con.COLOR_WINDOW)
        if msg == win32con.WM_DESTROY:
            debuglog.log("POPUP", "窗口销毁")
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    # 每次用唯一类名，确保窗口过程绑定到本次的 state，避免复用旧闭包。
    window_class = "InstallGuardPopup_%d" % int(time.monotonic_ns() & 0xFFFFFFFF)
    wrapper = win32gui.WNDCLASS()
    wrapper.hInstance = win32api.GetModuleHandle(None)
    wrapper.lpszClassName = window_class
    wrapper.lpfnWndProc = wndproc
    wrapper.hbrBackground = win32con.COLOR_WINDOW + 1
    wrapper.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    wrapper.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
    try:
        win32gui.RegisterClass(wrapper)
    except win32gui.error:
        pass

    screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    screen_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
    left = max(0, (screen_w - _WIDTH) // 2)
    top = max(0, (screen_h - _HEIGHT) // 2)

    hwnd = win32gui.CreateWindowEx(
        win32con.WS_EX_TOPMOST | win32con.WS_EX_DLGMODALFRAME,
        window_class,
        "安装守卫 · 请确认",
        win32con.WS_POPUP | win32con.WS_CAPTION | win32con.WS_SYSMENU,
        left, top, _WIDTH, _HEIGHT,
        0, 0, wrapper.hInstance, None,
    )
    state["hwnd"] = hwnd
    icons.set_window_icon(hwnd)

    font_bold = _create_font(19, win32con.FW_BOLD)
    font_normal = _create_font(15, win32con.FW_NORMAL)

    def add_static(text: str, x: int, y: int, w: int, h: int, font=font_normal):
        handle = win32gui.CreateWindow(
            "Static", text,
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.SS_LEFT,
            x, y, w, h, hwnd, 0, wrapper.hInstance, None,
        )
        win32gui.SendMessage(handle, win32con.WM_SETFONT, font, 1)
        return handle

    def add_button(text: str, x: int, y: int, w: int, command_id: int):
        handle = win32gui.CreateWindow(
            "Button", text,
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
            x, y, w, 34, hwnd, command_id, wrapper.hInstance, None,
        )
        win32gui.SendMessage(handle, win32con.WM_SETFONT, font_normal, 1)
        return handle

    add_static("!", 20, 16, 30, 30, font_bold)
    add_static("检测到软件准备安装", 60, 16, 410, 28, font_bold)
    add_static("请确认是否允许。不确定时建议点「阻止安装」。", 60, 46, 420, 22)
    win32gui.CreateWindow(
        "Static", "",
        win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.SS_ETCHEDHORZ,
        20, 77, 460, 2, hwnd, 0, wrapper.hInstance, None,
    )

    add_static("程序名称：" + _clip(detection.name), 24, 92, 450, 20)
    add_static("发行者：" + _clip(detection.publisher), 24, 114, 450, 20)
    add_static("来源：" + _clip(detection.source_text), 24, 136, 450, 20)
    add_static("路径：" + _clip(detection.exe_path, 58), 24, 158, 450, 20)

    reasons = "；".join(detection.reasons[:3]) or "未发现明显风险特征"
    risk_hwnd[0] = add_static(
        detection.level_text + "：" + _clip(reasons, 52) + "\n" + _LEVEL_ADVICE.get(detection.level, ""),
        24, 192, 452, 52,
    )

    button_block = add_button("阻止安装（推荐）", 318, 272, 158, _ID_BLOCK)
    add_button("允许本次", 216, 272, 94, _ID_ALLOW)
    add_button("始终允许此软件", 24, 272, 184, _ID_ALWAYS)

    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, left, top, _WIDTH, _HEIGHT, 0)
    win32gui.SendMessage(hwnd, win32con.WM_NEXTDLGCTL, button_block, 1)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        pass
    state["shown_at"] = time.monotonic()
    debuglog.log("POPUP", "弹窗已显示 hwnd=%s" % hwnd)

    # 关键：不用 PumpMessages/PostQuitMessage，避免 WM_QUIT 残留导致后续弹窗一闪而过。
    keep_alive_ticks = 0
    while not state["done"]:
        win32gui.PumpWaitingMessages()
        time.sleep(0.01)
        keep_alive_ticks += 1
        if keep_alive_ticks % 100 == 0 and win32gui.IsWindow(hwnd):
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)

    if win32gui.IsWindow(hwnd):
        win32gui.DestroyWindow(hwnd)
    debuglog.log("POPUP", "弹窗结束，返回 %s" % state["value"])
    return state["value"]
