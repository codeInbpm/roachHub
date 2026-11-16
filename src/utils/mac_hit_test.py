"""macOS 的"只在蟑螂身上接收鼠标"——靠 `ignoresMouseEvents` 轮询。

**为什么不能照搬 Windows 那条路**

Windows 是 `WM_NCHITTEST`：鼠标每移到一个点，系统问我们一句"这个点归谁"，
我们答 `HTTRANSPARENT` 就穿透。macOS 没有对应机制 —— `NSView.hitTest:` 只在
事件已经进了本应用之后才被调用，挡不住"点桌面图标"这件事。

**为什么也不能用 QRegion 遮罩**

`setMask()` 在 mac 上走的是窗口形状/图层遮罩，语义和 Windows 不一样：形状外的
区域能不能穿透取决于 Qt 实现细节（不同版本行为不同，且遮罩每秒重设一次就是
一次窗口合成器重排）。更糟的是兜底路径里"持拍时整屏接管"要把遮罩设成全屏，
一旦语义不成立，整块桌面就点不动了 —— 这是不能接受的失败模式。

**这里用的办法**

`NSWindow.ignoresMouseEvents` 是**硬开关**：置 YES，这个窗口对鼠标完全不存在，
点击直接落到下面的窗口/桌面；置 NO，它重新接管。而"鼠标现在在哪个点"可以用
`QCursor.pos()` 每帧问一次（不需要任何权限、不依赖事件），命中判定复用
`OverlayWindow.accepts_mouse_at()` —— 和 Windows 的判据**完全同一份代码**，
所以两个平台的手感天然一致。

唯一的代价是最多一帧的延迟：鼠标移进虫子身上之后，下一帧（16ms）窗口才开始
接管。人按下鼠标不会快过 16ms，实际感知不到。
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from . import objc_bridge

logger = logging.getLogger("roachHub.hit_test.mac")


def supports_cocoa_hit_test() -> bool:
    return sys.platform == "darwin"


class CocoaClickThrough:
    """把一个顶层窗口包成"可随时开关的鼠标接收器"。"""

    def __init__(self, widget) -> None:
        self._widget = widget
        self._window: int = 0
        self._accepting: Optional[bool] = None   # None = 还没设过
        self._warned = False

    def attach(self) -> bool:
        """窗口创建（拿到 NSView）之后调用一次。"""
        try:
            view = int(self._widget.winId())
            self._window = objc_bridge.window_of(view)
            if not self._window:
                if not self._warned:
                    logger.warning("拿不到 NSWindow，鼠标穿透降级（整窗接管）")
                    self._warned = True
                return False
            # 先设成"不接管"：覆盖层刚显示的第一帧不该吃点击
            objc_bridge.send_void_bool(self._window, "setIgnoresMouseEvents:", True)
            self._accepting = False
            logger.info("鼠标穿透: NSWindow.ignoresMouseEvents（逐帧轮询）")
            return True
        except Exception:
            if not self._warned:
                logger.warning("初始化 Cocoa 穿透失败，降级为整窗接管", exc_info=True)
                self._warned = True
            return False

    @property
    def ready(self) -> bool:
        return bool(self._window)

    @property
    def accepting(self) -> Optional[bool]:
        return self._accepting

    def set_accepts(self, accepts: bool) -> None:
        """幂等：状态没变就不打扰窗口服务器。"""
        if not self._window or self._accepting is accepts:
            return
        try:
            objc_bridge.send_void_bool(
                self._window, "setIgnoresMouseEvents:", not accepts
            )
            self._accepting = accepts
        except Exception:
            if not self._warned:
                logger.warning("切换 ignoresMouseEvents 失败", exc_info=True)
                self._warned = True

    def release(self) -> None:
        """退出时把窗口还原成正常接收鼠标，避免留下一个"点不动的洞"。"""
        if self._window:
            try:
                objc_bridge.send_void_bool(self._window, "setIgnoresMouseEvents:", False)
            except Exception:  # pragma: no cover - 退出路径
                pass
        self._accepting = None
