"""进程创建监控：WMI 事件为主，psutil 轮询兜底。"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import psutil

from . import debuglog, detector, processctl, storage

SEEN_TTL = 180.0


class Monitor:
    def __init__(self, on_candidate: Callable[[detector.Detection], str]) -> None:
        self.on_candidate = on_candidate
        self._stop = threading.Event()
        self._seen: dict[int, float] = {}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        # 启动时把已存在的进程标记为已见，避免开局弹一堆窗。
        now = time.time()
        try:
            for proc in psutil.process_iter(["pid"]):
                try:
                    self._seen[int(proc.info["pid"])] = now
                except (TypeError, ValueError):
                    continue
        except psutil.Error:
            pass

        for name, target in (("ig-wmi", self._wmi_loop), ("ig-poll", self._poll_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()

    def _is_new(self, pid: int) -> bool:
        with self._lock:
            if pid in self._seen:
                return False
            self._seen[pid] = time.time()
            cutoff = time.time() - SEEN_TTL
            stale = [key for key, value in self._seen.items() if value < cutoff]
            for key in stale:
                self._seen.pop(key, None)
            return True

    def _parent_name(self, ppid: int) -> str:
        if not ppid:
            return ""
        try:
            return psutil.Process(ppid).name()
        except (psutil.Error, OSError):
            return ""

    def _wmi_loop(self) -> None:
        import pythoncom

        pythoncom.CoInitialize()
        try:
            while not self._stop.is_set():
                try:
                    import win32com.client

                    locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
                    wmi = locator.ConnectServer(".", "root\\cimv2")
                    query = wmi.ExecNotificationQuery(
                        "SELECT * FROM Win32_ProcessStartTrace"
                    )
                    while not self._stop.is_set():
                        try:
                            event = query.NextEvent(1000)
                        except Exception:
                            continue
                        try:
                            pid = int(event.ProcessID)
                            name = event.ProcessName or ""
                            ppid = int(event.ParentProcessID or 0)
                        except (TypeError, ValueError, AttributeError):
                            continue
                        try:
                            self._dispatch(pid, name, ppid)
                        except Exception:
                            continue
                except Exception:
                    self._stop.wait(5)
        finally:
            pythoncom.CoUninitialize()

    def _poll_loop(self) -> None:
        interval = float(storage.load_config().get("poll_interval", 0.3))
        while not self._stop.is_set():
            try:
                for proc in psutil.process_iter(["pid", "name", "exe", "ppid"]):
                    if self._stop.is_set():
                        break
                    try:
                        info = proc.info
                    except psutil.Error:
                        continue
                    try:
                        pid = int(info["pid"])
                    except (TypeError, ValueError):
                        continue
                    try:
                        self._dispatch(
                            pid,
                            info.get("name") or "",
                            int(info.get("ppid") or 0),
                            info.get("exe") or "",
                            None,
                        )
                    except Exception:
                        continue
            except psutil.Error:
                pass
            self._stop.wait(interval)

    def _dispatch(
        self,
        pid: int,
        name: str,
        ppid: int,
        exe_path: str = "",
        cmdline: Any = None,
    ) -> None:
        if not pid or not name:
            return
        if processctl.is_protected(name):
            return
        if not self._is_new(pid):
            return

        need_detail = not exe_path or name.lower() == "msiexec.exe"
        if need_detail:
            info = processctl.get_info(pid)
            if not info:
                return
            exe_path = info.get("exe") or exe_path or ""
            cmdline = info.get("cmdline") or []
            parent_name = info.get("parent_name") or ""
        else:
            cmdline = cmdline or []
            parent_name = self._parent_name(ppid)

        if not detector.prefilter(name, exe_path, cmdline, parent_name):
            return
        if not processctl.suspend(pid, name):
            return

        # 兜底：90 秒内没有决策就自动恢复，避免进程被永久冻结。
        watchdog = threading.Timer(90.0, lambda: processctl.resume(pid))
        watchdog.daemon = True
        watchdog.start()

        full_info = processctl.get_info(pid)
        if not full_info:
            processctl.resume(pid)
            return

        debuglog.log("MONITOR", "分析 pid=%s name=%s" % (pid, name))
        detection = detector.analyze(pid, full_info, storage.load_rules())
        if not detection.suspicious:
            debuglog.log("MONITOR", "不拦截 pid=%s 原因=%s" % (pid, detection.reasons))
            processctl.resume(pid)
            return

        debuglog.log("MONITOR", "已挂起 pid=%s 分数=%s 等待决策" % (pid, detection.score))
        try:
            decision = self.on_candidate(detection)
        except Exception as exc:
            debuglog.log("MONITOR", "决策异常 pid=%s error=%s" % (pid, exc))
            decision = "block"

        if decision in ("allow", "allow_always"):
            processctl.resume(pid)
        else:
            processctl.terminate(pid)
        debuglog.log("MONITOR", "处置完成 pid=%s decision=%s" % (pid, decision))
