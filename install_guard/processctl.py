"""Windows 进程控制：挂起、恢复、终止，以及进程信息读取。"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Any

import psutil

from .config import PROTECTED_PROCESSES

PROCESS_TERMINATE = 0x0001
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtSuspendProcess.restype = wintypes.LONG
_ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtResumeProcess.restype = wintypes.LONG

_HANDLES: dict[int, int] = {}
_LOCK = threading.RLock()


def is_protected(name: str) -> bool:
    return (name or "").lower() in PROTECTED_PROCESSES


def get_info(pid: int) -> dict[str, Any]:
    """读取进程详情；任何读取失败都返回空字典。"""
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            name = proc.name()
            exe = ""
            cmdline: list[str] = []
            try:
                exe = proc.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                pass
            try:
                cmdline = proc.cmdline()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                pass
            ppid = proc.ppid()
            created = proc.create_time()
        parent_name = ""
        if ppid and ppid != pid:
            try:
                parent_name = psutil.Process(ppid).name()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                pass
        return {
            "pid": pid,
            "name": name,
            "exe": exe,
            "cmdline": cmdline,
            "ppid": ppid,
            "parent_name": parent_name,
            "created": created,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return {}


def suspend(pid: int, name: str) -> bool:
    """冻结目标进程。句柄会保留到 resume/terminate。"""
    if is_protected(name):
        return False
    handle = _kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not handle:
        return False
    status = _ntdll.NtSuspendProcess(handle)
    if status != 0:
        _kernel32.CloseHandle(handle)
        return False
    with _LOCK:
        old = _HANDLES.pop(pid, None)
        if old:
            _kernel32.CloseHandle(old)
        _HANDLES[pid] = handle
    return True


def resume(pid: int) -> bool:
    with _LOCK:
        handle = _HANDLES.pop(pid, None)
    if not handle:
        return False
    try:
        return _ntdll.NtResumeProcess(handle) == 0
    finally:
        _kernel32.CloseHandle(handle)


def terminate(pid: int) -> bool:
    # 挂起时保存的句柄只有 SUSPEND_RESUME 权限，终止必须另开带 TERMINATE 权限的句柄。
    with _LOCK:
        old = _HANDLES.pop(pid, None)
    if old:
        try:
            _ntdll.NtResumeProcess(old)
        finally:
            _kernel32.CloseHandle(old)
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(_kernel32.TerminateProcess(handle, 1))
    finally:
        _kernel32.CloseHandle(handle)


def release_all() -> None:
    """退出前兜底：恢复所有仍处于挂起状态的进程。"""
    with _LOCK:
        handles = list(_HANDLES.items())
        _HANDLES.clear()
    for pid, handle in handles:
        try:
            _ntdll.NtResumeProcess(handle)
            _kernel32.CloseHandle(handle)
        except OSError:
            pass
