"""用 cProfile 找出 200 只时主循环里的热点函数。

    QT_QPA_PLATFORM=offscreen python tools/sim_profile.py --count 200 --ticks 60
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--top", type=int, default=18)
    args = ap.parse_args()

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from src.app import RoachApp
    from src.entities.base_entity import LifeStage
    from src.entities.cockroach import Cockroach

    ch = RoachApp(app)
    ch.timer.stop()
    random.seed(5)
    w, h = ch.overlay.width() or 1920, ch.overlay.height() or 1080
    ents = []
    for _ in range(args.count):
        r = Cockroach(random.uniform(40, w - 40), random.uniform(40, h - 40),
                      stage=LifeStage.ADULT, gender="female", config={})
        r.scale = random.uniform(0.62, 1.0)
        r.on_footstep = ch._on_footstep
        r.on_smear = ch._on_smear
        r.on_sound = ch._on_sound
        ents.append(r)
    ch.entities = ents
    Cockroach.quality = 0

    for _ in range(5):
        ch._on_tick()

    prof = cProfile.Profile()
    prof.enable()
    for _ in range(args.ticks):
        ch._on_tick()
    prof.disable()

    st = pstats.Stats(prof)
    st.sort_stats("tottime")
    print(f"\n== 200 只 × {args.ticks} tick，按 tottime 排序 ==")
    st.print_stats(args.top, r"[\\/]roachHub[\\/]src[\\/]")
    st.sort_stats("cumulative")
    st.print_stats(args.top, r"[\\/]roachHub[\\/]src[\\/]")


if __name__ == "__main__":
    main()
