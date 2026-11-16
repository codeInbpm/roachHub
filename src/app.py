"""主应用程序控制器。"""

from __future__ import annotations
import sys
import time
import random
import logging
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, QTimer, QPoint, QRect, Qt
from PySide6.QtGui import QCursor, QRegion
from PySide6.QtWidgets import QApplication

from .config import Config
from .entities.base_entity import BaseEntity, EntityState, LifeStage
from .entities.cockroach import Cockroach
from .entities.ootheca import Ootheca
from .entities.food import Food
from .entities.food_kinds import pick_kind, RANDOM_KEY
from .entities.squashed import SquashedRoach
from .overlay_window import OverlayWindow
from .tray import RoachTrayIcon
from .settings_window import SettingsWindow
from .systems.movement import MovementSystem
from .systems.behavior import BehaviorSystem
from .systems.needs import NeedsSystem
from .systems.lifecycle import LifecycleSystem
from .systems.reproduction import ReproductionSystem
from .systems.ecology import EcologySystem
from .systems.audio import ScuttleAudio
from .systems.splatter import SplatterSystem
from .systems.swatter import Swatter
from .systems.voice import GrabVoice
from .systems.squash_voice import SquashVoice
from .utils.hotkeys import GlobalHotkeyManager, supports_global_hotkeys
from .utils.win_occlusion import covered_rects
from .utils.math_utils import Vector2D

logger = logging.getLogger("roachHub.app")


class RoachApp(QObject):
    """协调窗口、系统与生物生命周期的总控制器。"""

    # 一次"虫巢爆发"最多补这么多只，防止把 max_roaches 误设成 9999 时一次卡死
    BURST_HARD_LIMIT = 400

    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        self.config = Config()

        self.entities: List[BaseEntity] = []
        self.is_sim_visible: bool = True
        self._settings_window: Optional[SettingsWindow] = None
        self._food_timer: float = 0.0
        self._cursor_pos: Vector2D = Vector2D(0.0, 0.0)

        # 「刚蜕皮的奶白色嫩壳」是否展示（设置里可关）：类级开关，所有个体共享
        Cockroach.show_teneral = bool(self.config.get("render.show_teneral", True))

        # 初始化子系统
        screen_geom = self.qapp.primaryScreen().geometry()
        pad = float(self.config.get("simulation.boundary_padding", 45.0))
        boundary_mode = str(self.config.get("simulation.boundary_behavior", "rebound"))
        thigmo = float(self.config.get("behavior.thigmotaxis_weight", 0.55))

        self.movement_system = MovementSystem(
            screen_width=screen_geom.width(),
            screen_height=screen_geom.height(),
            boundary_padding=pad,
            boundary_behavior=boundary_mode,
            thigmotaxis_weight=thigmo,
        )
        self.behavior_system = BehaviorSystem(self.config.get("behavior", {}))
        self.needs_system = NeedsSystem(
            energy_decay_rate=float(self.config.get("creatures.cockroach.energy_decay_rate", 0.6))
        )
        self.lifecycle_system = LifecycleSystem(self.config.get("lifecycle", {}))
        self.reproduction_system = ReproductionSystem(self.config.get("lifecycle", {}))
        self.ecology_system = EcologySystem(
            max_population=int(self.config.get("simulation.max_roaches", 15))
        )

        # 覆盖层与托盘
        self.trail_fade = float(self.config.get("trails.fade_seconds", 6.0))
        # 1080p 及以下用原分辨率痕迹（更锐利，开销与半分辨率几乎持平）；
        # 4K 屏面积翻 4 倍，自动降到半分辨率以守住淡出成本。
        trail_scale = float(self.config.get("trails.resolution_scale", 1.0))
        if screen_geom.width() * screen_geom.height() > 3_200_000:
            trail_scale = min(trail_scale, 0.5)
        self.overlay = OverlayWindow(
            on_entity_clicked=self._on_entity_clicked,
            on_entity_dropped=self._on_entity_dropped,
            on_grab_started=self._on_grab_started,
            trail_scale=trail_scale,
            trail_fade=self.trail_fade,
        )
        self.overlay.set_trails_enabled(bool(self.config.get("trails.enabled", True)))
        self.overlay.drag_enabled = bool(self.config.get("interaction.drag_enabled", True))

        # 苍蝇拍 + 爆浆。拍子只是"把鼠标闸门临时开一条缝"，所有伤害结算都在
        # `_resolve_swat()` 里做 —— 它需要拿到完整的 entities 列表，所以放在 app 层。
        self.splatter = SplatterSystem(
            screen_shake=bool(self.config.get("swatter.screen_shake", True))
        )
        self.swatter = Swatter(self.config.get("swatter", {}))
        self.swatter.on_impact = self._resolve_swat
        self.swatter.on_state_changed = self._on_swatter_state_changed
        self.overlay.attach_swatter(self.swatter)
        self.overlay.splatter = self.splatter

        # 程序化音效（无音频设备时自动降级静音）
        self.audio = ScuttleAudio(
            enabled=bool(self.config.get("audio.enabled", True)),
            volume=float(self.config.get("audio.volume", 0.45)),
            max_per_second=int(self.config.get("audio.max_per_second", 18)),
        )

        # 抓取时播放整句「你干嘛~哎呦」（独立开关，音量也独立 —— 它比窸窣声响得多）
        self.grab_voice = GrabVoice(
            path=str(self.config.get("grab_voice.file", "assets/sounds/grab.wav")),
            enabled=bool(self.config.get("grab_voice.enabled", True)),
            volume=float(self.config.get("grab_voice.volume", 0.7)),
            stop_on_release=bool(self.config.get("grab_voice.stop_on_release", False)),
        )

        # 拍死时的叫声：默认完整一句（2.42 秒），可切剪辑版 / yamero / 哎哟
        self.squash_voice = SquashVoice(
            enabled=bool(self.config.get("squash_voice.enabled", True)),
            volume=float(self.config.get("squash_voice.volume", 0.75)),
            variant=str(self.config.get("squash_voice.variant", "myinstants")),
        )

        self.tray = RoachTrayIcon(
            on_toggle_visible=self.toggle_visibility,
            on_spawn_roach=self.spawn_cockroach,
            on_spawn_food=self.spawn_food,
            on_clear_roaches=self.clear_all,
            on_open_settings=self.summon_console,
            on_quit=self.quit,
            on_clear_trails=self.clear_trails,
            on_toggle_audio=self.toggle_audio,
            on_spawn_roaches=self.spawn_batch,
            on_spawn_foods=self.spawn_foods,
            on_nest_burst=self.nest_burst,
            on_toggle_swatter=self.toggle_swatter,
        )

        # 全局热键（RegisterHotKey）：应用没有焦点时也能召唤控制台/放虫。
        # 注意只在 start() 里真正注册 —— 单元测试构造 RoachApp 时不应该去抢系统热键。
        self.hotkeys = GlobalHotkeyManager()

        self.target_fps = int(self.config.get("app.target_fps", 60))
        self.timer = QTimer(self)
        # 必须用 PreciseTimer：Windows 下默认的 CoarseTimer 会把 33ms 量化成 46.6ms
        # （系统定时器粒度 15.6ms 的整数倍），"30 FPS"实际只有 21 FPS，而且间隔抖动。
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._on_tick)
        self._last_tick_time = time.perf_counter()

        # 控制台的"洞"要跟着窗口 z-order 走：面板沉到浏览器后面时必须撤销挖洞，
        # 否则屏幕上会留一块"虫子走进去就消失"的矩形。4Hz 足够，别拖累主循环。
        self._console_hole_timer = QTimer(self)
        self._console_hole_timer.setInterval(250)
        self._console_hole_timer.timeout.connect(self._poll_console_hole)

        # ---- 帧预算自适应（保证"不管多少只，都不会把 UI 卡死"）----
        # 逻辑：先降画质（LOD2→1→0），画质见底还不达标就降帧率。
        # 绝不"硬撑"目标帧率 —— 那会让事件循环永远排满，Windows 连拖动窗口都不响应，
        # 用户看到的就是"卡死"。宁可 25 FPS 流畅，也不要 60 FPS 要求下卡成幻灯片。
        self._budget_ms = 1000.0 / max(15, self.target_fps)
        self._frame_ema = self._budget_ms
        self._over_streak = 0
        self._under_streak = 0
        self._quality = 2
        self._fps_cap = self.target_fps
        self._quality_locked = False
        self.min_fps = int(self.config.get("performance.min_fps", 30))
        self._tray_update_at = 0.0
        self._tick_ms = 0.0
        self._tick_t0 = 0.0
        self._paints_seen = 0
        self._paints_per_tick = 0
        self._tick_t1 = 0.0
        self.unlimited_quality = bool(self.config.get("performance.fixed_quality", False))

    def start(self) -> None:
        """启动高拟真模拟器。"""
        logger.info("正在启动 roachHub 繁殖与高拟真模拟器...")

        self.tray.show()

        # 生成初始群落：1 雌性成虫、1 雄性成虫、1 只小若虫，即刻具备繁衍生态
        self.spawn_cockroach(stage=LifeStage.ADULT, gender="female")
        self.spawn_cockroach(stage=LifeStage.ADULT, gender="male")
        self.spawn_cockroach(stage=LifeStage.NYMPH, gender="female")

        self.spawn_food()

        start_hidden = bool(self.config.get("app.start_hidden", False))
        if start_hidden:
            self.is_sim_visible = False
            self.overlay.set_simulation_visible(False)
        else:
            self.overlay.show()

        interval_ms = max(1, int(1000.0 / self.target_fps))
        self._last_tick_time = time.perf_counter()
        self.timer.start(interval_ms)

        self._update_tray_counts()

        # 全局热键必须在消息循环起来之前装好
        self.hotkeys.install()
        self.register_hotkeys()

        self._console_hole_timer.start()

        # 默认直接弹出控制台（不用先去右下角翻托盘菜单）
        if bool(self.config.get("app.open_console_on_start", True)):
            self.open_settings()

        logger.info("模拟主循环已启动，当前目标帧率: %d FPS", self.target_fps)

    # ---------------------------------------------------------------- 热键

    def register_hotkeys(self) -> dict[str, str]:
        """按配置重新注册三个全局热键。返回 {逻辑名: 失败原因}（空 = 全部成功）。"""
        self.hotkeys.clear()
        self.hotkeys.bind(
            "console",
            str(self.config.get("app.hotkey_toggle_console", "Ctrl+Alt+R")),
            lambda: self._guarded(self.toggle_console),
        )
        self.hotkeys.bind(
            "visible",
            str(self.config.get("app.hotkey_toggle_visible", "Ctrl+Alt+H")),
            lambda: self._guarded(self.toggle_visibility),
        )
        self.hotkeys.bind(
            "burst",
            str(self.config.get("app.hotkey_nest_burst", "Ctrl+Alt+B")),
            lambda: self._guarded(self.nest_burst),
        )
        self.hotkeys.bind(
            "swatter",
            str(self.config.get("app.hotkey_swatter", "Ctrl+Alt+S")),
            lambda: self._guarded(self.toggle_swatter),
        )
        if self.hotkeys.failures:
            logger.warning("部分全局热键未生效：%s", self.hotkeys.failures)
        self._sync_hotkey_hints()
        return dict(self.hotkeys.failures)

    def _guarded(self, action: Callable[[], object]) -> None:
        """热键动作守门：正在控制台里录制热键时不要真的执行。

        否则用户点开"显示/隐藏控制台"的录入框、按下当前那个组合键，窗口会当场
        被自己藏起来 —— 录音时不能把自己静音，同一个道理。
        """
        if self._recording_hotkey():
            logger.debug("正在录制热键，忽略本次全局热键")
            return
        action()

    def _recording_hotkey(self) -> bool:
        w = self._settings_window
        if w is None or not w.isVisible() or not w.isActiveWindow():
            return False
        from PySide6.QtWidgets import QApplication, QKeySequenceEdit

        return isinstance(QApplication.focusWidget(), QKeySequenceEdit)

    def hotkey_status(self) -> dict[str, dict[str, str | bool]]:
        """给控制台显示用：每个热键的规范串 / 是否注册成功 / 失败原因。"""
        out: dict[str, dict[str, str | bool]] = {}
        for name in ("console", "visible", "burst", "swatter"):
            out[name] = {
                "spec": self.hotkeys.spec_of(name),
                "ok": self.hotkeys.is_registered(name),
                "error": self.hotkeys.failures.get(name, ""),
            }
        return out

    def _sync_hotkey_hints(self) -> None:
        """把热键文案同步到托盘菜单，用户不用翻设置也知道按什么。"""
        self.tray.set_hotkey_hints(
            console=self.hotkeys.spec_of("console"),
            visible=self.hotkeys.spec_of("visible"),
            burst=self.hotkeys.spec_of("burst"),
            swatter=self.hotkeys.spec_of("swatter"),
        )

    def spawn_cockroach(
        self,
        x: float | None = None,
        y: float | None = None,
        stage: LifeStage = LifeStage.ADULT,
        gender: Optional[str] = None,
        announce: bool = True,
    ) -> Cockroach:
        """在屏幕内生成一只蟑螂。"""
        screen_geom = self.qapp.primaryScreen().geometry()
        pad = float(self.config.get("simulation.boundary_padding", 45.0))

        if x is None:
            x = random.uniform(pad + 80.0, screen_geom.width() - pad - 80.0)
        if y is None:
            y = random.uniform(pad + 80.0, screen_geom.height() - pad - 80.0)

        if gender is None:
            f_ratio = float(self.config.get("lifecycle.female_ratio", 0.5))
            gender = "female" if random.random() < f_ratio else "male"

        roach_cfg = self.config.get("creatures.cockroach", {})
        max_instars = int(self.config.get("lifecycle.max_instars", 4))
        render_style = str(self.config.get("render.style", "realistic"))
        shadow_intensity = float(self.config.get("render.shadow_intensity", 0.42))

        roach = Cockroach(
            x=x,
            y=y,
            stage=stage,
            gender=gender,
            instar=1 if stage == LifeStage.NYMPH else max_instars,
            max_instars=max_instars,
            config=roach_cfg,
            render_style=render_style,
            shadow_intensity=shadow_intensity,
        )
        roach.instar_duration = float(self.config.get("lifecycle.instar_duration", 18.0))
        # 直接投放的成体当作"野生成虫"：多半已经被交配过，先给 1 次产卵额度，
        # 免得投进去半天不见卵。由卵鞘孵化、自己长成的个体仍是处女，必须先找雄虫。
        if stage == LifeStage.ADULT and gender == "female":
            roach.broods_left = 1
        roach.on_footstep = self._on_footstep
        roach.on_smear = self._on_smear
        roach.on_sound = self._on_sound
        self.entities.append(roach)

        if announce:
            logger.info("生成蟑螂: ID=%s (阶段: %s, 性别: %s, 风格: %s)",
                        roach.id, stage.name, gender, render_style)
            self._update_tray_counts()
        else:
            logger.debug("批量生成蟑螂: ID=%s (%s/%s)", roach.id, stage.name, gender)
        return roach

    def spawn_food(
        self,
        x: float | None = None,
        y: float | None = None,
        kind: str | None = None,
    ) -> Food:
        """投放一块食物。

        :param kind: ``food_kinds`` 里的 key；None 时读配置 ``simulation.food_kind``
        """
        if kind is None:
            kind = str(self.config.get("simulation.food_kind", RANDOM_KEY))
        screen_geom = self.qapp.primaryScreen().geometry()
        pad = float(self.config.get("simulation.boundary_padding", 45.0))

        if x is None:
            if random.random() < 0.65:
                side = random.choice(["left", "right", "top", "bottom"])
                if side == "left":
                    x = pad + random.uniform(20.0, 60.0)
                    y = random.uniform(pad + 50.0, screen_geom.height() - pad - 50.0)
                elif side == "right":
                    x = screen_geom.width() - pad - random.uniform(20.0, 60.0)
                    y = random.uniform(pad + 50.0, screen_geom.height() - pad - 50.0)
                elif side == "top":
                    x = random.uniform(pad + 50.0, screen_geom.width() - pad - 50.0)
                    y = pad + random.uniform(20.0, 60.0)
                else:
                    x = random.uniform(pad + 50.0, screen_geom.width() - pad - 50.0)
                    y = screen_geom.height() - pad - random.uniform(20.0, 60.0)
            else:
                x = random.uniform(pad + 90.0, screen_geom.width() - pad - 90.0)
                y = random.uniform(pad + 90.0, screen_geom.height() - pad - 90.0)

        food = Food(x=x, y=y, kind=pick_kind(kind))
        self.entities.append(food)
        logger.info(
            "投放食物 %s %s: ID=%s 营养=%.0f (坐标: %.1f, %.1f)",
            food.kind.icon, food.name, food.id, food.nutrition, x, y,
        )
        return food

    # ---------------------------------------------------------------- 批量投放

    def alive_roach_count(self) -> int:
        return sum(1 for e in self.entities if isinstance(e, Cockroach) and e.is_alive)

    def roach_capacity(self) -> int:
        """还能再放多少只（受 simulation.max_roaches 与 BURST_HARD_LIMIT 双重限制）。"""
        cap = int(self.config.get("simulation.max_roaches", 15))
        cap = min(cap, self.BURST_HARD_LIMIT)
        return max(0, cap - self.alive_roach_count())

    def spawn_batch(
        self,
        count: int = 10,
        stage: LifeStage | None = None,
        panic: bool = False,
    ) -> list[Cockroach]:
        """一次投放多只。

        :param count: 想要的数量；会被"剩余容量"截断（不会突破 max_roaches）
        :param stage: None = 混合（约 1/3 若虫），否则全部用指定阶段
        :param panic: True 时新生个体立刻受惊逃窜（虫巢爆发用）

        为什么不复用循环调 `spawn_cockroach` 就完事：那条路径每只都会打一条 INFO 日志
        并刷新托盘，一次 150 只就是 150 行日志 + 150 次托盘重绘。
        """
        want = max(0, int(count))
        room = self.roach_capacity()
        n = min(want, room)
        if n <= 0:
            logger.info("批量投放被忽略：已满（上限 %s 只）", self.config.get("simulation.max_roaches"))
            return []

        max_instars = int(self.config.get("lifecycle.max_instars", 4))
        females_bias = float(self.config.get("lifecycle.female_ratio", 0.5))
        out: list[Cockroach] = []
        for _ in range(n):
            if stage is None:
                # 混合群落更像真巢：若虫占三分之一，成虫里外雌雄各半
                use_stage = LifeStage.NYMPH if random.random() < 0.34 else LifeStage.ADULT
            else:
                use_stage = stage
            gender = "female" if random.random() < females_bias else "male"

            roach = self.spawn_cockroach(stage=use_stage, gender=gender, announce=False)
            if use_stage == LifeStage.NYMPH:
                # 让若虫体型随机分布在各龄期，别一堆同样大小
                roach.instar = random.randint(1, max(1, max_instars))
                roach._refresh_appearance_by_stage()
            if panic:
                roach.panic_timer = 3.0
                roach.state = EntityState.PANIC
                roach.target_speed = roach.panic_speed
                roach.angle = roach.target_angle = Vector2D.random_direction().angle()
            out.append(roach)

        logger.info("批量投放 %d 只（请求 %d，剩余容量 %d）", n, want, room)
        self._tray_update_at = 0.0        # 强制下一次统计立刻刷新，别等 0.5s 节流
        self._update_tray_counts()
        return out

    def spawn_foods(self, count: int = 5, kind: str | None = None) -> list[Food]:
        """批量投放食物。

        :param kind: 指定种类则全部相同；``"random"`` / None 逐块按营养加权随机
        """
        n = max(1, min(int(count), 40))
        out = [self.spawn_food(kind=kind) for _ in range(n)]
        names = sorted({f.name for f in out})
        logger.info("批量投放 %d 块食物：%s", n, "、".join(names))
        return out

    def nest_burst(self) -> int:
        """虫巢爆发：一键把桌面补到上限，并让整个巢炸开（全部受惊四散）。

        :return: 本次新增的只数（已达上限时为 0，但仍然会"炸"一次）

        语义选择：**补满**而不是"重置成满"。用户已经养了 50 只，他要的是"再来一波"，
        不是"把现有的清掉重来" —— 后者会让他刚放的那几只凭空消失。
        """
        alive = self.alive_roach_count()
        room = self.roach_capacity()
        spawned = self.spawn_batch(room, stage=None, panic=False) if room > 0 else []

        # 无论是否新增，都让全场炸开一次：这是"爆发"该有的观感
        self._burst_panic(2.6)
        if self.audio.enabled and self.audio.available:
            self.audio.play("rustle", 0.55)

        cap = min(int(self.config.get("simulation.max_roaches", 15)), self.BURST_HARD_LIMIT)
        logger.info("虫巢爆发：%d → %d 只（上限 %d）", alive, self.alive_roach_count(), cap)
        return len(spawned)

    def _burst_panic(self, duration: float = 2.6) -> None:
        """以屏幕中心为惊源，让所有个体瞬间僵直后向四周逃窜。"""
        geom = self.qapp.primaryScreen().geometry()
        center = Vector2D(geom.width() / 2.0, geom.height() / 2.0)
        for e in self.entities:
            if isinstance(e, Cockroach) and e.is_alive and e.state != EntityState.DRAGGED:
                e.trigger_freeze_and_panic(center, duration)

    def clear_all(self) -> None:
        # 拍子必须先放下：否则屏幕还处于"整屏接管"状态，用户会觉得桌面点不动了
        self.swatter.disarm("clear_all")
        self.splatter.clear()
        for e in self.entities:
            self.behavior_system.cleanup_entity(e.id)
        self.entities.clear()
        self.overlay.set_entities([])
        self._tray_update_at = 0.0
        self._update_tray_counts()
        logger.info("已清空所有物体")

    def toggle_visibility(self) -> bool:
        self.is_sim_visible = not self.is_sim_visible
        if not self.is_sim_visible:
            # 隐藏蟑螂还攥着拍子 = 一个看不见但吃掉所有点击的全屏窗口
            self.swatter.disarm("hidden")
        self.overlay.set_simulation_visible(self.is_sim_visible)
        logger.info("切换可见性: %s", "显示" if self.is_sim_visible else "隐藏")
        return self.is_sim_visible

    # ---------------------------------------------------------------- 苍蝇拍

    def toggle_swatter(self) -> bool:
        """拿起 / 放下蟑螂拍 —— 桌面端唯一会"整屏接管鼠标"的入口。"""
        if not self.swatter.enabled:
            logger.info("苍蝇拍已在设置里关闭")
            return False
        if not self.is_sim_visible:
            logger.info("桌面蟑螂已隐藏，先显示蟑螂再拿拍子")
            return False
        armed = self.swatter.toggle(self._cursor_pos)
        logger.info(
            "苍蝇拍: %s", "已拿起（整屏接管，右键/热键/12 秒闲置放下）" if armed else "已放下"
        )
        return armed

    def _on_swatter_state_changed(self) -> None:
        """拿起/放下时的统一善后：光标、托盘文案、统计。"""
        self.overlay.sync_swatter_cursor()
        self.tray.set_swatter_armed(self.swatter.armed)
        self._tray_update_at = 0.0
        self._update_tray_counts()

    def _resolve_swat(self, hit_point: Vector2D, direction: Vector2D) -> None:
        """一次挥拍的结算（WINDUP 结束那一帧调用）。"""
        max_victims = max(1, int(self.swatter.max_victims))
        candidates: list[tuple[float, Cockroach]] = []
        for e in self.entities:
            if not isinstance(e, Cockroach) or not e.is_alive:
                continue
            if e.state == EntityState.DRAGGED:
                continue
            if self.swatter.can_hit(e, hit_point):
                candidates.append((e.pos.distance_to(hit_point), e))
        candidates.sort(key=lambda item: item[0])
        victims = [e for _, e in candidates[:max_victims]]

        for roach in victims:
            self._squash_roach(roach)

        if victims:
            self.swatter.kills += len(victims)
            count = int(self.config.get("swatter.max_particles", 14))
            self.splatter.spawn_impact(
                hit_point.x,
                hit_point.y,
                direction=direction,
                amount=float(self.swatter.splat_amount),
                count=count,
            )
            # 音量随本次击杀数上浮：一只是"啪唧"，三只是"糊了一片"
            volume = 0.42 if len(victims) == 1 else (0.52 if len(victims) == 2 else 0.60)
            self.audio.play("splat", volume)
            # 半声「哎哟」叠在撞击声之后 25ms —— 没有它，拍死像在拍一块肉
            if self.audio.enabled:
                self.squash_voice.play(len(victims))
            logger.info("拍死 %d 只（累计 %d）", len(victims), self.swatter.kills)
        else:
            # 拍空也必须给反馈，否则挥空像点了张图。但**不留污渍**。
            self.splatter.spawn_whiff(hit_point.x, hit_point.y)
            self.audio.play("whiff", 0.30)

        self._scare_near(hit_point, killed=bool(victims))
        self._tray_update_at = 0.0
        self._update_tray_counts()

    def _squash_roach(self, roach: Cockroach) -> None:
        """把一只活虫变成一具尸体 + 一摊污渍，并清掉它身上的所有挂靠。"""
        # 1. 进食位点：不释放的话那块碎屑要白占 2.5 秒（痕迹层的 TTL 才兜底）
        food = getattr(roach, "eating_food", None)
        if food is not None:
            try:
                food.release(roach.id)
            except Exception:
                logger.debug("释放进食位点失败", exc_info=True)
            roach.eating_food = None

        # 2. 拖着的卵鞘一起爆：否则会留下一个没有母体的孤儿卵鞘继续孵化
        ootheca = getattr(roach, "carried_ootheca", None)
        if ootheca is not None:
            if bool(self.config.get("swatter.can_squash_ootheca", True)):
                ootheca.is_alive = False
                ootheca.is_carried = False
            else:
                # 关掉"连卵鞘一起爆"时把它掉在原地，继续正常孵化
                ootheca.is_carried = False
                ootheca.pos = Vector2D(roach.pos.x, roach.pos.y)
            ootheca.parent = None
            roach.carried_ootheca = None
            roach.is_carrying_ootheca = False

        corpse = SquashedRoach.from_roach(
            roach,
            lifetime=float(self.config.get("swatter.linger_seconds", 22.0)),
            splat_amount=float(self.swatter.splat_amount),
        )
        roach.die("squashed")
        self.entities.append(corpse)

        # 3. 飞溅进痕迹层：尸体 22 秒后消失，那几枚小斑点按痕迹的正常节奏淡出
        self.overlay.add_splat(roach.pos.x, roach.pos.y, corpse.width * 0.6)

    def _scare_near(self, hit_point: Vector2D, killed: bool) -> None:
        """拍击惊逃 —— 这一条比所有粒子加起来都重要。

        拍中时：240px 内全部僵直后炸开（"手起拍落，全场炸锅"）；
        拍空时：180px 内只加警觉、记下威胁方向，**不**触发僵直狂逃 ——
        否则在虫子旁边随便挥两下就成了赶虫器。
        """
        radius = float(self.config.get("swatter.scare_radius", 240.0))
        for e in self.entities:
            if not isinstance(e, Cockroach) or not e.is_alive:
                continue
            if e.state == EntityState.DRAGGED:
                continue
            dist = e.pos.distance_to(hit_point)
            if killed:
                if radius <= 0.0:
                    return
                if dist <= radius:
                    e.trigger_freeze_and_panic(hit_point, 2.0)
            elif dist <= 180.0:
                e.alertness = min(1.0, e.alertness + 0.30)
                e.threat_vector = hit_point - e.pos

    def open_settings(self) -> None:
        if self._settings_window is None:
            self._settings_window = SettingsWindow(
                config=self.config,
                on_applied=self.apply_runtime_settings,
                on_clear_trails=self.clear_trails,
                on_preview_voice=self.preview_grab_voice,
                on_preview_squash_voice=self.preview_squash_voice,
                on_nest_burst=self.nest_burst,
                on_spawn_roaches=self.spawn_batch,
                on_spawn_foods=self.spawn_foods,
                on_clear_all=self.clear_all,
                on_query_stats=self.console_stats,
                on_toggle_sim_visible=self.toggle_visibility,
                on_toggle_swatter=self.toggle_swatter,
            )
            self._settings_window.geometry_changed.connect(self._sync_console_hole)
        w = self._settings_window
        # 最小化过就必须先还原：show() 对"已可见但最小化"的窗口是无操作，
        # 用户点托盘会以为程序死了。
        if w.isMinimized():
            w.setWindowState(w.windowState() & ~Qt.WindowState.WindowMinimized)
        if not w.isVisible():
            # 只在"重新弹出"时回读配置；已经开着就别 reload —— 否则用户没点的修改会被抹掉
            w.load_from_config()
        w.show()
        w.raise_()
        w.activateWindow()
        self._sync_console_hole()
        # raise_() 之后 z-order 才真正生效，隔一拍再算一次，免得洞算成"还被压着"
        QTimer.singleShot(60, self._poll_console_hole)
        # 建窗口 / 首次布局会卡住消息循环上百毫秒，别让自适应器把这当成"性能不够"
        self.reset_frame_governor()

    def _sync_console_hole(self) -> None:
        """把控制台**真正露出来**的那几块告诉覆盖层，让它在绘制和命中上"挖空"。

        没有这一步，蟑螂会爬到面板上：看起来只是难看，实际是**按钮点不动** ——
        覆盖层比面板更靠前，蟑螂身上的点击会被它吃掉。

        但洞必须跟着"面板是不是还在最前面"走：面板是普通窗口，用户点一下浏览器它就
        沉到后面去了。如果洞还留在原地，屏幕上就出现一个**虫子走进去就消失**的矩形
        （面板被挡住，用户只能看到洞）。所以这里先减去压在上面的窗口矩形。
        """
        w = self._settings_window
        hidden = w is None or not w.isVisible() or w.isMinimized()
        if hidden:
            self.overlay.set_hole_region(None)
            return
        r = w.frameGeometry()
        if r.width() <= 0 or r.height() <= 0:
            self.overlay.set_hole_region(None)
            return

        # 多让 4px：窗口阴影/圆角边缘也归面板，别在边框上留一条"能吃点击"的缝
        region = QRegion(QRect(r).adjusted(-4, -4, 4, 4))
        for cover in covered_rects(int(w.winId()), r):
            region = region.subtracted(QRegion(cover))
        if region.isEmpty():
            # 面板被完全挡住（例如最大化浏览器压在上面）：整屏都归蟑螂
            self.overlay.set_hole_region(None)
            return

        offset = self.overlay.mapFromGlobal(QPoint(0, 0))
        self.overlay.set_hole_region(region.translated(offset.x(), offset.y()))

    def _poll_console_hole(self) -> None:
        """低频重算洞（4Hz）：面板被抬起/压住时，洞要跟着变化。"""
        if self._settings_window is None:
            return
        try:
            self._sync_console_hole()
        except Exception:
            logger.debug("重算控制台洞失败", exc_info=True)

    def summon_console(self) -> bool:
        """托盘单击 / 菜单「打开控制台」：**总是**把它叫出来，不做可见性取反。

        托盘图标被点的时候用户期待的是"把面板拿来"，而不是"如果开着就收起来" ——
        后者在面板被别的窗口挡住时，表现就是"点了没反应"。
        """
        self.open_settings()
        return True

    def reset_frame_governor(self) -> None:
        """丢掉帧耗时的历史估计，从"期望预算"重新观察。

        用于已知会抖一下的时刻：创建控制台、应用配置、修改帧率设置。
        否则一次 200ms 的窗口构建会污染 EMA，让画质莫名其妙掉一档。
        """
        self._frame_ema = self._budget_ms
        self._over_streak = 0
        self._under_streak = 0
        self._last_tick_time = time.perf_counter()

    def console_visible(self) -> bool:
        return self._settings_window is not None and self._settings_window.isVisible()

    def console_in_front(self) -> bool:
        """控制台是否可见、且没有被别的窗口压在下面。

        "可见"不等于"看得见"：面板是普通窗口，点一下浏览器它就沉下去了。
        热键和"洞"的取舍都取决于这个判断。
        """
        w = self._settings_window
        if w is None or not w.isVisible() or w.isMinimized():
            return False
        try:
            return not covered_rects(int(w.winId()), w.frameGeometry())
        except Exception:
            logger.debug("遮挡检测失败", exc_info=True)
            return True

    def toggle_console(self) -> bool:
        """微信式开关：**真的看得见**才收起，否则一律召唤到前台。

        注意两个坑：
        1. "可见但最小化"要当成不可见 —— 否则按热键只会把它再藏一次，永远叫不出来；
        2. "可见但被浏览器压着"同理 —— 用户按热键就是想把面板叫出来，不能反而藏掉。
        """
        if self.console_in_front():
            self._settings_window.hide()
            visible = False
        else:
            self.open_settings()
            visible = True
        logger.info("控制台: %s", "显示" if visible else "隐藏")
        return visible

    def console_stats(self) -> dict:
        """控制台顶部那块状态区要的数据（每次刷新都是现算，不做增量维护）。"""
        adults_f = adults_m = nymphs = eggs = foods = alive = unmated = 0
        for e in self.entities:
            if isinstance(e, Cockroach):
                if not e.is_alive:
                    continue
                alive += 1
                if e.stage == LifeStage.NYMPH:
                    nymphs += 1
                elif e.gender == "female":
                    adults_f += 1
                    # 待受精雌虫：能量够、但受精额度已空，正等着交配
                    if getattr(e, "broods_left", 0) <= 0:
                        unmated += 1
                else:
                    adults_m += 1
            elif isinstance(e, Ootheca) and e.is_alive:
                eggs += 1
            elif isinstance(e, Food) and e.is_alive:
                foods += 1
        cap = min(int(self.config.get("simulation.max_roaches", 15)), self.BURST_HARD_LIMIT)
        return {
            "alive": alive,
            "cap": cap,
            "females": adults_f,
            "males": adults_m,
            "unmated": unmated,
            "nymphs": nymphs,
            "eggs": eggs,
            "foods": foods,
            "sim_visible": self.is_sim_visible,
            "quality": self._quality,
            "fps": self._fps_cap,
            "kills": self.swatter.kills,
            "swatter_armed": self.swatter.armed,
            "hotkeys": self.hotkey_status(),
        }

    def apply_runtime_settings(self) -> None:
        """即时热应用配置。"""
        logger.info("正在应用繁殖与高拟真新配置...")

        pad = float(self.config.get("simulation.boundary_padding", 45.0))
        thigmo = float(self.config.get("behavior.thigmotaxis_weight", 0.55))
        self.movement_system.padding = pad
        self.movement_system.thigmotaxis_weight = thigmo

        self.behavior_system.update_config(self.config.get("behavior", {}))
        self.lifecycle_system.update_config(self.config.get("lifecycle", {}))
        self.reproduction_system.update_config(self.config.get("lifecycle", {}))
        self.needs_system.energy_decay_rate = float(
            self.config.get("creatures.cockroach.energy_decay_rate", 0.6)
        )
        self.ecology_system.max_population = int(
            self.config.get("simulation.max_roaches", 15)
        )

        render_style = str(self.config.get("render.style", "realistic"))
        shadow_intensity = float(self.config.get("render.shadow_intensity", 0.42))
        Cockroach.show_teneral = bool(self.config.get("render.show_teneral", True))

        for e in self.entities:
            if isinstance(e, Cockroach):
                e.render_style = render_style
                e.shadow_intensity = shadow_intensity

        # 互动与氛围：痕迹、音效、抓取
        self.overlay.set_trails_enabled(bool(self.config.get("trails.enabled", True)))
        self.overlay.set_trails_fade(float(self.config.get("trails.fade_seconds", 6.0)))
        self.overlay.drag_enabled = bool(self.config.get("interaction.drag_enabled", True))
        self.audio.apply_config(
            enabled=bool(self.config.get("audio.enabled", True)),
            volume=float(self.config.get("audio.volume", 0.45)),
            max_per_second=int(self.config.get("audio.max_per_second", 18)),
        )
        self.grab_voice.apply_config(
            enabled=bool(self.config.get("grab_voice.enabled", True)),
            volume=float(self.config.get("grab_voice.volume", 0.7)),
            stop_on_release=bool(self.config.get("grab_voice.stop_on_release", False)),
        )
        self.squash_voice.apply_config(
            enabled=bool(self.config.get("squash_voice.enabled", True)),
            volume=float(self.config.get("squash_voice.volume", 0.75)),
        )
        self.squash_voice.set_variant(
            str(self.config.get("squash_voice.variant", "myinstants"))
        )

        # 苍蝇拍：判径 / 冷却 / 闲置收拍 / 爆浆量
        self.swatter.apply_config(self.config.get("swatter", {}))
        self.splatter.screen_shake = bool(self.config.get("swatter.screen_shake", True))
        if not self.swatter.enabled:
            self.swatter.disarm("disabled")

        new_fps = int(self.config.get("app.target_fps", 60))
        if new_fps != self.target_fps:
            self.target_fps = new_fps
            self._budget_ms = 1000.0 / max(15, self.target_fps)
            self._fps_cap = self.target_fps
            interval_ms = max(1, int(1000.0 / self.target_fps))
            self.timer.setInterval(interval_ms)
        self.unlimited_quality = bool(self.config.get("performance.fixed_quality", False))
        self.min_fps = int(self.config.get("performance.min_fps", 30))
        if not self.unlimited_quality:
            # 允许自适应器重新从最精细档试起，否则改了一次设置就永远停在低画质
            self._quality = 2
            self.reset_frame_governor()

        always_top = bool(self.config.get("window.always_on_top", True))
        from PySide6.QtCore import Qt
        self.overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always_top)
        self.overlay.show()

        # 热键可能被改过：重新注册（失败的会写进 failures，控制台会展示原因）
        self.register_hotkeys()

    def quit(self) -> None:
        logger.info("正在退出 roachHub...")
        self.swatter.disarm("quit")
        self.timer.stop()
        self.audio.stop_all()
        self.grab_voice.stop()
        self.squash_voice.close()
        if self._settings_window:
            self._settings_window.close()
        self.overlay.close()
        self.tray.hide()
        self.qapp.quit()

    def _on_entity_clicked(self, entity: BaseEntity, click_pos: Vector2D) -> None:
        logger.info("命中实体: %s (%s)", entity.id, entity.__class__.__name__)

    def _on_grab_started(self, entity: BaseEntity) -> None:
        """捏住的那一瞬间出声 —— 这是唯一一个"用户主动触发"的音效。"""
        if not self.audio.enabled:  # 托盘总静音时连它一起压掉
            return
        self.grab_voice.play()

    def preview_grab_voice(self) -> None:
        """设置面板的「试听」：用面板上当前（未必已保存）的音量播一遍。"""
        vol = None
        if self._settings_window is not None:
            vol = float(self._settings_window.spin_voice_volume.value())
        self.grab_voice.preview(vol)

    def preview_squash_voice(self) -> None:
        """试听拍死音效：连播三个变体，让用户一次听出"三只一起死"的区别。"""
        vol = 0.75
        if self._settings_window is not None:
            vol = float(self._settings_window.spin_squash_volume.value())
        self.squash_voice.preview(vol)

    def _on_entity_dropped(self, entity: BaseEntity) -> None:
        logger.debug("松手释放实体: %s", entity.id)
        # 整句「你干嘛~哎呦」有 3.45 秒，而实际拖拽通常不到 1 秒 —— 松手就掐
        # 等于后半句永远听不见。默认让它说完，想要利落感的在设置里打开"松手即停"。
        self.grab_voice.release()

    # ---------------------------------------------------------- 氛围：痕迹与音效

    def _on_footstep(
        self, wx: float, wy: float, side: int, strength: float, tone: float
    ) -> None:
        """脚掌落地：落一枚足印，并按距离播放极轻的一声。"""
        self.overlay.add_footprint(wx, wy, strength, tone)
        if not self.audio.enabled or not self.audio.available:
            return
        dist = self._cursor_pos.distance_to(Vector2D(wx, wy))
        # 只让"注意力范围内"的个体发声，同时稀疏采样，避免 150 只时糊成噪声
        if dist < 620.0 and random.random() < 0.55:
            self.audio.play("step", ScuttleAudio.step_volume(dist, strength))

    def _on_smear(self, wx: float, wy: float, angle: float, length: float) -> None:
        self.overlay.add_smear(wx, wy, angle, length)

    def _on_sound(self, kind: str, strength: float, roach: BaseEntity) -> None:
        """事件音效：受惊 / 咬食 / 被抓 / 落地。"""
        if not self.audio.enabled or not self.audio.available:
            return
        dist = self._cursor_pos.distance_to(roach.pos)
        prox = max(0.12, min(1.0, 1.0 - dist / 900.0))
        if kind == "munch":
            self.audio.play("munch", prox * 0.28 * strength)
        elif kind == "rustle":
            self.audio.play("rustle", prox * 0.34 * strength)
        else:  # grab / drop / limp
            self.audio.play("thud", prox * 0.30 * strength)

    def clear_trails(self) -> None:
        self.overlay.clear_trails()
        logger.info("已擦掉桌面痕迹")

    def toggle_audio(self) -> bool:
        enabled = self.audio.toggle_mute()
        self.config.set("audio.enabled", enabled)
        self.config.save()
        if not enabled:
            # 用户按了静音就该真的安静，正在响的那句也要掐掉
            self.grab_voice.stop()
        logger.info("音效: %s", "开启" if enabled else "静音")
        return enabled

    def _govern_frame(self, raw_ms: float) -> None:
        """帧预算自适应：先降画质，再降帧率，永远不让事件循环饱和。

        :param raw_ms: 距上一次 tick 的真实间隔（含上一次的绘制与合成耗时）

        为什么必须有这一层：Windows 上"卡死"不等于 CPU 100%，而是**窗口消息排不满**。
        定时器按 16.7ms 排，而一帧要 28ms —— 每次 tick 都晚点，消息队列里永远压着
        重绘，于是拖动窗口、点托盘都要等上几百毫秒，观感就是死机。把周期主动放慢到
        比实测帧耗时略长，循环才有空处理输入，这是"不卡死"的关键。

        **两道阈值要分清**：
        * `budget_ms`（=1000/target_fps）是"期望"，达不成就该降画质；
        * `floor_ms`（=1000/min_fps）是"底线"，达不成就必须降帧率。
        早期版本把两者混用，200 只时明明能跑 33ms，却被一路压到 14 FPS。
        """
        if self.unlimited_quality:
            self._quality = int(self.config.get("performance.fixed_quality_level", 2))
            return

        budget = self._budget_ms
        floor_ms = 1000.0 / max(12, self.min_fps)

        # EMA 让单帧抖动（比如刚好拖动了一下窗口）不要立刻触发降档
        self._frame_ema = self._frame_ema * 0.88 + raw_ms * 0.12
        ema = self._frame_ema

        if ema > floor_ms:
            self._over_streak += 1
            self._under_streak = 0
        elif ema < budget * 1.25:
            self._over_streak = 0
            self._under_streak += 1
        else:
            self._over_streak = 0
            self._under_streak = 0

        # 连续 8 帧超过底线 → 先降画质（画质见底后再放慢帧率，但绝不主动低于 min_fps）
        if self._over_streak >= 8:
            self._over_streak = 0
            if self._quality > 0:
                self._quality -= 1
                logger.info("帧耗时 %.1fms 超过 %.0fms 底线 → 画质降到 LOD%d",
                            ema, floor_ms, self._quality)
            else:
                emergency = int(1000.0 / max(ema * 1.12, floor_ms * 1.05))
                if emergency < self._fps_cap:
                    self._fps_cap = max(12, emergency)
                    self.timer.setInterval(max(1, int(1000.0 / self._fps_cap)))
                    logger.warning("帧耗时 %.1fms，画质已见底 → 应急降到 %d FPS",
                                   ema, self._fps_cap)

        # 连续 90 帧（约 2 秒）富余 → 先恢复帧率，再逐档恢复画质
        elif self._under_streak >= 90:
            self._under_streak = 0
            if self._fps_cap < self.target_fps:
                self._fps_cap = min(self.target_fps, int(self._fps_cap * 1.25) + 1)
                self.timer.setInterval(max(1, int(1000.0 / self._fps_cap)))
                logger.info("帧耗时 %.1fms 富余 → 帧率恢复到 %d FPS", ema, self._fps_cap)
            elif self._quality < 2:
                self._quality += 1
                logger.info("帧耗时 %.1fms 富余 → 画质恢复到 LOD%d", ema, self._quality)

        # 帧率跟随实测：把周期放到"比实测略长"，循环才有空处理输入。
        # 上限仍是 target_fps，下限是 min_fps（只有画质见底才会被应急代码突破）。
        if self._quality == 0:
            want = int(1000.0 / max(ema * 1.12, 1.0))
            want = max(self.min_fps, min(self.target_fps, want))
            if want != self._fps_cap and abs(want - self._fps_cap) >= 2:
                self._fps_cap = want
                self.timer.setInterval(max(1, int(1000.0 / want)))

    def performance_snapshot(self) -> dict[str, float]:
        """当前帧耗时/画质/帧率（托盘提示与验收用）。"""
        return {
            "frame_ms": self._frame_ema,
            "budget_ms": self._budget_ms,
            "quality": float(self._quality),
            "fps_cap": float(self._fps_cap),
            "min_fps": float(self.min_fps),
        }

    def _update_tray_counts(self) -> None:
        """托盘统计。**必须限频**：`QSystemTrayIcon.setToolTip` 每次都会走一遍
        shell 的 NOTIFYICON_MODIFY，实测单次 5~30ms —— 每帧调一次直接把帧率砍半。
        这里 0.5 秒一次，且内容不变就不下发。"""
        now = time.perf_counter()
        if now - self._tray_update_at < 0.5:
            return
        females = sum(1 for e in self.entities if isinstance(e, Cockroach) and e.stage == LifeStage.ADULT and e.gender == "female" and e.is_alive)
        males = sum(1 for e in self.entities if isinstance(e, Cockroach) and e.stage == LifeStage.ADULT and e.gender == "male" and e.is_alive)
        nymphs = sum(1 for e in self.entities if isinstance(e, Cockroach) and e.stage == LifeStage.NYMPH and e.is_alive)
        eggs = sum(1 for e in self.entities if isinstance(e, Ootheca) and e.is_alive)
        self._tray_update_at = now
        kills = self.swatter.kills if bool(self.config.get("swatter.count_kills", True)) else 0
        self.tray.update_population_detail(
            females, males, nymphs, eggs, kills=kills
        )

    def _on_tick(self) -> None:
        """主循环。"""
        self._tick_t0 = time.perf_counter()
        now = self._tick_t0
        raw_ms = (now - self._last_tick_time) * 1000.0
        dt = min(now - self._last_tick_time, 0.1)
        self._last_tick_time = now

        self._govern_frame(raw_ms)

        if not self.is_sim_visible:
            return

        qcursor_pos = QCursor.pos()
        cursor_vec = Vector2D(qcursor_pos.x(), qcursor_pos.y())
        self._cursor_pos = cursor_vec

        # 0. 拍子与爆浆推进。放在实体更新之前：这一帧的击杀要**当帧**就出现在
        #    绘制里，否则玩家会觉得"我拍下去了，它过了一帧才死"。
        self.swatter.update(dt, cursor_vec)
        self.splatter.update(dt)

        # 0. 食物生成
        food_interval = float(self.config.get("simulation.food_spawn_interval", 12.0))
        max_foods = int(self.config.get("simulation.max_food_count", 8))
        self._food_timer += dt
        if self._food_timer >= food_interval:
            self._food_timer = 0.0
            cur_foods = sum(1 for e in self.entities if isinstance(e, Food) and e.is_alive)
            if cur_foods < max_foods:
                self.spawn_food()

        # 1. 行为状态机更新 (寻食、游走、分级警戒、梳理触角)
        self.behavior_system.update(self.entities, dt, cursor_pos=cursor_vec)

        # 2. 运动学物理模拟 (趋边性与同类斥力；被抓起的个体由拖拽弹簧接管位移)
        self.movement_system.update(self.entities, dt)

        # 3. 实体自身推进：动画骨架、状态计时器(freeze->panic)、拖拽弹簧、卵鞘孵化钟
        for entity in self.entities:
            if entity.is_alive:
                entity.update(dt)

        # 4. 基础需求代谢
        self.needs_system.update(self.entities, dt)

        # 5. 繁殖系统运转 (自动繁殖 / 轻量有性尾对尾交配产卵)
        max_pop = int(self.config.get("simulation.max_roaches", 15))
        new_oothecas = self.reproduction_system.update(
            entities=self.entities,
            dt=dt,
            max_population=max_pop,
        )
        if new_oothecas:
            self.entities.extend(new_oothecas)

        # 6. 生命周期演化 (卵鞘破壳孵化为若虫、若虫蜕皮羽化成虫)
        roach_cfg = self.config.get("creatures.cockroach", {})
        newborn_nymphs = self.lifecycle_system.update(
            entities=self.entities,
            dt=dt,
            roach_config=roach_cfg,
            max_population=max_pop,
        )
        if newborn_nymphs:
            cur_style = str(self.config.get("render.style", "realistic"))
            cur_shadow = float(self.config.get("render.shadow_intensity", 0.42))
            for nb in newborn_nymphs:
                if isinstance(nb, Cockroach):
                    nb.render_style = cur_style
                    nb.shadow_intensity = cur_shadow
                    nb.on_footstep = self._on_footstep
                    nb.on_smear = self._on_smear
                    nb.on_sound = self._on_sound
            self.entities.extend(newborn_nymphs)

        # 7. 生态清理回收死亡实体
        self.entities = self.ecology_system.update(
            self.entities, on_entity_removed=self.behavior_system.cleanup_entity
        )

        # 8. 渲染精度由帧预算自适应器决定（见 _govern_frame）
        #    这里只保留一个"起步猜测"，避免从最精细档一路试错。
        roach_count = sum(1 for e in self.entities if isinstance(e, Cockroach))
        if roach_count > 110:
            self._quality = min(self._quality, 0)
        elif roach_count > 55:
            self._quality = min(self._quality, 1)
        Cockroach.quality = self._quality
        # 拍面网格跟着画质降档（>110 只时用 8 条主筋的简化贴图）
        self.swatter.detail = self._quality

        # 9. 渲染层（痕迹 + 实体）；遮罩只在非 Windows 平台按 4Hz 兜底更新
        self.overlay.tick_trails(dt)
        self.overlay.set_entities(self.entities)
        self.overlay.tick_mask(dt)

        # 10. 更新托盘数据统计（内部限频 2Hz）
        self._update_tray_counts()

        self._tick_t1 = time.perf_counter()
        self._tick_ms = (self._tick_t1 - now) * 1000.0
        self._paints_per_tick = self.overlay._paint_calls - self._paints_seen
        self._paints_seen = self.overlay._paint_calls
