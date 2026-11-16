"""苍蝇拍 / 爆浆的脚本化验收：不靠肉眼，逐条核对 docs/swatter.md §13 的 17 项。

    QT_QPA_PLATFORM=offscreen python tools/swatter_test.py

跑完会额外输出一张 tools/out/swatter_sheet.png（挥拍 + 爆浆 + 尸体三阶段的拼图），
方便逐帧挑毛病 —— 数字过关不代表好看。
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.app import RoachApp                      # noqa: E402
from src.entities.base_entity import EntityState, LifeStage  # noqa: E402
from src.entities.cockroach import Cockroach      # noqa: E402
from src.entities.food import Food               # noqa: E402
from src.entities.ootheca import Ootheca         # noqa: E402
from src.entities.squashed import SquashedRoach   # noqa: E402
from src.systems.splatter import PARTICLE_CAP, SplatterSystem  # noqa: E402
from src.systems.swatter import Swatter           # noqa: E402
from src.utils.math_utils import Vector2D         # noqa: E402

DT = 1.0 / 60.0
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def make_app(qapp: QApplication) -> RoachApp:
    app = RoachApp(qapp)
    app.behavior_system.circadian_rhythm = False
    app.entities.clear()
    app.audio.enabled = False
    app.swatter.kills = 0
    return app


def add_roach(app: RoachApp, x: float, y: float, **kw) -> Cockroach:
    r = Cockroach(x, y, stage=kw.pop("stage", LifeStage.ADULT), config={}, **kw)
    r.scale = 1.0
    r.state = EntityState.WALK
    app.entities.append(r)
    return r


def swat(app: RoachApp, x: float, y: float, ready: bool = True) -> bool:
    """在 (x, y) 挥一拍，并把 WINDUP 推到结算那一帧。

    :param ready: True 时继续把 RECOVER 与冷却走完，让状态回到 ARMED ——
                  连续用例之间必须这样，否则第二拍会因为"还在收拍"被拒绝。
    """
    app.swatter.arm(Vector2D(x, y))
    # 每个用例都从"举着、可挥"的干净状态开始：否则上一拍的收拍/冷却会吞掉这一拍，
    # 而那种失败看起来像"判定半径错了"，非常误导
    app.swatter.state = "armed"
    app.swatter._cd = 0.0
    ok = app.swatter.swing(Vector2D(x, y))
    for _ in range(6):
        app.swatter.update(DT, Vector2D(x, y))
    if ready:
        for _ in range(60):
            if app.swatter.state == "armed" and app.swatter._cd <= 0.0:
                break
            app.swatter.update(DT, Vector2D(x, y))
    return ok


def corpses(app: RoachApp) -> list[SquashedRoach]:
    return [e for e in app.entities if isinstance(e, SquashedRoach)]


# ---------------------------------------------------------------------- 用例


def test_input_gate(app: RoachApp) -> None:
    print("\n[1-3] 输入闸门：持拍接管整屏，但永远让出面板与任务栏")
    ov = app.overlay
    hole = QRectF(100, 100, 300, 300)
    from PySide6.QtCore import QRect

    ov.set_hole_rect(QRect(100, 100, 300, 300))
    work = ov._work_rect

    app.swatter.disarm()
    blank = (700.0, 500.0)
    check("未持拍时空桌面不接管（默认穿透不被破坏）",
          ov.accepts_mouse_at(*blank) is False)

    app.swatter.arm(Vector2D(*blank))
    check("持拍时空桌面接管鼠标（空拍才有反馈）",
          ov.accepts_mouse_at(*blank) is True, f"hole={hole}")
    check("持拍时面板洞内仍然穿透（按钮点得动）",
          ov.accepts_mouse_at(200.0, 200.0) is False)
    if work is not None:
        outside = (float(work.right() + 3), float(work.bottom() + 3))
        check("持拍时任务栏区域仍然穿透（右下角不能卡死）",
              ov.accepts_mouse_at(*outside) is False, f"工作区右下 {work.bottomRight()}")
    else:  # pragma: no cover
        check("持拍时任务栏区域仍然穿透（右下角不能卡死）", False, "没有工作区矩形")

    ov.set_hole_region(None)
    app.swatter.disarm()


def test_mouse_route(app: RoachApp) -> None:
    """左键真的会被覆盖层截走吗（而不是走抓取），右键真的能放下吗。

    这一段是 docs/swatter.md §8 那个"不报错、但每次挥空都在框选桌面图标"的坑，
    必须用真实的鼠标事件走一遍，不能只测 Swatter 的方法。
    """
    print("\n[3b] 覆盖层鼠标路由：左键挥拍 / 右键放下")
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent

    app.entities.clear()
    app.splatter.clear()
    target = add_roach(app, 700, 400)
    app.swatter.arm(Vector2D(700, 400))
    app.swatter.state = "armed"
    app.swatter._cd = 0.0

    def press(button, x, y):
        ev = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(x, y),
            QPointF(x, y),
            button,
            button,
            Qt.KeyboardModifier.NoModifier,
        )
        app.overlay.mousePressEvent(ev)

    press(Qt.MouseButton.LeftButton, 700, 400)
    check("左键被拍子截走（起手而不是捏起）", app.swatter.state == "windup",
          f"state={app.swatter.state} drag={app.overlay._drag_entity is not None}")
    for _ in range(6):
        app.swatter.update(DT, Vector2D(700, 400))
    check("左键挥拍真的拍死了目标", not target.is_alive)

    press(Qt.MouseButton.RightButton, 700, 400)
    check("右键随时能放下拍子", app.swatter.armed is False)
    check("放下后桌面重新回到穿透", app.overlay.accepts_mouse_at(40.0, 40.0) is False)


def test_kill(app: RoachApp) -> None:
    print("\n[4-6] 命中判定：单杀 / 半径外 1px 存活 / 双杀")
    app.entities.clear()
    r = add_roach(app, 600, 500)
    radius = app.swatter.radius_for(r)
    swat(app, 600, 500)
    got = corpses(app)
    check("半径内命中：当帧死亡 + 同帧出现 1 具尸体",
          (not r.is_alive) and len(got) == 1,
          f"半径 {radius:.1f}px，尸体 {len(got)} 具")
    check("尸体落在被拍死的位置附近（不飞出屏幕）",
          bool(got) and abs(got[0].pos.x - 600) < 1.0 and abs(got[0].pos.y - 500) < 1.0)

    app.entities.clear()
    app.splatter.clear()
    r2 = add_roach(app, 600, 500)
    rad2 = app.swatter.radius_for(r2)
    app.swatter._cd = 0.0
    swat(app, 600 + rad2 + 1.0, 500)
    check("半径外 1px 存活",
          r2.is_alive and not corpses(app), f"半径 {rad2:.1f}px")

    app.entities.clear()
    app.splatter.clear()
    a = add_roach(app, 600, 500)
    b = add_roach(app, 630, 500)
    app.swatter._cd = 0.0
    swat(app, 615, 500)
    check("30px 内两只一起死（一拍多杀）",
          (not a.is_alive) and (not b.is_alive) and len(corpses(app)) == 2)


def test_whiff(app: RoachApp) -> None:
    print("\n[7] 拍空：有反馈、无尸体、不留污渍")
    app.entities.clear()
    app.splatter.clear()
    app.overlay.clear_trails()
    before = app.overlay.trails.total_prints
    spawned = app.splatter.spawned
    app.swatter._cd = 0.0
    swat(app, 900, 700)
    check("拍空不产生尸体", len(corpses(app)) == 0)
    check("拍空产生灰尘环/涟漪", app.splatter.spawned > spawned)
    check("拍空不留污渍（痕迹层笔数不变）",
          app.overlay.trails.total_prints == before,
          f"{before} → {app.overlay.trails.total_prints}")


def test_cooldown(app: RoachApp) -> None:
    print("\n[8] 冷却：280ms 内连点只结算一次")
    app.entities.clear()
    app.splatter.clear()
    app.swatter.kills = 0
    r = add_roach(app, 600, 500)
    app.swatter._cd = 0.0
    swat(app, 600, 500, ready=False)
    # 跳过收拍动画，只考察冷却本身（收拍期间本来也不该接受挥拍）
    app.swatter.state = "armed"
    second = app.swatter.swing(Vector2D(600, 500))
    check("冷却期内的第二次挥拍被拒绝", second is False)
    for _ in range(int(0.30 / DT) + 2):
        app.swatter.update(DT, Vector2D(600, 500))
    third = app.swatter.swing(Vector2D(600, 500))
    check("冷却结束后可以再次挥拍", third is True)
    check("全程只结算了一次", app.swatter.kills == 1 and len(corpses(app)) == 1)


def test_scare(app: RoachApp) -> None:
    print("\n[9] 惊逃：240px 内炸开，远处不动")
    app.entities.clear()
    app.splatter.clear()
    victim = add_roach(app, 800, 450)
    near = add_roach(app, 800, 650)      # 距落点 200px
    far = add_roach(app, 800, 850)       # 距落点 400px
    app.swatter._cd = 0.0
    swat(app, 800, 450)
    check("200px 内的邻居进入僵直/惊逃", near.state in (EntityState.FREEZE, EntityState.PANIC),
          f"{near.state.name}")
    check("400px 外不受影响", far.state == EntityState.WALK, f"{far.state.name}")
    check("落点上的那只确实死了", not victim.is_alive)


def test_corpse_lifecycle(app: RoachApp) -> None:
    print("\n[10-11] 尸体寿命与命中豁免")
    app.entities.clear()
    c = SquashedRoach(400, 400, lifetime=22.0)
    app.entities.append(c)
    check("尸体默认 22 秒", abs(c.lifetime - 22.0) < 1e-6)
    check("尸体不被 hit_test 认领（点它等于点空气）",
          app.overlay.hit_test_at(400.0, 400.0) is None)
    c.age = 21.98
    c.update(0.05)
    check("到期后 is_alive=False", c.is_alive is False)
    survivors = app.ecology_system.update(app.entities, None)
    check("交给 EcologySystem 回收（不新增回收逻辑）",
          all(not isinstance(e, SquashedRoach) for e in survivors))


def test_carried_ootheca(app: RoachApp) -> None:
    print("\n[12] 拖卵母虫：卵鞘同帧消失，不留孤儿引用")
    app.entities.clear()
    mother = add_roach(app, 700, 500, gender="female")
    ooth = Ootheca(mother.pos.x, mother.pos.y, parent=mother, is_carried=True)
    mother.is_carrying_ootheca = True
    mother.carried_ootheca = ooth
    app.entities.append(ooth)
    app.swatter._cd = 0.0
    swat(app, 700, 500)
    check("卵鞘同帧消失", ooth.is_alive is False)
    check("母体不再持有孤儿引用",
          mother.carried_ootheca is None and mother.is_carrying_ootheca is False)


def test_eating_release(app: RoachApp) -> None:
    print("\n[13] 进食中被拍：食物位点立刻释放")
    app.entities.clear()
    food = Food(x=500, y=500, nutrition=40.0)
    app.entities.append(food)
    r = add_roach(app, 500, 500)
    if not food.claim(r.id):
        check("食物占位可用（前置条件）", False, "claim 失败")
        return
    r.eating_food = food
    r.state = EntityState.EAT
    app.swatter._cd = 0.0
    swat(app, 500, 500)
    check("食物位点已释放（不会白占 2.5 秒）", r.id not in run(food), "占位表已清空")


def run(food: Food) -> set:
    for attr in ("occupants", "_occupants", "claimed_by", "_claimed"):
        v = getattr(food, attr, None)
        if isinstance(v, (set, list, dict)):
            return set(v)
    return set()


def test_particle_cap(app: RoachApp) -> None:
    print("\n[14] 颗粒上限 240 且淘汰最老的")
    sp = SplatterSystem(screen_shake=False)
    first_ids = []
    for _ in range(30):
        sp.spawn_impact(500, 500, Vector2D(1, 0), amount=1.0, count=14)
        if not first_ids and sp._drops:
            first_ids.append(id(sp._drops[0]))
    check("颗粒总数不超过硬上限", sp.particle_count() <= PARTICLE_CAP,
          f"{sp.particle_count()} / {PARTICLE_CAP}")
    check("最老的颗粒被淘汰（先进先出）",
          bool(first_ids) and id(sp._drops[0]) != first_ids[0])


def test_shake_only_paint(app: RoachApp) -> None:
    print("\n[15] 震屏只动绘制，绝不动 entity.pos")
    sp = SplatterSystem(screen_shake=True)
    r = add_roach(app, 640, 480)
    pos_before = (r.pos.x, r.pos.y)
    sp.spawn_impact(640, 480, Vector2D(1, 0))
    worst = 0.0
    for _ in range(20):
        dx, dy = sp.shake_offset()
        worst = max(worst, abs(dx), abs(dy))
        sp.update(DT)
    check("位移幅度不超过 3px", worst <= 3.05, f"最大 {worst:.2f}px")
    check("实体坐标完全没变", (r.pos.x, r.pos.y) == pos_before,
          f"{pos_before} → {(r.pos.x, r.pos.y)}")
    app.entities.clear()


def test_audio_budget(app: RoachApp) -> None:
    print("\n[16] 音效预算：连拍 30 次不超过 max_per_second")

    class _FakeEff:
        def setVolume(self, v):  # noqa: N802
            pass

        def play(self):
            pass

    audio = app.audio
    audio._pools = {k: [_FakeEff() for _ in range(3)] for k in ("splat", "whiff")}
    audio._round = {k: 0 for k in ("splat", "whiff")}
    audio.available = True
    audio.enabled = True
    audio.max_per_second = 18
    audio._budget_t0 = time.perf_counter()
    audio._budget_n = 0
    for _ in range(30):
        audio.play("splat", 0.5)
    check("1 秒窗口内的播放预算被守住", audio._budget_n <= audio.max_per_second,
          f"计费 {audio._budget_n} / 上限 {audio.max_per_second}")
    audio.enabled = False


def test_squash_voice(app: RoachApp) -> None:
    print("\n[18] 拍死音效：素材指标 + 调度规则")

    # --- 素材本身（不怕没音频设备，直接读 WAV 头与样本）
    import struct
    import wave

    from src.systems.squash_voice import (
        BUDGET_LINE, BUDGET_LONG, BUDGET_SHORT, LEAD_MS, SOUND_SETS, SquashVoice,
    )

    # 时长区间按素材组分别给：完整一句 2.42s（三个变体 2.42/2.16/1.93），
    # 剪辑版 1.51s（1.51/1.34/1.20），yamero 1.1~1.4s，短素材 0.2~0.5s
    RANGES = {
        "myinstants": (1.90, 2.50),
        "myinstants_short": (1.15, 1.60),
        "yamero": (1.00, 1.50),
        "aiyo": (0.20, 0.50),
    }

    for group, files in SOUND_SETS.items():
        ok_meta = True
        detail = []
        lo, hi = RANGES[group]
        for raw_name in files:
            name = raw_name.rsplit("/", 1)[-1]
            path = ROOT / "assets" / "sounds" / name
            if not path.exists():
                ok_meta = False
                detail.append(f"{name} 缺失")
                continue
            with wave.open(str(path), "rb") as w:
                sr = w.getframerate()
                n = w.getnframes()
                ch = w.getnchannels()
                raw = w.readframes(n)
            samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
            if ch > 1:
                samples = samples[::ch]
            dur = n / sr
            peak = max(abs(v) for v in samples) / 32768.0
            # 爆音只取决于"最后一个样本是否归零"：振膜从非零值瞬间归位才会有咔哒声。
            # 不能看末尾 64 个样本的峰值 —— 淡出本来就是渐降的，那里还有一半幅度。
            tail = abs(samples[-1]) / 32768.0
            head = max(abs(v) for v in samples[: int(0.01 * sr)]) / 32768.0
            detail.append(f"{name[7:]}={dur:.2f}s/pk{peak:.2f}")
            if not (lo <= dur <= hi):
                ok_meta = False
                detail.append(f"{name} 时长 {dur:.2f}s 越界({lo}~{hi})")
            if not (0.60 <= peak <= 0.80):
                ok_meta = False
                detail.append(f"{name} 峰值 {peak:.2f} 越界")
            if tail > 0.002:
                ok_meta = False
                detail.append(f"{name} 末样本 {tail:.4f} 没归零（会爆音）")
            # 起音必须在 10ms 内就出来，否则叠加 25ms 延迟会听成"两声"
            if head < 0.05:
                ok_meta = False
                detail.append(f"{name} 起音太慢 {head:.3f}")
            # 不能有削顶：软削波只在合成音里用，人声素材削平了就是"糊"
            clip = sum(1 for v in samples if abs(v) >= 0.99 * peak * 32768) / max(1, n)
            if clip > 0.002:
                ok_meta = False
                detail.append(f"{name} 削顶 {clip * 100:.1f}%")
        check(f"{group} 组三个变体的时长/峰值/收尾/起音都合规", ok_meta, " ".join(detail))

    # --- 调度规则（用假实例，本机没音频设备也能验）
    class _FakeEff:
        __slots__ = ("played", "playing")

        def __init__(self) -> None:
            self.played = 0
            self.playing = False

        def setVolume(self, v):  # noqa: N802
            pass

        def play(self) -> None:
            self.played += 1
            self.playing = True

        def stop(self) -> None:
            self.playing = False

        def isPlaying(self) -> bool:  # noqa: N802
            return self.playing

    def make(durations=(1.38, 1.23, 1.10)) -> SquashVoice:
        v = SquashVoice.__new__(SquashVoice)
        v.enabled = True
        v.volume = 0.75
        v.available = True
        v.variant = "yamero"
        v._voices = [[_FakeEff(), _FakeEff()] for _ in durations]
        v._durations = list(durations)
        v._hits = []
        v._line_until = 0.0
        v._closed = False
        v.played = 0
        return v

    v = make()
    check("拍空不叫（victims=0 → 无声）", v.plan(0) == [])
    p1, p2, p3 = v.plan(1), v.plan(2), v.plan(3)
    check("单杀 1 声、延迟 25ms", len(p1) == 1 and p1[0][0] == LEAD_MS, str(p1))
    echo = v._echo_ms()
    check(
        "双杀 2 声，错峰按素材时长算（≈1/3 句，长素材不能被压成 80ms）",
        len(p2) == 2 and p2[1][0] - p2[0][0] == echo and p2[0][1] != p2[1][1]
        and 300 <= echo <= 520,
        f"错峰 {echo}ms",
    )
    check(
        "三杀以上 2 声、不同变体、同样错峰（齐叫会叠成一个更响的同一声）",
        len(p3) == 2 and p3[1][0] - p3[0][0] == echo and p3[0][1] != p3[1][1],
        str(p3),
    )

    # 短素材的错峰要跟着缩回去，否则 0.3 秒的声音会被拉开成两声独立的
    vs = make((0.44, 0.34, 0.25))
    short_echo = vs._echo_ms()
    check(
        "换回短素材组后错峰自动缩短（0.34 × 0.44s ≈ 150ms）",
        100 <= short_echo <= 200,
        f"错峰 {short_echo}ms（长素材是 {echo}ms）",
    )
    vs._closed = True

    # 同一变体连发要轮换实例，不能把上一声从中间掐断。
    # 直接调 _fire：play() 的延迟由 QTimer 承担，离屏跑测试时不会推进到触发点。
    v = make()
    v._fire(0, 0.75)
    v._fire(0, 0.75)
    first_voice = v._voices[0]
    check(
        "同变体连发换空闲实例（1.38 秒的一声不会被 stop 掐成结巴）",
        first_voice[0].played == 1 and first_voice[1].played == 1,
        f"实例0 {first_voice[0].played} 次 / 实例1 {first_voice[1].played} 次",
    )
    # 两个都占着时，第三声只能回头打断最早那个
    v._fire(0, 0.75)
    check(
        "两个实例都忙时第三声才打断最早的",
        first_voice[0].played == 2 and first_voice[1].played == 1,
        f"实例0 {first_voice[0].played} 次 / 实例1 {first_voice[1].played} 次",
    )
    v._closed = True

    v = make()
    scheduled = sum(v.play(1) for _ in range(10))
    check(
        f"长素材 1 秒内最多 {BUDGET_LONG} 声（连拍 10 次不会变噪音墙）",
        scheduled == BUDGET_LONG == v.budget,
        f"排入 {scheduled} 声",
    )
    v._closed = True

    # 预算跟着素材长度分档：0.6 秒的短句可以密集连发
    vs = make((0.60, 0.54, 0.48))
    short_scheduled = sum(vs.play(1) for _ in range(10))
    check(
        f"0.75 秒以内的短素材放宽到 {BUDGET_SHORT} 声/秒",
        vs.budget == BUDGET_SHORT and short_scheduled == BUDGET_SHORT,
        f"排入 {short_scheduled} 声",
    )
    vs._closed = True

    # 完整一句（2.42 秒）：不叠、带冷却 —— 否则每句都被顶掉，永远听不到结尾的「打咩打咩」
    vl = make((2.42, 2.16, 1.93))
    first = vl.play(1)
    second = vl.play(1)
    multi = vl.play(3)
    check(
        "完整一句：多杀也只叫一声（不叠成两个人抢话）",
        vl.long_line and vl.budget == BUDGET_LINE and first == 1,
        f"首声 {first} 声 / 预算 {vl.budget}",
    )
    check(
        "完整一句：冷却期内再拍不响（连拍不会把上一句从中间掐断）",
        second == 0 and multi == 0,
        f"第二次 {second} 声 / 多杀 {multi} 声",
    )
    check(
        "完整一句：冷却 = 0.85 × 时长（2.42s → 2.06s，落在打咩之后）",
        1.95 <= vl.line_cooldown <= 2.15,
        f"{vl.line_cooldown:.2f}s",
    )
    vl._line_until = 0.0
    check("完整一句：冷却过后能再喊", vl.play(1) == 1)
    vl._closed = True

    v = make()
    v.enabled = False
    check("关掉开关后 play 返回 0", v.play(3) == 0)
    v._closed = True

    v = make()
    v.volume = 0.0
    check("音量为 0 时 play 返回 0", v.play(1) == 0)
    v._closed = True

    # 真实实例：确认两组素材都能被加载（没有音频设备时 available 会是 False，跳过）
    real = SquashVoice(enabled=True, volume=0.75, variant="yamero")
    if real.available:
        check(
            "真实实例加载了 3 个变体 × 2 个播放实例",
            len(real._voices) == 3 and all(len(vs) == 2 for vs in real._voices),
            f"{len(real._voices)} 变体",
        )
        real.set_variant("aiyo")
        check(
            "运行中切到 aiyo 组，时长缓存跟着换（错峰才不会被算错）",
            real.variant == "aiyo" and max(real._durations) < 0.6,
            f"最长 {max(real._durations):.2f}s",
        )
        real.set_variant("myinstants")
        check(
            "默认组是完整一句：2.42 秒、一声/秒、冷却 2.05s",
            real.variant == "myinstants" and real.long_line
            and real.budget == BUDGET_LINE and 2.0 <= real.line_cooldown <= 2.1,
            f"最长 {max(real._durations):.2f}s 冷却 {real.line_cooldown:.2f}s",
        )
        real.set_variant("myinstants_short")
        check(
            "剪辑版（1.51 秒）也走完整一句模式，不会一秒两声互相咬结尾",
            real.long_line and real.budget == BUDGET_LINE,
            f"最长 {max(real._durations):.2f}s",
        )
        real.set_variant("yamero")
        check(
            "yamero（1.38 秒）仍在上一档：2 声/秒 + 双杀错峰，行为没被动到",
            not real.long_line and real.budget == BUDGET_LONG and real._echo_ms() > 300,
            f"错峰 {real._echo_ms()}ms",
        )
        real.close()
    else:
        print("  [SKIP] 本机没有音频设备，跳过真实实例加载（假实例用例已覆盖调度）")


def test_perf(app: RoachApp) -> None:
    print("\n[17] 性能：颗粒与拍面的绘制成本")
    img = QImage(1600, 900, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    sp = SplatterSystem(screen_shake=False)
    for _ in range(20):
        sp.spawn_impact(800, 450, Vector2D(1, 0), amount=1.0, count=14)
    frames = 60

    t0 = time.perf_counter()
    for _ in range(frames):
        p = QPainter(img)
        sp.render(p)
        p.end()
    drop_ms = (time.perf_counter() - t0) / frames * 1000.0
    check("240 颗颗粒的绘制 < 1.5ms/帧", drop_ms < 1.5,
          f"{drop_ms:.2f}ms（{sp.particle_count()} 颗）")

    sw = Swatter({})
    sw.arm(Vector2D(800, 450))
    sw.detail = 2
    _ = sw.render  # 触发 import 顺序
    t0 = time.perf_counter()
    for _ in range(frames):
        p = QPainter(img)
        sw.render(p, 1600.0, 900.0)
        p.end()
    sw_ms = (time.perf_counter() - t0) / frames * 1000.0
    check("拍子（预渲染贴图 + 手柄）< 0.60ms/帧", sw_ms < 0.60, f"{sw_ms:.2f}ms")

    # 同帧 3 次挥拍（最坏情况）：结算 + 生成颗粒
    app.entities.clear()
    for i in range(30):
        add_roach(app, 300 + i * 30, 300)
    app.swatter._cd = 0.0
    t0 = time.perf_counter()
    app.splatter.clear()
    for x in (400, 460, 520):
        app._resolve_swat(Vector2D(x, 300), Vector2D(1, 0))
    burst_ms = (time.perf_counter() - t0) * 1000.0
    check("同帧 3 次击杀的结算 < 2.0ms", burst_ms < 2.0, f"{burst_ms:.2f}ms（一次性）")


# ---------------------------------------------------------------------- 拼图


def render_sheet(app: RoachApp) -> Path | None:
    """挥拍 4 帧 + 爆浆 4 帧 + 尸体 3 阶段，拼成一张图方便肉眼看。"""
    try:
        out_dir = ROOT / "tools" / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        tile = 320
        cols, rows = 4, 3
        sheet = QImage(tile * cols, tile * rows, QImage.Format.Format_ARGB32)
        sheet.fill(QColor(238, 236, 232))

        p = QPainter(sheet)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 行 1：挥拍姿态（ARMED → WINDUP 33% → 66% → RECOVER）
        sw = Swatter({})
        sw.arm(Vector2D(0, 0))
        poses = [(0.0, "armed"), (0.33, "down 33%"), (0.66, "down 66%"), (1.0, "impact")]
        for i, (frac, label) in enumerate(poses):
            sw.state = "windup"
            sw._t = sw.swing_time * frac
            p.save()
            p.setClipRect(QRectF(tile * i, 0, tile, tile))
            p.translate(tile * i + tile * 0.58, tile * 0.42)
            p.scale(0.62, 0.62)
            sw.pos = Vector2D(0, 0)
            sw.render(p, 0.0, 0.0)
            p.restore()
            p.setPen(QColor(60, 60, 70))
            p.drawText(tile * i + 10, tile - 10, label)

        # 行 2：爆浆时间轴
        sp = SplatterSystem(screen_shake=False)
        sp.spawn_impact(0, 0, Vector2D(1, 0.2), amount=1.2, count=14)
        for i, label in enumerate(["0ms", "60ms", "120ms", "180ms"]):
            p.save()
            p.setClipRect(QRectF(tile * i, tile, tile, tile))
            p.translate(tile * i + tile / 2.0, tile + tile / 2.0)
            sp.render(p)
            p.restore()
            p.setPen(QColor(60, 60, 70))
            p.drawText(tile * i + 10, tile * 2 - 10, label)
            for _ in range(3):
                sp.update(0.02)

        # 行 3：尸体三阶段（落下 20ms / 稳定 2s / 淡出前）
        for i, (age, label) in enumerate([(0.02, "fresh"), (2.0, "settled"), (20.0, "fading")]):
            c = SquashedRoach(0, 0, lifetime=22.0)
            c.age = age
            p.save()
            p.setClipRect(QRectF(tile * i, tile * 2, tile, tile))
            p.translate(tile * i + tile / 2.0, tile * 2 + tile / 2.0)
            p.scale(1.9, 1.9)
            c.render(p)
            p.restore()
            p.setPen(QColor(60, 60, 70))
            p.drawText(tile * i + 10, tile * 3 - 10, label)

        p.end()
        path = out_dir / "swatter_sheet.png"
        sheet.save(str(path))
        return path
    except Exception as exc:  # 拼图只是辅助，失败不该让验收变红
        print(f"  (拼图生成失败：{exc})")
        return None


def main() -> int:
    global qapp
    qapp = QApplication(sys.argv)
    app = make_app(qapp)

    print("== 苍蝇拍 / 爆浆 离屏验收 ==")
    test_input_gate(app)
    test_mouse_route(app)
    test_kill(app)
    test_whiff(app)
    test_cooldown(app)
    test_scare(app)
    test_corpse_lifecycle(app)
    test_carried_ootheca(app)
    test_eating_release(app)
    test_particle_cap(app)
    test_shake_only_paint(app)
    test_audio_budget(app)
    test_squash_voice(app)
    test_perf(app)

    app.swatter.disarm()
    sheet = render_sheet(app)
    if sheet is not None:
        print(f"\n视觉拼图: {sheet}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n== {passed}/{total} 项通过 ==")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
