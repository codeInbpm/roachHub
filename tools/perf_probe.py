"""帧预算拆解：定位"多少只开始卡"到底卡在哪一段。

    QT_QPA_PLATFORM=offscreen python tools/perf_probe.py
    QT_QPA_PLATFORM=offscreen python tools/perf_probe.py --counts 50 100 200
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--counts", type=int, nargs="+", default=[50, 100, 150, 200])
    ap.add_argument("--frames", type=int, default=40)
    args = ap.parse_args()

    from PySide6.QtGui import QImage, QPainter, QRegion
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    from src.app import RoachApp
    from src.entities.cockroach import Cockroach
    from src.entities.base_entity import LifeStage

    for n in args.counts:
        random.seed(7)
        ch = RoachApp(app)
        ch.entities = []
        w, h = ch.overlay.width() or 1920, ch.overlay.height() or 1080
        for i in range(n):
            r = Cockroach(
                random.uniform(40, w - 40), random.uniform(40, h - 40),
                stage=LifeStage.ADULT, gender="female", config={},
            )
            r.scale = random.uniform(0.62, 1.0)
            r.render_style = "realistic"
            # 挂上与真实运行时一致的回调，否则足迹/音效的开销会被漏测
            r.on_footstep = ch._on_footstep
            r.on_smear = ch._on_smear
            r.on_sound = ch._on_sound
            ch.entities.append(r)

        Cockroach.quality = 2 if n <= 55 else (1 if n <= 110 else 0)
        ents = ch.entities
        overlay = ch.overlay

        # 预热一帧，让精灵/贴图缓存就绪
        ch.overlay.set_entities(ents)

        t_beh = t_mov = t_upd = t_needs = t_trail = t_mask = t_paint = 0.0
        canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)

        for _ in range(args.frames):
            dt = 1 / 30.0

            t0 = time.perf_counter()
            ch.behavior_system.update(ents, dt, cursor_pos=ch._cursor_pos)
            t1 = time.perf_counter()
            ch.movement_system.update(ents, dt)
            t2 = time.perf_counter()
            for e in ents:
                if e.is_alive:
                    e.update(dt)
            t3 = time.perf_counter()
            ch.needs_system.update(ents, dt)
            t4 = time.perf_counter()
            overlay.tick_trails(dt)
            t5 = time.perf_counter()

            # 命中测试：模拟鼠标移动时 WM_NCHITTEST 的代价（每帧问两次随机点）
            ch.overlay.hit_test_at(w * 0.5, h * 0.5)
            ch.overlay.hit_test_at(w * 0.2, h * 0.7)
            t6 = time.perf_counter()

            canvas.fill(0)
            p = QPainter(canvas)
            overlay.trails.render(p)
            for e in ents:
                if e.is_alive and e.__class__.__name__ == "Cockroach":
                    e.render(p)
            p.end()
            t7 = time.perf_counter()

            t_beh += t1 - t0
            t_mov += t2 - t1
            t_upd += t3 - t2
            t_needs += t4 - t3
            t_trail += t5 - t4
            t_mask += t6 - t5
            t_paint += t7 - t6

        f = args.frames
        total = (t_beh + t_mov + t_upd + t_needs + t_trail + t_mask + t_paint) / f * 1000
        print(f"\n== {n} 只  LOD{Cockroach.quality}  共 {total:.1f} ms/帧  ({1000 / total:.1f} FPS) ==")
        for name, v in (
            ("behavior", t_beh), ("movement", t_mov), ("entity.update", t_upd),
            ("needs", t_needs), ("trails.tick", t_trail), ("click mask", t_mask),
            ("render", t_paint),
        ):
            ms = v / f * 1000
            bar = "#" * int(ms / max(total, 0.01) * 40)
            print(f"  {name:14s} {ms:7.2f} ms  {ms / max(total, 0.01) * 100:5.1f}%  {bar}")

        # 遮罩尺寸也报一下：QRegion 的复杂度会直接决定 SetWindowRgn 的代价
        print(f"  同一批实体: 足迹 {overlay.trails.count} 笔，最坏覆盖率 = 100%（已不再用遮罩）")
        ch.quit()
        app.processEvents()


if __name__ == "__main__":
    main()
