"""管理界面：拦截记录、白名单规则、设置。

Win32 原生自绘界面，无第三方 GUI 依赖。视觉设计：
蓝色渐变顶栏 + 盾牌徽标 + 卡片分区 + 彩色风险徽章 + 底部状态栏。
控件 ID 与旧版保持一致，兼容既有测试与托盘入口。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import Any

import win32api
import win32con
import win32gui

from . import app as app_module
from . import config, debuglog, icons, storage

_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR,
]
_gdi32.CreateFontW.restype = wintypes.HANDLE


class _DrawItem(ctypes.Structure):
    _fields_ = [
        ("CtlType", wintypes.UINT), ("CtlID", wintypes.UINT),
        ("itemID", wintypes.UINT), ("itemAction", wintypes.UINT),
        ("itemState", wintypes.UINT), ("hwndItem", wintypes.HWND),
        ("hDC", wintypes.HDC), ("rcItem", wintypes.RECT),
        ("itemData", ctypes.c_size_t),
    ]


class _MeasureItem(ctypes.Structure):
    _fields_ = [
        ("CtlType", wintypes.UINT), ("CtlID", wintypes.UINT),
        ("itemID", wintypes.UINT), ("itemWidth", wintypes.UINT),
        ("itemHeight", wintypes.UINT), ("itemData", ctypes.c_size_t),
    ]


def _font(height: int, weight: int):
    return _gdi32.CreateFontW(height, 0, 0, 0, weight, 0, 0, 0, "Microsoft YaHei")


# 控件 ID（保持兼容）
_ID_TAB_HISTORY = 3001
_ID_TAB_RULES = 3002
_ID_TAB_SETTINGS = 3003
_ID_LIST_RECORDS = 3010
_ID_LIST_RULES = 3011
_ID_BTN_REFRESH = 3020
_ID_BTN_CLEAR_LOG = 3021
_ID_BTN_OPEN_LOG = 3022
_ID_BTN_ADD_RULE = 3030
_ID_BTN_DEL_RULE = 3031
_ID_COMBO_TYPE = 3032
_ID_EDIT_VALUE = 3033
_ID_EDIT_NOTE = 3034
_ID_CHK_ENABLED = 3040
_ID_CHK_SOUND = 3041
_ID_CHK_AUTOSTART = 3042
_ID_EDIT_INTERVAL = 3043
_ID_BTN_SAVE = 3050

_WIN_W = 900
_WIN_H = 700
_HEADER_H = 100
_TAB_Y = 108
_TAB_H = 38
_CONTENT_Y = 156
_STATUS_Y = 624

# 配色
C_BG = (245, 247, 251)
C_CARD = (255, 255, 255)
C_BORDER = (226, 232, 240)
C_TEXT = (30, 41, 59)
C_MUTED = (100, 116, 139)
C_PRIMARY = (37, 99, 235)
C_PRIMARY_DARK = (29, 78, 216)
C_PRIMARY_LIGHT = (219, 234, 254)
C_DANGER = (220, 38, 38)
C_DANGER_LIGHT = (254, 226, 226)
C_SUCCESS = (22, 163, 74)
C_WARN = (180, 83, 9)
C_WARN_LIGHT = (254, 243, 199)
C_ROW_ALT = (249, 250, 252)
C_SELECTED = (219, 234, 254)

LEVEL_STYLE = {
    "high": ("高风险", C_DANGER_LIGHT, C_DANGER, (239, 68, 68)),
    "medium": ("中风险", C_WARN_LIGHT, C_WARN, (245, 158, 11)),
    "low": ("低风险", C_PRIMARY_LIGHT, C_PRIMARY_DARK, (59, 130, 246)),
}

_INSTANCE_LOCK = threading.Lock()
_instance: "_ManagerWindow | None" = None


def _rgb(color):
    return win32api.RGB(*color)


def _fill(hdc, rect, color) -> None:
    brush = win32gui.CreateSolidBrush(_rgb(color))
    try:
        win32gui.FillRect(hdc, rect, brush)
    finally:
        win32gui.DeleteObject(brush)


def _round(hdc, rect, fill, outline=None, radius=12) -> None:
    def draw(r, color):
        brush = win32gui.CreateSolidBrush(_rgb(color))
        old = win32gui.SelectObject(hdc, brush)
        try:
            win32gui.RoundRect(hdc, r[0], r[1], r[2], r[3], radius, radius)
        finally:
            win32gui.SelectObject(hdc, old)
            win32gui.DeleteObject(brush)

    if outline:
        draw(rect, outline)
        x1, y1, x2, y2 = rect
        inner = (x1 + 2, y1 + 2, x2 - 2, y2 - 2)
    else:
        inner = rect
    draw(inner, fill)


def _text(hdc, text, rect, font, color, flags=0) -> None:
    old = win32gui.SelectObject(hdc, font)
    old_color = win32gui.SetTextColor(hdc, _rgb(color))
    old_mode = win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
    try:
        win32gui.DrawText(hdc, text, -1, rect, flags)
    finally:
        win32gui.SetTextColor(hdc, old_color)
        win32gui.SetBkMode(hdc, old_mode)
        win32gui.SelectObject(hdc, old)


def _lerp(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


class _ManagerWindow:
    def __init__(self, app) -> None:
        self.app = app
        self.hwnd = 0
        self.controls: dict[int, int] = {}
        self.static_colors: dict[int, tuple] = {}
        self.list_rows: dict[int, list] = {_ID_LIST_RECORDS: [], _ID_LIST_RULES: []}
        self.done = False
        self.current_tab = "history"

        self.font_title = _font(26, win32con.FW_BOLD)
        self.font_sub = _font(14, win32con.FW_NORMAL)
        self.font_h2 = _font(19, win32con.FW_BOLD)
        self.font_body = _font(15, win32con.FW_NORMAL)
        self.font_small = _font(13, win32con.FW_NORMAL)
        self.font_badge = _font(13, win32con.FW_BOLD)
        self.font_button = _font(15, win32con.FW_BOLD)
        self.brush_card = win32gui.CreateSolidBrush(_rgb(C_CARD))
        self.brush_white = win32gui.CreateSolidBrush(_rgb((255, 255, 255)))

    # ---------- 生命周期 ----------
    def show(self) -> None:
        wrapper = win32gui.WNDCLASS()
        wrapper.hInstance = win32api.GetModuleHandle(None)
        wrapper.lpszClassName = "InstallGuardManager"
        wrapper.lpfnWndProc = self._wndproc
        wrapper.hbrBackground = win32con.COLOR_WINDOW + 1
        wrapper.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        wrapper.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
        try:
            win32gui.RegisterClass(wrapper)
        except win32gui.error:
            pass

        screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        screen_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        left = max(0, (screen_w - _WIN_W) // 2)
        top = max(0, (screen_h - _WIN_H) // 2)

        self.hwnd = win32gui.CreateWindowEx(
            0, "InstallGuardManager", "安装守卫 · 管理中心",
            win32con.WS_OVERLAPPED | win32con.WS_CAPTION | win32con.WS_SYSMENU
            | win32con.WS_MINIMIZEBOX | win32con.WS_CLIPCHILDREN,
            left, top, _WIN_W, _WIN_H, 0, 0, wrapper.hInstance, None,
        )
        icons.set_window_icon(self.hwnd)
        self._build_controls()
        self._switch_tab("history")

        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except win32gui.error:
            pass
        debuglog.log("MANAGER", "管理界面已打开")

        while not self.done:
            win32gui.PumpWaitingMessages()
            time.sleep(0.01)

        if win32gui.IsWindow(self.hwnd):
            win32gui.DestroyWindow(self.hwnd)
        debuglog.log("MANAGER", "管理界面已关闭")

    def _add(self, window_class: str, text: str, x: int, y: int, w: int, h: int,
             control_id: int, style: int = 0, font=None, color=None):
        handle = win32gui.CreateWindow(
            window_class, text, win32con.WS_CHILD | win32con.WS_VISIBLE | style,
            x, y, w, h, self.hwnd, control_id, win32api.GetModuleHandle(None), None,
        )
        win32gui.SendMessage(handle, win32con.WM_SETFONT, font or self.font_body, 1)
        self.controls[control_id] = handle
        if color:
            self.static_colors[control_id] = color
        return handle

    def _button(self, text, x, y, w, control_id, kind="secondary", h=36):
        return self._add("Button", text, x, y, w, h, control_id,
                         win32con.BS_OWNERDRAW, self.font_button)

    def _build_controls(self) -> None:
        # 顶部标签（自绘）
        self._button("拦截记录", 30, _TAB_Y, 130, _ID_TAB_HISTORY, "tab", _TAB_H)
        self._button("白名单规则", 170, _TAB_Y, 140, _ID_TAB_RULES, "tab", _TAB_H)
        self._button("设置", 320, _TAB_Y, 110, _ID_TAB_SETTINGS, "tab", _TAB_H)

        # 记录页
        self._add("Static", "拦截记录", 40, 168, 300, 28, 4001, font=self.font_h2, color=C_TEXT)
        self._add("Static", "最近 200 条 · 最新在上", 620, 174, 240, 22, 4013,
                  font=self.font_small, color=C_MUTED)
        self._add("ListBox", "", 40, 204, 820, 340, _ID_LIST_RECORDS,
                  win32con.WS_BORDER | win32con.WS_VSCROLL | win32con.LBS_NOTIFY
                  | win32con.LBS_OWNERDRAWFIXED | win32con.LBS_HASSTRINGS
                  | win32con.LBS_NOINTEGRALHEIGHT)
        self._button("刷新", 40, 558, 110, _ID_BTN_REFRESH, "primary")
        self._button("清空记录", 160, 558, 110, _ID_BTN_CLEAR_LOG, "danger")
        self._button("打开日志", 280, 558, 110, _ID_BTN_OPEN_LOG)

        # 规则页
        self._add("Static", "白名单规则", 40, 168, 300, 28, 4002, font=self.font_h2, color=C_TEXT)
        self._add("Static", "匹配后自动放行，不再弹窗", 590, 174, 270, 22, 4014,
                  font=self.font_small, color=C_MUTED)
        self._add("ListBox", "", 40, 204, 820, 200, _ID_LIST_RULES,
                  win32con.WS_BORDER | win32con.WS_VSCROLL | win32con.LBS_NOTIFY
                  | win32con.LBS_OWNERDRAWFIXED | win32con.LBS_HASSTRINGS
                  | win32con.LBS_NOINTEGRALHEIGHT)
        self._add("Static", "类型", 40, 422, 40, 22, 4003, color=C_MUTED)
        combo = self._add("ComboBox", "", 86, 416, 140, 30, _ID_COMBO_TYPE,
                          win32con.CBS_DROPDOWNLIST | win32con.WS_VSCROLL)
        for label in ("文件哈希", "发行者", "完整路径", "目录"):
            win32gui.SendMessage(combo, win32con.CB_ADDSTRING, 0, label)
        win32gui.SendMessage(combo, win32con.CB_SETCURSEL, 0, 0)
        self._add("Static", "值", 240, 422, 30, 22, 4004, color=C_MUTED)
        self._add("Edit", "", 274, 416, 320, 30, _ID_EDIT_VALUE,
                  win32con.WS_BORDER | win32con.ES_AUTOHSCROLL)
        self._add("Static", "备注", 606, 422, 40, 22, 4005, color=C_MUTED)
        self._add("Edit", "", 650, 416, 210, 30, _ID_EDIT_NOTE,
                  win32con.WS_BORDER | win32con.ES_AUTOHSCROLL)
        self._button("添加规则", 40, 462, 110, _ID_BTN_ADD_RULE, "primary")
        self._button("删除选中", 160, 462, 110, _ID_BTN_DEL_RULE, "danger")
        self._add("Static", "提示：哈希最精确；发行者按签名主体匹配；目录规则会放行该目录下所有程序。",
                  40, 516, 820, 24, 4006, font=self.font_small, color=C_MUTED)

        # 设置页
        self._add("Static", "运行设置", 40, 168, 300, 28, 4007, font=self.font_h2, color=C_TEXT)
        self._add("Button", "", 44, 214, 22, 22, _ID_CHK_ENABLED, win32con.BS_AUTOCHECKBOX)
        self._add("Static", "启用安装拦截保护", 80, 212, 400, 26, 4008, color=C_TEXT)
        self._add("Button", "", 44, 258, 22, 22, _ID_CHK_SOUND, win32con.BS_AUTOCHECKBOX)
        self._add("Static", "拦截时播放提示音", 80, 256, 400, 26, 4009, color=C_TEXT)
        self._add("Button", "", 44, 302, 22, 22, _ID_CHK_AUTOSTART, win32con.BS_AUTOCHECKBOX)
        self._add("Static", "开机自动启动", 80, 300, 400, 26, 4010, color=C_TEXT)
        self._add("Static", "轮询间隔（秒，0.1 - 2.0）", 44, 352, 230, 26, 4011, color=C_TEXT)
        self._add("Edit", "", 280, 348, 90, 30, _ID_EDIT_INTERVAL,
                  win32con.WS_BORDER | win32con.ES_AUTOHSCROLL)
        self._button("保存设置", 44, 400, 120, _ID_BTN_SAVE, "primary")
        self._add("Static", "说明：关闭保护后所有安装程序都将直接放行。", 44, 456, 780, 24,
                  4012, font=self.font_small, color=C_MUTED)
        self._add("Static", "隐私：所有规则与记录仅保存在本机 %APPDATA%\\InstallGuard，不上传任何数据。",
                  44, 486, 780, 24, 4015, font=self.font_small, color=C_MUTED)
        self._add("Static", "提示：托盘图标可快速开关保护、打开管理中心或退出程序。",
                  44, 516, 780, 24, 4016, font=self.font_small, color=C_MUTED)

    # ---------- 绘制 ----------
    def _paint(self, hwnd) -> None:
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            w, h = win32gui.GetClientRect(hwnd)[2:]
            _fill(hdc, (0, 0, w, h), C_BG)

            # 顶部渐变
            for y in range(_HEADER_H):
                t = y / max(1, _HEADER_H - 1)
                _fill(hdc, (0, y, w, y + 1), _lerp((30, 64, 175), (37, 99, 235), t))

            # 盾牌徽标
            cx, cy = 52, 52
            brush = win32gui.CreateSolidBrush(_rgb((255, 255, 255)))
            old_brush = win32gui.SelectObject(hdc, brush)
            win32gui.Polygon(hdc, [
                (cx - 19, cy - 24), (cx + 19, cy - 24), (cx + 19, cy + 2),
                (cx, cy + 25), (cx - 19, cy + 2),
            ])
            win32gui.SelectObject(hdc, old_brush)
            win32gui.DeleteObject(brush)
            pen = win32gui.CreatePen(win32con.PS_SOLID, 4, _rgb(C_PRIMARY))
            old_pen = win32gui.SelectObject(hdc, pen)
            win32gui.Polyline(hdc, [(cx - 10, cy - 1), (cx - 2, cy + 8), (cx + 12, cy - 10)])
            win32gui.SelectObject(hdc, old_pen)
            win32gui.DeleteObject(pen)

            _text(hdc, "安装守卫", (90, 16, 400, 44), self.font_title, (255, 255, 255),
                  win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
            _text(hdc, "管理中心 · 守护每一次安装", (92, 52, 552, 80), self.font_sub,
                  (219, 234, 254), win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE)

            enabled = storage.load_config().get("enabled", True)
            pill_color = C_SUCCESS if enabled else C_MUTED
            pill_bg = (16, 185, 129) if enabled else (100, 116, 139)
            _round(hdc, (748, 30, 866, 68), pill_bg, radius=16)
            _text(hdc, "●", (762, 34, 784, 62), self.font_sub, pill_color,
                  win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
            _text(hdc, "保护中" if enabled else "已暂停", (786, 32, 860, 64), self.font_body,
                  (255, 255, 255), win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE)

            # 标签栏底色与分隔线
            _fill(hdc, (0, _HEADER_H, w, _CONTENT_Y), (255, 255, 255))
            _fill(hdc, (0, _CONTENT_Y - 1, w, _CONTENT_Y), C_BORDER)

            # 内容区卡片
            card_top, card_bottom = 164, 600
            _round(hdc, (28, card_top, w - 28, card_bottom), C_CARD, C_BORDER, radius=14)
            _fill(hdc, (28, card_top + 44, w - 28, card_top + 45), C_BORDER)

            # 底部状态栏
            _fill(hdc, (0, _STATUS_Y, w, h), (238, 242, 247))
            _fill(hdc, (0, _STATUS_Y, w, _STATUS_Y + 1), C_BORDER)
            _text(hdc, "安装守卫 · 本地运行，不上传任何数据",
                  (24, _STATUS_Y + 4, 524, _STATUS_Y + 32),
                  self.font_small, C_MUTED, win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
            _text(hdc, "v1.1 · 守护每一次安装", (w - 260, _STATUS_Y + 4, w - 24, _STATUS_Y + 32),
                  self.font_small, C_MUTED, win32con.DT_RIGHT | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
        finally:
            win32gui.EndPaint(hwnd, ps)
        return 0

    def _draw_tab(self, hdc, rect, control_id) -> None:
        active = (control_id == _ID_TAB_HISTORY and self.current_tab == "history") or \
                 (control_id == _ID_TAB_RULES and self.current_tab == "rules") or \
                 (control_id == _ID_TAB_SETTINGS and self.current_tab == "settings")
        text = {_ID_TAB_HISTORY: "拦截记录", _ID_TAB_RULES: "白名单规则",
                _ID_TAB_SETTINGS: "设置"}[control_id]
        _text(hdc, text, rect, self.font_button,
              C_PRIMARY if active else C_MUTED,
              win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
        if active:
            x1, y1, x2, y2 = rect
            _fill(hdc, (x1 + 14, y2 - 4, x2 - 14, y2), C_PRIMARY)

    def _draw_button(self, hdc, rect, control_id, selected) -> None:
        x1, y1, x2, y2 = rect
        inset = (x1 + 1, y1 + 1, x2 - 1, y2 - 1)
        if control_id in (_ID_BTN_REFRESH, _ID_BTN_ADD_RULE, _ID_BTN_SAVE):
            fill = C_PRIMARY_DARK if selected else C_PRIMARY
            _round(hdc, inset, fill, radius=10)
            color = (255, 255, 255)
        elif control_id in (_ID_BTN_CLEAR_LOG, _ID_BTN_DEL_RULE):
            _round(hdc, inset, (254, 242, 242) if selected else (255, 255, 255),
                   outline=(248, 113, 113), radius=10)
            color = C_DANGER
        else:
            _round(hdc, inset, (241, 245, 249) if selected else (255, 255, 255),
                   outline=C_BORDER, radius=10)
            color = C_TEXT
        text = win32gui.GetWindowText(self.controls.get(control_id, 0)) or ""
        _text(hdc, text, rect, self.font_button, color,
              win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE)

    def _draw_record_row(self, hdc, rect, row, selected) -> None:
        x1, y1, x2, y2 = rect
        _fill(hdc, rect, C_SELECTED if selected else (C_CARD if (row.get("_idx", 0) % 2 == 0) else C_ROW_ALT))
        level = row.get("level", "low")
        label, badge_bg, badge_fg, bar_color = LEVEL_STYLE.get(level, LEVEL_STYLE["low"])
        _fill(hdc, (x1, y1, x1 + 4, y2), bar_color)
        _text(hdc, row.get("name", "?"), (x1 + 16, y1 + 6, x2 - 190, y1 + 30),
              self.font_body, C_TEXT, win32con.DT_LEFT | win32con.DT_VCENTER
              | win32con.DT_SINGLELINE | win32con.DT_END_ELLIPSIS)
        _text(hdc, row.get("time", ""), (x2 - 180, y1 + 6, x2 - 16, y1 + 30),
              self.font_small, C_MUTED, win32con.DT_RIGHT | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
        _round(hdc, (x1 + 16, y1 + 34, x1 + 82, y1 + 56), badge_bg, radius=8)
        _text(hdc, label, (x1 + 16, y1 + 34, x1 + 82, y1 + 56), self.font_badge, badge_fg,
              win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
        decision = row.get("decision", "")
        decision_text = {"block": "已阻止", "allow": "允许一次",
                         "allow_always": "永久允许"}.get(decision, decision or "?")
        decision_color = C_SUCCESS if decision == "block" else (C_WARN if decision == "allow" else C_PRIMARY)
        _text(hdc, decision_text, (x1 + 96, y1 + 34, x1 + 184, y1 + 56),
              self.font_small, decision_color, win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
        _text(hdc, row.get("path", ""), (x1 + 190, y1 + 34, x2 - 16, y1 + 56),
              self.font_small, C_MUTED, win32con.DT_LEFT | win32con.DT_VCENTER
              | win32con.DT_SINGLELINE | win32con.DT_END_ELLIPSIS)

    def _draw_rule_row(self, hdc, rect, row, selected) -> None:
        x1, y1, x2, y2 = rect
        _fill(hdc, rect, C_SELECTED if selected else (C_CARD if (row.get("_idx", 0) % 2 == 0) else C_ROW_ALT))
        type_label = {"hash": "哈希", "publisher": "发行者", "path": "路径", "dir": "目录"}.get(
            row.get("type", ""), row.get("type", "?"))
        _round(hdc, (x1 + 14, y1 + 8, x1 + 86, y1 + 32), C_PRIMARY_LIGHT, radius=8)
        _text(hdc, type_label, (x1 + 14, y1 + 8, x1 + 86, y1 + 32), self.font_badge,
              C_PRIMARY_DARK, win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
        _text(hdc, row.get("value", ""), (x1 + 100, y1 + 6, x1 + 520, y2 - 4),
              self.font_body, C_TEXT, win32con.DT_LEFT | win32con.DT_VCENTER
              | win32con.DT_SINGLELINE | win32con.DT_END_ELLIPSIS)
        _text(hdc, row.get("note", ""), (x1 + 530, y1 + 6, x2 - 16, y2 - 4),
              self.font_small, C_MUTED, win32con.DT_LEFT | win32con.DT_VCENTER
              | win32con.DT_SINGLELINE | win32con.DT_END_ELLIPSIS)

    def _on_draw_item(self, lparam) -> None:
        di = ctypes.cast(lparam, ctypes.POINTER(_DrawItem)).contents
        hdc = int(di.hDC) if di.hDC else 0
        if not hdc:
            return
        rect = (di.rcItem.left, di.rcItem.top, di.rcItem.right, di.rcItem.bottom)
        control_id = di.CtlID
        if di.CtlType == win32con.ODT_LISTBOX:
            rows = self.list_rows.get(control_id, [])
            index = di.itemID
            if 0 <= index < len(rows):
                selected = bool(di.itemState & win32con.ODS_SELECTED)
                if control_id == _ID_LIST_RECORDS:
                    self._draw_record_row(hdc, rect, rows[index], selected)
                else:
                    self._draw_rule_row(hdc, rect, rows[index], selected)
            return
        if control_id in (_ID_TAB_HISTORY, _ID_TAB_RULES, _ID_TAB_SETTINGS):
            self._draw_tab(hdc, rect, control_id)
        else:
            self._draw_button(hdc, rect, control_id, bool(di.itemState & win32con.ODS_SELECTED))

    def _on_measure_item(self, lparam) -> None:
        mi = ctypes.cast(lparam, ctypes.POINTER(_MeasureItem)).contents
        if mi.CtlType == win32con.ODT_LISTBOX:
            mi.itemHeight = 62 if mi.CtlID == _ID_LIST_RECORDS else 44

    # ---------- 数据 ----------
    def _show_controls(self, ids: list[int]) -> None:
        all_page_ids = set(range(3001, 3060)) | set(range(4001, 4020))
        for control_id in all_page_ids:
            handle = self.controls.get(control_id)
            if not handle:
                continue
            try:
                win32gui.ShowWindow(handle, win32con.SW_SHOW if control_id in ids else win32con.SW_HIDE)
            except win32gui.error:
                pass
        for tab_id in (_ID_TAB_HISTORY, _ID_TAB_RULES, _ID_TAB_SETTINGS):
            handle = self.controls.get(tab_id)
            if handle and win32gui.IsWindow(handle):
                win32gui.InvalidateRect(handle, None, True)

    def _switch_tab(self, tab: str) -> None:
        self.current_tab = tab
        if tab == "history":
            self._show_controls([_ID_TAB_HISTORY, _ID_TAB_RULES, _ID_TAB_SETTINGS,
                                 4001, 4013, _ID_LIST_RECORDS, _ID_BTN_REFRESH,
                                 _ID_BTN_CLEAR_LOG, _ID_BTN_OPEN_LOG])
            self._refresh_records()
        elif tab == "rules":
            self._show_controls([_ID_TAB_HISTORY, _ID_TAB_RULES, _ID_TAB_SETTINGS,
                                 4002, 4014, _ID_LIST_RULES, 4003, _ID_COMBO_TYPE,
                                 4004, _ID_EDIT_VALUE, 4005, _ID_EDIT_NOTE,
                                 _ID_BTN_ADD_RULE, _ID_BTN_DEL_RULE, 4006])
            self._refresh_rules()
        else:
            self._show_controls([_ID_TAB_HISTORY, _ID_TAB_RULES, _ID_TAB_SETTINGS,
                                 4007, _ID_CHK_ENABLED, 4008, _ID_CHK_SOUND, 4009,
                                 _ID_CHK_AUTOSTART, 4010, 4011, _ID_EDIT_INTERVAL,
                                 _ID_BTN_SAVE, 4012, 4015, 4016])
            self._load_settings()
        if self.hwnd:
            win32gui.RedrawWindow(
                self.hwnd, None, None,
                win32con.RDW_INVALIDATE | win32con.RDW_UPDATENOW | win32con.RDW_ALLCHILDREN,
            )

    def _list_clear(self, control_id: int) -> None:
        self.list_rows[control_id] = []
        handle = self.controls.get(control_id)
        if handle:
            win32gui.SendMessage(handle, win32con.LB_RESETCONTENT, 0, 0)

    def _list_add(self, control_id: int, text: str, row: dict | None = None) -> None:
        handle = self.controls.get(control_id)
        if not handle:
            return
        index = self.list_rows[control_id].__len__()
        win32gui.SendMessage(handle, win32con.LB_ADDSTRING, 0, text)
        row = dict(row or {})
        row["_idx"] = index
        self.list_rows[control_id].append(row)

    def _refresh_records(self) -> None:
        self._list_clear(_ID_LIST_RECORDS)
        records = storage.read_history(200)
        if not records:
            self._list_add(_ID_LIST_RECORDS, "暂无拦截记录", {
                "name": "暂无拦截记录", "time": "", "level": "low",
                "decision": "", "path": "有拦截事件后会显示在这里",
            })
            return
        for item in records:
            ts = item.get("ts", "")
            time_text = ts[11:19] if len(ts) >= 19 else ts
            self._list_add(_ID_LIST_RECORDS, ts, {
                "name": item.get("name", "?"), "time": time_text,
                "level": item.get("level", "low"),
                "decision": item.get("decision", ""), "path": item.get("path", ""),
            })

    def _refresh_rules(self) -> None:
        self._list_clear(_ID_LIST_RULES)
        rules = storage.load_rules().get("allow", [])
        if not rules:
            self._list_add(_ID_LIST_RULES, "暂无白名单规则", {
                "type": "提示", "value": "暂无白名单规则", "note": "添加后匹配的程序不再弹窗",
            })
            return
        for rule in rules:
            self._list_add(_ID_LIST_RULES, str(rule), {
                "type": rule.get("type", "?"), "value": rule.get("value", ""),
                "note": rule.get("note", ""),
            })

    def _load_settings(self) -> None:
        cfg = storage.load_config()
        win32gui.SendMessage(self.controls[_ID_CHK_ENABLED], win32con.BM_SETCHECK,
                             1 if cfg.get("enabled", True) else 0, 0)
        win32gui.SendMessage(self.controls[_ID_CHK_SOUND], win32con.BM_SETCHECK,
                             1 if cfg.get("sound", True) else 0, 0)
        win32gui.SendMessage(self.controls[_ID_CHK_AUTOSTART], win32con.BM_SETCHECK,
                             1 if storage.is_autostart_enabled() else 0, 0)
        win32gui.SetWindowText(self.controls[_ID_EDIT_INTERVAL], str(cfg.get("poll_interval", 0.3)))

    def _save_settings(self) -> None:
        enabled = win32gui.SendMessage(self.controls[_ID_CHK_ENABLED], win32con.BM_GETCHECK, 0, 0)
        sound = win32gui.SendMessage(self.controls[_ID_CHK_SOUND], win32con.BM_GETCHECK, 0, 0)
        autostart = win32gui.SendMessage(self.controls[_ID_CHK_AUTOSTART], win32con.BM_GETCHECK, 0, 0)
        interval_text = win32gui.GetWindowText(self.controls[_ID_EDIT_INTERVAL])
        try:
            interval = float(interval_text)
            interval = max(0.1, min(2.0, interval))
        except ValueError:
            interval = 0.3

        cfg = storage.load_config()
        cfg["enabled"] = bool(enabled)
        cfg["sound"] = bool(sound)
        cfg["poll_interval"] = interval
        storage.save_config(cfg)

        if bool(autostart) != storage.is_autostart_enabled():
            self.app.toggle_autostart()
        win32gui.InvalidateRect(self.hwnd, None, True)
        win32gui.MessageBox(self.hwnd, "设置已保存", "安装守卫", win32con.MB_OK)
        debuglog.log("MANAGER", "设置已保存 enabled=%s interval=%s" % (bool(enabled), interval))

    def _add_rule(self) -> None:
        combo = self.controls[_ID_COMBO_TYPE]
        index = win32gui.SendMessage(combo, win32con.CB_GETCURSEL, 0, 0)
        type_map = {0: "hash", 1: "publisher", 2: "path", 3: "dir"}
        rule_type = type_map.get(index, "hash")
        value = win32gui.GetWindowText(self.controls[_ID_EDIT_VALUE]).strip()
        note = win32gui.GetWindowText(self.controls[_ID_EDIT_NOTE]).strip()
        if not value:
            win32gui.MessageBox(self.hwnd, "请填写规则值", "安装守卫", win32con.MB_OK)
            return
        storage.add_allow({"type": rule_type, "value": value, "note": note or "手动添加"})
        win32gui.SetWindowText(self.controls[_ID_EDIT_VALUE], "")
        win32gui.SetWindowText(self.controls[_ID_EDIT_NOTE], "")
        self._refresh_rules()
        debuglog.log("MANAGER", "添加规则 %s=%s" % (rule_type, value))

    def _delete_rule(self) -> None:
        handle = self.controls[_ID_LIST_RULES]
        index = win32gui.SendMessage(handle, win32con.LB_GETCURSEL, 0, 0)
        if index < 0:
            win32gui.MessageBox(self.hwnd, "请先选中一条规则", "安装守卫", win32con.MB_OK)
            return
        if storage.remove_allow(index):
            self._refresh_rules()
            debuglog.log("MANAGER", "删除规则索引 %s" % index)

    # ---------- 消息 ----------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_PAINT:
            return self._paint(hwnd)
        if msg == win32con.WM_ERASEBKGND:
            return 1
        if msg == win32con.WM_DRAWITEM:
            self._on_draw_item(lparam)
            return 0
        if msg == win32con.WM_MEASUREITEM:
            self._on_measure_item(lparam)
            return 0
        if msg == win32con.WM_CTLCOLORSTATIC:
            control_id = win32gui.GetDlgCtrlID(lparam)
            color = self.static_colors.get(control_id, C_TEXT)
            win32gui.SetBkMode(wparam, win32con.TRANSPARENT)
            win32gui.SetTextColor(wparam, _rgb(color))
            return self.brush_white
        if msg == win32con.WM_CTLCOLORBTN:
            return self.brush_card
        if msg == win32con.WM_CTLCOLORLISTBOX:
            win32gui.SetBkColor(wparam, _rgb((255, 255, 255)))
            win32gui.SetTextColor(wparam, _rgb(C_TEXT))
            return self.brush_white
        if msg == win32con.WM_CTLCOLOREDIT:
            win32gui.SetBkColor(wparam, _rgb((255, 255, 255)))
            win32gui.SetTextColor(wparam, _rgb(C_TEXT))
            return self.brush_white
        if msg == win32con.WM_COMMAND:
            control_id = win32api.LOWORD(wparam)
            if control_id == _ID_TAB_HISTORY:
                self._switch_tab("history")
            elif control_id == _ID_TAB_RULES:
                self._switch_tab("rules")
            elif control_id == _ID_TAB_SETTINGS:
                self._switch_tab("settings")
            elif control_id == _ID_BTN_REFRESH:
                self._refresh_records()
            elif control_id == _ID_BTN_CLEAR_LOG:
                result = win32gui.MessageBox(
                    self.hwnd, "确定清空所有拦截记录吗？", "安装守卫",
                    win32con.MB_YESNO | win32con.MB_ICONQUESTION)
                if result == win32con.IDYES:
                    storage.clear_history()
                    self._refresh_records()
            elif control_id == _ID_BTN_OPEN_LOG:
                try:
                    debuglog.log("MANAGER", "打开日志文件")
                    if debuglog.path().exists():
                        os.startfile(str(debuglog.path()))
                    else:
                        win32gui.MessageBox(self.hwnd, "暂无日志文件", "安装守卫", win32con.MB_OK)
                except OSError:
                    pass
            elif control_id == _ID_BTN_ADD_RULE:
                self._add_rule()
            elif control_id == _ID_BTN_DEL_RULE:
                self._delete_rule()
            elif control_id == _ID_BTN_SAVE:
                self._save_settings()
            return 0
        if msg == win32con.WM_CLOSE:
            self.done = True
            return 0
        if msg == win32con.WM_DESTROY:
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def open_manager(app) -> None:
    """打开管理界面（独立线程，单一实例）。"""
    global _instance
    if not _INSTANCE_LOCK.acquire(blocking=False):
        return
    try:
        if _instance is not None and not _instance.done:
            try:
                win32gui.SetForegroundWindow(_instance.hwnd)
            except win32gui.error:
                pass
            return
        window = _ManagerWindow(app)
        _instance = window

        def runner():
            try:
                window.show()
            finally:
                global _instance
                _instance = None

        thread = threading.Thread(target=runner, name="ig-manager", daemon=True)
        thread.start()
    finally:
        _INSTANCE_LOCK.release()
