"""Windows 下用 WM_NCHITTEST 实现"只在蟑螂身上接收鼠标"的穿透。

**为什么不能每帧 setMask（这是曾经的闪烁根因）**

旧实现每次 `set_entities()` 都重建 `QRegion` 再 `setMask()`，33 次/秒：

1. 每次调用都会改变窗口形状 —— Windows 要重建 window region 并整窗重绘，
   肉眼看到的就是**屏幕闪烁**；
2. 窗口被裁成若干矩形，触须 / 腿 / 足迹一伸出矩形就被截断，**肢体忽隐忽现**；
3. 实体一多，矩形并集覆盖 67%~100% 屏幕，遮罩不仅白算，还让**整个桌面点不动**。

**WM_NCHITTEST 为什么更好**

它只参与命中测试，不碰绘制、不裁像素、不重建窗口。鼠标移过来时 Windows 问一句
"这个点归谁"，我们答"螳螂身上归我，其余交给下面的窗口"（`HTTRANSPARENT`）。
代价只有"鼠标移动时才回调"，与实体数量线性相关且非常轻。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Callable, Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QPoint

logger = logging.getLogger("roachHub.hit_test")

WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1


class _MSG(ctypes.Structure):
    """Win32 MSG。字段顺序与对齐必须完全一致，否则 message/lParam 会错位。"""

    _fields_ = [
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def supports_native_hit_test() -> bool:
    return sys.platform.startswith("win")


class ClickThroughFilter(QAbstractNativeEventFilter):
    """把"空桌面"上的鼠标消息让给下面的窗口。

    :param window:     覆盖层 QWidget（用于取 hwnd / 坐标映射）
    :param hit_test:   (x, y) -> bool，窗口内坐标是否命中实体
    :param interactive: () -> bool，当前是否允许接收鼠标（隐藏时直接全穿透）
    """

    def __init__(
        self,
        window,
        hit_test: Callable[[float, float], bool],
        interactive: Callable[[], bool],
    ) -> None:
        super().__init__()
        self._window = window
        self._hit_test = hit_test
        self._interactive = interactive
        self._hwnd: Optional[int] = None
        self.queries = 0        # 诊断用：命中测试次数
        self.transparent = 0    # 诊断用：判定为穿透的次数
        self.last_hit = False   # 最近一次判定结果（True = 窗口接收鼠标）

    def hwnd(self) -> int:
        if self._hwnd is None:
            self._hwnd = int(self._window.winId())
        return self._hwnd

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt 命名)
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        except Exception:
            return False, 0

        if msg.message != WM_NCHITTEST or msg.hWnd != self.hwnd():
            return False, 0

        self.queries += 1
        if not self._interactive():
            self.transparent += 1
            return True, HTTRANSPARENT

        # lParam 是屏幕坐标：低 16 位 x，高 16 位 y（有符号，支持多屏负坐标）
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value

        local = self._window.mapFromGlobal(QPoint(x, y))
        hit = bool(self._hit_test(float(local.x()), float(local.y())))
        self.last_hit = hit
        if hit:
            return False, 0  # 交给 Qt 默认处理 → HTCLIENT，正常收鼠标

        self.transparent += 1
        return True, HTTRANSPARENT
