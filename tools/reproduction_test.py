"""繁衍（性别 / 求偶 / 受精 / 产卵）的脚本化验收。

    QT_QPA_PLATFORM=offscreen python tools/reproduction_test.py

覆盖 docs/reproduction.md 里写下的每条硬规则，重点是三个以前会静默烂掉的地方：
- 交尾被打断后雌虫卡在 MATING（永久定妆照）；伴侣被拍死也一样；
- 行为层/运动层每帧把交配状态改回 WALK，有性模式因此几乎产不出卵鞘；
- 写死的 26px 对接距离 < 70px 体长，两只虫是叠在一起而不是尾部相接。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.entities.base_entity import EntityState, LifeStage      # noqa: E402
from src.entities.cockroach import Cockroach                      # noqa: E402
from src.entities.ootheca import Ootheca                          # noqa: E402
from src.systems.behavior import BehaviorSystem                    # noqa: E402
from src.systems.movement import MovementSystem                    # noqa: E402
from src.systems.reproduction import ReproductionSystem            # noqa: E402
from src.utils.math_utils import Vector2D                          # noqa: E402

DT = 1.0 / 60.0
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


BASE_CFG = dict(
    reproduce_mode="auto",
    female_ratio=0.5,
    mating_search_radius=180.0,
    reproduce_min_energy=55.0,
    reproduce_cost_energy=25.0,
    reproduce_cooldown=14.0,
    allow_reproduce_near_cap=True,
    ootheca_mode="dropped",
    egg_incubation_time=15.0,
    fertilized_broods=2,
    auto_mating_time=1.5,
    mating_duration=2.2,
)


def make_roach(x=500.0, y=500.0, gender="female", stage=LifeStage.ADULT,
               energy=100.0) -> Cockroach:
    r = Cockroach(x=x, y=y, stage=stage, gender=gender,
                  instar=4 if stage == LifeStage.ADULT else 1,
                  max_instars=4, config={})
    r.energy = energy
    r.reproduce_timer = 0.0
    r.body_len = 70.0          # 固定体长，让对接距离可预期
    return r


class World:
    """behavior → movement → entity.update → reproduction，和 app 主循环同序。"""

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = dict(BASE_CFG)
        if cfg:
            self.cfg.update(cfg)
        self.repro = ReproductionSystem(self.cfg)
        self.behavior = BehaviorSystem({})
        self.movement = MovementSystem(1920, 1080, 45.0, "rebound", 0.0)
        self.entities: list = []
        self.oothecas: list[Ootheca] = []

    def add(self, roach: Cockroach) -> Cockroach:
        self.entities.append(roach)
        return roach

    def step(self, seconds: float, cursor: Vector2D | None = None) -> None:
        for _ in range(max(1, int(round(seconds / DT)))):
            self.behavior.update(self.entities, DT, cursor_pos=cursor)
            self.movement.update(self.entities, DT)
            for e in list(self.entities):
                e.update(DT)
            for o in self.repro.update(self.entities, DT, 9999):
                self.oothecas.append(o)
                self.entities.append(o)


def test_virgin_female_cannot_breed() -> None:
    print("\n[1] 处女雌虫：没有雄虫就不该有卵鞘")
    w = World()
    f = w.add(make_roach(500, 500, "female"))
    w.step(60.0)
    check("60s 内 0 枚卵鞘", len(w.oothecas) == 0, f"{len(w.oothecas)} 枚")
    check("受精额度仍为 0", f.broods_left == 0, f"broods_left={f.broods_left}")
    check("没有卡在 MATING", f.state != EntityState.MATING, f.state.name)


def test_male_seeks_and_fertilizes() -> None:
    print("\n[2] 雄虫循气味找过来 → 自动模式受精")
    w = World()
    f = w.add(make_roach(500, 500, "female"))
    m = w.add(make_roach(900, 500, "male"))
    w.step(0.5)
    check("雄虫获得求偶目标（信息素远程吸引）", m.mate_seek is not None,
          f"mate_seek={'有' if m.mate_seek else '无'}")
    w.step(20.0)
    check("受精并开始产卵（额度 2 = 已产枚数 + 剩余额度）",
          f.broods_left == 2 - len(w.oothecas), f"剩余 {f.broods_left} / 已产 {len(w.oothecas)}")
    check("雌虫已离开交配状态", f.state != EntityState.MATING, f.state.name)
    check("交配消耗了能量", f.energy < 100.0, f"energy={f.energy:.1f}")


def test_brood_quota() -> None:
    print("\n[3] 受精额度：一次交配连产 2 枚，然后必须重新交配")
    w = World()
    f = w.add(make_roach(500, 500, "female"))
    m = w.add(make_roach(560, 500, "male"))
    w.step(10.0)
    n1 = len(w.oothecas)
    check("首枚卵鞘已产出", n1 >= 1, f"{n1} 枚")
    w.step(20.0)
    n2 = len(w.oothecas)
    check("额度用尽后停在 2 枚（雄虫仍在，额度会续）", n2 >= 2, f"{n2} 枚")

    # 把雄虫拿走：额度归零后必须停产
    w.entities = [e for e in w.entities if e is not m]
    for _ in range(int(0.1 / DT)):
        w.step(DT)
    f.broods_left = 0
    f.reproduce_timer = 0.0
    before = len(w.oothecas)
    w.step(40.0)
    check("额度为 0 且无雄虫 → 40s 不再产卵", len(w.oothecas) == before,
          f"{len(w.oothecas) - before} 枚")


def test_contact_distance() -> None:
    print("\n[4] 对接距离按体长算，不再是写死的 26px")
    auto = ReproductionSystem(dict(BASE_CFG, reproduce_mode="auto"))
    sex = ReproductionSystem(dict(BASE_CFG, reproduce_mode="sexual"))
    a, b = make_roach(), make_roach()
    d_auto = auto._contact_distance(a, b)
    d_sex = sex._contact_distance(a, b)
    check("auto 距离 > 半体长（不重叠）", d_auto > 70.0, f"{d_auto:.1f}px")
    check("sexual 距离 ≈ 尾部贴合（0.92 倍）", 60.0 < d_sex < 70.0, f"{d_sex:.1f}px")
    check("两者都远大于旧的 26px", min(d_auto, d_sex) > 50.0, f"{min(d_auto, d_sex):.1f}px")


def test_interrupt_does_not_deadlock() -> None:
    print("\n[5] 交尾被打断 / 伴侣死亡：不能卡在 MATING")
    w = World()
    f = w.add(make_roach(500, 500, "female"))
    m = w.add(make_roach(560, 500, "male"))
    w.repro._begin_mating(f, m)
    check("已进入交尾", f.state == EntityState.MATING and m.state == EntityState.MATING)
    m.trigger_freeze_and_panic(Vector2D(0, 0), duration=1.0)
    w.step(0.3)
    check("受惊打断后不再处于交尾状态（交还行为层）",
          f.state != EntityState.MATING, f.state.name)
    check("雌虫 partner 已清空", f.mating_partner_id is None)

    w2 = World()
    f2 = w2.add(make_roach(500, 500, "female"))
    m2 = w2.add(make_roach(560, 500, "male"))
    w2.repro._begin_mating(f2, m2)
    m2.die("test")
    w2.step(0.2)
    check("伴侣死亡后不卡 MATING", f2.state != EntityState.MATING, f2.state.name)


def test_behavior_keeps_mating() -> None:
    print("\n[6] 行为层 / 运动层不得拆散正在交配的一对")
    w = World()
    f = w.add(make_roach(500, 500, "female"))
    m = w.add(make_roach(560, 500, "male"))
    w.repro._begin_mating(f, m)
    progress0 = f.mating_progress
    w.step(1.0)
    check("1s 后仍在交配（有性模式能走完进度）", f.state == EntityState.MATING, f.state.name)
    check("进度在推进", f.mating_progress > progress0 + 0.5, f"{f.mating_progress:.2f}s")
    check("交配中不动（target_speed = 0）", f.target_speed == 0.0, f"{f.target_speed}")

    w2 = World()
    f2 = w2.add(make_roach(50, 500, "female"))   # 贴边：趋边性会把它拽走
    m2 = w2.add(make_roach(110, 500, "male"))
    w2.repro._begin_mating(f2, m2)
    x0 = f2.pos.x
    w2.step(1.0)
    check("贴边交配不会被趋边性推走", abs(f2.pos.x - x0) < 1.5, f"Δx={f2.pos.x - x0:.2f}px")


def test_sexual_survives_cursor() -> None:
    print("\n[7] 有性模式：光标在中距离（120px）不该打断交尾")
    w = World(dict(reproduce_mode="sexual"))
    f = w.add(make_roach(500, 500, "female"))
    m = w.add(make_roach(555, 500, "male"))
    w.repro._begin_mating(f, m)          # 先让它俩对上，再让光标凑过来
    # 光标放在雌虫另一侧：距雌虫 100px（r_mid 内、r_panic 外），离雄虫更远，
    # 避免雄虫被"贴脸"惊吓 —— 那是设计上允许的打断，不属于本条要验的行为
    cursor = Vector2D(400, 500)
    w.step(4.0, cursor=cursor)
    check("光标 100px 处交尾仍能完成受精", f.broods_left > 0, f"broods_left={f.broods_left}")


def test_male_only_population() -> None:
    print("\n[8] 全雄种群：不该凭空产卵")
    w = World()
    for i in range(5):
        w.add(make_roach(300 + i * 90, 400, "male"))
    w.step(60.0)
    check("60s 内 0 枚卵鞘", len(w.oothecas) == 0, f"{len(w.oothecas)} 枚")


def test_female_ratio_matters() -> None:
    print("\n[9] female_ratio 在 auto 模式下真实生效")
    w = World()
    for i in range(4):
        w.add(make_roach(300 + i * 150, 400, "female"))
    w.step(30.0)
    check("有雌无雄 → 0 枚（证明 auto 不再绕过性别）", len(w.oothecas) == 0,
          f"{len(w.oothecas)} 枚")


def test_app_integration() -> None:
    print("\n[10] 应用层：配置键、投放个体、控制台统计")
    from src.config import Config

    cfg = Config()
    check("默认配置含 fertilized_broods", cfg.get("lifecycle.fertilized_broods", None) == 2)
    check("默认配置含 auto_mating_time", cfg.get("lifecycle.auto_mating_time", None) == 1.5)
    check("默认配置含 mating_duration", cfg.get("lifecycle.mating_duration", None) == 2.2)

    qapp = QApplication.instance() or QApplication(sys.argv)
    from src.app import RoachApp

    app = RoachApp(qapp)
    app.timer.stop()
    female = app.spawn_cockroach(stage=LifeStage.ADULT, gender="female", announce=False)
    nymph = app.spawn_cockroach(stage=LifeStage.NYMPH, gender="female", announce=False)
    check("投放的雌性成虫已受精 1 次", female.broods_left == 1, f"{female.broods_left}")
    check("若虫是处女（长大后要先交配）", nymph.broods_left == 0, f"{nymph.broods_left}")
    stats = app.console_stats()
    check("控制台统计含 unmated 字段", "unmated" in stats, str(stats.get("unmated")))


def test_mating_motion() -> None:
    print("\n[11] 交配动作：律动包络 / 抽离 / 心形粒子 / 绘制")
    import math

    from src.animation import mating as M

    # 1. 相位：起点为 0、单调递增、越到后面涨得越快（频率爬升）
    p0 = M.phase_at(0.0, 2.2)
    p1 = M.phase_at(1.0, 2.2)
    early = M.phase_at(0.20, 2.2) - M.phase_at(0.10, 2.2)
    late = M.phase_at(0.90, 2.2) - M.phase_at(0.80, 2.2)
    check("相位从 0 起、随进度单调增长", p0 == 0.0 and p1 > 0.0, f"{p0:.2f} → {p1:.1f}rad")
    check("频率越到后面越快（末端急促）", late > early * 1.5,
          f"前段 {early:.2f} < 后段 {late:.2f} rad/10%")
    mono = all(M.phase_at((i + 1) / 50.0, 2.2) > M.phase_at(i / 50.0, 2.2) for i in range(50))
    check("50 段采样严格单调（不会跳拍）", mono)

    # 2. 幅度包络：两头软、中段满
    check("包络两端为 0（对接/抽离都是软的）",
          M.envelope(0.0) == 0.0 and M.envelope(1.0) == 0.0)
    check("包络中段为 1", M.envelope(0.5) == 1.0)

    # 3. 挺动 / 挤压 / 抽离
    male = make_roach(gender="male")
    rig = male._mating_rig
    peak_push = 0.0
    for i in range(120):
        # 只测"律动段"（0~85%）：再往后是抽离位移，那是另一回事
        rig.update(DT, i / 119.0 * 0.85, 2.2, "male", junction=(0.0, 0.0))
        peak_push = max(peak_push, abs(rig.push))
    check("挺动幅度在 1~6px 之间（体长 6% 以内，不会像弹簧）",
          1.0 <= peak_push <= 6.0, f"峰值 {peak_push:.2f}px")

    rig.update(DT, 1.0, 2.2, "male", junction=(0.0, 0.0))
    check("结束时向后抽离（两只分开，不是原地定住）",
          rig.push <= -M.DETACH_PX * 0.9, f"{rig.push:.2f}px")

    rig.update(DT, 0.5, 2.2, "female", junction=(0.0, 0.0))
    check("雌性跟随幅度小于雄性（被顶得挪一点，不是同步平移）",
          0.0 < abs(rig.push) <= M.PUSH_PX * M.FEMALE_FOLLOW + 0.01,
          f"{rig.push:.2f}px")

    # 4. 心形粒子：有上限、只雄虫生、离场清空
    male2 = make_roach(gender="male")
    male2.start_mating("x", duration=2.2)
    female2 = make_roach(x=560.0, gender="female")
    female2.start_mating("y", duration=2.2)
    peak_hearts = 0
    for _ in range(int(2.4 / DT)):
        male2.update(DT)
        female2.update(DT)
        peak_hearts = max(peak_hearts, len(male2._mating_rig.hearts))
    check("心形粒子有上限（不会每秒几十个）",
          1 <= peak_hearts <= M.HEART_MAX, f"峰值 {peak_hearts} 颗")
    check("雌虫不生成心形（两头各画一份会叠成双倍亮度）",
          len(female2._mating_rig.hearts) == 0)

    male2.finish_mating()
    male2.update(DT)
    check("退出交配后粒子清空", len(male2._mating_rig.hearts) == 0)
    check("退出交配后姿态回正（push=0, squeeze=1）",
          abs(male2._mating_rig.push) < 1e-6 and abs(male2._mating_rig.squeeze - 1.0) < 1e-6)

    # 5. 绘制：真的画出东西，且两帧不同（不是静态图）
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    def frame(u: float) -> QImage:
        img = QImage(320, 320, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f, m = make_roach(x=160.0, y=130.0, gender="female"), make_roach(x=160.0, y=200.0, gender="male")
        f.start_mating(m.id, duration=2.2)
        m.start_mating(f.id, duration=2.2)
        for r in (f, m):
            r.mating_progress = u * 2.2
            r._mating_rig.update(0.016, u, 2.2, r.gender,
                                 junction=(r.pos.x, r.pos.y + (32 if r is f else -32)))
            r.render(p)
        for r in (f, m):
            r.render_mating_hearts(p)
        p.end()
        return img

    a, b = frame(0.35), frame(0.72)
    ink = sum(1 for y in range(0, 320, 4) for x in range(0, 320, 4)
              if QColor(a.pixel(x, y)).alpha() > 0)
    diff = sum(1 for y in range(0, 320, 4) for x in range(0, 320, 4)
               if a.pixel(x, y) != b.pixel(x, y))
    check("交配帧确实画出了非透明像素", ink > 400, f"{ink} 个采样点")
    check("不同进度画出的两帧不同（有动作，不是静帧）", diff > 60, f"{diff} 个采样点有差异")

    # 6. 开销：特效开关对比
    import time

    f, m = make_roach(x=160.0, y=130.0, gender="female"), make_roach(x=160.0, y=200.0, gender="male")
    f.start_mating(m.id, duration=2.2)
    m.start_mating(f.id, duration=2.2)
    img = QImage(400, 400, QImage.Format.Format_ARGB32)
    for r in (f, m):
        for _ in range(40):
            r.update(DT)

    def cost(quality: int) -> float:
        old = Cockroach.quality
        Cockroach.quality = quality
        p = QPainter(img)
        t = time.perf_counter()
        for _ in range(60):
            for r in (f, m):
                r.render(p)
            f.render_mating_hearts(p)
            m.render_mating_hearts(p)
        ms = (time.perf_counter() - t) / 60 * 1000
        p.end()
        Cockroach.quality = old
        return ms

    full = cost(2)
    bare = cost(0)
    check("一对交配的绘制开销 < 1.2ms/帧", full < 1.2, f"{full:.2f}ms（极简档 {bare:.2f}ms）")


def main() -> None:
    # Cockroach 构造时要取 QPixmap（躯干贴图），没有 QApplication 会直接 abort
    global _QAPP
    _QAPP = QApplication.instance() or QApplication(sys.argv[:1])

    print("=" * 62)
    print("繁衍系统验收（性别 / 求偶 / 受精 / 产卵）")
    print("=" * 62)
    test_virgin_female_cannot_breed()
    test_male_seeks_and_fertilizes()
    test_brood_quota()
    test_contact_distance()
    test_interrupt_does_not_deadlock()
    test_behavior_keeps_mating()
    test_sexual_survives_cursor()
    test_male_only_population()
    test_female_ratio_matters()
    test_app_integration()
    test_mating_motion()

    ok = sum(1 for _, passed, _ in results if passed)
    total = len(results)
    print("\n" + "=" * 62)
    print(f"结果：{ok}/{total} 通过")
    if ok != total:
        for name, passed, detail in results:
            if not passed:
                print(f"  ✗ {name}  {detail}")
    print("=" * 62)
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
