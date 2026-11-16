"""繁衍速率的量化探针：用真实的 ReproductionSystem / LifecycleSystem / NeedsSystem
跑一遍，测出「孵化 → 成虫 → 产卵鞘 → 再孵化」的实际节奏与种群曲线。

    QT_QPA_PLATFORM=offscreen python tools/repro_probe.py --food natural --seconds 600

三条曲线分别回答三件事：
- unlimited：能量管够时的生物学上限速率（只看冷却与发育时间）；
- natural  ：按当前配置的食物投放（12s 一块、最多 8 块）时的真实速率；
- none     ：不给食物，看它怎么塌。
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DT = 1.0 / 60.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--food", choices=["unlimited", "natural", "none"], default="natural")
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--initial", type=int, default=2)
    ap.add_argument("--sample", type=float, default=30.0)
    args = ap.parse_args()

    random.seed(args.seed)

    from PySide6.QtWidgets import QApplication

    qapp = QApplication(sys.argv[:1])
    from src.app import RoachApp
    from src.entities.base_entity import LifeStage
    from src.entities.cockroach import Cockroach
    from src.entities.food import Food
    from src.entities.ootheca import Ootheca

    ch = RoachApp(qapp)
    ch.timer.stop()
    Cockroach.quality = 0

    cfg = ch.config
    cap = int(cfg.get("simulation.max_roaches", 15))
    food_interval = float(cfg.get("simulation.food_spawn_interval", 12.0))
    max_food = int(cfg.get("simulation.max_food_count", 8))
    nymph_min = int(cfg.get("lifecycle.nymphs_per_ootheca_min", 1))
    nymph_max = int(cfg.get("lifecycle.nymphs_per_ootheca_max", 3))
    egg_t = float(cfg.get("lifecycle.egg_incubation_time", 15.0))
    instar_t = float(cfg.get("lifecycle.instar_duration", 18.0))
    lifespan = float(cfg.get("lifecycle.adult_lifespan", 300.0))
    cooldown = float(cfg.get("lifecycle.reproduce_cooldown", 28.0))
    min_e = float(cfg.get("lifecycle.reproduce_min_energy", 55.0))
    cost_e = float(cfg.get("lifecycle.reproduce_cost_energy", 25.0))
    mode = str(cfg.get("lifecycle.reproduce_mode", "auto"))
    ooth_mode = str(cfg.get("lifecycle.ootheca_mode", "carried"))

    print(f"\n== 配置 == 模式={mode} 卵鞘={ooth_mode} 冷却={cooldown}s 门槛={min_e} 消耗={cost_e}")
    print(f"           孵化={egg_t}s 每龄={instar_t}s 若虫/鞘={nymph_min}~{nymph_max} "
          f"成虫寿命={lifespan}s 上限={cap} 食物={food_interval}s/块 最多{max_food}块")
    print(f"== 场景 == {args.food}  时长={args.seconds}s  初始={args.initial} 只成虫\n")

    ents: list = []
    for i in range(args.initial):
        r = ch.spawn_cockroach(stage=LifeStage.ADULT, gender="female" if i else "male")
        r.reproduce_timer = 0.0 if args.food != "none" else 0.0
        r.energy = 100.0
        ents.append(r)

    head = (f"{'t':>6} {'总数':>5} {'成虫':>5} {'若虫':>5} {'卵鞘':>5} "
            f"{'待受精':>6} {'食物':>5}  {'均能量':>6}")
    print(head)
    print("-" * len(head))

    t = 0.0
    next_food = 0.0
    next_sample = 0.0
    events: list[tuple[float, str]] = []
    first_birth = None
    first_adult_from_egg = None
    prev_alive = len(ents)
    birthed = 0
    hatched = 0

    while t < args.seconds:
        # --- 食物 -----------------------------------------------------------------
        if args.food == "natural":
            if t >= next_food:
                next_food += food_interval
                if sum(1 for e in ents if isinstance(e, Food) and e.is_alive) < max_food:
                    f = Food(x=random.uniform(100, 1000), y=random.uniform(100, 600),
                             nutrition=40.0)
                    ents.append(f)
        foods = [e for e in ents if isinstance(e, Food) and e.is_alive]
        roaches = [e for e in ents if isinstance(e, Cockroach) and e.is_alive]

        # --- 行为 → 运动 → 实体推进（与 app 主循环同序，求偶靠这两个系统落地）--------
        ch.behavior_system.update(ents, DT, cursor_pos=None)
        ch.movement_system.update(ents, DT)
        for e in list(ents):
            e.update(DT)

        if args.food == "unlimited":
            for r in roaches:
                r.energy = 100.0
        else:
            ch.needs_system.update(ents, DT)

        # --- 繁殖 → 生命周期 → 清尸 --------------------------------------------------
        new_o = ch.reproduction_system.update(ents, DT, cap)
        if new_o:
            birthed += len(new_o)
            if first_birth is None:
                first_birth = t
                events.append((t, f"首枚卵鞘（{len(new_o)}）"))
            ents.extend(new_o)

        new_n = ch.lifecycle_system.update(ents, DT, cfg.get("creatures.cockroach", {}), cap)
        if new_n:
            fresh = [e for e in new_n if isinstance(e, Cockroach)]
            hatched += len(fresh)
            if fresh and first_adult_from_egg is None:
                pass
            ents.extend(new_n)

        ents = ch.ecology_system.update(ents, None)

        for r in roaches:
            if r.is_alive and r.stage == LifeStage.ADULT and first_adult_from_egg is None and hatched:
                first_adult_from_egg = t

        t += DT
        if t >= next_sample:
            next_sample += args.sample
            rs = [e for e in ents if isinstance(e, Cockroach) and e.is_alive]
            ad = [e for e in rs if e.stage == LifeStage.ADULT]
            ny = [e for e in rs if e.stage == LifeStage.NYMPH]
            eg = [e for e in ents if isinstance(e, Ootheca) and e.is_alive]
            fo = [e for e in ents if isinstance(e, Food) and e.is_alive]
            avg_e = sum(r.energy for r in rs) / max(1, len(rs))
            unm = sum(1 for r in ad if r.gender == "female" and r.broods_left <= 0)
            print(f"{t:6.0f} {len(rs):5d} {len(ad):5d} {len(ny):5d} {len(eg):5d} "
                  f"{unm:6d} {len(fo):5d}  {avg_e:6.1f}")

    # --- 统计 -------------------------------------------------------------------
    rs = [e for e in ents if isinstance(e, Cockroach) and e.is_alive]
    ad = [r for r in rs if r.stage == LifeStage.ADULT]
    print("\n== 结论 ==")
    print(f"最终存活 {len(rs)}（成虫 {len(ad)} / 若虫 {len(rs) - len(ad)}）")
    print(f"累计产卵鞘 {birthed} 枚，累计孵化 {hatched} 只")
    if first_birth is not None:
        print(f"首枚卵鞘出现在 t={first_birth:.1f}s")
    if args.seconds > 0:
        print(f"平均出生速率 {birthed / args.seconds:.3f} 鞘/秒 = {birthed / args.seconds * 60:.1f} 鞘/分钟")


if __name__ == "__main__":
    main()
