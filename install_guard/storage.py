"""规则、配置与历史的本地持久化（线程安全）。"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any

from . import config

_LOCK = threading.RLock()


def _read_json(path: str, default: dict[str, Any]) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            merged = dict(default)
            merged.update(data)
            return merged
    except (OSError, ValueError):
        pass
    return dict(default)


def _write_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def ensure_dirs() -> None:
    config.BASE_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    with _LOCK:
        return _read_json(config.CONFIG_PATH, config.DEFAULT_CONFIG)


def save_config(data: dict[str, Any]) -> None:
    with _LOCK:
        merged = dict(config.DEFAULT_CONFIG)
        merged.update(data)
        _write_json(config.CONFIG_PATH, merged)


def load_rules() -> dict[str, Any]:
    with _LOCK:
        return _read_json(config.RULES_PATH, config.DEFAULT_RULES)


def add_allow(rule: dict[str, str]) -> bool:
    """rule: {'type': 'hash'|'publisher'|'path'|'dir', 'value': str, 'note': str}"""
    with _LOCK:
        rules = _read_json(config.RULES_PATH, config.DEFAULT_RULES)
        for existing in rules["allow"]:
            if existing.get("type") == rule.get("type") and existing.get("value", "").lower() == rule.get("value", "").lower():
                return False
        item = dict(rule)
        item["created"] = datetime.now().isoformat(timespec="seconds")
        rules["allow"].append(item)
        _write_json(config.RULES_PATH, rules)
        return True


def remove_allow(index: int) -> bool:
    with _LOCK:
        rules = _read_json(config.RULES_PATH, config.DEFAULT_RULES)
        if 0 <= index < len(rules["allow"]):
            rules["allow"].pop(index)
            _write_json(config.RULES_PATH, rules)
            return True
    return False


def append_history(record: dict[str, Any]) -> None:
    with _LOCK:
        ensure_dirs()
        entry = dict(record)
        entry.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        with open(config.HISTORY_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history(limit: int = 200) -> list[dict[str, Any]]:
    with _LOCK:
        try:
            with open(config.HISTORY_PATH, "r", encoding="utf-8") as fh:
                lines = [line.strip() for line in fh if line.strip()]
        except OSError:
            return []
        result = []
        for line in reversed(lines[-limit:]):
            try:
                result.append(json.loads(line))
            except ValueError:
                continue
        return result


def clear_history() -> None:
    with _LOCK:
        try:
            if config.HISTORY_PATH.exists():
                config.HISTORY_PATH.write_text("", encoding="utf-8")
        except OSError:
            pass


def autostart_command() -> str:
    """读取注册表里的开机自启命令；未设置时返回空串。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, config.RUN_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, config.RUN_VALUE_NAME)
            return str(value or "")
    except OSError:
        return ""


def command_target(command: str) -> str:
    """从自启命令行中提取主程序路径（去掉引号与后续参数）。"""
    text = (command or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 1 else text[1:]
    return text.split()[0]


def is_autostart_enabled() -> bool:
    """以注册表实际状态为准。"""
    return bool(command_target(autostart_command()))


def set_autostart(enabled: bool, command: str = "") -> None:
    """设置或取消开机自启；command 为自启命令行。"""
    if enabled and not command:
        return
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.RUN_KEY_PATH, 0, winreg.KEY_WRITE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, config.RUN_VALUE_NAME, 0, winreg.REG_SZ, command
                )
            else:
                try:
                    winreg.DeleteValue(key, config.RUN_VALUE_NAME)
                except OSError:
                    pass
    except OSError:
        pass


def sync_autostart(expected_command: str) -> bool:
    """自启已开启时，把注册表里的路径纠正为当前程序的实际位置。

    Windows 在登录时执行 Run 键，不会切换到 exe 所在目录，因此无法使用
    纯相对路径。改为每次启动时把当前绝对路径写回，效果上等价于"exe 放在
    任何地方都能自启"：移动后只需运行一次，后续开机自动指向新位置。
    返回是否发生了更新。
    """
    if not expected_command:
        return False
    current = autostart_command()
    if not current:
        return False
    if command_target(current).lower() == command_target(expected_command).lower():
        return False
    set_autostart(True, expected_command)
    return True
