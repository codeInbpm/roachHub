"""macOS 全局热键（Carbon `RegisterEventHotKey`）。

**为什么走 Carbon 而不是更"现代"的路子**

Qt 没有全局热键 API，macOS 上能选的只有三条，前两条都有硬伤：

* `NSEvent.addGlobalMonitorForEventsMatchingMask` —— 必须在"系统设置 → 隐私与
  安全性 → 辅助功能"里授权。授权是**绑代码签名**的：每次重新打包签名变了就得
  重授一次，用户看到的是一个"莫名其妙要我开权限"的桌面小玩具。
* `CGEventTap` —— 除了辅助功能还要输入监控权限，更重。
* `RegisterEventHotKey` —— 不需要任何权限。热键由窗口服务器直接投递给本进程，
  和前台是哪个应用无关。QHotkey 之类的库在 mac 上也是这么做的。

**四个必须注意的点**

1. 这是 **Carbon 事件**，不是 `NSEvent`，所以收不到 `nativeEventFilter` 里那种
   `windows_generic_MSG`。要在 `GetApplicationEventTarget()` 上装 handler，
   并且必须等 `QApplication` 建好之后再装（`install()` 的调用时机由上层保证）。
2. `RegisterEventHotKey` 的 id 是**我们自己编的 32 位整数**，回调里靠
   `GetEventParameter(kEventParamDirectObject, typeEventHotKeyID)` 取回来。
   它和窗口、和 `RegisterHotKey` 的 id 完全是两套体系。
3. 修饰键是**独立的一套位**（cmd 0x0100 / shift 0x0200 / option 0x0800 /
   control 0x1000），和 Windows 的 `MOD_*` 不能混用，必须换算。
4. 组合被别的程序占用时返回非 0 OSStatus（常见 -9878 = eventHotKeyExistsErr），
   必须把错误码透给用户，否则又是一例"我设了但没反应"。
"""

from __future__ import annotations

import ctypes
import logging
from typing import Callable, Dict, Optional, Tuple

from . import objc_bridge

logger = logging.getLogger("roachHub.hotkeys.mac")

#: 四字符签名，只用于区分"这组热键是我们的"
_SIGNATURE = int.from_bytes(b"rHub", "big")

#: kEventClassKeyboard / kEventHotKeyPressed
_EVENT_CLASS_KEYBOARD = 0x6B657962
_EVENT_HOTKEY_PRESSED = 5
#: kEventParamDirectObject / typeEventHotKeyID
_PARAM_DIRECT_OBJECT = 0x2D2D2D2D
_TYPE_EVENT_HOTKEY_ID = 0x686B6964

#: Carbon 修饰键
C_META = 0x0100     # Command
C_SHIFT = 0x0200
C_ALT = 0x0800      # Option
C_CTRL = 0x1000

_OSSTATUS_TEXT = {
    -50: "参数非法",
    -9878: "已被其他程序占用",
    -9879: "热键无效",
}


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_EventHandlerProc = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)

_carbon = None


def _lib():
    global _carbon
    if _carbon is None:
        c = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/Carbon.framework/Carbon"
        )
        c.GetApplicationEventTarget.restype = ctypes.c_void_p
        c.GetApplicationEventTarget.argtypes = []

        c.InstallEventHandler.restype = ctypes.c_int32
        c.InstallEventHandler.argtypes = [
            ctypes.c_void_p,          # inTarget
            _EventHandlerProc,        # inHandler
            ctypes.c_ulong,           # inNumTypes
            ctypes.POINTER(EventTypeSpec),
            ctypes.c_void_p,          # inUserData
            ctypes.POINTER(ctypes.c_void_p),  # outRef
        ]

        c.RemoveEventHandler.restype = ctypes.c_int32
        c.RemoveEventHandler.argtypes = [ctypes.c_void_p]

        c.RegisterEventHotKey.restype = ctypes.c_int32
        c.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,          # inHotKeyCode
            ctypes.c_uint32,          # inHotKeyModifiers
            EventHotKeyID,            # inHotKeyID（按值传，8 字节走寄存器）
            ctypes.c_void_p,          # inTarget
            ctypes.c_uint32,          # inOptions
            ctypes.POINTER(ctypes.c_void_p),  # outRef
        ]

        c.UnregisterEventHotKey.restype = ctypes.c_int32
        c.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]

        c.GetEventParameter.restype = ctypes.c_int32
        c.GetEventParameter.argtypes = [
            ctypes.c_void_p,          # inEvent
            ctypes.c_uint32,          # inName
            ctypes.c_uint32,          # inDesiredType
            ctypes.c_void_p,          # outActualType
            ctypes.c_uint32,          # inBufferSize
            ctypes.c_void_p,          # outActualSize
            ctypes.c_void_p,          # outData
        ]
        _carbon = c
    return _carbon


#: 键名 → Carbon 虚拟键码（kVK_*）。键名与 `hotkeys.key_label()` 的输出对齐
#: （大写字母 / 数字 / "F1" / "Space" / "PageUp" 之类）。
_KEYCODES: Dict[str, int] = {
    "A": 0x00, "S": 0x01, "D": 0x02, "F": 0x03, "H": 0x04, "G": 0x05,
    "Z": 0x06, "X": 0x07, "C": 0x08, "V": 0x09, "B": 0x0B, "Q": 0x0C,
    "W": 0x0D, "E": 0x0E, "R": 0x0F, "Y": 0x10, "T": 0x11,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17,
    "=": 0x18, "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D,
    "]": 0x1E, "O": 0x1F, "U": 0x20, "[": 0x21, "I": 0x22, "P": 0x23,
    "L": 0x25, "J": 0x26, "'": 0x27, "K": 0x28, ";": 0x29, "\\": 0x2A,
    ",": 0x2B, "/": 0x2C, "N": 0x2D, "M": 0x2E, ".": 0x2F,
    "ENTER": 0x24, "RETURN": 0x24,
    "TAB": 0x30, "SPACE": 0x31, "`": 0x32, "BACKSPACE": 0x33, "ESC": 0x35,
    "HOME": 0x73, "END": 0x77, "PAGEUP": 0x74, "PAGEDOWN": 0x79,
    "UP": 0x7E, "DOWN": 0x7D, "LEFT": 0x7B, "RIGHT": 0x7C,
    # mac 上"删除"只有 forward delete 这个键；Insert 落在 Help 键上
    "DELETE": 0x75, "DEL": 0x75, "INSERT": 0x72, "INS": 0x72,
    "F1": 0x7A, "F2": 0x78, "F3": 0x63, "F4": 0x76, "F5": 0x60, "F6": 0x61,
    "F7": 0x62, "F8": 0x64, "F9": 0x65, "F10": 0x6D, "F11": 0x67, "F12": 0x6F,
    "F13": 0x69, "F14": 0x6B, "F15": 0x71, "F16": 0x6A, "F17": 0x40, "F18": 0x4F,
    "F19": 0x50, "F20": 0x5A,
}


def keycode_of(label: str) -> Optional[int]:
    return _KEYCODES.get(label.upper())


def carbon_modifiers(mods: int, mod_win: int, mod_control: int,
                     mod_alt: int, mod_shift: int) -> int:
    """Windows 风格的 MOD_* 位 → Carbon 修饰键位。

    **Win 键在 mac 上是 Command**（`_MOD_ALIASES` 里 cmd/super/meta 都归到
    MOD_WIN），所以这里把 MOD_WIN 映射成 cmdKey，用户写 "Cmd+Alt+H" 是符合直觉的。
    """
    out = 0
    if mods & mod_win:
        out |= C_META
    if mods & mod_control:
        out |= C_CTRL
    if mods & mod_alt:
        out |= C_ALT
    if mods & mod_shift:
        out |= C_SHIFT
    return out


def describe_status(status: int) -> str:
    text = _OSSTATUS_TEXT.get(status)
    if text:
        return f"{text}（OSStatus {status}）"
    return f"注册失败（OSStatus {status}）"


class MacHotkeyBackend:
    """Carbon 全局热键后端。接口与 `hotkeys._WinBackend` 一致：

    `install() / uninstall() / register(name, keycode, mods) / unregister(name)`，
    触发时调 `on_fire(name)`。
    """

    def __init__(self, on_fire: Callable[[str], None]) -> None:
        self._on_fire = on_fire
        self._installed = False
        self._handler_ref: Optional[int] = None
        self._handler_proc = None      # 必须留引用，否则 CFUNCTYPE 被回收后回调变野指针
        self._refs: Dict[str, int] = {}       # 逻辑名 -> EventHotKeyRef
        self._ids: Dict[int, str] = {}        # hotkey id -> 逻辑名

    # ------------------------------------------------------------ 生命周期

    def install(self) -> bool:
        if self._installed:
            return True
        if not objc_bridge.available():
            return False
        try:
            spec = EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
            out = ctypes.c_void_p()
            self._handler_proc = _EventHandlerProc(self._handle)
            target = _lib().GetApplicationEventTarget()
            status = _lib().InstallEventHandler(
                target, self._handler_proc, 1, ctypes.byref(spec),
                None, ctypes.byref(out)
            )
            if status != 0:
                logger.warning("装 Carbon 事件处理器失败：%s", describe_status(status))
                self._handler_proc = None
                return False
            self._handler_ref = out
            self._installed = True
            logger.info("Carbon 事件处理器已就绪（全局热键可用）")
            return True
        except Exception:
            logger.warning("Carbon 不可用，全局热键降级为不可用", exc_info=True)
            self._handler_proc = None
            return False

    def uninstall(self) -> None:
        for name in list(self._refs):
            self.unregister(name)
        if self._handler_ref is not None:
            try:
                _lib().RemoveEventHandler(self._handler_ref)
            except Exception:  # pragma: no cover - 退出路径
                logger.debug("移除碳事件处理器失败", exc_info=True)
            self._handler_ref = None
        self._handler_proc = None
        self._installed = False

    # ---------------------------------------------------------------- 注册

    def register(self, name: str, keycode: int, modifiers: int) -> Tuple[bool, str]:
        if not self._installed:
            return False, "Carbon 未就绪"
        ref = ctypes.c_void_p()
        hotkey_id = EventHotKeyID(_SIGNATURE, self._next_id())
        try:
            status = _lib().RegisterEventHotKey(
                keycode, modifiers, hotkey_id,
                _lib().GetApplicationEventTarget(), 0, ctypes.byref(ref)
            )
        except Exception as exc:  # pragma: no cover - 只在 mac 上走到
            return False, f"注册异常：{exc}"
        if status != 0:
            return False, describe_status(status)
        self._refs[name] = ref
        self._ids[int(hotkey_id.id)] = name
        return True, ""

    def unregister(self, name: str) -> None:
        ref = self._refs.pop(name, None)
        if ref is None:
            return
        try:
            _lib().UnregisterEventHotKey(ref)
        except Exception:  # pragma: no cover - 退出路径
            logger.debug("注销热键失败：%s", name, exc_info=True)
        for hid, bound in list(self._ids.items()):
            if bound == name:
                self._ids.pop(hid, None)

    def _next_id(self) -> int:
        # 和 Windows 那套一样从 0xA100 起，避免和 id 0（Carbon 里 0 常被当成无效）
        used = set(self._ids)
        hid = 0xA100
        while hid in used:
            hid += 1
        return hid

    # ---------------------------------------------------------------- 回调

    def _handle(self, _call_ref, event, _user_data) -> int:
        """Carbon 事件回调。

        只在主线程被调用（Carbon 事件由主 run loop 派发），所以这里直接执行
        上层回调是安全的 —— 和 Windows 的 nativeEventFilter 同一条约束。
        """
        hid = EventHotKeyID()
        status = _lib().GetEventParameter(
            event, _PARAM_DIRECT_OBJECT, _TYPE_EVENT_HOTKEY_ID,
            None, ctypes.sizeof(hid), None, ctypes.byref(hid)
        )
        if status == 0 and int(hid.signature) == _SIGNATURE:
            name = self._ids.get(int(hid.id))
            if name is not None:
                try:
                    self._on_fire(name)
                except Exception:
                    logger.exception("热键回调异常：%s", name)
        return 0        # noErr：事件已处理，别让系统再响一声
