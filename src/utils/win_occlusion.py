"""面试遮挡检测：控制台面板此刻是否真的露在最前面。

**为什么需要这个模块**

覆盖层为了让控制台的按钮点得动，会在面板所占的矩形上「挖洞」：既不画蟑螂，
也不接管鼠标（见 `overlay_window.set_hole_region`）。但控制台是**普通窗口** ——
用户按 `Alt+Tab` 或点一下浏览器，它就沉到后面去了，而洞还留在原地。

结果就是用户看到的现象：**蟑螂跑到屏幕中间那块区域就凭空消失**，走进去看不见、
也抓不着，而且那块矩形恰好是"上一次面板所在的位置"。面板本身被浏览器挡着，
屏幕上只剩一个洞，看起来就像灵异事件。

所以挖洞必须挂在"面板确实露着"这个前提上：

    hole = 面板矩形 − 挡在面板上面的窗口矩形

「挡在上面」用 z-order 走一遍 `GetTopWindow` / `GW_HWNDNEXT` 判断，
只统计**真正可见、没最小化、不是我们自己的**窗口。走到的窗口数量是个位数，
4Hz 轮询的开销可以忽略。
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from typing import List, Optional

from PySide6.QtCore import QRect

logger = logging.getLogger("roachHub.occlusion")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
GW_HWNDNEXT = 2

_MAX_WALK = 400  # 防御：极端桌面环境（几百个窗口）下别把主线程拖住

_user32 = None
_init_failed = False


def _api():
    """惰性加载 user32 并固化函数签名（64 位下不写 restype 会截断指针）。"""
    global _user32, _init_failed
    if _user32 is not None or _init_failed:
        return _user32
    if os.name != "nt":
        _init_failed = True
        return None
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetTopWindow.restype = wintypes.HWND
        u.GetTopWindow.argtypes = [wintypes.HWND]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsWindow.restype = wintypes.BOOL
        _user32 = u
    except Exception as e:  # pragma: no cover - 只在异常环境触发
        logger.warning("遮挡检测不可用: %s", e)
        _init_failed = True
    return _user32


def supports_occlusion_check() -> bool:
    return _api() is not None


def _own_pid() -> int:
    return os.getpid()


def _occluder_rect(u, hwnd: int, own_pid: int) -> Optional[QRect]:
    """这个窗口是否挡住了别人；是则返回它的屏幕矩形。"""
    if not u.IsWindowVisible(hwnd):
        return None
    if u.IsIconic(hwnd):
        return None

    ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    # 点击穿透的窗口（其它 overlay 工具、HUD）不挡视觉，但会挡住 WM_PAINT 吗？
    # 不会 —— 它自己都不接收鼠标、通常也全透明，当成遮挡会让洞永远算不出来。
    if ex & WS_EX_TRANSPARENT:
        return None
    # 不抢焦点的辅助窗口（输入法候选框、提示气泡）高度不可预测，跳过
    if ex & WS_EX_NOACTIVATE:
        return None

    pid = wintypes.DWORD(0)
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == own_pid:
        return None  # 我们自己的覆盖层/面板，不算遮挡

    r = wintypes.RECT()
    if not u.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    return QRect(r.left, r.top, w, h)


def covered_rects(hwnd: int, panel: QRect) -> List[QRect]:
    """返回压在 `hwnd` 上方、且与 `panel`（屏幕坐标）相交的窗口矩形。

    从 z-order 最顶层往下走，**在遇到 `hwnd` 时停止** —— 它下面的窗口不可能
    挡住它。列表为空 = 面板完整露出（洞可以照常挖）。
    """
    if hwnd <= 0 or panel.isNull() or panel.isEmpty():
        return []
    u = _api()
    if u is None:
        return []

    own_pid = _own_pid()
    out: List[QRect] = []
    if not u.IsWindow(hwnd):
        # 传进来的不是真实存在的窗口句柄（off-screen 测试平台给的是假句柄）。
        # 这时候"没找到它"绝不能当成"被所有窗口挡住"，否则测试环境里洞会永远为空。
        return []
    cur = u.GetTopWindow(None)
    walked = 0
    try:
        while cur and walked < _MAX_WALK:
            walked += 1
            h = int(cur)
            if h == hwnd:
                break
            rect = _occluder_rect(u, h, own_pid)
            if rect is not None and panel.intersects(rect):
                out.append(rect)
            cur = u.GetWindow(cur, GW_HWNDNEXT)
    except Exception as e:  # pragma: no cover
        logger.debug("遍历 z-order 失败: %s", e)
    return out
