"""系统托盘图标与菜单。"""
from __future__ import annotations

import os

import pystray

from . import config, icons, storage


def _make_image():
    return icons.tray_image(64)


def _open_file(path, empty_content: str = "") -> None:
    storage.ensure_dirs()
    if not path.exists():
        path.write_text(empty_content, encoding="utf-8")
    try:
        os.startfile(str(path))
    except OSError:
        pass


def create_icon(app) -> pystray.Icon:
    def toggle_enabled(icon, item):
        cfg = storage.load_config()
        cfg["enabled"] = not cfg.get("enabled", True)
        storage.save_config(cfg)

    def toggle_autostart(icon, item):
        app.toggle_autostart()

    def open_history(icon, item):
        _open_file(config.HISTORY_PATH, "")

    def open_rules(icon, item):
        _open_file(config.RULES_PATH, '{\n  "allow": [],\n  "block": []\n}\n')

    def open_manager(icon, item):
        app.open_manager()

    def quit_app(icon, item):
        app.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            "保护已开启",
            toggle_enabled,
            checked=lambda item: storage.load_config().get("enabled", True),
        ),
        pystray.MenuItem(
            "开机自启",
            toggle_autostart,
            checked=lambda item: storage.is_autostart_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开管理中心", open_manager),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )
    return pystray.Icon("InstallGuard", _make_image(), config.TRAY_TITLE, menu)
