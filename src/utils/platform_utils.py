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
    def _setup_macos_styles(view_ptr: int) -> bool:
        """macOS：把覆盖层提成"跨 Space、压住 Dock/菜单栏、失活也不隐藏"的浮层。

        三件事 Qt 的 `WindowStaysOnTopHint` 都做不到：

        1. **跨 Space** —— 用户切到别的桌面（或全屏 App）时，`StaysOnTop` 的窗口
           会留在原来那个 Space 上，虫子凭空消失。要
           `NSWindowCollectionBehaviorCanJoinAllSpaces | Stationary |
           FullScreenAuxiliary`。
        2. **压住 Dock 与菜单栏** —— Qt 的置顶只到 `NSFloatingWindowLevel`(3)，
           而 Dock 在第 20 层、菜单栏在第 24 层。必须显式 `setLevel:` 到
           kCGScreenSaverWindowLevel(1000)，否则屏幕右下角那条永远压不住 —— 现象
           和 Windows 版注释里写的"右下角卡住"是同一类问题。
        3. **失活不隐藏** —— `.app` 是 LSUIElement（无 Dock 图标），这类程序的窗口
           默认会在应用失去焦点时 `hidesOnDeactivate`，用户点一下别处虫子就没了。

        另外**必须亲手做一遍"透明窗口三件套"**（`setOpaque:NO` + clearColor 背景 +
        `setHasShadow:NO`）：`WA_TranslucentBackground` 在 cocoa 插件里对窗口类型的
        依赖很怪（QTBUG-97004），光靠 Qt 这层不可靠。v0.1.0 在 mac 上"窗口整个
        不显示"就是这么来的 —— 窗口存在、事件在跑、音效在响，就是合成器里没有
        这一层。这里直接把 NSWindow 钉成"非不透明 + 全透明背景"，Qt 那边设没设
        成都不再要紧。

        最后顺手 `orderFrontRegardless`：不激活应用也能把窗口提到最前。
        """
        from . import objc_bridge as ob

        if not ob.available():
            return False
        try:
            window = ob.window_of(int(view_ptr))
            if not window:
                logger.debug("macOS: 拿不到 NSWindow，跳过窗口层级设置")
                return False

            ob.send_void_bool(window, "setOpaque:", False)
            ob.send_void_obj(window, "setBackgroundColor:", ob.clear_color())
            ob.send_void_bool(window, "setHasShadow:", False)
            ob.send_void_long(window, "setLevel:", ob.window_level())
            behavior = (
                ob.NSWindowCollectionBehaviorCanJoinAllSpaces
                | ob.NSWindowCollectionBehaviorStationary
                | ob.NSWindowCollectionBehaviorFullScreenAuxiliary
                | ob.NSWindowCollectionBehaviorIgnoresCycle
            )
            ob.send_void_ulong(window, "setCollectionBehavior:", behavior)
            ob.send_void_bool(window, "setHidesOnDeactivate:", False)
            ob.send_void_bool(window, "setCanHide:", False)
            ob.send_void(window, "orderFrontRegardless")
            logger.info("macOS 窗口层级已提升（level=%s, 跨 Space, isVisible=%s）",
                        ob.window_level(),
                        ob.send_bool(window, "isVisible"))
            return True
        except Exception as e:
            logger.warning("设置 macOS 窗口属性失败: %s", e)
            return False
