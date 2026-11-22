"""统一图标：托盘、弹窗、管理窗口共用同一枚盾牌图标。"""
from __future__ import annotations

from PIL import Image, ImageDraw
import win32con
import win32gui

from . import resources

_ICONS: list[int] = []


def tray_image(size: int = 64) -> Image.Image:
    """返回托盘用 RGBA 图像；资源缺失时回退到简易盾牌。"""
    path = resources.icon_path()
    if path.exists():
        try:
            image = Image.open(path)
            best = min(image.ico.sizes(), key=lambda s: abs(s[0] - size))
            return image.ico.getimage(best).convert("RGBA").resize((size, size), Image.LANCZOS)
        except (OSError, ValueError):
            pass

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = size
    pts = [(int(x * s), int(y * s)) for x, y in (
        (0.20, 0.13), (0.80, 0.13), (0.80, 0.45),
        (0.72, 0.66), (0.50, 0.89), (0.28, 0.66), (0.20, 0.45),
    )]
    draw.polygon(pts, fill=(37, 99, 235, 255), outline=(30, 64, 175, 255))
    draw.line([(int(x * s), int(y * s)) for x, y in ((0.32, 0.47), (0.46, 0.61), (0.70, 0.34))],
              fill=(255, 255, 255, 255), width=max(2, s // 11), joint="curve")
    return image


def set_window_icon(hwnd: int) -> None:
    """给窗口设置小图标与 alt-tab 大图标。"""
    path = resources.icon_path()
    if not path.exists() or not hwnd:
        return
    try:
        small = win32gui.LoadImage(0, str(path), win32con.IMAGE_ICON, 16, 16,
                                   win32con.LR_LOADFROMFILE)
        big = win32gui.LoadImage(0, str(path), win32con.IMAGE_ICON, 64, 64,
                                 win32con.LR_LOADFROMFILE)
    except win32gui.error:
        return
    if small:
        win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_SMALL, small)
        _ICONS.append(small)
    if big:
        win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_BIG, big)
        _ICONS.append(big)
