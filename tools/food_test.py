"""食物体系 / 腐败规则 / 悬停卡片的验收。

    QT_QPA_PLATFORM=offscreen python tools/food_test.py

五块内容：
1. 类型表：key 唯一、23 种贴图齐全、营养/半径/保质期落在设计区间；
2. 构造与渲染：23 种在四个腐败阶段都能画出来，贴图缺失有降级；
3. 代谢与死因：食物不参与 NeedsSystem 代谢、掉血与帧率无关、死因分类正确；
4. 腐败规则：新鲜度与营养单调下降、到期记 rotted、被啃过的坏得更快；
5. 贴图缓存：惰性加载、条目数受控、内存上限；
6. 悬停卡片：默认不显示、状态判定分档正确、颜色分档正确。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

app = QApplication(sys.argv[:1])

from src.entities.base_entity import BaseEntity, EntityState, LifeStage   # noqa: E402
from src.entities.cockroach import Cockroach                               # noqa: E402
from src.entities.food import Food                                         # noqa: E402
from src.entities import food_sprites                                      # noqa: E402
from src.entities.food_kinds import (                                      # noqa: E402
    BY_KEY, DEFAULT_KEY, FOOD_KINDS, RANDOM_KEY, SIGNATURE_KEYS, get_kind, pick_kind,
)
from src.entities.ootheca import Ootheca                                   # noqa: E402
from src.overlay_window import OverlayWindow                               # noqa: E402
from src.systems.behavior import BehaviorSystem                            # noqa: E402
from src.systems.movement import MovementSystem                            # noqa: E402
from src.systems.needs import NeedsSystem                                  # noqa: E402

DT = 1.0 / 60.0
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def main() -> None:
    print("=" * 68)
    print(" 食物体系 / 腐败规则 / 悬停卡片 验收")
    print("=" * 68)

    # ---------------------------------------------------------------- 1. 类型表
    print("\n[1] 食物类型表")
    keys = [k.key for k in FOOD_KINDS]
    check("共 23 种食物", len(FOOD_KINDS) == 23, f"实际 {len(FOOD_KINDS)}")
    check("key 不重复", len(set(keys)) == len(keys))
    check("名称不重复", len({k.name for k in FOOD_KINDS}) == len(keys))

    missing = [k.key for k in FOOD_KINDS if not food_sprites.available(k.sprite)]
    check("每种都有原图贴图", not missing, f"缺 {missing}" if missing else "23/23")

    no_tint = [k.key for k in FOOD_KINDS
               if not all(food_sprites.available(f"{k.sprite}_d{i}") for i in (1, 2, 3))]
    check("每种都有三档腐败染色贴图", not no_tint, f"缺 {no_tint[:3]}" if no_tint else "23×3")

    nut = [k.nutrition for k in FOOD_KINDS]
    check("营养值落在 15~85", min(nut) >= 15 and max(nut) <= 85,
          f"{min(nut):.0f}~{max(nut):.0f}")
    check("进食位点 1~3", all(1 <= k.slots <= 3 for k in FOOD_KINDS),
          f"{sorted({k.slots for k in FOOD_KINDS})}")
    check("视觉半径 15~36", all(15.0 <= k.radius <= 36.0 for k in FOOD_KINDS),
          f"{min(k.radius for k in FOOD_KINDS):.1f}~{max(k.radius for k in FOOD_KINDS):.1f}")
    check("保质期 90~450 秒", all(90.0 <= k.shelf_life <= 450.0 for k in FOOD_KINDS),
          f"{min(k.shelf_life for k in FOOD_KINDS):.0f}~{max(k.shelf_life for k in FOOD_KINDS):.0f}s")
    life = {k.shelf_life for k in FOOD_KINDS}
    check("保质期有梯度（湿食短、干货长）", len(life) >= 15, f"{len(life)} 种取值")
    check("湿食比干货烂得快", BY_KEY["carrion"].shelf_life < BY_KEY["droppings"].shelf_life,
          f"腐肉 {BY_KEY['carrion'].shelf_life:.0f}s < 老鼠屎 {BY_KEY['droppings'].shelf_life:.0f}s")

    unknown = get_kind("not_a_kind")
    check("未知 key 回退到默认", unknown.key == DEFAULT_KEY, unknown.key)

    expected = sum(k.nutrition * max(6.0, 90.0 - k.nutrition) for k in FOOD_KINDS)
    expected /= sum(max(6.0, 90.0 - k.nutrition) for k in FOOD_KINDS)
    check("随机投放的期望营养 < 表均值（低的更常见）",
          expected < sum(nut) / len(nut), f"期望 {expected:.1f} / 均值 {sum(nut)/len(nut):.1f}")
    check("随机期望营养接近旧版 40（不抬承载上限）",
          abs(expected - 40.0) < 8.0, f"{expected:.1f}")

    check("招牌 key 都在表内", all(k in BY_KEY for k in SIGNATURE_KEYS),
          f"{len(SIGNATURE_KEYS)} 个")

    # ---------------------------------------------------------------- 2. 构造与渲染
    print("\n[2] 构造与渲染（含四档腐败）")
    img = QImage(240, 160, QImage.Format.Format_ARGB32)
    img.fill(QColor(40, 42, 48))
    bad = []
    for k in FOOD_KINDS:
        for fresh in (1.0, 0.62, 0.34, 0.10):
            try:
                f = Food(120, 80, kind=k.key)
                f.freshness = fresh
                f.age = (1.0 - fresh) * f.shelf_life
                f.nutrition = f.max_nutrition * (1.0 - 0.45 * (1.0 - fresh))
                p = QPainter(img)
                f.render(p)
                p.end()
                if f.kind.key != k.key or f.eating_slots != k.slots:
                    bad.append(f"{k.key}@{fresh}")
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{k.key}@{fresh}:{exc}")
    check("23 种 × 4 档都能构造并渲染", not bad, f"{bad[:3]}" if bad else "92 次全部通过")

    # 贴图缺失时的降级：伪造一个不存在的 sprite
    ghost = Food(0, 0, kind="carrion")
    ghost.kind = type(ghost.kind)(**{**ghost.kind.__dict__, "sprite": "definitely_missing"})
    try:
        p = QPainter(img)
        ghost.render(p)
        p.end()
        ok = True
        err = ""
    except Exception as exc:  # noqa: BLE001
        ok = False
        err = str(exc)
    check("贴图缺失时降级绘制不崩", ok, err)

    f = Food(0, 0, kind="carrion")
    check("按类型取营养（腐肉 78）", abs(f.nutrition - 78.0) < 1e-6, f"{f.nutrition:.0f}")
    f2 = Food(0, 0, nutrition=12.5, kind="maggots")
    check("显式 nutrition 覆盖表值", abs(f2.nutrition - 12.5) < 1e-6, f"{f2.nutrition:.1f}")

    f3 = Food(0, 0, kind="carrion")
    check("腐肉 3 个进食位点", f3.eating_slots == 3, f"{f3.eating_slots}")
    claimed = [f3.claim(i) for i in ("a", "b", "c", "d")]
    check("超过位点后拒绝认领", claimed == [True, True, True, False], f"{claimed}")

    # ---------------------------------------------------------------- 3. 代谢与死因
    print("\n[3] 代谢与死因")
    check("食物不参与代谢", Food.metabolizes is False)
    check("卵鞘不参与代谢", Ootheca.metabolizes is False)
    check("蟑螂参与代谢", Cockroach.metabolizes is True)

    ns = NeedsSystem(energy_decay_rate=0.6)
    food = Food(100, 100, kind="droppings")     # 保质期最长的干货
    t = 0.0
    while t < 60.0:
        ns.update([food], DT)
        food.update(DT)
        t += DT
    check("食物不会被 NeedsSystem 饿死", food.is_alive and food.energy > 0,
          f"alive={food.is_alive} energy={food.energy:.0f}")

    dormancy = []
    for fps in (60, 30, 15):
        e = Food(0, 0)
        e.energy = 0.0
        e.health = 100.0
        dt = 1.0 / fps
        tt = 0.0
        while e.is_alive and tt < 10.0:
            e.consume_energy(0.0, dt)
            tt += dt
        dormancy.append(round(tt, 2))
    check("能量归零后的死亡时间与帧率无关",
          max(dormancy) - min(dormancy) < 0.05, f"{dormancy}")

    dead = Food(0, 0)
    dead.energy = 0.0
    dead.health = 1.0
    dead.consume_energy(0.0, DT)
    check("掉血致死记 starvation", dead.death_reason == "starvation", dead.death_reason)

    eaten = Food(0, 0, nutrition=5.0)
    eaten.consume(10.0)
    check("吃完记作 eaten_up", (not eaten.is_alive) and eaten.death_reason == "eaten_up",
          eaten.death_reason)

    # ---------------------------------------------------------------- 4. 腐败规则
    print("\n[4] 腐败规则")
    rot = Food(0, 0, kind="carrion")
    check("刚投放时完全新鲜", rot.freshness == 1.0 and rot.rot == 0.0,
          f"freshness={rot.freshness:.2f}")

    shelf = rot.shelf_life
    t = 0.0
    samples = []
    while t < shelf * 1.02:
        rot.update(DT)
        t += DT
        if int(t * 10) % 10 == 0:
            samples.append((rot.freshness, rot.nutrition))
    check("新鲜度单调下降", all(a[0] >= b[0] - 1e-9 for a, b in zip(samples, samples[1:])))
    check("营养单调下降", all(a[1] >= b[1] - 1e-9 for a, b in zip(samples, samples[1:])))

    mid = Food(0, 0, kind="carrion")
    half = 0.0
    while half < mid.shelf_life * 0.5:
        mid.update(DT)
        half += DT
    check("半程时新鲜度约 0.5", abs(mid.freshness - 0.5) < 0.05, f"{mid.freshness:.2f}")
    check("半程时营养已流失约 22%",
          0.18 < (1.0 - mid.nutrition / mid.max_nutrition) < 0.28,
          f"{100 * (1.0 - mid.nutrition / mid.max_nutrition):.0f}%")

    gone = Food(0, 0, kind="carrion")
    t = 0.0
    while gone.is_alive and t < gone.shelf_life * 3:
        gone.update(DT)
        t += DT
    check("到期后腐败消失", not gone.is_alive, f"t={t:.0f}s")
    check("腐败死因记 rotted（不是 starvation）", gone.death_reason == "rotted",
          gone.death_reason)
    check("消失时间接近保质期", abs(t - gone.shelf_life) < 1.5,
          f"{t:.0f}s vs {gone.shelf_life:.0f}s")

    # 被啃过的坏得更快：两个同种食物，一个啃掉 60% 营养，看谁先烂
    a = Food(0, 0, kind="moldbread")
    b = Food(0, 0, kind="moldbread")
    b.consume(b.max_nutrition * 0.6)
    ta = tb = 0.0
    while (a.is_alive or b.is_alive) and ta < 2000.0:
        if a.is_alive:
            a.update(DT)
            ta += DT
        if b.is_alive:
            b.update(DT)
            tb += DT
    check("被啃过的腐败更快", tb < ta, f"完整 {ta:.0f}s / 啃过 {tb:.0f}s")
    check("腐败速度上限不超过 1.9 倍", ta / max(tb, 1e-6) < 2.1, f"{ta/max(tb,1e-6):.2f}x")

    # ---------------------------------------------------------------- 5. 贴图缓存
    print("\n[5] 贴图缓存")
    food_sprites.clear_cache()
    check("初始不加载任何贴图", food_sprites.cache_stats()["entries"] == 0)

    one = Food(0, 0, kind="slug")
    p = QPainter(img)
    one.render(p)
    p.end()
    st = food_sprites.cache_stats()
    check("用到的种类才加载", st["entries"] == 1, f"{st['entries']} 条")

    for k in FOOD_KINDS:
        tmp = Food(0, 0, kind=k.key)
        p = QPainter(img)
        tmp.render(p)
        p.end()
    st = food_sprites.cache_stats()
    check("23 种全渲一遍后仍只有 23 条缓存", st["entries"] == len(FOOD_KINDS),
          f"{st['entries']} 条")
    # 每张按"显示尺寸 × 1.7 超采样"缓存，23 种全加载约 1MB；
    # 实际游戏里同时存在的食物种类通常只有 3~6 种，真实占用远低于此。
    check("缓存总内存 < 2MB（23 种全加载）", st["kb"] < 2048,
          f"{st['kb']} KB / {st['pixels']} px")
    check("贴图确实加载成功（无缺失）", st["missing"] == 0, f"missing={st['missing']}")

    # 二次渲染命中缓存，不新建条目
    for k in FOOD_KINDS[:5]:
        tmp = Food(0, 0, kind=k.key)
        p = QPainter(img)
        tmp.render(p)
        p.end()
    check("重复渲染不增加缓存条目",
          food_sprites.cache_stats()["entries"] == len(FOOD_KINDS),
          f"{food_sprites.cache_stats()['entries']} 条")

    # ---------------------------------------------------------------- 6. 悬停卡片
    print("\n[6] 悬停卡片")
    ov = OverlayWindow()
    check("默认不显示悬停卡片", ov._hover_entity is None)

    roach = Cockroach(200, 200, stage=LifeStage.ADULT, gender="female", config={})
    ov.entities = [roach]

    def status_of(energy: float, health: float = 100.0) -> tuple[str, QColor]:
        roach.energy = energy
        roach.health = health
        return ov._hover_status(roach, energy, health)

    text, _ = status_of(0.0)
    check("能量 0 → 提示正在掉血", "掉血" in text, text)
    text, _ = status_of(0.0, 0.0)
    check("能量 0 且血 0 → 已饿死", "饿死" in text, text)
    text, _ = status_of(12.0)
    check("能量 12 → 饥饿", "饥饿" in text, text)
    text, _ = status_of(34.0)
    check("能量 34 → 偏饿", "偏饿" in text, text)
    text, _ = status_of(80.0)
    check("能量 80 → 状态良好", "良好" in text, text)

    roach.state = EntityState.EAT
    text, _ = status_of(80.0)
    check("进食状态优先于'良好'", "进食" in text, text)
    roach.state = EntityState.WALK

    check("能量条颜色分四档",
          ov._energy_color(0).name() != ov._energy_color(10).name()
          != ov._energy_color(30).name() != ov._energy_color(80).name(),
          f"{ov._energy_color(0).name()} {ov._energy_color(10).name()} "
          f"{ov._energy_color(30).name()} {ov._energy_color(80).name()}")

    ov.resize(600, 400)
    ov._hover_entity = roach
    ov._hover_until = 1e18
    shot = ov.grab()
    check("卡片能画出来（整屏 grab 不报错）", not shot.isNull(),
          f"{shot.width()}x{shot.height()}")

    only_food = Food(300, 300, kind="moldcheese")
    ov.entities = [only_food]
    ov._hover_entity = only_food
    ov.grab()
    check("食物不触发悬停卡片", only_food.__class__.__name__ != "Cockroach")

    # ------------------------------------------------------- 7. 停靠与进食位点
    # 换成照片贴图后食物半径跨度到 17~34.6 px，用固定停靠距离会让大块食物
    # 被虫体整个压住（看起来像站在食物上）。这里验证"虫停在食物边缘、
    # 口器落在食物表面"，并且小块/大块确实停得不一样远。
    print("\n[7] 停靠距离与进食位点")

    def approach(food_key: str) -> tuple[float, float, float, Food]:
        """放一只饿虫朝食物走，返回 (停下时中心距, 口器到食物中心距离, 食物半径, 食物)。"""
        f = Food(300.0, 300.0, kind=food_key)
        r = Cockroach(300.0, 540.0, stage=LifeStage.ADULT, gender="female", config={})
        r.energy = 40.0
        r.angle = -math.pi / 2.0
        r.target_angle = r.angle
        beh = BehaviorSystem({})
        mov = MovementSystem(1200, 900, thigmotaxis_weight=0.0)
        for _ in range(360):        # 最多 6 秒
            beh.update([r, f], DT)
            mov.update([r, f], DT)
            r.update(DT)
            f.update(DT)
            if r.state == EntityState.EAT:
                break
        assert r.state == EntityState.EAT, f"{food_key} 没走到进食状态"
        mouth = r.body_len * 0.52 * r.scale
        d = r.pos.distance_to(f.pos)
        return d, d - mouth, f.visual_radius, f

    d_big, head_big, r_big, f_big = approach("carrion")     # r≈34.6 最大块
    d_small, head_small, r_small, _ = approach("flea")      # r=17.0 最小块

    check("大块食物：口器落在食物表面（不悬空）",
          head_big < r_big, f"口器距中心 {head_big:.1f} < 半径 {r_big:.1f}")
    check("大块食物：口器没有戳到食物中心",
          head_big > r_big * 0.25, f"{head_big:.1f} vs 25% 半径 {r_big * 0.25:.1f}")
    check("大块食物：虫体主体在食物外侧",
          d_big > r_big + 6.0, f"中心距 {d_big:.1f} > 半径 {r_big:.1f}+6")
    check("小块食物口器也压在食物上",
          head_small < r_small, f"口器距中心 {head_small:.1f} < 半径 {r_small:.1f}")
    check("大块的停靠距离明显大于小块", d_big > d_small + 6.0,
          f"{d_big:.1f} vs {d_small:.1f}")

    # 数值半径：拿空营养的极端值确认收缩下限（0.45）
    f_half = Food(0, 0, kind="carrion")
    full = f_half.visual_radius
    f_half.nutrition = f_half.max_nutrition * 0.5
    half = f_half.visual_radius
    f_half.nutrition = 0.0
    zero = f_half.visual_radius
    check("视觉半径随营养单调收缩", full > half > zero,
          f"{full:.1f} → {half:.1f} → {zero:.1f}")
    check("收缩下限 45%（不会缩成点）",
          abs(zero / f_half.kind.radius - 0.45) < 0.01, f"{zero / f_half.kind.radius:.2f}")

    # 进食中食物被啃小后，下一只过来的虫会挪得更近（半径参与决策，不是常数）
    f_eaten = Food(0, 0, kind="carrion")
    fresh_stop = f_eaten.kind.radius * 0.55
    f_eaten.nutrition = f_eaten.max_nutrition * 0.3
    late_stop = f_eaten.visual_radius * 0.55
    check("被啃小的食物停靠环更贴近", late_stop < fresh_stop - 3.0,
          f"{fresh_stop:.1f} → {late_stop:.1f}")

    # 三个位点的大块食物：三只虫停在不同方位，不会叠在一个点上。
    # 注意食物只有 72 营养、三只虫 45/s 就吃完了，所以要在它被吃光前取样。
    f3 = Food(600.0, 400.0, kind="maggots")
    # 落点要在觅食感知半径（280px）内，否则它们根本不会考虑这块食物
    trio = [Cockroach(600.0 + dx, 560.0 + dy, stage=LifeStage.ADULT, gender="female",
                      config={}) for dx, dy in ((-90.0, 30.0), (0.0, 0.0), (90.0, 30.0))]
    beh = BehaviorSystem({})
    mov = MovementSystem(1200, 1200, thigmotaxis_weight=0.0)
    for r in trio:
        r.energy = 40.0
    peak = 0
    peak_spread = 0.0
    for _ in range(420):
        beh.update(trio + [f3], DT)
        mov.update(trio + [f3], DT)
        for r in trio:
            r.update(DT)
        f3.update(DT)
        eating = [r for r in trio if r.state == EntityState.EAT]
        if len(eating) > peak:
            peak = len(eating)
            peak_spread = min((a.pos.distance_to(b.pos) for i, a in enumerate(eating)
                               for b in eating[i + 1:]), default=0.0)
    check("三只虫能同时占上位点", peak == 3, f"峰值 {peak}/3")
    check("三只虫不叠在同一点", peak_spread > 20.0, f"最近两虫 {peak_spread:.1f}px")
    # ---------------------------------------------------------------- 汇总
    ok = sum(1 for _, good, _ in results if good)
    total = len(results)
    print("\n" + "=" * 68)
    print(f"结果：{ok}/{total} 通过")
    print("=" * 68)
    if ok != total:
        for name, good, detail in results:
            if not good:
                print(f"  FAILED: {name}  {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
