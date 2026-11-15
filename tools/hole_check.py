"""诊断「蟑螂跑到某块矩形里就凭空消失」。

覆盖层为了"按钮点得动"，会在控制台面板上挖洞（不画、不接管鼠标）。洞算错的时候，
屏幕上就出现一块**虫子走进去就消失**的区域 —— 而那块矩形往往是"面板以前待过的地方"。

本工具做三件事：
  1. 打印当前洞的位置（面板完整露出时 = 面板矩形）；
  2. 起一个**独立进程**的普通窗口压住面板 → 洞必须被撤销（否则虫子在浏览器前面也会消失）；
  3. 只压住左半边 → 洞必须只剩右半边（被挡住的部分不许挖）。

用法：python tools/hole_check.py
"""

from __future__ import annotations

import subprocess
import sys
import time

sys.path.insert(0, ".")

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from src.app import RoachApp

# 独立进程的"遮挡窗口"：必须另起进程 —— 同进程的窗口会被遮挡检测当成"我们自己的"跳过
_CHILD = (
    "import sys\n"
    "from PySide6.QtWidgets import QApplication, QWidget\n"
    "x, y, w, h = (int(v) for v in sys.argv[1:5])\n"
    "app = QApplication(sys.argv[:1])\n"
    "win = QWidget()\n"
    "win.setStyleSheet('background:#ffffff')\n"
    "win.setGeometry(x, y, w, h)\n"
    "win.show()\n"
    "win.raise_()\n"
    "win.activateWindow()\n"
    "print('READY', flush=True)\n"
    "app.exec()\n"
)

_ok = 0
_bad = 0


def check(name: str, passed: bool, detail: str = "") -> None:
    global _ok, _bad
    if passed:
        _ok += 1
        print(f"  [PASS] {name}" + (f"  — {detail}" if detail else ""))
    else:
        _bad += 1
        print(f"  [FAIL] {name}" + (f"  — {detail}" if detail else ""))


def pump(app: QApplication, seconds: float) -> None:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


def describe(region) -> str:
    if region is None:
        return "None（整屏都归蟑螂）"
    r = region.boundingRect()
    return f"x {r.left()}..{r.right()} y {r.top()}..{r.bottom()}（{len(list(region))} 块）"


def spawn_cover(x: int, y: int, w: int, h: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(x), str(y), str(w), str(h)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    proc.stdout.readline()  # 等 READY
    return proc


def main() -> int:
    app = QApplication([])
    ra = RoachApp(app)
    ra.audio.enabled = False
    ra.start()
    pump(app, 0.4)

    w = ra._settings_window
    fg = w.frameGeometry()
    print(f"控制台 frameGeometry: {fg.x()},{fg.y()} {fg.width()}x{fg.height()}")

    real_hole = ra.overlay.hole_region()
    print(f"[1] 面板在前: 洞 = {describe(real_hole)}")
    check("面板完整露出时，洞 = 面板矩形", real_hole is not None and not real_hole.isEmpty())
    check("覆盖层绘制裁剪已生效", ra.overlay._clip_region is not None, "clip 有值")

    child = spawn_cover(fg.x(), fg.y(), fg.width(), fg.height())
    pump(app, 1.0)
    covered = ra.overlay.hole_region()
    print(f"[2] 面板被压住: 洞 = {describe(covered)}")
    check("面板被完全压住 → 撤销挖洞", covered is None, "虫子不会再凭空消失")
    check("绘制裁剪同步清掉", ra.overlay._clip_region is None)
    check("此时按热键会把面板叫到前台（不是藏起来）", ra.console_in_front() is False)

    child.terminate()
    child.wait(timeout=5)
    ra.summon_console()
    pump(app, 1.0)
    back = ra.overlay.hole_region()
    print(f"[3] 重新召唤: 洞 = {describe(back)}")
    check("面板回到前面 → 洞恢复", back is not None and not back.isEmpty())

    half = spawn_cover(fg.x() - 10, fg.y() - 10, fg.width() // 2, fg.height() + 20)
    pump(app, 1.0)
    reg = ra.overlay.hole_region()
    print(f"[4] 左半边被压住: 洞 = {describe(reg)}")
    if reg is None:
        check("半遮挡 → 只让出露出来的右半边", False, "洞被整块撤销了")
    else:
        r = reg.boundingRect()
        cut = r.left() > fg.x() + fg.width() * 0.25
        check("半遮挡 → 只让出露出来的右半边", cut, f"洞左边界 {r.left()}（面板左边界 {fg.x()}）")
        check("半遮挡：被挡住的部分不再挖（虫子照常画在浏览器上）",
              ra.overlay._clip_region is not None
              and ra.overlay._clip_region.contains(QRect(fg.x() + 5, fg.y() + 5, 1, 1).center()))

    half.terminate()
    half.wait(timeout=5)

    ra.hotkeys.uninstall()
    ra.quit()

    print(f"\n总计 {_ok + _bad} 项，通过 {_ok}，失败 {_bad}")
    return 0 if _bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
