"""极简 Objective-C 运行时桥（纯 ctypes，不引入 pyobjc）。

macOS 上有三件事 Qt 没有 API，只能问原生层：

| 要做的事                     | 原生入口                                   |
| ---------------------------- | ------------------------------------------ |
| 窗口层级 / 跨 Space / 不随失活隐藏 | `NSWindow.setLevel:` `setCollectionBehavior:` |
| 逐点鼠标穿透                 | `NSWindow.setIgnoresMouseEvents:`          |
| 面板是否被别的窗口盖住       | `CGWindowListCopyWindowInfo`               |

pyobjc 是 400MB+ 的额外依赖，为一个桌面玩具不值当，所以这里直接
`objc_msgSend`。**用法约束**：所有调用都走下面几个定点函数——`objc_msgSend`
是变参函数，必须按"这个选择器确切的返回类型 + 参数类型"重新取一次函数指针
（`_fn` 干的活），直接拿一个通用的指针去调，在 x86_64 上会读到垃圾寄存器、
在 arm64 上会直接把指针截断。这是这类胶水代码最容易出事的地方。

**全模块懒加载**：Windows 上 import 本模块是安全的（`available()` 返回 False），
只有真的调用才会去 `LoadLibrary("/usr/lib/libobjc.A.dylib")`。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Dict, Optional, Tuple

logger = logging.getLogger("roachHub.objc")

_lib = None
_cg = None
_sel_cache: Dict[str, int] = {}
_cls_cache: Dict[str, int] = {}
_msg_cache: Dict[Tuple, object] = {}

#: NSAutoreleasePool 之类不需要——Qt 的 run loop 每轮都会 drain 默认 pool，
#: 我们创建的都是 autoreleased 的 NSString，4Hz 的量无所谓。


def available() -> bool:
    """当前进程能不能用这套桥（只在 macOS 上）。"""
    return sys.platform == "darwin"


def _objc():
    global _lib
    if _lib is None:
        lib = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        _lib = lib
    return _lib


def _coregraphics():
    global _cg
    if _cg is None:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cg.CGWindowLevelForKey.restype = ctypes.c_int32
        cg.CGWindowLevelForKey.argtypes = [ctypes.c_int32]
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_int32, ctypes.c_uint32]
        cg.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
        cg.CGRectMakeWithDictionaryRepresentation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        _cg = cg
    return _cg


def sel(name: str) -> int:
    """选择器名字 → SEL（缓存：字符串比较换成整数比较）。"""
    cached = _sel_cache.get(name)
    if cached is None:
        cached = int(_objc().sel_registerName(name.encode("utf-8")) or 0)
        _sel_cache[name] = cached
    return cached


def cls(name: str) -> int:
    """类名 → Class。取不到返回 0（调用方自己判）。"""
    cached = _cls_cache.get(name)
    if cached is None:
        cached = int(_objc().objc_getClass(name.encode("utf-8")) or 0)
        _cls_cache[name] = cached
    return cached


def _fn(restype, argtypes):
    """按 (返回类型, 参数类型) 取一份 objc_msgSend 的函数指针。

    `objc_msgSend` 本身没有可用原型，ABI 完全由调用点的类型签名决定：
    浮点返回值走 xmm0、小结构体走寄存器对、大结构体走隐藏指针 —— 所以
    每种签名必须单独 cast 一次，不能共用一个 `c_void_p` 版本。
    """
    key = (restype, argtypes)
    fn = _msg_cache.get(key)
    if fn is None:
        proto = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)
        fn = proto(("objc_msgSend", _objc()))
        _msg_cache[key] = fn
    return fn


# --------------------------------------------------------------------- 调用点
# 每个函数名就是"参数形状"，别嫌啰嗦：这是唯一能保证 ABI 正确的方式。

def send_void(obj: int, name: str) -> None:
    _fn(None, ())(obj, sel(name))


def send_void_bool(obj: int, name: str, value: bool) -> None:
    _fn(None, (ctypes.c_bool,))(obj, sel(name), bool(value))


def send_void_obj(obj: int, name: str, other: int) -> None:
    """`setBackgroundColor:` 这类吃一个对象参数、无返回的选择器。"""
    _fn(None, (ctypes.c_void_p,))(obj, sel(name), other)


def send_void_long(obj: int, name: str, value: int) -> None:
    _fn(None, (ctypes.c_long,))(obj, sel(name), int(value))


def send_void_ulong(obj: int, name: str, value: int) -> None:
    _fn(None, (ctypes.c_ulong,))(obj, sel(name), int(value))


def send_obj(obj: int, name: str) -> int:
    return int(_fn(ctypes.c_void_p, ())(obj, sel(name)) or 0)


def send_obj_cstr(obj: int, name: str, text: str) -> int:
    return int(_fn(ctypes.c_void_p, (ctypes.c_char_p,))(
        obj, sel(name), text.encode("utf-8")) or 0)


def send_obj_idx(obj: int, name: str, index: int) -> int:
    return int(_fn(ctypes.c_void_p, (ctypes.c_ulong,))(obj, sel(name), int(index)) or 0)


def send_obj_key(obj: int, name: str, key: int) -> int:
    return int(_fn(ctypes.c_void_p, (ctypes.c_void_p,))(obj, sel(name), key) or 0)


def send_long(obj: int, name: str) -> int:
    return int(_fn(ctypes.c_long, ())(obj, sel(name)))


def send_ulong(obj: int, name: str) -> int:
    return int(_fn(ctypes.c_ulong, ())(obj, sel(name)))


def send_int(obj: int, name: str) -> int:
    return int(_fn(ctypes.c_int, ())(obj, sel(name)))


def send_bool(obj: int, name: str) -> bool:
    """返回 `BOOL` 的选择器（如 `isVisible`）。

    BOOL 在 arm64 上是 `unsigned char`，用 c_int 去接会连高位一起读，
    偶然也能对——但那是碰运气，别学。
    """
    return bool(_fn(ctypes.c_bool, ())(obj, sel(name)))


def send_double(obj: int, name: str) -> float:
    """返回 `double` 的选择器（如 NSNumber.doubleValue）。

    注意必须走 c_double 原型：浮点返回值在 x86_64/arm64 上都走浮点寄存器，
    用 c_void_p 去接会读到一段无关的通用寄存器内容。
    """
    return float(_fn(ctypes.c_double, ())(obj, sel(name)))


# ------------------------------------------------------------------ 几个常量

#: NSWindowOcclusionState（10.9+）
NSWindowOcclusionStateVisible = 0x2

#: NSWindowCollectionBehavior
NSWindowCollectionBehaviorCanJoinAllSpaces = 1 << 0
NSWindowCollectionBehaviorStationary = 1 << 4
NSWindowCollectionBehaviorIgnoresCycle = 1 << 6
NSWindowCollectionBehaviorFullScreenAuxiliary = 1 << 8

#: CGWindowLevelKey —— 覆盖层要压在 Dock（7）/ 菜单栏（8）之上。
#: 13 = kCGScreenSaverWindowLevelKey。不用 kCGMaximumWindowLevelKey：那一层会
#: 盖住系统弹窗和授权对话框，用户会以为程序卡死。
WINDOW_LEVEL_SCREENSAVER_KEY = 13


def window_level(level_key: int = WINDOW_LEVEL_SCREENSAVER_KEY) -> int:
    return int(_coregraphics().CGWindowLevelForKey(int(level_key)))


def nsstring(text: str) -> int:
    """Python 字符串 → NSString（autoreleased）。"""
    return send_obj_cstr(cls("NSString"), "stringWithUTF8String:", text)


def clear_color() -> int:
    """`[NSColor clearColor]`（autoreleased）——透明覆盖层窗口的背景色。"""
    return send_obj(cls("NSColor"), "clearColor")


def window_of(view_or_window: int) -> int:
    """NSView / NSWindow → NSWindow。

    NSView 走 `[view window]`；NSWindow 自己也是 NSResponder，`[win window]`
    返回它自己 —— 所以传错类型也不会炸。
    """
    if not view_or_window:
        return 0
    return send_obj(view_or_window, "window")


def describe(obj: int) -> str:
    """调试用：取 `[obj description]` 的 UTF-8 文本。"""
    try:
        ns = send_obj(obj, "description")
        if not ns:
            return "<nil>"
        raw = send_obj(ns, "UTF8String")
        if not raw:
            return "<non-utf8>"
        return ctypes.c_char_p(raw).value.decode("utf-8", "replace")
    except Exception:  # pragma: no cover - 仅调试
        return "<description 失败>"


_cf_release = None


def cf_release(ref: int) -> None:
    """CFRelease：CGWindowListCopyWindowInfo 返回的数组是 +1 引用，必须还回去。

    4Hz × 每个窗口一个 CFDictionary，不释放的话一次会话能攒出几十 MB。
    """
    global _cf_release
    if not ref:
        return
    try:
        if _cf_release is None:
            lib = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            lib.CFRelease.argtypes = [ctypes.c_void_p]
            lib.CFRelease.restype = None
            _cf_release = lib.CFRelease
        _cf_release(ctypes.c_void_p(ref))
    except Exception:  # pragma: no cover - 退出路径
        pass


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def rect_from_dict(window_dict: int) -> Optional[Tuple[float, float, float, float]]:
    """CGWindow 字典 → (x, y, w, h)。

    坐标是**屏幕空间**、原点在主屏左上角、y 向下 —— 和 Qt 的全局坐标同一套，
    不需要翻转（这点和 `NSEvent.mouseLocation` 的 y 向上相反，别搞混）。
    """
    if not window_dict:
        return None
    r = CGRect()
    ok = _coregraphics().CGRectMakeWithDictionaryRepresentation(
        ctypes.c_void_p(window_dict), ctypes.byref(r)
    )
    if not ok:
        return None
    return (r.origin.x, r.origin.y, r.size.width, r.size.height)
