"""饿死诊断探针：用真实主循环跑一把，把"供给—需求—死亡原因"逐项摊开。

回答三个问题：
1. 种群上限到底卡在哪一层（max_roaches / 食物供给 / 自然寿命）；
2. 一只虫从"能量见底"到"断气"要多久；
3. 现在能不能从运行时把"饿死"和"老死"区分出来。

用法：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -u tools/starve_probe.py --seconds 300
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--sample", type=float, default=20.0)
    ap.add_argument("--cap", type=int, default=None, help="覆盖 simulation.max_roaches")
    args = ap.parse_args()

    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from PySide6.QtWidgets import QApplication

    qapp = QApplication(sys.argv[:1])

    import logging

    logging.disable(logging.CRITICAL)

    from src.app import RoachApp
    from src.entities.base_entity import BaseEntity, EntityState, LifeStage
    from src.entities.cockroach import Cockroach
    from src.entities.food import Food

    # --- 记录死亡原因：die() 目前把 reason 丢掉了，这里临时接出来 -------------
    deaths: Counter = Counter()
    _orig_die = BaseEntity.die

    def _die(self, reason: str = "natural") -> None:
        if self.is_alive:
            deaths[reason] += 1
        _orig_die(self, reason)

    BaseEntity.die = _die  # type: ignore[method-assign]

    app = RoachApp(qapp)
    app.timer.stop()
    if args.cap is not None:
        app.config.set("simulation.max_roaches", int(args.cap))
    app.start()

    cfg = app.config
    feed_interval = float(cfg.get("simulation.food_spawn_interval", 12.0))
    decay = float(app.needs_system.energy_decay_rate)
    nutrition = 40.0

    DT = 1.0 / 60.0
    steps = int(args.seconds / DT)
    t = 0.0
    next_sample = args.sample

    # 供给/需求累计
    supply_total = 0.0
    demand_total = 0.0
    foods_seen = set()
    starve_events = 0
    energy_hist: Counter = Counter()

    print(f"食物供给上限: {nutrition:.0f} 能量 / {feed_interval:.0f}s = {nutrition / feed_interval:.2f} 能量/s")
    print(f"单只基础代谢: {decay:.2f} 能量/s  (IDLE ×0.5 / PANIC ×3)")
    print(f"→ 理论可持续成虫数(不恐慌): {nutrition / feed_interval / decay:.1f} 只")
    print("=" * 96)
    print(f"{'t':>5} {'存活':>4} {'成虫':>4} {'若虫':>4} {'食物':>4} {'食物营养':>8} "
          f"{'均能量':>6} {'最低':>5} {'残血':>4} {'饿死':>4} {'老死':>4} {'需求/s':>6}  {'供给/s':>6}")
    print("-" * 96)

    while t < args.seconds:
        app._last_tick_time = time.perf_counter() - DT  # type: ignore[attr-defined]
        app._on_tick()  # type: ignore[attr-defined]
        t += DT

        roaches = [e for e in app.entities
                   if isinstance(e, Cockroach) and e.is_alive]
        foods = [e for e in app.entities if isinstance(e, Food) and e.is_alive]
        for f in foods:
            if f.id not in foods_seen:
                foods_seen.add(f.id)
                supply_total += f.max_nutrition

        # 需求：按当前状态换算的实际燃烧率
        for r in roaches:
            m = 1.0
            if r.state == EntityState.PANIC:
                m = 3.0
            elif r.state == EntityState.IDLE:
                m = 0.5
            demand_total += decay * m * DT

        if t >= next_sample:
            next_sample += args.sample
            adults = [r for r in roaches if r.stage == LifeStage.ADULT]
            nymphs = [r for r in roaches if r.stage == LifeStage.NYMPH]
            energies = [r.energy for r in roaches] or [0.0]
            sick = sum(1 for r in roaches if r.health < 100.0)
            for r in roaches:
                energy_hist[int(r.energy // 10) * 10] += 1
            food_nut = sum(f.nutrition for f in foods)
            window = min(t, args.sample)
            print(f"{t:5.0f} {len(roaches):4d} {len(adults):4d} {len(nymphs):4d} {len(foods):4d} "
                  f"{food_nut:8.1f} {sum(energies) / len(energies):6.1f} {min(energies):5.1f} "
                  f"{sick:4d} {deaths.get('starvation', 0):4d} {deaths.get('natural_lifespan', 0):4d} "
                  f"{demand_total / window:6.2f}  {supply_total / window:6.2f}")

    print("=" * 96)
    roaches = [e for e in app.entities if isinstance(e, Cockroach) and e.is_alive]
    print(f"全程 {t:.0f}s：存活 {len(roaches)}，死亡原因 {dict(deaths)}")
    print(f"累计需求 {demand_total:.0f} 能量，累计供给 {supply_total:.0f} 能量，"
          f"缺口 {demand_total - supply_total:+.0f}")
    tot = sum(energy_hist.values()) or 1
    print("能量分布（采样累计占比）：")
    for lo in range(0, 100, 10):
        n = energy_hist.get(lo, 0)
        bar = "█" * int(n / tot * 60)
        print(f"  {lo:3d}-{lo + 10:3d} {n:6d} {n / tot * 100:5.1f}% {bar}")


if __name__ == "__main__":
    main()
