"""macOS 的遮挡检测：控制台面板此刻是不是被别的窗口压住了。

Windows 版（`win_occlusion.py`）用 `GetTopWindow` / `GW_HWNDNEXT` 走 z-order，
mac 上没有这套 API，改用 `CGWindowListCopyWindowInfo` —— 它返回的数组**就是**
前到后的顺序，语义能一一对上：

1. 从最前面往后走，**遇到我们自己的面板窗口就停**（它下面的窗口不可能挡住它）；
2. 途中遇到的、属于别的进程、且是普通层（layer 0）、又和面板相交的窗口，
   就是"压在面板上"的那些；
3. 因为覆盖层是第 1000 层（kCGScreenSaverWindowLevel），它一定会出现在面板
   前面 —— 靠 `ownerPID` 把自己所有窗口滤掉，否则会永远判定"被挡住"。

**坐标系**：`kCGWindowBounds` 是屏幕空间、原点在主屏左上角、y 向下，和 Qt 的
全局坐标同一套，不需要翻转。（`NSEvent.mouseLocation` 才是 y 向上的，别混。）

**权限**：从 10.15 起读别的进程的窗口**标题**要"屏幕录制"授权，但
`kCGWindowOwnerPID` / `kCGWindowLayer` / `kCGWindowBounds` / `kCGWindowNumber`
这些结构化字段一直可读。这里只碰结构化字段，所以不需要任何授权 —— 这点很关键，
不然又是"为什么一个小玩具要我开屏幕录制"。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import List

from PySide6.QtCore import QRect

from . import objc_bridge

logger = logging.getLogger("roachHub.occlusion.mac")

#: kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
_LIST_OPTIONS = 0x1 | 0x10

_KEYS = None
_failed = False


def _keys():
    """常用的 CGWindow 字典键，取一次 NSString 留着复用。"""
    global _KEYS
    if _KEYS is None:
        _KEYS = {
            name: objc_bridge.nsstring(name)
            for name in (
                "kCGWindowNumber",
                "kCGWindowOwnerPID",
                "kCGWindowLayer",
                "kCGWindowBounds",
                "kCGWindowAlpha",
            )
        }
    return _KEYS


def _int_of(window_dict: int, key: int) -> int:
    value = objc_bridge.send_obj_key(window_dict, "objectForKey:", key)
    return int(objc_bridge.send_int(value, "intValue")) if value else 0


def _double_of(window_dict: int, key: int) -> float:
    value = objc_bridge.send_obj_key(window_dict, "objectForKey:", key)
    return objc_bridge.send_double(value, "doubleValue") if value else 0.0


def supports_cocoa_occlusion() -> bool:
    return sys.platform == "darwin"


def covered_rects(view_ptr: int, panel: QRect) -> List[QRect]:
    """返回压在面板上方、且与面板相交的窗口矩形（屏幕坐标）。

    空列表 = 面板完整露出。任何一步失败都返回空列表 —— 宁可"认为没被挡"
    （洞多挖一点），也不要因为异常把洞永远关掉（面板被虫子盖住）。
    """
    global _failed
    if _failed or not supports_cocoa_occlusion() or not view_ptr:
        return []
    if panel.isNull() or panel.isEmpty():
        return []

    array = 0
    try:
        window = objc_bridge.window_of(int(view_ptr))
        if not window:
            return []
        own_number = objc_bridge.send_long(window, "windowNumber")
        own_pid = os.getpid()

        cg = objc_bridge._coregraphics()
        array = int(cg.CGWindowListCopyWindowInfo(_LIST_OPTIONS, 0) or 0)
        if not array:
            return []

        keys = _keys()
        count = objc_bridge.send_ulong(array, "count")
        out: List[QRect] = []
        for index in range(count):
            info = objc_bridge.send_obj_idx(array, "objectAtIndex:", index)
            if not info:
                continue
            number = _int_of(info, keys["kCGWindowNumber"])
            if number == own_number:
                break                      # 走到自己了，后面的都在下面
            if _int_of(info, keys["kCGWindowOwnerPID"]) == own_pid:
                continue                   # 自己的窗口（覆盖层 / 控制台）不算遮挡
            if _int_of(info, keys["kCGWindowLayer"]) != 0:
                continue                   # 菜单栏 / Dock / 悬浮面板等非普通层
            if _double_of(info, keys["kCGWindowAlpha"]) <= 0.01:
                continue                   # 全透明窗口挡不住任何东西
            bounds = objc_bridge.rect_from_dict(
                objc_bridge.send_obj_key(info, "objectForKey:", keys["kCGWindowBounds"])
            )
            if bounds is None:
                continue
            x, y, w, h = bounds
            if w <= 0 or h <= 0:
                continue
            rect = QRect(int(x), int(y), int(w), int(h))
            if panel.intersects(rect):
                out.append(rect)
        return out
    except Exception:
        # 只报一次，别让 4Hz 的轮询把日志刷满
        _failed = True
        logger.warning("macOS 遮挡检测不可用，退化为「永远认为面板露着」", exc_info=True)
        return []
    finally:
        if array:
            objc_bridge.cf_release(array)
