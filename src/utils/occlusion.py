"""遮挡检测的平台分发层。

上层（`app.py`）只关心一件事：**面板现在被谁压着**，好决定"洞"挖多大。
Windows 和 macOS 的实现完全独立（一个走 Win32 z-order，一个走
`CGWindowListCopyWindowInfo`），但对外契约一致：

    covered_rects(window_handle, panel) -> list[QRect]

* 空列表 = 面板完整露出；
* 非空 = 列表里那些矩形压在面板上（调用方从面板矩形里减掉它们）；
* 完全不支持检测的平台（Linux）一律返回空列表，等价于"永远认为露着"——
  这和改造前 `win_occlusion.covered_rects` 在非 Windows 上的行为一致。
"""

from __future__ import annotations

import sys
from typing import List

from PySide6.QtCore import QRect


def covered_rects(window_handle: int, panel: QRect) -> List[QRect]:
    if sys.platform.startswith("win"):
        from .win_occlusion import covered_rects as _win

        return _win(window_handle, panel)
    if sys.platform == "darwin":
        from .mac_occlusion import covered_rects as _mac

        return _mac(window_handle, panel)
    return []
