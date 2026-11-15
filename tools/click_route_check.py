"""点击归属检查：这个屏幕点上的鼠标，到底会交给谁？

    python tools/click_route_check.py

回答的是"为什么右下角点不动"这类问题 —— 用 Win32 `WindowFromPoint`（它内部就走真正的
WM_NCHITTEST），逐点比较"系统的答案"和"我们期望的答案"：

  * 任务栏 / 托盘：必须交给系统（`Shell_TrayWnd` / `TrayNotifyWnd` ...）；
  * 控制台面板：必须交给控制台窗口；
  * 桌面上的蟑螂：才允许归覆盖层。

覆盖层如果在这些地方"认领"了点击，用户看到的就是某个区域整块卡死。
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.app import RoachApp  # noqa: E402

u32 = ctypes.windll.user32
u32.WindowFromPoint.restype = wintypes.HWND
u32.WindowFromPoint.argtypes = [wintypes.POINT]
u32.GetClassNameA.argtypes = [wintypes.HWND, ctypes.c_char_p, ctypes.c_int]
u32.GetWindowTextA.argtypes = [wintypes.HWND, ctypes.c_char_p, ctypes.c_int]


def describe(hwnd: int) -> str:
    if not hwnd:
        return "None"
    cls = ctypes.create_string_buffer(256)
    u32.GetClassNameA(hwnd, cls, 256)
    title = ctypes.create_string_buffer(512)
    u32.GetWindowTextA(hwnd, title, 512)
    name = cls.value.decode("mbcs", "replace")
    text = title.value.decode("mbcs", "replace")
    return f"{name}{('/' + text) if text else ''}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=200, help="场上蟑螂数量")
    args = ap.parse_args()

    qapp = QApplication([])
    app = RoachApp(qapp)
    app.audio.enabled = False
    app.config.set("app.open_console_on_start", False)
    app.start()
    app.spawn_batch(args.count)
    app.summon_console()

    results: list[tuple[str, bool, str]] = []

    def check(label: str, x: int, y: int, expect_overlay: bool) -> None:
        hwnd = int(u32.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0)
        overlay = int(app.overlay.winId())
        ok = (hwnd == overlay) == expect_overlay
        results.append((label, ok, describe(hwnd)))

    def run() -> None:
        screen = qapp.primaryScreen()
        geo, avail = screen.geometry(), screen.availableGeometry()
        panel = app._settings_window.frameGeometry()
        print(f"屏幕 {geo.width()}x{geo.height()} | 工作区 {avail.width()}x{avail.height()}"
              f" | 任务栏 {geo.height() - avail.height()}px | 场上 {app.alive_roach_count()} 只")

        tray = (geo.width() - 60, geo.height() - 24)
        check("托盘（密集虫群下）", *tray, expect_overlay=False)
        check("任务栏中部", geo.width() // 2, geo.height() - 20, expect_overlay=False)
        check("控制台面板正中", panel.center().x(), panel.center().y(), expect_overlay=False)
        check("控制台面板左上", panel.left() + 14, panel.top() + 14, expect_overlay=False)

        width = max(len(r[0]) for r in results) + 2
        print()
        for label, ok, where in results:
            print(f"  {'OK ' if ok else 'BAD'}  {label:<{width}} -> {where}")
        bad = [r[0] for r in results if not r[1]]
        print("\n结论:", "全部符合预期" if not bad else f"以下位置归属错误：{bad}")
        app.hotkeys.uninstall()
        app.quit()

    QTimer.singleShot(2200, run)
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
