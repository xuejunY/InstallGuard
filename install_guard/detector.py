"""安装行为识别与风险评分。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from . import signature

INSTALLER_NAME_RE = re.compile(
    r"(setup|install|installer|uninstall|update|upgrade|patch|repair|"
    r"安装|卸载|升级|更新|补丁|修复|打包|绿色|精简)",
    re.IGNORECASE,
)
INSTALLER_EXT = (".msi", ".msu", ".msp")
DOWNLOAD_HINTS = (
    "\\downloads\\", "\\desktop\\", "\\appdata\\local\\temp\\",
    "\\temp\\", "\\tmp\\", "\\download\\", "\\桌面\\", "\\下载\\",
)
BROWSERS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "iexplore.exe", "opera.exe",
    "brave.exe", "360se.exe", "360chrome.exe", "qqbrowser.exe",
    "sogouexplorer.exe", "chromium.exe", "vivaldi.exe", "duckduckgo.exe",
}
DOWNLOADERS = {
    "thunder.exe", "xunlei.exe", "idm.exe", "internetdownloadmanager.exe",
    "qdownload.exe", "qqdownloader.exe", "wget.exe", "aria2c.exe",
    "bitcomet.exe", "bittorrent.exe", "qbittorrent.exe", "emule.exe",
    "nettransport.exe", "flashget.exe",
}
ARCHIVERS = {
    "winrar.exe", "7z.exe", "7za.exe", "winzip.exe", "bandizip.exe",
    "peazip.exe", "haozip.exe", "2345zip.exe",
}
LAUNCHER_PARENTS = BROWSERS | DOWNLOADERS | ARCHIVERS
MSI_INSTALL_RE = re.compile(r"(^|\s)/[ia]\b", re.IGNORECASE)

# 安装器框架特征字节串（大小写不敏感匹配）
_INSTALLER_MARKERS = (
    (b"nullsoft instal", "NSIS 安装器"),
    (b"inno setup", "Inno Setup 安装器"),
    (b"installshield", "InstallShield 安装器"),
    (b"wise installation", "Wise 安装器"),
    (b"wixtoolset", "WiX 安装器"),
    (b"advanced installer", "Advanced Installer 安装器"),
    (b"setup factory", "Setup Factory 安装器"),
)
_MARKER_CACHE: dict[str, tuple[str, ...]] = {}


@dataclass
class Detection:
    suspicious: bool
    pid: int = 0
    name: str = ""
    exe_path: str = ""
    cmdline: str = ""
    parent_name: str = ""
    score: int = 0
    level: str = "low"
    reasons: list[str] = field(default_factory=list)
    publisher: str = ""
    product: str = ""
    version: str = ""
    signed: bool = False
    exe_hash: str = ""
    matched_rule: dict[str, Any] | None = None

    @property
    def level_text(self) -> str:
        return {"high": "高风险", "medium": "中风险", "low": "低风险"}.get(self.level, "低风险")

    @property
    def source_text(self) -> str:
        path = self.exe_path.replace("/", "\\").lower()
        parent = self.parent_name.lower()
        if parent in BROWSERS:
            return "浏览器启动 · " + self.parent_name
        if parent in DOWNLOADERS:
            return "下载工具启动 · " + self.parent_name
        if parent in ARCHIVERS:
            return "压缩包启动 · " + self.parent_name
        if any(hint in path for hint in DOWNLOAD_HINTS):
            return "下载或临时文件夹"
        if "\\program files" in path:
            return "已安装程序目录"
        return (self.parent_name or "系统") + " 启动"


def _norm(path: str) -> str:
    return (path or "").replace("/", "\\").lower()


def is_download_path(path: str) -> bool:
    lowered = _norm(path)
    return any(hint in lowered for hint in DOWNLOAD_HINTS)


def detect_framework(path: str) -> tuple[str, ...]:
    """扫描文件中是否含常见安装器框架标记，带缓存。"""
    if not path or not os.path.isfile(path):
        return ()
    key = _norm(path)
    if key in _MARKER_CACHE:
        return _MARKER_CACHE[key]
    found: list[str] = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(min(size, 2 * 1024 * 1024)).lower()
            tail = b""
            if size > 2 * 1024 * 1024:
                fh.seek(max(0, size - 2 * 1024 * 1024))
                tail = fh.read().lower()
            blob = head + tail
        for marker, label in _INSTALLER_MARKERS:
            if marker in blob and label not in found:
                found.append(label)
    except OSError:
        pass
    _MARKER_CACHE[key] = tuple(found)
    return tuple(found)


def prefilter(name: str, exe_path: str, cmdline: list[str] | str, parent_name: str) -> bool:
    """廉价预判：只把"像安装行为"的进程交给完整分析，避免全量扫签名。"""
    proc = (name or "").lower()
    exe = _norm(exe_path)
    command = " ".join(cmdline) if isinstance(cmdline, (list, tuple)) else (cmdline or "")
    command = command.lower()
    parent = (parent_name or "").lower()

    if proc == "msiexec.exe" and MSI_INSTALL_RE.search(command):
        return True
    if INSTALLER_NAME_RE.search(proc):
        return True
    if exe.endswith(INSTALLER_EXT):
        return True
    if is_download_path(exe) and exe.endswith(".exe"):
        return True
    if parent in LAUNCHER_PARENTS and exe.endswith(".exe"):
        return True
    if "setup" in command or "/install" in command or "--install" in command:
        return True
    return False


def _match_allow(rules: dict[str, Any], exe_hash: str, publisher: str, exe_path: str) -> dict[str, Any] | None:
    for rule in rules.get("allow", []):
        kind = (rule.get("type") or "").lower()
        value = rule.get("value") or ""
        if kind == "hash" and value.lower() == exe_hash.lower() and exe_hash:
            return rule
        if kind == "publisher" and publisher and value.lower() == publisher.lower():
            return rule
        if kind == "path" and _norm(value) == _norm(exe_path):
            return rule
        if kind == "dir":
            prefix = _norm(value).rstrip("\\")
            if prefix and _norm(exe_path).startswith(prefix + "\\"):
                return rule
    return None


def _match_block(rules: dict[str, Any], exe_hash: str, publisher: str, exe_path: str) -> dict[str, Any] | None:
    for rule in rules.get("block", []):
        kind = (rule.get("type") or "").lower()
        value = rule.get("value") or ""
        if kind == "hash" and value.lower() == exe_hash.lower() and exe_hash:
            return rule
        if kind == "publisher" and publisher and value.lower() == publisher.lower():
            return rule
    return None


def analyze(pid: int, info: dict[str, Any], rules: dict[str, Any]) -> Detection:
    name = info.get("name") or ""
    exe = info.get("exe") or ""
    command = " ".join(info.get("cmdline") or [])
    parent = info.get("parent_name") or ""

    version = signature.version_info(exe) if exe else {}
    publisher = version.get("company") or version.get("product") or ""
    signed = signature.verify(exe) if exe else False
    exe_hash = signature.file_hash(exe) if exe else ""
    frameworks = detect_framework(exe)

    score = 0
    reasons: list[str] = []

    if exe and not signed:
        score += 35
        reasons.append("没有有效的数字签名")
    if is_download_path(exe):
        score += 25
        reasons.append("来自下载或临时文件夹")
    parent_lower = parent.lower()
    if parent_lower in BROWSERS:
        score += 20
        reasons.append("由浏览器直接启动")
    elif parent_lower in DOWNLOADERS:
        score += 15
        reasons.append("由下载工具启动")
    elif parent_lower in ARCHIVERS:
        score += 12
        reasons.append("从压缩包中启动")
    if INSTALLER_NAME_RE.search(name):
        score += 15
        reasons.append("文件名带有安装特征")
    lowered = _norm(exe)
    if lowered.endswith(INSTALLER_EXT):
        score += 10
        reasons.append("是 Windows 安装包格式")
    if frameworks:
        score += 30
        reasons.append("检测到" + "、".join(frameworks))
    command_lower = command.lower()
    if "setup" in command_lower or "/install" in command_lower or "--install" in command_lower:
        score += 15
        reasons.append("命令行含安装参数")
    if signed and "\\program files" in lowered:
        score -= 30
    score = max(0, min(100, score))

    allow = _match_allow(rules, exe_hash, publisher, exe)
    block = _match_block(rules, exe_hash, publisher, exe)
    suspicious = True
    if allow:
        suspicious = False
        score = 0
        reasons = ["已在白名单，自动放行"]
    elif block:
        suspicious = False
        score = 100
        reasons = ["已在黑名单，自动阻止"]
    if not exe:
        suspicious = False
        reasons = ["无法读取程序路径，已放行"]

    level = "high" if score >= 60 else ("medium" if score >= 30 else "low")

    return Detection(
        suspicious=suspicious,
        pid=pid,
        name=name or "未知程序",
        exe_path=exe,
        cmdline=command,
        parent_name=parent,
        score=score,
        level=level,
        reasons=reasons,
        publisher=publisher or "未知（无版本信息）",
        product=version.get("product", ""),
        version=version.get("version", ""),
        signed=signed,
        exe_hash=exe_hash,
        matched_rule=allow or block,
    )
