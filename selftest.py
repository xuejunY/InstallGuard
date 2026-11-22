"""无 GUI 自检：验证 Windows API、签名、识别与持久化链路。"""
from __future__ import annotations

import os
import sys

from install_guard import (config, debuglog, detector, icons, manager, monitor,
                           processctl, resources, signature, storage, tray, ui)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  OK   " if condition else "  FAIL ") + label + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(label)


print("InstallGuard self-test")
print("Python:", sys.version.split()[0], "| frozen:", getattr(sys, "frozen", False))

check("进程保护名单", processctl.is_protected("csrss.exe") and not processctl.is_protected("setup.exe"))
check("系统文件签名验证", signature.verify(r"C:\Windows\System32\notepad.exe"))
check("不存在文件判定为未签名", not signature.verify(r"C:\Windows\System32\__no_such_file__.exe"))
check("文件哈希可读", len(signature.file_hash(r"C:\Windows\System32\notepad.exe")) == 64)

prefilter_true = detector.prefilter(
    "setup.exe", r"C:\Users\test\Downloads\setup.exe", [], "chrome.exe"
)
prefilter_false = detector.prefilter(
    "notepad.exe", r"C:\Windows\System32\notepad.exe", [], "explorer.exe"
)
check("下载目录安装器预判", prefilter_true)
check("系统程序不触发预判", not prefilter_false)

info = {
    "name": "setup.exe",
    "exe": r"C:\Users\test\Downloads\setup.exe",
    "cmdline": [],
    "parent_name": "chrome.exe",
}
detection = detector.analyze(1234, info, {"allow": [], "block": []})
check("风险评分生效", detection.score >= 80, "score=%d level=%s" % (detection.score, detection.level))
check("默认判定为需确认", detection.suspicious)

allowed = detector.analyze(
    1234,
    info,
    {"allow": [{"type": "dir", "value": r"C:\Users\test\Downloads", "note": "test"}], "block": []},
)
check("白名单目录命中后放行", not allowed.suspicious)

storage.ensure_dirs()
cfg = storage.load_config()
check("配置读写", isinstance(cfg.get("enabled"), bool))
import tempfile
from pathlib import Path

original_rules_path = config.RULES_PATH
config.RULES_PATH = Path(tempfile.mkdtemp(prefix="ig-selftest-")) / "rules.json"
storage.add_allow({"type": "hash", "value": "selftest-" + os.urandom(4).hex(), "note": "self-test"})
check("白名单新增", len(storage.load_rules()["allow"]) >= 1)
config.RULES_PATH = original_rules_path
check("图标资源存在", resources.icon_path().exists())
check("托盘守卫图标可构建", icons.tray_image(64).size == (64, 64))
check("托盘菜单图标一致", tray._make_image().size == (64, 64))
check("弹窗模块可加载", callable(ui.show_confirm))
check("监控模块可加载", callable(monitor.Monitor))
check("调试日志可写", (debuglog.log("SELFTEST", "check"), debuglog.path().exists())[1])
check("管理界面可加载", callable(manager.open_manager))

# 开机自启路径跟随：用假函数替换注册表访问，避免污染真实环境
check(
    "自启命令路径解析（带空格与参数）",
    storage.command_target(r'"D:\My Tools\InstallGuard.exe" --minimized')
    == r"D:\My Tools\InstallGuard.exe",
)
check(
    "自启命令路径解析（无引号）",
    storage.command_target(r"D:\Tools\InstallGuard.exe") == r"D:\Tools\InstallGuard.exe",
)
_orig_read = storage.autostart_command
_orig_set = storage.set_autostart
_writes: list[tuple[bool, str]] = []
try:
    storage.autostart_command = lambda: r'"C:\Old Place\InstallGuard.exe"'
    storage.set_autostart = lambda enabled, command="": _writes.append((enabled, command))
    changed = storage.sync_autostart(r'"D:\New Place\InstallGuard.exe"')
    check(
        "移动 exe 后自启路径自动更新",
        changed and _writes == [(True, r'"D:\New Place\InstallGuard.exe"')],
    )

    _writes.clear()
    storage.autostart_command = lambda: r'"D:\New Place\InstallGuard.exe"'
    changed_same = storage.sync_autostart(r'"D:\New Place\InstallGuard.exe"')
    check("路径未变不重复写入", not changed_same and not _writes)

    _writes.clear()
    storage.autostart_command = lambda: ""
    changed_off = storage.sync_autostart(r'"D:\New Place\InstallGuard.exe"')
    check("未开启自启时不写入", not changed_off and not _writes)
finally:
    storage.autostart_command = _orig_read
    storage.set_autostart = _orig_set

# 新增识别信号：下载工具父进程、安装器框架标记
check("下载工具父进程识别", detector.prefilter("setup.exe", r"C:\x\setup.exe", [], "thunder.exe"))
check("命令行安装参数识别", detector.prefilter("app.exe", r"C:\x\app.exe", ["app.exe", "/install"], "explorer.exe"))

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
