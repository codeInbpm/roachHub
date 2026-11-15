"""全局热键（Windows `RegisterHotKey`）。

**为什么必须自己做**

Qt 没有全局热键 API。`QShortcut` / `QAction.setShortcut` 只在**本应用拥有焦点**时
才收得到键 —— 而桌面宠物的核心场景恰恰是"用户正在用别的软件，想顺手把控制台叫出来"
（微信的 Ctrl+Alt+W 就是这种语义）。所以只能走 Win32 的 `RegisterHotKey`。

**两个容易踩的点**

1. `RegisterHotKey(None, ...)` 是把热键注册到**当前线程**，`WM_HOTKEY` 投递到线程
   消息队列，Qt 会以 `windows_dispatcher_MSG` 交给 native event filter —— 只判断
   `windows_generic_MSG` 会永远收不到。这里两种都收。
2. 热键是**系统独占**的。被别的程序占了（或本进程重复注册同一个键）时，
   `RegisterHotKey` 返回 0，`GetLastError()` 是 1409（ERROR_HOTKEY_ALREADY_REGISTERED）。
   必须把失败暴露给用户，否则用户会以为"我设了但没反应"。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractNativeEventFilter

logger = logging.getLogger("roachHub.hotkeys")

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# 修饰键的规范顺序（显示用；也保证 "Alt+Ctrl+W" 与 "Ctrl+Alt+W" 是同一条）
_MOD_ORDER: List[Tuple[int, str]] = [
    (MOD_CONTROL, "Ctrl"),
    (MOD_ALT, "Alt"),
    (MOD_SHIFT, "Shift"),
    (MOD_WIN, "Win"),
]

_MOD_ALIASES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "ctl": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
    "cmd": MOD_WIN,
}

# 非字母数字键 → 虚拟键码
_VK_NAMES: Dict[str, int] = {
    "space": 0x20, "空格": 0x20,
    "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B,
    "enter": 0x0D, "return": 0x0D,
    "backspace": 0x08,
    "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "`": 0xC0, "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}

_VK_TO_NAME = {v: k.upper() if len(k) == 1 else k.title() for k, v in _VK_NAMES.items()}
# 同一个 VK 有多个别名时保留最常用的显示名
for _vk, _label in {
    0x20: "Space", 0x1B: "Esc", 0x0D: "Enter", 0x2E: "Delete", 0x2D: "Insert",
    0x21: "PageUp", 0x22: "PageDown", 0x26: "Up", 0x28: "Down",
    0x25: "Left", 0x27: "Right", 0x09: "Tab", 0x08: "Backspace",
    0x24: "Home", 0x23: "End",
}.items():
    _VK_TO_NAME[_vk] = _label


def parse_hotkey(spec: str) -> Optional[Tuple[int, int]]:
    """把 "Ctrl+Alt+W" 解析成 (modifiers, vk)。非法或缺少修饰键时返回 None。

    要求**至少一个修饰键**：裸字母（比如只写 "W"）注册成全局热键会把整个系统里
    每一次按下 W 都吃掉，这是不可接受的。
    """
    if not spec or not isinstance(spec, str):
        return None
    parts = [p.strip() for p in spec.replace("＋", "+").split("+") if p.strip()]
    if not parts:
        return None

    mods = 0
    key: Optional[str] = None
    for part in parts:
        low = part.lower()
        if low in _MOD_ALIASES:
            mods |= _MOD_ALIASES[low]
        elif key is None:
            key = part
        else:
            return None  # 两个主键，非法

    if key is None or mods == 0:
        return None

    low = key.lower()
    if low in _VK_NAMES:
        vk = _VK_NAMES[low]
    elif len(key) == 1 and key.isalnum() and key.isascii():
        vk = ord(key.upper())
    elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        vk = 0x70 + int(low[1:]) - 1
    else:
        return None
    return mods, vk


def format_hotkey(mods: int, vk: int) -> str:
    """(modifiers, vk) → "Ctrl+Alt+W"（规范顺序，用于回显/比较）。"""
    names = [name for bit, name in _MOD_ORDER if mods & bit]
    if 0x41 <= vk <= 0x5A:            # A-Z
        names.append(chr(vk))
    elif 0x30 <= vk <= 0x39:          # 0-9
        names.append(chr(vk))
    elif 0x70 <= vk <= 0x87:          # F1-F24
        names.append(f"F{vk - 0x70 + 1}")
    else:
        names.append(_VK_TO_NAME.get(vk, f"VK{vk}"))
    return "+".join(names)


def normalize_hotkey(spec: str) -> Optional[str]:
    """规范化写法；非法返回 None。空格表示"未设置/禁用"。"""
    if not spec or not spec.strip():
        return ""
    parsed = parse_hotkey(spec)
    if parsed is None:
        return None
    return format_hotkey(*parsed)


def supports_global_hotkeys() -> bool:
    return sys.platform.startswith("win")


class GlobalHotkeyManager(QAbstractNativeEventFilter):
    """把配置里的热键串注册成系统级热键，并分发给回调。

    用法::

        mgr = GlobalHotkeyManager()
        mgr.install()                       # 装到 QApplication 上
        mgr.bind("console", "Ctrl+Alt+W", toggle_console)
    """

    def __init__(self) -> None:
        super().__init__()
        self._installed = False
        self._next_id = 0xA100          # 避开系统 / 其他库常用的低位 id
        self._by_id: Dict[int, str] = {}          # hotkey id -> 逻辑名
        self._callbacks: Dict[str, Callable[[], None]] = {}
        # 逻辑名 -> (规范串, 注册用 id, 是否注册成功)
        self._bindings: Dict[str, Tuple[str, int, bool]] = {}
        self.failures: Dict[str, str] = {}        # 逻辑名 -> 失败原因
        self.triggered: List[str] = []            # 诊断/测试用：触发顺序

    # ---------------------------------------------------------------- 生命周期

    def install(self) -> bool:
        from PySide6.QtWidgets import QApplication

        if self._installed or not supports_global_hotkeys():
            return self._installed
        app = QApplication.instance()
        if app is None:
            return False
        app.installNativeEventFilter(self)
        self._installed = True
        return True

    def uninstall(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.clear()
        if not self._installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self)
        self._installed = False

    # ---------------------------------------------------------------- 注册

    def bind(self, name: str, spec: str, callback: Callable[[], None]) -> bool:
        """注册（或重注册）一个热键。空串 = 仅解绑。返回是否注册成功。"""
        self.unbind(name)
        self._callbacks[name] = callback

        norm = normalize_hotkey(spec)
        if norm == "":
            logger.info("热键 %s 未设置，跳过", name)
            return False
        if norm is None:
            self.failures[name] = f"无法识别的热键写法：{spec!r}"
            logger.warning("热键 %s 写法非法：%r", name, spec)
            return False

        parsed = parse_hotkey(norm)
        if parsed is None:
            self.failures[name] = f"无法识别的热键写法：{spec!r}"
            return False
        mods, vk = parsed

        if not supports_global_hotkeys():
            # 非 Windows 平台不注册，但保留绑定信息，便于上层照常显示文案
            self._bindings[name] = (norm, -1, False)
            self.failures[name] = "当前平台不支持全局热键"
            return False

        hid = self._next_id
        self._next_id += 1
        user32 = ctypes.windll.user32
        ok = bool(user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk))
        if not ok:
            err = ctypes.get_last_error() or ctypes.GetLastError()
            reason = (
                "已被其他程序占用" if err == ERROR_HOTKEY_ALREADY_REGISTERED
                else f"注册失败（错误码 {err}）"
            )
            self.failures[name] = f"{norm} {reason}"
            self._bindings[name] = (norm, -1, False)
            logger.warning("全局热键 %s (%s) 注册失败：%s", name, norm, reason)
            return False

        self._bindings[name] = (norm, hid, True)
        self._by_id[hid] = name
        self.failures.pop(name, None)
        logger.info("全局热键已注册：%s → %s", norm, name)
        return True

    def unbind(self, name: str) -> None:
        entry = self._bindings.pop(name, None)
        if entry is None:
            return
        _, hid, ok = entry
        if ok and hid >= 0 and supports_global_hotkeys():
            try:
                ctypes.windll.user32.UnregisterHotKey(None, hid)
            except Exception:  # pragma: no cover - 退出路径
                pass
            self._by_id.pop(hid, None)

    def clear(self) -> None:
        for name in list(self._bindings):
            self.unbind(name)
        self.failures.clear()

    # ---------------------------------------------------------------- 查询

    def is_registered(self, name: str) -> bool:
        entry = self._bindings.get(name)
        return bool(entry and entry[2])

    def spec_of(self, name: str) -> str:
        entry = self._bindings.get(name)
        return entry[0] if entry else ""

    # ---------------------------------------------------------------- 消息

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt 命名)
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message != WM_HOTKEY:
            return False, 0
        name = self._by_id.get(int(msg.wParam))
        if name is None:
            return False, 0
        self.triggered.append(name)
        cb = self._callbacks.get(name)
        if cb is not None:
            try:
                cb()
            except Exception:
                logger.exception("热键回调异常：%s", name)
        # 吃掉这条消息，避免系统提示音
        return True, 0
