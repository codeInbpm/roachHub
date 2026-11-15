"""隐藏"只属于本进程"的控制台窗口。

双击 `main.py`（或用 `python.exe` 启动）时，Windows 会给进程配一个控制台窗口 ——
任务栏里那个黑底 `>_` 图标。对桌面小工具来说它纯属噪音：用户只想要托盘图标。

但**不能无脑藏**：从 cmd / Windows Terminal 里 `python main.py` 时，那个控制台是
用户自己的终端窗口，藏掉等于把人家的窗口弄没了。判断办法是 `GetConsoleProcessList`：
只有"这个控制台里只挂着我们一个进程"时，它才是系统专门为我们创建的。
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger("roachHub.console")

SW_HIDE = 0
SW_SHOW = 5
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: 只有"整条控制台里都是 Python 启动器/解释器"时，这个控制台才算是给我们开的。
#: 双击 .py 的实际链路是 explorer.exe → py.exe → python.exe，所以有 2~3 个进程很正常。
LAUNCHER_NAMES = ("python.exe", "pythonw.exe", "py.exe", "pyw.exe")

_k32 = None


def _api():
    global _k32
    if _k32 is not None:
        return _k32
    if os.name != "nt":
        return None
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetConsoleWindow.restype = wintypes.HWND
        k.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        k.GetConsoleProcessList.restype = wintypes.DWORD
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        _api.user32 = u
        _k32 = k
    except Exception as e:  # pragma: no cover
        logger.debug("控制台接口不可用: %s", e)
        return None
    return _k32


def console_window() -> int:
    k = _api()
    if k is None:
        return 0
    try:
        return int(k.GetConsoleWindow() or 0)
    except Exception:
        return 0


def console_process_pids() -> list[int]:
    """挂在这个控制台上的所有进程 ID。"""
    k = _api()
    if k is None:
        return []
    try:
        buf = (wintypes.DWORD * 16)()
        n = int(k.GetConsoleProcessList(buf, 16))
        return [int(p) for p in list(buf)[:n] if p]
    except Exception:
        return []


def console_process_count() -> int:
    return len(console_process_pids())


def _image_name(pid: int) -> str:
    k = _api()
    if k is None:
        return ""
    try:
        handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ok = k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        k.CloseHandle(handle)
        return os.path.basename(buf.value).lower() if ok else ""
    except Exception:
        return ""


def parent_process_name() -> str:
    """父进程的可执行文件名（小写）。拿不到就返回空串。"""
    return _image_name(os.getppid())


def console_process_names() -> list[str]:
    return [_image_name(p) for p in console_process_pids()]


def console_is_ours() -> bool:
    """这个控制台是不是系统为"双击 / 快捷方式启动"而单独开的。

    判据：控制台里的**每一个**进程都是 Python 启动器或解释器
    （双击 .py 的链路是 explorer.exe → py.exe → python.exe，控制台里只有这两个）。

    只要控制台里混进了 cmd.exe / powershell.exe / WindowsTerminal.exe / bash.exe，
    就说明那是用户自己的终端 —— 藏了等于把人家的窗口弄没，绝对不能动。
    """
    if not console_window():
        return False
    names = console_process_names()
    if not names:
        return False
    for name in names:
        if not name or name not in LAUNCHER_NAMES:
            return False
    return True


def hide_own_console() -> bool:
    """只为"独占控制台"的情况隐藏窗口。返回是否真的藏了。"""
    hwnd = console_window()
    if not hwnd:
        return False
    if not console_is_ours():
        logger.debug("控制台是共享终端，保持不动")
        return False
    u = getattr(_api, "user32", None)
    if u is None:
        return False
    try:
        if not u.IsWindowVisible(hwnd):
            return False
        u.ShowWindow(hwnd, SW_HIDE)
        logger.debug("已隐藏本进程的控制台窗口 hwnd=%s", hwnd)
        return True
    except Exception:
        return False


def show_own_console() -> bool:
    hwnd = console_window()
    u = getattr(_api, "user32", None)
    if not hwnd or u is None:
        return False
    try:
        u.ShowWindow(hwnd, SW_SHOW)
        return True
    except Exception:
        return False
