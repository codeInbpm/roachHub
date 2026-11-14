"""跨平台特性兼容与 Windows/macOS API 适配。"""

from __future__ import annotations
import sys
import logging
from typing import Optional

logger = logging.getLogger("roachHub.platform")


class PlatformManager:
    """平台特异性操作管理器。"""

    @staticmethod
    def is_windows() -> bool:
        return sys.platform.startswith("win")

    @staticmethod
    def is_macos() -> bool:
        return sys.platform == "darwin"

    @classmethod
    def setup_window_styles(cls, window_handle: int) -> bool:
        """为平台配置无干扰置顶覆盖层样式（如隐藏任务栏图标、不抢焦点）。

        :param window_handle: QWidget.winId() 返回的原生窗口句柄
        """
        if cls.is_windows():
            return cls._setup_windows_styles(window_handle)
        elif cls.is_macos():
            return cls._setup_macos_styles(window_handle)
        return True

    @classmethod
    def set_click_through(cls, window_handle: int, enable: bool = True) -> bool:
        """设置窗口全局是否完全鼠标穿透（Win32 方式）。
        注：若使用 Qt QRegion Mask 方式，则无需全局 WS_EX_TRANSPARENT。
        """
        if not cls.is_windows():
            return False

        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000

            style = user32.GetWindowLongW(window_handle, GWL_EXSTYLE)
            if enable:
                new_style = style | WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                new_style = (style & ~WS_EX_TRANSPARENT) | WS_EX_LAYERED

            user32.SetWindowLongW(window_handle, GWL_EXSTYLE, new_style)
            return True
        except Exception as e:
            logger.warning("设置 Windows 窗口穿透样式失败: %s", e)
            return False

    @staticmethod
    def _setup_windows_styles(hwnd: int) -> bool:
        """在 Windows 下配置 WS_EX_TOOLWINDOW 与 WS_EX_NOACTIVATE。"""
        try:
            import ctypes

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080   # 不在任务栏显示图标
            WS_EX_NOACTIVATE = 0x08000000   # 点击时不激活/夺取前台焦点

            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_style = style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            logger.debug("Windows 扩展样式设置成功: 0x%X -> 0x%X", style, new_style)
            return True
        except Exception as e:
            logger.warning("Windows 原生窗口扩展样式设置失败: %s", e)
            return False

    @staticmethod
    def _setup_macos_styles(_hwnd: int) -> bool:
        """macOS 下的额外窗口特性预留。"""
        # macOS 下 PySide6 的 Qt.WindowStaysOnTopHint 和 Qt.SubWindow 已经提供大部分能力
        logger.debug("macOS 窗口样式保持默认 Qt 管理")
        return True
