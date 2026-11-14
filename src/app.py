"""主应用程序控制器。"""

from __future__ import annotations
import sys
import time
import random
import logging
from typing import List, Optional

from PySide6.QtCore import QObject, QTimer, QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from .config import Config
from .entities.base_entity import BaseEntity, LifeStage
from .entities.cockroach import Cockroach
from .entities.ootheca import Ootheca
from .entities.food import Food
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
from .systems.voice import GrabVoice
from .utils.math_utils import Vector2D

logger = logging.getLogger("roachHub.app")


class RoachApp(QObject):
    """协调窗口、系统与生物生命周期的总控制器。"""

    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        self.config = Config()

        self.entities: List[BaseEntity] = []
        self.is_sim_visible: bool = True
        self._settings_window: Optional[SettingsWindow] = None
        self._food_timer: float = 0.0
        self._cursor_pos: Vector2D = Vector2D(0.0, 0.0)

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

        self.tray = RoachTrayIcon(
            on_toggle_visible=self.toggle_visibility,
            on_spawn_roach=self.spawn_cockroach,
            on_spawn_food=self.spawn_food,
            on_clear_roaches=self.clear_all,
            on_open_settings=self.open_settings,
            on_quit=self.quit,
            on_clear_trails=self.clear_trails,
            on_toggle_audio=self.toggle_audio,
        )

        self.target_fps = int(self.config.get("app.target_fps", 60))
        self.timer = QTimer(self)
        # 必须用 PreciseTimer：Windows 下默认的 CoarseTimer 会把 33ms 量化成 46.6ms
        # （系统定时器粒度 15.6ms 的整数倍），"30 FPS"实际只有 21 FPS，而且间隔抖动。
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._on_tick)
        self._last_tick_time = time.perf_counter()

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
        logger.info("模拟主循环已启动，当前目标帧率: %d FPS", self.target_fps)

    def spawn_cockroach(
        self,
        x: float | None = None,
        y: float | None = None,
        stage: LifeStage = LifeStage.ADULT,
        gender: Optional[str] = None,
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
        roach.on_footstep = self._on_footstep
        roach.on_smear = self._on_smear
        roach.on_sound = self._on_sound
        self.entities.append(roach)

        logger.info("生成蟑螂: ID=%s (阶段: %s, 性别: %s, 风格: %s)", roach.id, stage.name, gender, render_style)
        self._update_tray_counts()
        return roach

    def spawn_food(self, x: float | None = None, y: float | None = None) -> Food:
        """投放食物碎屑。"""
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

        food = Food(x=x, y=y, nutrition=40.0)
        self.entities.append(food)
        logger.info("投放食物碎屑: ID=%s (坐标: %.1f, %.1f)", food.id, x, y)
        return food

    def clear_all(self) -> None:
        for e in self.entities:
            self.behavior_system.cleanup_entity(e.id)
        self.entities.clear()
        self.overlay.set_entities([])
        self._update_tray_counts()
        logger.info("已清空所有物体")

    def toggle_visibility(self) -> bool:
        self.is_sim_visible = not self.is_sim_visible
        self.overlay.set_simulation_visible(self.is_sim_visible)
        logger.info("切换可见性: %s", "显示" if self.is_sim_visible else "隐藏")
        return self.is_sim_visible

    def open_settings(self) -> None:
        if self._settings_window is None:
            self._settings_window = SettingsWindow(
                config=self.config,
                on_applied=self.apply_runtime_settings,
                on_clear_trails=self.clear_trails,
                on_preview_voice=self.preview_grab_voice,
            )
        self._settings_window.load_from_config()
        self._settings_window.show()
        self._settings_window.activateWindow()
        self._settings_window.raise_()

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
            self._frame_ema = self._budget_ms
            self._over_streak = self._under_streak = 0

        always_top = bool(self.config.get("window.always_on_top", True))
        from PySide6.QtCore import Qt
        self.overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always_top)
        self.overlay.show()

    def quit(self) -> None:
        logger.info("正在退出 roachHub...")
        self.timer.stop()
        self.audio.stop_all()
        self.grab_voice.stop()
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
        self.tray.update_population_detail(females, males, nymphs, eggs)

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
