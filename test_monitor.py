"""端到端冒烟测试：伪装安装器 -> 监控捕获 -> 挂起 -> 决策终止。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

import psutil

from install_guard import monitor

tmp = tempfile.mkdtemp(prefix="ig-test-")
fake_installer = os.path.join(tmp, "setup.exe")
shutil.copy(r"C:\Windows\System32\cmd.exe", fake_installer)

seen: list = []


def on_candidate(detection):
    seen.append(detection)
    print("detected:", detection.name, "score:", detection.score)
    return "block"


watcher = monitor.Monitor(on_candidate)
watcher.start()
time.sleep(0.5)

process = subprocess.Popen([fake_installer, "/c", "ping -n 30 127.0.0.1 >nul"])
time.sleep(2.5)
alive = psutil.pid_exists(process.pid)
if alive:
    try:
        process.terminate()
        process.wait(timeout=3)
    except Exception:
        pass
watcher.stop()

print("captured:", len(seen), "still alive:", alive)
assert seen, "监控未捕获伪装安装器"
assert not alive, "决策为阻止但进程仍存活"
print("MONITOR TEST PASSED")
