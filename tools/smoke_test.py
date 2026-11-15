"""无人值守冒烟测试：离屏启动主程序、跑若干帧、并做渲染性能基准。

    QT_QPA_PLATFORM=offscreen python tools/smoke_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.app import RoachApp            # noqa: E402
from src.entities.base_entity import LifeStage  # noqa: E402
from src.entities.cockroach import Cockroach    # noqa: E402


def bench_render(count: int, frames: int = 30) -> float:
    """渲染 N 只蟑螂的每帧耗时（毫秒）。"""
    roaches = [Cockroach(x=40 + (i % 20) * 60, y=40 + (i // 20) * 70) for i in range(count)]
    img = QImage(1600, 900, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    t0 = time.perf_counter()
    for _ in range(frames):
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for r in roaches:
            r.update(1 / 60)
            r.render(p)
        p.end()
    elapsed = time.perf_counter() - t0
    return elapsed / frames * 1000.0


def main() -> int:
    app = QApplication(sys.argv)
    print("== 渲染性能基准 ==")
    for q, tag in ((2, "精细 LOD2"), (1, "标准 LOD1"), (0, "极简 LOD0")):
        Cockroach.quality = q
        for n in (10, 50, 150):
            ms = bench_render(n)
            print(f"  {tag} {n:3d} 只: {ms:6.2f} ms/帧  ({1000.0 / ms:5.1f} FPS 上限)")
    Cockroach.quality = 2

    print("== 主程序冒烟测试 ==")
    controller = RoachApp(app)
    controller.start()
    print(f"  启动完成，实体数: {len(controller.entities)}")
    for _ in range(180):
        controller._on_tick()
        time.sleep(1 / 120)
    roaches = [e for e in controller.entities if isinstance(e, Cockroach)]
    alive = [r for r in roaches if r.is_alive]
    print(f"  运行 180 tick 后: 蟑螂 {len(roaches)} 只（存活 {len(alive)}），"
          f"食物 {len([e for e in controller.entities if type(e).__name__ == 'Food'])}")
    if alive:
        r = alive[0]
        print(f"  样本身: state={r.state.name} stride={r.pose.value('stride'):.2f} "
              f"foot0=({r.leg_rig.legs[0].foot[0]:.1f},{r.leg_rig.legs[0].foot[1]:.1f})")
    controller.quit()
    print("  完成：无异常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
