"""把单只蟑螂的 render() 拆开计时：影子 / 六足 / 躯干 / 触须各占多少。

    QT_QPA_PLATFORM=offscreen python tools/render_breakdown.py --count 200
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
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--frames", type=int, default=30)
    args = ap.parse_args()

    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from src.entities.base_entity import LifeStage
    from src.entities.cockroach import PALETTE_REALISTIC, Cockroach

    random.seed(3)
    roaches = [Cockroach(random.uniform(40, 1800), random.uniform(40, 1000),
                         stage=LifeStage.ADULT, gender="female", config={})
               for _ in range(args.count)]
    for r in roaches:
        r.scale = random.uniform(0.62, 1.0)
        r.render_style = "realistic"

    img = QImage(1920, 1080, QImage.Format.Format_ARGB32_Premultiplied)
    acc = {"shadow": 0.0, "legs": 0.0, "body": 0.0, "antenna": 0.0, "total": 0.0}

    for q in (2, 1, 0):
        Cockroach.quality = q
        for k in acc:
            acc[k] = 0.0
        for _ in range(args.frames):
            img.fill(0)
            p = QPainter(img)
            t0 = time.perf_counter()
            for r in roaches:
                r.update(1 / 30.0)
                p.save()
                p.translate(r.pos.x, r.pos.y)
                p.rotate(90.0)
                p.scale(r.scale, r.scale)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                pt = dict.fromkeys(("shadow", "legs", "body", "antenna"), 0.0)
                s = time.perf_counter()
                r._render_contact_shadow(p)
                pt["shadow"] = time.perf_counter() - s
                s = time.perf_counter()
                pal = dict(PALETTE_REALISTIC)
                pal["tone"] = r.individual_tone
                r.leg_rig.draw(p, pal, simple=(q == 0), detail=1 if q < 2 else 2)
                pt["legs"] = time.perf_counter() - s
                s = time.perf_counter()
                r._render_realistic_body(p)
                pt["body"] = time.perf_counter() - s
                s = time.perf_counter()
                r.antenna_rig.draw(p, pal["antenna"], simple=(q == 0), detail=1 if q < 2 else 2)
                pt["antenna"] = time.perf_counter() - s
                p.restore()
                for k, v in pt.items():
                    acc[k] += v
            p.end()
            acc["total"] += time.perf_counter() - t0

        f = args.frames * args.count
        print(f"\n== LOD{q} · {args.count} 只 · {args.frames} 帧 ==")
        tot = acc["total"] / args.frames * 1000
        print(f"  合计 {tot:7.2f} ms/帧  ({1000 / tot:.1f} FPS)")
        for k in ("shadow", "legs", "body", "antenna"):
            ms = acc[k] / f * 1000
            print(f"  {k:8s} {ms:7.3f} ms/只 = {ms * args.count:6.2f} ms/帧 "
                  f"{'#' * int(ms * args.count / max(tot, 0.01) * 40)}")


if __name__ == "__main__":
    main()
