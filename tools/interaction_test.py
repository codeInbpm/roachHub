"""互动与氛围的脚本化验收：不靠肉眼，逐条核对验收标准。

    QT_QPA_PLATFORM=offscreen python tools/interaction_test.py

覆盖：
  1. 实体 update 是否在主循环里被推进（肢体动画 / 僵直计时器）
  2. 触须跟随光标的方向误差
  3. 极近距离的"先收触须、后僵直"顺序
  4. 捏起、拖拽跟随、松手逃窜
  5. 进食：能量上升、食物体积下降、食物耗尽
  6. 足迹落笔与淡出
  7. 音效预算限流
  8. 抓取音效的裁切与播放策略
  9. 鼠标穿透与帧预算自适应（"多少只都不卡死"的护栏）
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.app import RoachApp  # noqa: E402
from src.entities.base_entity import EntityState, LifeStage  # noqa: E402
from src.entities.cockroach import Cockroach  # noqa: E402
from src.entities.food import Food  # noqa: E402
from src.systems.voice import GrabVoice  # noqa: E402
from src.utils.math_utils import Vector2D  # noqa: E402

DT = 1.0 / 60.0
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def step(app: RoachApp, frames: int, cursor: Vector2D | None = None) -> None:
    """与 app._on_tick 相同的推进顺序（跳过真实光标与 Qt 定时器）。"""
    for _ in range(frames):
        app.behavior_system.update(app.entities, DT, cursor_pos=cursor)
        app.movement_system.update(app.entities, DT)
        for e in app.entities:
            if e.is_alive:
                e.update(DT)
        app.needs_system.update(app.entities, DT)
        app.overlay.tick_trails(DT)


def fresh_app() -> RoachApp:
    app = RoachApp(qapp)
    app.behavior_system.circadian_rhythm = False   # 去掉昼夜因子，保证可复现
    app.entities.clear()
    app.audio.enabled = False                       # 测试期间不出声
    return app


def test_antennae_tracking(app: RoachApp) -> None:
    print("\n[1] 触须跟随光标")
    r = Cockroach(600, 500, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    r.state = EntityState.IDLE
    r.pose.snap_all()
    app.entities.append(r)

    worst = 0.0
    for ang_deg in (0, 90, 180, 270, 45):
        target = Vector2D(r.pos.x + 260 * math.cos(math.radians(ang_deg)),
                          r.pos.y + 260 * math.sin(math.radians(ang_deg)))
        for _ in range(70):
            app.behavior_system.update(app.entities, DT, cursor_pos=target)
            r.state = EntityState.IDLE          # 固定状态，只看触须
            r.target_speed = 0.0
            r.update(DT)
        # 两根触须的平均朝向 vs 期望朝向（体坐标）
        rel = (target - r.pos).angle() - r.angle
        expect = math.atan2(-math.cos(rel), math.sin(rel))
        # 注意：必须用圆周平均。当期望方向恰在 ±π 处时，_aim_l/_aim_r 会分别落在
        # -179° 与 +179°，朴素的 (l+r)/2 平均会得到 0° —— 凭空造出 180° 的假误差。
        aim = math.atan2(math.sin(r._aim_l) + math.sin(r._aim_r),
                         math.cos(r._aim_l) + math.cos(r._aim_r))
        err = abs((aim - expect + math.pi) % math.tau - math.pi)
        worst = max(worst, math.degrees(err))
        app.entities.clear()
        r.pos = Vector2D(600, 500)
        app.entities.append(r)
    check("光标绕行一周，触须平均朝向误差 < 25°", worst < 25.0, f"最大误差 {worst:.1f}°")


def test_prestartle_order(app: RoachApp) -> None:
    print("\n[2] 极近距离：先收触须、后僵直")
    r = Cockroach(600, 500, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    r.angle = -math.pi / 2.0          # 头部朝上，基线触须朝前
    r.target_angle = r.angle
    r.state = EntityState.IDLE
    r.pose.snap_all()
    app.entities.append(r)
    cursor = Vector2D(r.pos.x, r.pos.y - 18.0)   # 光标从正前方逼近
    step(app, 3)

    seq = []
    for _ in range(30):
        app.behavior_system.update(app.entities, DT, cursor_pos=cursor)
        r.update(DT)
        # 触须朝向角：<-1.2 rad 是朝前张开，>0.8 rad 是向身后收伏
        seq.append((round(r._aim_l, 2), r.state.name))

    # 基线约 -1.3 rad（朝前张开），后伏目标 +1.72 rad（指向身后）
    retract_frame = next((i for i, (a, _) in enumerate(seq) if a > 0.8), None)
    freeze_frame = next((i for i, (_, s) in enumerate(seq) if s == "FREEZE"), None)
    ok = retract_frame is not None and freeze_frame is not None and retract_frame < freeze_frame
    check("触须后收先于身体僵直", ok, f"后收第 {retract_frame} 帧 / 僵直第 {freeze_frame} 帧")


def test_drag(app: RoachApp) -> None:
    print("\n[3] 捏起、拖拽、松手")
    r = Cockroach(400, 400, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    app.entities.append(r)
    app.overlay.set_entities(app.entities)
    app.overlay.drag_enabled = True

    def mouse(kind, x, y, buttons):
        btn = Qt.MouseButton.LeftButton
        ev = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), btn,
                         buttons, Qt.KeyboardModifier.NoModifier)
        if kind == QEvent.Type.MouseButtonPress:
            app.overlay.mousePressEvent(ev)
        elif kind == QEvent.Type.MouseMove:
            app.overlay.mouseMoveEvent(ev)
        else:
            app.overlay.mouseReleaseEvent(ev)

    mouse(QEvent.Type.MouseButtonPress, 400, 400, Qt.MouseButton.LeftButton)
    entered = r.state == EntityState.DRAGGED
    for i in range(40):
        mouse(QEvent.Type.MouseMove, 500 + i, 300 + i, Qt.MouseButton.LeftButton)
        step(app, 1)
    follow_err = math.hypot(r.pos.x - 539, r.pos.y - 339)
    followed = follow_err < 24.0
    flail = r.pose.value("flail")
    mouse(QEvent.Type.MouseButtonRelease, 539, 339, Qt.MouseButton.NoButton)
    released = r.state in (EntityState.PANIC, EntityState.FREEZE)
    step(app, 60)
    speed = r.current_speed

    check("按下即进入 DRAGGED", entered)
    check("拖拽时位置跟随光标（误差 < 24px）", followed, f"距离 {follow_err:.1f}px")
    check("拖拽时六足乱蹬（flail > 0.8）", flail > 0.8, f"flail={flail:.2f}")
    check("松手立刻惊慌", released)
    check("松手后 1s 内跑起来（> 200px/s）", speed > 200.0, f"{speed:.0f} px/s")


def test_playdead(app: RoachApp) -> None:
    print("\n[4] 长时间捏住 -> 装死 -> 松手瘫软后逃")
    r = Cockroach(400, 400, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    app.entities.append(r)
    r.begin_drag(Vector2D(400, 400))
    for _ in range(int(6.6 / DT)):
        r.update(DT)
        r.drag_to(Vector2D(400, 400))
    limp = r._playdead and r.pose.value("dead") > 0.4 and r.pose.value("flail") < 0.2
    check("按住 6.6s 后装死（瘫软且停止挣扎）", limp,
          f"dead={r.pose.value('dead'):.2f} flail={r.pose.value('flail'):.2f}")

    r.end_drag()
    limp_after = r.state == EntityState.DRAGGED and r._release_limp > 0
    for _ in range(int(1.3 / DT)):
        r.update(DT)
    check("松手后先瘫软再逃", limp_after and r.state in (EntityState.PANIC, EntityState.FREEZE),
          f"1.3s 后状态 {r.state.name}")


def test_eating(app: RoachApp) -> None:
    print("\n[5] 进食：能量上升 / 食物缩小 / 吃完消失")
    food = Food(600, 400, nutrition=40.0)
    r = Cockroach(600, 520, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    r.energy = 40.0
    app.entities.extend([food, r])

    energies = []
    ratios = []
    for _ in range(int(9.0 / DT)):
        step(app, 1, cursor=Vector2D(1600, 1200))     # 光标扔远，别打断进食
        if r.is_alive:
            energies.append(r.energy)
            ratios.append(food.ratio)

    reached = any(e > 40.5 for e in energies)
    shrank = (max(ratios) - min(ratios)) > 0.3 if ratios else False
    finished = not food.is_alive
    check("蟑螂靠过去开始进食", reached, f"能量 {energies[0]:.0f} -> {energies[-1]:.0f}")
    check("食物体积随啃食缩小", shrank, f"营养比 {ratios[0]:.2f} -> {ratios[-1]:.2f}")
    check("啃完后食物消失", finished)


def test_trails(app: RoachApp) -> None:
    print("\n[6] 足迹落笔与淡出")
    app.overlay.clear_trails()
    r = Cockroach(300, 400, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    r.state = EntityState.WALK
    r.current_speed = 110.0
    r.target_speed = 110.0
    r.angle = 0.0
    r.target_angle = 0.0
    r.on_footstep = app._on_footstep
    r.on_smear = app._on_smear
    app.entities.append(r)
    for _ in range(int(3.0 / DT)):
        r.update(DT)
        r.pos.x += r.current_speed * DT
        app.overlay.tick_trails(DT)
    counted = app.overlay.trails.total_prints
    check("行走 3s 有足印落笔", counted > 40, f"{counted} 笔（存活估值 {app.overlay.trails.count}）")

    for _ in range(int(16.0 / DT)):
        app.overlay.tick_trails(DT)
    check("16s 后痕迹清空（不会永久脏屏）",
          app.overlay.trails.count == 0 and not app.overlay.trails._has_content)


def test_audio_budget(app: RoachApp) -> None:
    print("\n[7] 音效限流")
    app.audio.enabled = True
    app.audio.max_per_second = 10
    played = {"n": 0}
    if app.audio.available:
        for pool in app.audio._pools.values():
            for eff in pool:
                eff.play = lambda *a, **k: played.__setitem__("n", played["n"] + 1)
        app.audio._budget_t0 = time.perf_counter()
        app.audio._budget_n = 0
        for _ in range(500):
            app.audio.play("step", 0.5)
        check("每秒播放次数被预算截断", played["n"] <= 10, f"实际触发 {played['n']} 次 / 上限 10")
    else:
        check("无音频设备时静默降级", True, "本机无音频输出，已降级")


def test_grab_voice(app: RoachApp) -> None:
    print("\n[8] 抓取音效「你干嘛~哎呦」")
    voice = app.grab_voice
    check(
        "抓取音效素材已就绪",
        voice.available,
        f"{voice._path.name} · {voice._path.stat().st_size / 1024:.0f} KB"
        if voice._path.exists()
        else "素材缺失",
    )

    # 素材必须覆盖整句。这里卡的是"哎呦"那一半：尾音在 1.97~3.62s，
    # 早先只裁到 1.96s 就把它整段切掉了，光看"能播"是发现不了的。
    import wave

    with wave.open(str(voice._path), "rb") as w:
        dur = w.getnframes() / w.getframerate()
        rate = w.getframerate()
        tail = w.readframes(w.getnframes())
    import struct as _struct

    samples = _struct.unpack(f"<{len(tail) // 2}h", tail)
    a, b = int(1.90 * rate), int(3.40 * rate)
    rms = math.sqrt(sum(v * v for v in samples[a:b]) / max(1, b - a)) / 32768.0
    tail_db = 20 * math.log10(rms) if rms > 1e-9 else -99.0
    check("素材含后半句「哎呦」（≥3.4s 且尾段有声）", dur >= 3.4 and tail_db > -30.0,
          f"时长 {dur:.2f}s，1.9~3.4s 段 {tail_db:.1f} dBFS")

    # 不依赖音频设备：把 play/stop 换成记录器，只验接线
    calls: list[str] = []
    voice.play = lambda: calls.append("play")
    voice.stop = lambda: calls.append("stop")

    r = Cockroach(500, 420, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    app.entities.append(r)
    app.overlay.set_entities(app.entities)
    app.overlay.drag_enabled = True
    app.audio.enabled = True

    btn = Qt.MouseButton.LeftButton
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(500, 420), QPointF(500, 420),
        btn, btn, Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(500, 420), QPointF(500, 420),
        btn, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )

    voice.stop_on_release = False
    app.overlay.mousePressEvent(press)
    app.overlay.mouseReleaseEvent(release)
    check("默认松手不打断（否则「哎呦」永远听不见）", calls == ["play"], f"调用序列 {calls}")

    calls.clear()
    voice.stop_on_release = True
    app.overlay.mousePressEvent(press)
    app.overlay.mouseReleaseEvent(release)
    check("勾选「松手即停」后松手会掐断", calls == ["play", "stop"], f"调用序列 {calls}")
    voice.stop_on_release = False

    # 托盘总静音必须连它一起压掉，否则"静音了还在叫"
    calls.clear()
    app.audio.enabled = False
    app.overlay.mousePressEvent(press)
    app.overlay.mouseReleaseEvent(release)
    check("总静音时不出声", "play" not in calls, f"调用序列 {calls}")
    app.audio.enabled = True

    # 手抖连点不该把同一句叠三遍。
    # 注意：要打桩的是音频后端（_effect），不能替换 play 本身 —— 那样连防抖逻辑
    # 一起被替换掉，测出来永远是"每次都响"。
    class _StubEffect:
        def __init__(self) -> None:
            self.plays = 0

        def setVolume(self, v: float) -> None:  # noqa: N802
            pass

        def play(self) -> None:
            self.plays += 1

        def stop(self) -> None:
            pass

        def isPlaying(self) -> bool:  # noqa: N802
            return False

    gv = GrabVoice(volume=0.7)
    gv.enabled = True
    gv.available = True
    stub = _StubEffect()
    gv._effect = stub
    for _ in range(5):
        gv.play()
    check("连点 5 次只响 1 次（120ms 防抖）", stub.plays == 1, f"实际 {stub.plays} 次")

    # 过了防抖窗口就该重新响
    gv._last_play -= 1.0
    gv.play()
    check("间隔足够时允许重播", stub.plays == 2, f"实际 {stub.plays} 次")



def test_clickthrough_and_budget(app: RoachApp) -> None:
    print("\n[9] 鼠标穿透与帧预算自适应")
    overlay = app.overlay

    # 1) Windows 下必须走 WM_NCHITTEST，绝不能再每帧 setMask。
    #    每帧 setMask 会让窗口形状 30+ 次/秒重排 —— 那就是用户看到的"屏幕闪烁"。
    if overlay._native_hit_test:
        check("Windows 下启用原生命中测试", overlay._hit_filter is not None, "filter 已安装")
        overlay.set_entities(app.entities)
        check("不再设置窗口遮罩（QRegion）", overlay.mask().isEmpty(), "mask 为空")

        import ctypes
        from ctypes import wintypes

        from src.utils.win_hit_test import _MSG

        f = overlay._hit_filter
        hwnd = f.hwnd()

        def nchittest(x: int, y: int) -> tuple[bool, int]:
            msg = _MSG()
            msg.hWnd = hwnd
            msg.message = 0x0084  # WM_NCHITTEST
            msg.lParam = (y << 16) | (x & 0xFFFF)
            return f.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))

        # 空白处：返回 HTTRANSPARENT(-1)，让鼠标落到桌面
        handled, result = nchittest(5, 5)
        check("空白桌面判定穿透", handled and result == -1, f"返回 ({handled}, {result})")

        # 蟑螂身上：交回 Qt 默认处理
        r = app.entities[0]
        handled, result = nchittest(int(r.pos.x), int(r.pos.y))
        check("蟑螂身上不穿透（可抓取）", not handled, f"返回 ({handled}, {result})")

        # 隐藏时全穿透
        overlay.set_simulation_visible(False)
        handled, result = nchittest(int(r.pos.x), int(r.pos.y))
        check("隐藏后整体穿透", handled and result == -1, f"返回 ({handled}, {result})")
        overlay.set_simulation_visible(True)

    # 2) 命中测试必须便宜：鼠标每移动一次就要跑一遍（200 只同屏）
    from src.entities.cockroach import Cockroach as _Roach

    backup = list(app.entities)
    app.entities = [
        _Roach(100 + (i * 53) % 1800, 100 + (i * 71) % 900, stage=LifeStage.ADULT,
               gender="female", config={})
        for i in range(200)
    ]
    overlay.set_entities(app.entities)
    t0 = time.perf_counter()
    for _ in range(200):
        overlay.hit_test_at(400.0, 300.0)
    per = (time.perf_counter() - t0) / 200 * 1000
    check("200 只时单次命中测试 < 0.5ms", per < 0.5, f"{per:.3f} ms")

    # 3) 帧预算自适应：超预算就降画质，长时间富余再升回去
    app.unlimited_quality = False
    app._quality = 2
    app._frame_ema = 16.0
    app._fps_cap = app.target_fps
    # EMA 平滑得比较重，喂够 40 帧才能把 16ms 的初值推到 40ms 附近
    for _ in range(40):
        app._govern_frame(40.0)
    after_drop = app._quality
    check("帧耗时超标会降画质", after_drop < 2, f"LOD 2 -> LOD{after_drop}")

    for _ in range(600):
        app._govern_frame(12.0)
    check("长期富余会恢复画质", app._quality > after_drop, f"LOD{after_drop} -> LOD{app._quality}")

    # 4) 帧率下限必须被守住（否则会像早期版本那样一路降到 14FPS）
    app._quality = 0
    for _ in range(40):
        app._govern_frame(30.0)
    check("画质见底时仍守住 min_fps", app._fps_cap >= app.min_fps - 1,
          f"fps_cap={app._fps_cap}, min={app.min_fps}")

    # 5) 让位区域：任务栏（工作区之外）与控制台面板都不许接管鼠标。
    #    真机实测：托盘图标上压一只蟑螂时，WindowFromPoint 返回的是覆盖层窗口，
    #    而不是 TrayNotifyWnd —— 用户看到的就是"右下角卡住、什么都点不了"。
    from PySide6.QtCore import QPoint, QRect as _QRect
    from PySide6.QtGui import QRegion

    overlay.set_entities(app.entities)
    sample = next(e for e in app.entities if e.is_alive)
    sample.pos.x, sample.pos.y = 60.0, 60.0
    check("工作区内的蟑螂可以抓", overlay.hit_test_at(60.0, 60.0) is sample, "命中")

    saved_work = overlay._work_rect
    taskbar_y = overlay.height() - 20
    overlay._work_rect = _QRect(0, 0, overlay.width(), max(1, overlay.height() - 48))
    sample.pos.x, sample.pos.y = 60.0, float(taskbar_y)
    check("任务栏上的蟑螂不接管点击（托盘必须点得动）",
          overlay.hit_test_at(60.0, float(taskbar_y)) is None, f"y={taskbar_y}")

    # 控制台面板：绘制要挖空，命中也要让开
    sample.pos.x, sample.pos.y = 60.0, 60.0
    overlay.set_hole_rect(_QRect(10, 10, 200, 150))
    check("面板罩住的蟑螂不接管点击（按钮必须点得动）",
          overlay.hit_test_at(60.0, 60.0) is None, "hole=(10,10,200,150)")
    check("绘制同时挖空（蟑螂不会盖住面板文字）",
          overlay._clip_region is not None
          and not overlay._clip_region.contains(QPoint(60, 60)),
          "clip 区域不含面板")
    overlay.set_hole_rect(None)
    check("面板收起后洞被清掉",
          overlay._clip_region is None and overlay.hit_test_at(60.0, 60.0) is sample,
          "clip=None 且恢复命中")

    # 面板被别的窗口压住一部分时，只有露出来的那半该挖空；
    # 整块被压住时必须完全不挖 —— 否则屏幕上会留一块"虫子走进去就消失"的矩形。
    overlay.set_hole_region(QRegion(_QRect(10, 10, 200, 150)).subtracted(QRegion(_QRect(10, 10, 100, 150))))
    check("半遮挡：只有露出来的右半边挖空",
          overlay.hit_test_at(60.0, 60.0) is sample
          and overlay.hit_test_at(160.0, 60.0) is None,
          "左半可抓 / 右半让给面板")
    check("半遮挡：绘制只裁掉露出来的右半边",
          overlay._clip_region is not None
          and overlay._clip_region.contains(QPoint(60, 60)),
          "左半照常画虫子")
    overlay.set_hole_region(QRegion())
    check("面板被完全压住 = 洞消失（虫子不会再凭空失踪）",
          overlay._clip_region is None and overlay.hit_test_at(60.0, 60.0) is sample,
          "空区域等价于 None")
    overlay.set_hole_rect(None)
    overlay._work_rect = saved_work

    # 6) 托盘单击 = 召唤（不做可见性取反），最小化也要能还原
    app.summon_console()
    w = app._settings_window
    check("召唤后控制台可见", w is not None and w.isVisible(), "visible")
    w.setWindowState(w.windowState() | Qt.WindowState.WindowMinimized)
    app.summon_console()
    check("最小化后再召唤会还原",
          w.isVisible() and not w.isMinimized(), f"minimized={w.isMinimized()}")
    app.toggle_console()

    app.entities = backup
    overlay.set_entities(app.entities)
    app._quality = 2


def test_hotkeys(app: RoachApp) -> None:
    print("\n[10] 全局热键（微信式召唤）")
    import ctypes
    from ctypes import wintypes

    from src.utils.hotkeys import (
        GlobalHotkeyManager,
        format_hotkey,
        normalize_hotkey,
        parse_hotkey,
        supports_global_hotkeys,
    )

    # 1) 写法解析：大小写/顺序无关，但必须带修饰键
    p = parse_hotkey("alt+CTRL+w")
    check("热键写法解析（顺序/大小写无关）", p == (0x0003, ord("W")), f"{p} -> {format_hotkey(*p) if p else None}")
    check("规范化后与配置一致", normalize_hotkey("alt+ctrl+w") == "Ctrl+Alt+W", str(normalize_hotkey("alt+ctrl+w")))
    check("拒绝裸字母（否则会吃掉全系统的 W）", normalize_hotkey("W") is None, "返回 None")
    check("拒绝无法识别的键名", normalize_hotkey("Ctrl+Alt+Nope") is None, "返回 None")
    check("空串表示禁用", normalize_hotkey("") == "", "返回空串")

    # 2) 真正注册 + 消息分发（不依赖抢到某个具体组合）
    mgr = GlobalHotkeyManager()
    ok_install = mgr.install()
    fired: list[str] = []
    bound = mgr.bind("probe", "Ctrl+Alt+F11", lambda: fired.append("probe"))
    if bound:
        check("全局热键注册成功", mgr.is_registered("probe"), mgr.spec_of("probe"))
        hid = next(i for i, n in mgr._by_id.items() if n == "probe")
        msg = wintypes.MSG()
        msg.message = 0x0312  # WM_HOTKEY
        msg.wParam = hid
        handled, _ = mgr.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg))
        check("WM_HOTKEY 能分发到回调", handled and fired == ["probe"], f"triggered={mgr.triggered}")
    else:
        # 机器上这个组合被占了：注册失败路径本身也必须给出可读原因
        check("注册失败时给出可读原因", "Ctrl+Alt+F11" in mgr.failures.get("probe", ""),
              mgr.failures.get("probe", ""))

    mgr.bind("dup", "Ctrl+Alt+F11", lambda: None)
    if bound:
        check("同一组合无法被注册两次", not mgr.is_registered("dup"),
              mgr.failures.get("dup", "没有报错（异常）"))
    mgr.uninstall()
    check("卸载后清空注册", not mgr.is_registered("probe"), "is_registered=False")
    check("平台能力查询与实现一致",
          supports_global_hotkeys() == (sys.platform.startswith("win")
                                        or sys.platform == "darwin"),
          f"{supports_global_hotkeys()}")

    # 3) 三个热键的默认值（只看配置，不在测试里抢真实系统热键）
    check("控制台热键默认 Ctrl+Alt+R",
          app.config.get("app.hotkey_toggle_console") == "Ctrl+Alt+R",
          str(app.config.get("app.hotkey_toggle_console")))
    check("隐藏蟑螂热键默认 Ctrl+Alt+H",
          app.config.get("app.hotkey_toggle_visible") == "Ctrl+Alt+H",
          str(app.config.get("app.hotkey_toggle_visible")))
    check("虫巢爆发热键默认 Ctrl+Alt+B",
          app.config.get("app.hotkey_nest_burst") == "Ctrl+Alt+B",
          str(app.config.get("app.hotkey_nest_burst")))
    check("蟑螂拍热键默认 Ctrl+Alt+S",
          app.config.get("app.hotkey_swatter") == "Ctrl+Alt+S",
          str(app.config.get("app.hotkey_swatter")))
    check("状态接口覆盖四个热键",
          set(app.hotkey_status()) == {"console", "visible", "burst", "swatter"},
          str(sorted(app.hotkey_status())))


def test_console_and_burst(app: RoachApp) -> None:
    print("\n[11] 控制台 / 虫巢爆发 / 批量投放")
    from src.entities.cockroach import Cockroach as _Roach

    # 1) 默认启动就打开控制台（用户诉求：不用先去右下角点托盘）
    check("默认「启动时打开控制台」", bool(app.config.get("app.open_console_on_start", True)), "true")

    # 2) 开关控制台（热键与托盘共用的同一入口）
    app.open_settings()
    check("open_settings 后控制台可见", app.console_visible(), "isVisible=True")
    v1 = app.toggle_console()
    v2 = app.toggle_console()
    check("toggle_console 能收起再召唤", v1 is False and v2 is True, f"{v1} -> {v2}")

    w = app._settings_window
    check("控制台首页就是虫巢控制台", w.tabs.tabText(0).endswith("控制台"), w.tabs.tabText(0))
    bar = w.tabs.tabBar()
    check("八个页签没有被挤成滚动箭头",
          w.tabs.count() == 8 and bar.sizeHint().width() <= w.tabs.width(),
          f"{w.tabs.count()} 页，页签宽 {bar.sizeHint().width()} / 可用 {w.tabs.width()}")

    # 3) 批量投放：数量正确，且自动截断到剩余容量
    app.clear_all()
    got = app.spawn_batch(50)
    check("批量投放 50 只", len(got) == 50 and app.alive_roach_count() == 50,
          f"实际 {len(got)} / 场上 {app.alive_roach_count()}")
    stages = {g.stage.name for g in got}
    check("混合投放同时含成虫与若虫", len(stages) >= 2, str(stages))

    app.clear_all()
    stage_only = app.spawn_batch(8, LifeStage.NYMPH)
    check("可以只投指定阶段（全若虫）",
          len(stage_only) == 8 and all(n.stage == LifeStage.NYMPH for n in stage_only),
          f"{len(stage_only)} 只，全部若虫={all(n.stage == LifeStage.NYMPH for n in stage_only)}")
    app.clear_all()
    app.spawn_batch(50)

    cap = int(app.config.get("simulation.max_roaches", 200))
    got2 = app.spawn_batch(999)
    check("超额请求被截断到上限", app.alive_roach_count() == cap,
          f"{app.alive_roach_count()} / 上限 {cap}（本次放 {len(got2)}）")

    only_nymph = app.spawn_batch(5, LifeStage.NYMPH)
    check("满员后不再新增", len(only_nymph) == 0, f"新增 {len(only_nymph)}")

    # 4) 虫巢爆发：补满 → 全体受惊 → 再按不重复
    app.clear_all()
    app.spawn_batch(3)
    added = app.nest_burst()
    check("虫巢爆发一次刷满", added == cap - 3 and app.alive_roach_count() == cap,
          f"新增 {added}，场上 {app.alive_roach_count()} / {cap}")
    panicked = sum(1 for e in app.entities
                   if isinstance(e, _Roach) and e.state.name in ("PANIC", "FREEZE"))
    check("爆发后全体受惊四散", panicked == cap, f"{panicked}/{cap} 处于 PANIC/FREEZE")
    check("已达上限时再爆发不重复增加", app.nest_burst() == 0, "返回 0")

    # 5) 爆发不应该把帧预算搞崩：极端情况下仍要守住 min_fps
    snap = app.performance_snapshot()
    check("爆发后帧率下限依然守得住", snap["fps_cap"] >= snap["min_fps"] - 1,
          f"fps_cap={snap['fps_cap']:.0f}, min={snap['min_fps']:.0f}")

    # 6) 批量食物 + 热键提示
    app.clear_all()
    foods = app.spawn_foods(6)
    check("批量投放食物", len(foods) == 6, f"{len(foods)} 块")
    check("托盘菜单里有虫巢爆发入口", "虫巢爆发" in app.tray.burst_action.text(),
          app.tray.burst_action.text())

    app.clear_all()

def test_icons_and_single_window(app) -> None:
    print("\n[13] 图标 / 单窗口 / 控制台")
    from pathlib import Path

    from PySide6.QtGui import QImage

    from src.resources import ResourceManager
    from src.utils import console_window as cw

    want = {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}

    ico = Path("assets/icons/roachhub.ico")
    check("应用图标文件存在", ico.exists(), str(ico))
    app_sizes = {(s.width(), s.height()) for s in ResourceManager.get_app_icon().availableSizes()}
    check("应用图标含 7 档尺寸", want <= app_sizes, f"{sorted(app_sizes)}")

    tray_ico = Path("assets/icons/tray_icon.ico")
    check("托盘图标文件存在", tray_ico.exists(), str(tray_ico))
    tray_sizes = {(s.width(), s.height()) for s in ResourceManager.get_tray_icon().availableSizes()}
    check("托盘图标含 16/24/32 三档", {(16, 16), (24, 24), (32, 32)} <= tray_sizes,
          f"{sorted(tray_sizes)}")

    pm = ResourceManager.get_tray_icon().pixmap(16, 16)
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    opaque = 0
    for y in range(img.height()):
        line = bytes(img.constScanLine(y))
        for x in range(img.width()):
            if line[x * 4 + 3] > 96:
                opaque += 1
    check("16px 托盘图标不是空白（缩到 16px 仍有实体）", opaque > 30, f"{opaque} 个不透明像素")

    app.open_settings()
    w = app._settings_window
    check("控制台标题栏用上了新图标",
          not w.windowIcon().isNull() and want <= {(s.width(), s.height()) for s in w.windowIcon().availableSizes()},
          "含 7 档")

    qapp.setWindowIcon(ResourceManager.get_app_icon())
    check("QApplication 级图标已设置（对话框/任务栏共用）",
          not qapp.windowIcon().isNull(), "非空")

    check("双击入口 roachHub.pyw 存在", Path("roachHub.pyw").exists())

    # 控制台隐藏：只要控制台里混着 shell，就绝不能藏（会把用户的终端弄没）
    launchers = set(cw.LAUNCHER_NAMES)
    check("启动器白名单覆盖 py/python/pythonw",
          {"py.exe", "python.exe", "pythonw.exe"} <= launchers, str(sorted(launchers)))
    names = cw.console_process_names()
    check("本进程当前控制台里混有 shell → 判定为共享终端",
          (not names) or cw.console_is_ours() is False,
          f"names={names}")
    check("共享终端下 hide_own_console 是空操作", cw.hide_own_console() is False, "没有动手")

    app.toggle_console()


def test_molting(app: RoachApp) -> None:
    """蜕皮（嫩壳）验收：白色是"刚蜕壳"的生理状态，不是贴图错误。"""
    print("\n[12] 蜕皮：白化窗口 / 渐变 / 空壳 / 鼠标不干扰")
    from src.entities.exuvia import Exuvia

    app.entities.clear()
    r = Cockroach(700, 500, stage=LifeStage.NYMPH, gender="female", instar=1, config={})
    app.entities.append(r)
    app.overlay.set_entities(app.entities)

    r.advance_instar()
    check("蜕皮瞬间躯干贴图切到奶白嫩壳",
          r._current_body_sprite_name() == "cockroach_white_body",
          f"{r._current_body_sprite_name()} / molt={r.molt_timer:.1f}s")

    # 1. 鼠标贴到身上：软壳个体只能感知，不能行动
    cursor = Vector2D(r.pos.x, r.pos.y)
    states = set()
    for _ in range(int(2.0 / DT)):
        step(app, 1, cursor=cursor)
        states.add(r.state)
    check("蜕皮期间鼠标贴脸也不会惊慌逃跑",
          states == {EntityState.MOLTING} and r.current_speed < 1.0,
          f"states={[s.name for s in states]} speed={r.current_speed:.1f}")

    r.begin_drag(Vector2D(r.pos.x, r.pos.y))
    step(app, 3, cursor=cursor)
    r.end_drag()
    step(app, 3, cursor=cursor)
    check("蜕皮中被抓起放下也不会惊慌冲刺（软壳）",
          r.state != EntityState.PANIC and r.current_speed < 5.0,
          f"state={r.state.name} speed={r.current_speed:.1f}")

    # 2. 白化不是"啪"一下切回棕：molt_timer 归零后 alpha 连续下降
    while r.molt_timer > 0:
        step(app, 1, cursor=Vector2D(r.pos.x, r.pos.y))
    alphas = []
    for _ in range(int(2.0 / DT)):
        step(app, 1, cursor=Vector2D(-5000, -5000))
        alphas.append(r.teneral_alpha)
    check("白化窗口结束后开始渐变（不是硬切）",
          alphas[0] > 0.8 and alphas[-1] == 0.0,
          f"alpha {alphas[0]:.2f} -> {alphas[-1]:.2f}")
    check("渐变过程单调下降",
          all(b <= a + 1e-6 for a, b in zip(alphas, alphas[1:])),
          f"共 {len(alphas)} 帧采样")
    mid = alphas[len(alphas) // 4]
    check("中途确实是半透明过渡色（0.2~0.8）", 0.15 < mid < 0.85, f"alpha={mid:.2f}")

    # 3. 蜕下的空壳：留下、看得见、点不到、会消失
    app.entities.clear()
    r2 = Cockroach(400, 400, stage=LifeStage.NYMPH, gender="female", instar=1, config={})
    r2.instar_duration = app.lifecycle_system.instar_duration
    r2.instar_age = r2.instar_duration + 0.01
    app.entities.append(r2)
    spawned = app.lifecycle_system.update(app.entities, DT, {}, 200)
    app.entities.extend(spawned)
    app.overlay.set_entities(app.entities)
    husks = [e for e in spawned if isinstance(e, Exuvia)]
    check("蜕皮后原地留下一具空壳", len(husks) == 1, f"{len(husks)} 具")
    if husks:
        husk = husks[0]
        check("空壳不可被抓（点它等于点空气）",
              app.overlay.hit_test_at(husk.pos.x, husk.pos.y) is None,
              "hit_test=None")
        check("空壳不占种群上限（只算 Cockroach）",
              app.alive_roach_count() == 1, f"roach_count={app.alive_roach_count()}")
        for _ in range(int(11.0 / DT)):
            step(app, 1, cursor=Vector2D(-5000, -5000))
        check("空壳到期自动消失",
              (not husk.is_alive) or husk not in app.entities,
              f"is_alive={husk.is_alive}")

    # 4. 总开关：关掉后蜕皮不再变白
    Cockroach.show_teneral = False
    try:
        r3 = Cockroach(300, 300, stage=LifeStage.NYMPH, gender="female", config={})
        r3.advance_instar()
        check("关掉「奶白嫩壳」后蜕皮不变白",
              r3._current_body_sprite_name() != "cockroach_white_body"
              and r3.teneral_alpha == 0.0,
              f"{r3._current_body_sprite_name()} alpha={r3.teneral_alpha:.2f}")
    finally:
        Cockroach.show_teneral = True


def test_platform_layer(app: RoachApp) -> None:
    """跨平台适配层：mac 的键码/修饰键换算、路径解析、穿透模式选择。

    这些都是"在 Windows 上跑不出来的分支"，只能把**纯数据部分**（映射表、换算、
    路径推导）和**降级行为**（拿不到原生能力时不许崩）在这里钉死；真正的
    Carbon / CGWindowList 调用只能在 mac 上实测。
    """
    print("\n[14] 跨平台适配层")

    from src.utils import app_paths, objc_bridge
    from src.utils.mac_hit_test import CocoaClickThrough, supports_cocoa_hit_test
    from src.utils.mac_hotkeys import carbon_modifiers, keycode_of
    from src.utils.occlusion import covered_rects as covered_any

    # 1) mac 键码表：配置里四个默认热键必须都映射得上（否则 mac 上等于没热键）
    missing = [k for k in ("R", "H", "B", "S", "F11", "Space")
               if keycode_of(k) is None]
    check("mac 键码表覆盖默认热键与常用键", not missing, f"缺失={missing}")

    # 2) 修饰键换算：Windows 的 MOD_* 和 Carbon 的位是两套，错一位就是"设了没反应"
    check("Ctrl+Alt → control|option",
          carbon_modifiers(0x0002 | 0x0001, 0x8, 0x2, 0x1, 0x4) == 0x1800,
          hex(carbon_modifiers(0x0002 | 0x0001, 0x8, 0x2, 0x1, 0x4)))
    check("Win 键在 mac 上归到 Command",
          carbon_modifiers(0x0008, 0x8, 0x2, 0x1, 0x4) == 0x0100,
          hex(carbon_modifiers(0x0008, 0x8, 0x2, 0x1, 0x4)))

    # 3) 穿透模式：三档里只能选中当前平台那一档
    expected = ("win32" if sys.platform.startswith("win")
                else "cocoa" if sys.platform == "darwin" else "mask")
    check("穿透模式与平台匹配", app.overlay._hit_mode == expected,
          f"{app.overlay._hit_mode}（期望 {expected}）")
    check("原生命中与遮罩互斥",
          app.overlay._native_hit_test == (expected != "mask"),
          f"_native_hit_test={app.overlay._native_hit_test}")
    app.overlay.release_click_through()      # 非 mac 上是空操作，但不能抛

    # 4) 遮挡检测分发：不支持的平台/无效窗口一律返回空（= 认为面板露着），
    #    绝不能抛异常 —— 这个函数挂在 4Hz 的轮询里
    from PySide6.QtCore import QRect
    check("遮挡检测：空面板返回空", covered_any(0, QRect()) == [], "[]")
    check("遮挡检测：无效窗口不抛", covered_any(0, QRect(0, 0, 100, 100)) == [], "[]")

    # 5) 路径：开发期必须落在项目根（打包后由 _MEIPASS / 用户目录接管）
    check("开发期资源根 = 项目根", app_paths.bundle_dir() == ROOT,
          str(app_paths.bundle_dir()))
    check("开发期配置落在项目根", app.config.config_path == ROOT / "config.toml",
          str(app.config.config_path))
    check("开发期日志落在项目根 logs/", app_paths.log_dir() == ROOT / "logs",
          str(app_paths.log_dir()))
    check("非 mac 上 objc 桥不可用", objc_bridge.available() == (sys.platform == "darwin"),
          str(objc_bridge.available()))

    # 6) mac 后端缺失时必须**明确失败**，而不是掉进 Windows 分支去碰 `ctypes.windll`
    #    （mac 上没有 windll 这个属性，掉下去就是 AttributeError 崩在 bind 里）
    import src.utils.hotkeys as hk
    real_platform = hk.sys.platform
    try:
        hk.sys.platform = "darwin"
        probe = hk.GlobalHotkeyManager()          # 没 install → 没有 Carbon 后端
        bound = probe.bind("x", "Ctrl+Alt+R", lambda: None)
    finally:
        hk.sys.platform = real_platform
    check("mac 无后端时 bind 明确失败（不碰 windll）",
          (not bound) and "Carbon" in probe.failures.get("x", ""),
          probe.failures.get("x", "没有失败原因"))

    # 7) Cocoa 穿透对象在非 mac 上必须"构造不炸、attach 失败但不影响主流程"
    if not supports_cocoa_hit_test():
        cocoa = CocoaClickThrough(app.overlay)
        check("Cocoa 穿透在非 mac 上安全降级", cocoa.attach() is False, "attach=False")
        cocoa.set_accepts(True)      # 无窗口时必须是空操作
        cocoa.release()
        check("Cocoa 穿透无窗口时状态不变", cocoa.accepting is None,
              str(cocoa.accepting))


def main() -> int:
    global qapp
    qapp = QApplication(sys.argv)
    app = fresh_app()

    test_antennae_tracking(app)
    test_prestartle_order(app)
    test_drag(app)
    test_playdead(app)
    test_eating(app)
    test_trails(app)
    test_audio_budget(app)
    test_grab_voice(app)
    test_clickthrough_and_budget(app)
    test_hotkeys(app)
    test_console_and_burst(app)
    test_molting(app)
    test_icons_and_single_window(app)
    test_platform_layer(app)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n总计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n in failed:
        print("  FAILED:", n)
    app.quit()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
