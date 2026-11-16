"""高真实感写实风格桌面蟑螂实体。

实现高拟真生物渲染、性别形态分化与写实交配体系：
- 程序化六足步态（交替三足 + 两段式 IK）与触须运动链，肢体连续自然；
- 雄/雌形态解剖学差异 (雄虫修长、雌虫腹部宽厚)；
- 真实尾对尾交配 (End-to-End Mating) 抽搐与生物黏液分泌动效；
- 双渲染管线 (写实照片躯干 + 简洁卡通矢量)；
- 接触立体软阴影与状态间姿态平滑过渡。

肢体动画不是逐帧图，而是「速度驱动的程序化骨架」：步态相位推进 -> 足端轨迹
（支撑相钉地后移 / 摆动相抬腿前摆）-> 两段式 IK 求膝点 -> 锥形肢体绘制。
因此 0~430 px/s 任意速度下步幅步频都自动匹配，帧与帧之间天然连贯。
"""

from __future__ import annotations
import math
import random
from typing import Callable, Optional

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QBrush,
    QLinearGradient,
    QRadialGradient,
    QPainterPath,
    QPixmap,
)

from .base_entity import BaseEntity, EntityState, LifeStage
from ..resources import ResourceManager
from ..animation import LegRig, AntennaRig, PoseBlender
from ..animation.mating import MatingRig
from ..utils.math_utils import Vector2D, clamp, angle_diff, normalize_angle

# 姿态通道：(初始值, 平滑时间常数秒)
POSE_SPECS = {
    "stride": (1.0, 0.10),
    "splay": (0.30, 0.16),
    "lift": (1.0, 0.12),
    "tremor": (0.0, 0.07),
    "groom": (0.0, 0.14),
    "hold": (0.0, 0.12),      # 前足抱持食物
    "flail": (0.0, 0.09),     # 被抓住时的原地乱蹬
    "probe": (0.0, 0.10),     # 静止探嗅时的前倾
    "anchored": (0.0, 0.11),
    "dead": (0.0, 0.25),
    "duty": (0.62, 0.18),
    "ant_deploy": (1.0, 0.14),
    "ant_wave": (0.22, 0.14),
    "ant_freq": (7.0, 0.14),
    "ant_curl": (0.0, 0.16),
}

# 所有通道的默认目标：每个状态只需声明自己关心的通道，其余回落到这里。
# 缺少这一步的话，某个状态设过的通道（例如拥抱食物的 hold）在切换到不声明它的
# 状态后会一直保留旧目标 —— 这正是"状态间串味"的常见来源。
BASE_POSE = dict(
    stride=0.15, splay=0.30, lift=1.0, tremor=0.0, groom=0.0, hold=0.0,
    flail=0.0, probe=0.0, anchored=0.92, dead=0.0, duty=0.62,
    ant_deploy=1.0, ant_wave=0.16, ant_freq=4.2, ant_curl=0.0,
)

# 各行为状态的姿态目标
STATE_POSES = {
    EntityState.IDLE:     dict(stride=0.15, splay=0.30, anchored=0.92, duty=0.62,
                               tremor=0.0, groom=0.0, ant_deploy=1.0, ant_wave=0.16, ant_freq=4.2),
    EntityState.WALK:     dict(stride=1.0, splay=0.36, anchored=0.0, duty=0.62,
                               tremor=0.0, groom=0.0, ant_deploy=1.0, ant_wave=0.23, ant_freq=7.6),
    EntityState.PANIC:    dict(stride=1.65, splay=0.52, anchored=0.0, duty=0.50,
                               tremor=0.18, groom=0.0, ant_deploy=0.35, ant_wave=0.30, ant_freq=22.0),
    EntityState.FREEZE:   dict(stride=0.0, splay=1.0, anchored=1.0, duty=0.62,
                               tremor=0.05, groom=0.0, ant_deploy=0.25, ant_wave=0.05, ant_freq=26.0),
    EntityState.GROOMING: dict(stride=0.0, splay=0.22, anchored=1.0, duty=0.62,
                               tremor=0.0, groom=1.0, ant_deploy=0.8, ant_wave=0.28, ant_freq=8.5),
    EntityState.EAT:      dict(stride=0.0, splay=0.26, anchored=1.0, duty=0.62,
                               tremor=0.02, groom=0.0, hold=1.0,
                               ant_deploy=0.9, ant_wave=0.18, ant_freq=6.0),
    EntityState.MOLTING:  dict(stride=0.0, splay=0.75, anchored=1.0, duty=0.62,
                               tremor=0.12, groom=0.0, ant_deploy=0.6, ant_wave=0.20, ant_freq=5.0),
    EntityState.MATING:   dict(stride=0.0, splay=0.48, anchored=1.0, duty=0.62,
                               tremor=0.45, groom=0.0, ant_deploy=0.4, ant_wave=0.30, ant_freq=13.0),
    # 被手指/鼠标捏住：六足腾空乱蹬，触须狂扫，腹部抽搐
    EntityState.DRAGGED:  dict(stride=0.9, splay=0.85, anchored=0.0, duty=0.55,
                               tremor=0.5, groom=0.0, flail=1.0,
                               ant_deploy=0.30, ant_wave=0.48, ant_freq=30.0),
    EntityState.DEAD:     dict(stride=0.0, splay=0.0, anchored=1.0, duty=0.62,
                               tremor=0.0, groom=0.0, dead=1.0, ant_deploy=0.3, ant_wave=0.05, ant_freq=1.0),
}

# 被抓住后持续这么久的挣扎，会转入"装死"
PLAYDEAD_HOLD_TIME = 6.0
# 松手后先瘫软这么久才逃
PLAYDEAD_RELEASE_LIMP = 0.9

# 刚蜕壳时的"嫩壳期"(teneral)：新角质层还没硬化、色素也没沉积，整只虫子是
# 奶白半透明的。这里 2.2s 是纯白阶段，之后再用 1.4s 渐变回本来颜色 ——
# 真实蟑螂也是几小时内慢慢变深的，只是桌面上按比例压缩成几秒。
MOLT_WHITE_SECONDS = 2.2
TENERAL_FADE_SECONDS = 1.4

PALETTE_REALISTIC = {
    "leg": QColor(118, 66, 36),
    "joint": QColor(163, 108, 66),
    "spine": QColor(28, 13, 7),
    "antenna": QColor(58, 26, 13),
}

PALETTE_SIMPLE = {
    "leg": QColor(122, 72, 36),
    "joint": QColor(152, 98, 56),
    "spine": QColor(70, 34, 16),
    "antenna": QColor(112, 62, 26),
}


class Cockroach(BaseEntity):
    """高拟真桌面蟑螂实体。"""

    # 接触阴影预渲染缓存：径向渐变逐帧重建开销很大，这里做成一次性的贴图
    _shadow_pixmap: Optional[QPixmap] = None

    # 渲染质量档位（0=极简 1=标准 2=精细），由主循环按同屏数量动态调整
    quality: int = 2

    # 是否展示"刚蜕皮的奶白嫩壳"（设置里可关；关掉后蜕皮不再变白）
    show_teneral: bool = True

    def __init__(
        self,
        x: float = 0.0,
        y: float = 0.0,
        stage: LifeStage = LifeStage.ADULT,
        gender: str = "female",
        instar: int = 1,
        max_instars: int = 4,
        config: Optional[dict] = None,
        render_style: str = "realistic",
        shadow_intensity: float = 0.42,
    ) -> None:
        cfg = config or {}
        base_len = float(cfg.get("base_length", 72.0))
        base_wid = float(cfg.get("base_width", 38.0))
        scale_var = float(cfg.get("scale_variation", 0.15))

        # 性别解剖学差异：雄虫相对较窄长，雌虫腹部宽大饱满（用于容纳卵鞘）
        if gender == "male":
            base_wid *= 0.93
            base_len *= 0.98
        else:
            base_wid *= 1.06
            base_len *= 1.02

        initial_scale = 1.0 + random.uniform(-scale_var, scale_var)

        super().__init__(
            x=x,
            y=y,
            base_width=base_wid,
            base_height=base_len,
            scale=initial_scale,
        )

        self.stage = stage
        self.gender = gender  # "male" 或 "female"
        self.instar = instar
        self.max_instars = max_instars
        self.render_style = render_style
        self.shadow_intensity = shadow_intensity

        # 交配与繁殖状态
        self.mating_partner_id: Optional[str] = None
        self.mating_duration: float = 2.2
        self.mating_progress: float = 0.0
        # 交配动作（挺动相位 / 腹部挤压 / 心形粒子）。雄雌各一个，
        # 参数按性别取，粒子只由雄虫生成（两头都生会叠成双倍亮度）。
        self._mating_rig = MatingRig()
        # 求偶驱力目标点：由繁殖系统写入，行为层消费（见 behavior.py）。
        # 求偶必须能被"看见"——雄虫循着雌虫的气味走过去，而不是凭空受孕。
        self.mate_seek: Optional[Vector2D] = None

        # 机动性
        speed_ratio = random.uniform(0.9, 1.15)
        self.min_walk_speed = float(cfg.get("min_walk_speed", 65.0)) * speed_ratio
        self.max_walk_speed = float(cfg.get("max_walk_speed", 120.0)) * speed_ratio
        self.panic_speed = float(cfg.get("panic_speed", 430.0)) * speed_ratio
        self.panic_duration = float(cfg.get("panic_duration", 2.4))
        self.turn_rate = float(cfg.get("normal_turn_rate", 3.8))
        self.panic_turn_rate = float(cfg.get("panic_turn_rate", 14.0))
        self.leg_frequency = float(cfg.get("leg_swing_frequency", 17.0))

        self.angle = random.uniform(-math.pi, math.pi)
        self.target_angle = self.angle
        self.state = EntityState.WALK
        self.target_speed = random.uniform(self.min_walk_speed, self.max_walk_speed)
        self.current_speed = self.target_speed

        # ---- 程序化肢体动画 ----
        self.pose = PoseBlender(POSE_SPECS)
        self.body_len: float = self.base_height * 0.98
        self.body_w: float = self.body_len / 1.85
        self._has_body_sprite: bool = False
        self._aim_l: float = -math.pi / 2.0 - 0.26
        self._aim_r: float = -math.pi / 2.0 + 0.26
        # ---- 蜕皮（嫩壳）----
        # 1 = 完全是奶白色，0 = 已恢复本来体色。molt_timer 走完之后由这个
        # 计时器接着把白化"化"开，避免贴图硬切带来的跳变。
        self.teneral_timer: float = 0.0

        self._anim_clock: float = random.uniform(0.0, 100.0)
        self._setup_rig()

        # ---- 交互与氛围钩子（由 app 注入，缺省时全部为空操作）----
        self.on_footstep: Optional[Callable[..., None]] = None   # 脚掌落地 -> 痕迹层
        self.on_smear: Optional[Callable[..., None]] = None      # 腹部蹭过 -> 油渍
        self.on_sound: Optional[Callable[..., None]] = None      # 事件音效

        # ---- 光标嗅探 ----
        self.threat_proximity: float = 0.0   # 0=很远 1=贴脸，仅影响触须跟随权重
        self.prestartle_timer: float = 0.0   # 触须后收后、身体僵直前的 0.1s 预警
        self._prestartle_source: Optional[Vector2D] = None
        self._probe_timer: float = 0.0       # 静止探嗅微动作剩余时长

        # ---- 抓取与挣扎 ----
        self.drag_active: bool = False
        self.drag_target: Vector2D = Vector2D(x, y)
        self._drag_vel: list[float] = [0.0, 0.0]
        self._drag_hold: float = 0.0         # 被捏住的累计时长
        self._playdead: bool = False
        self._release_limp: float = 0.0
        self._splay_boost: float = 0.0

        # ---- 进食 ----
        self.eating_food: Optional[object] = None
        self.bite_timer: float = 0.0
        self._feed_pulse: float = 0.0

        # ---- 痕迹 ----
        self._smear_timer: float = random.uniform(0.8, 3.0)

        self._prev_state: EntityState = self.state

        self._refresh_appearance_by_stage()

    # ------------------------------------------------------------------ 骨架

    def _current_body_sprite_name(self) -> str:
        # 刚蜕壳：奶白半透明的新角质层（真实蟑螂蜕皮后就是这样，几小时后才变深）
        if self.molt_timer > 0 and Cockroach.show_teneral:
            return "cockroach_white_body"
        if self.stage == LifeStage.NYMPH:
            return "cockroach_nymph_body"
        return "cockroach_adult_body"

    def _setup_rig(self) -> None:
        """按躯干贴图的长宽比确定体长/体宽，使骨架与照片躯干尺寸一致。"""
        pix = ResourceManager.get_sprite(self._current_body_sprite_name())
        self._has_body_sprite = bool(pix and not pix.isNull())
        aspect = (pix.width() / pix.height()) if self._has_body_sprite else (1.0 / 1.85)
        self.body_len = self.base_height * 0.98
        self.body_w = self.body_len * aspect

        leg_scale = self.individual_leg_scale
        self.leg_rig = LegRig(self.body_len, self.body_w, leg_scale=leg_scale)
        self.antenna_rig = AntennaRig(self.body_len, self.body_w)

    def _refresh_appearance_by_stage(self) -> None:
        if self.stage == LifeStage.NYMPH:
            growth_ratio = (self.instar - 1) / max(1, self.max_instars - 1)
            self.scale = 0.55 + growth_ratio * 0.40
        else:
            self.scale = 1.0 + random.uniform(-0.05, 0.05)
        self._setup_rig()

    def advance_instar(self) -> None:
        if self.stage == LifeStage.NYMPH:
            if self.instar < self.max_instars:
                self.instar += 1
            else:
                self.stage = LifeStage.ADULT

            self.instar_age = 0.0
            self.molt_timer = MOLT_WHITE_SECONDS
            # 白化计时比 molt_timer 长一个渐变段：贴图切回"正常色"的那一刻，
            # 颜色是从奶白平滑化开的，而不是"啪"一下变棕。
            self.teneral_timer = MOLT_WHITE_SECONDS + TENERAL_FADE_SECONDS
            self.state = EntityState.MOLTING
            self.target_speed = 0.0
            self._refresh_appearance_by_stage()

    @property
    def teneral_alpha(self) -> float:
        """当前"奶白嫩壳"的浓度：1=全白，0=完全恢复本色。"""
        if not Cockroach.show_teneral:
            return 0.0
        if self.molt_timer > 0:
            return 1.0
        if self.teneral_timer <= 0.0:
            return 0.0
        a = min(1.0, self.teneral_timer / TENERAL_FADE_SECONDS)
        return a * a * (3.0 - 2.0 * a)   # smoothstep：两头更稳，中间化得快

    def start_grooming(self, duration: float = 2.0) -> None:
        self.state = EntityState.GROOMING
        self.grooming_timer = duration
        self.grooming_target = random.choice(["left", "right"])
        self.target_speed = 0.0

    def start_mating(self, partner_id: str, duration: float = 2.2) -> None:
        """进入真实的尾对尾交配状态。"""
        self.state = EntityState.MATING
        self.mating_partner_id = partner_id
        self.mating_duration = duration
        self.mating_progress = 0.0
        self.mate_seek = None
        self.target_speed = 0.0

    def finish_mating(self) -> None:
        """完成交尾，恢复正常状态。"""
        self.state = EntityState.WALK
        self.mating_partner_id = None
        self.mating_progress = 0.0
        self.mate_seek = None
        self.target_speed = self.min_walk_speed

    def interrupt_mating(self) -> None:
        """交尾受惊被打断。

        关键：必须把状态放回行走。只清 partner_id 而不动 state 的话，雌虫会
        永远停在 MATING —— 姿态是抽搐的定妆照、繁殖循环也再进不去（判定用的
        是 state == MATING，而 partner 已经没了），等于整只虫报废。
        """
        self.mating_partner_id = None
        self.mating_progress = 0.0
        self.mate_seek = None
        if self.state == EntityState.MATING:
            self.state = EntityState.WALK
            self.target_speed = self.min_walk_speed

    # ------------------------------------------------------------------ 动画更新

    def update(self, dt: float) -> None:
        super().update(dt)

        if self.teneral_timer > 0.0:
            self.teneral_timer = max(0.0, self.teneral_timer - dt)

        if self.state == EntityState.MATING:
            self.mating_progress += dt
            # 结合处（尾端）的世界坐标，交给动作层撒心形 —— 心形要在世界坐标里飘，
            # 不能挂在会随身体旋转的局部坐标上
            tail_x = self.pos.x - math.cos(self.angle) * self.body_len * 0.46 * self.scale
            tail_y = self.pos.y - math.sin(self.angle) * self.body_len * 0.46 * self.scale
            self._mating_rig.update(
                dt,
                self.mating_progress / max(1e-3, self.mating_duration),
                self.mating_duration,
                self.gender,
                junction=(tail_x, tail_y),
            )
        elif self._mating_rig.active:
            # 打完/被打断：连粒子一起清掉，别让心形飘到下一段人生里
            self._mating_rig.reset()

        self._anim_clock += dt
        self._tick_interaction(dt)

        # 1. 状态 -> 姿态目标，交给平滑器过渡（避免状态切换时肢体瞬移）
        target = dict(BASE_POSE)
        target.update(STATE_POSES.get(self.state, STATE_POSES[EntityState.WALK]))
        if self.molt_timer > 0 and self.state != EntityState.MOLTING:
            target.update(STATE_POSES[EntityState.MOLTING])
        # 被抓太久后的"装死"：全身瘫软，只在松手后短暂保持
        if self.state == EntityState.DRAGGED and (self._playdead or self._release_limp > 0):
            target.update(dict(dead=0.55, flail=0.0, tremor=0.04, splay=0.10,
                               ant_deploy=0.12, ant_wave=0.04, ant_freq=2.0))
        # 体力衰弱时步态变小、身体压得更低
        if self.energy < 25.0:
            target["splay"] += 0.18
            target["stride"] *= 0.8
        # 松手落地瞬间六足外展
        if self._splay_boost > 0.0:
            target["splay"] += 0.55 * self._splay_boost
        # 静止探嗅：触须频率抬高、身体前倾
        if self._probe_timer > 0.0:
            target["probe"] = 1.0
            target["ant_freq"] = max(target["ant_freq"], 6.8)
            target["ant_wave"] = max(target["ant_wave"], 0.26)
        # 交配：让律动穿透姿态层 —— 六足随拍轻颤、重心随挤压下沉、
        # 触须高频后扫。只改姿态通道，不改坐标，所以不影响配对判定。
        if self.state == EntityState.MATING:
            rig = self._mating_rig
            beat = math.sin(rig.phase)
            target["tremor"] = 0.28 + 0.26 * rig.energy
            target["splay"] = 0.44 + 0.08 * rig.energy * (0.5 + 0.5 * beat)
            target["anchored"] = 1.0
            target["ant_freq"] = 11.0 + 8.0 * rig.energy
            target["ant_wave"] = 0.28 + 0.10 * rig.energy
        self.pose.set_target(target)
        self.pose.step(dt)

        # 2. 触须朝向
        self._update_antennae_aim(dt)

        # 3. 步态骨架与触须链
        self.leg_rig.update(dt, self.current_speed, self.pose)
        self.antenna_rig.update(
            dt,
            self._aim_l,
            self._aim_r,
            wave_amp=self.pose.value("ant_wave"),
            freq=self.pose.value("ant_freq"),
            curl=self.pose.value("ant_curl"),
        )

        # 4. 足迹 / 油渍 / 事件音效
        self._emit_trails(dt)
        if self.state == EntityState.PANIC and self._prev_state != EntityState.PANIC:
            self._emit_sound("rustle", 0.4 + 0.6 * self.fear_level)
        self._prev_state = self.state

    # ------------------------------------------------------- 交互（抓/嗅/食）

    def _tick_interaction(self, dt: float) -> None:
        """拖拽弹簧、预警计时、探嗅计时、进食释放等与行为状态机无关的推进。"""
        # 1. 被抓在手上：临界阻尼弹簧跟随（硬绑定会像拖图标，弹一下才像活物）
        if self.drag_active:
            self._drag_hold += dt
            k = 260.0
            c = 2.0 * math.sqrt(k)
            ax = k * (self.drag_target.x - self.pos.x) - c * self._drag_vel[0]
            ay = k * (self.drag_target.y - self.pos.y) - c * self._drag_vel[1]
            self._drag_vel[0] += ax * dt
            self._drag_vel[1] += ay * dt
            self.pos.x += self._drag_vel[0] * dt
            self.pos.y += self._drag_vel[1] * dt
            self.current_speed = 0.0
            self.velocity = Vector2D(0.0, 0.0)
            # 被拎着甩动时身体朝向跟着晃动方向
            if self._drag_vel[0] * self._drag_vel[0] + self._drag_vel[1] * self._drag_vel[1] > 900.0:
                self.target_angle = math.atan2(self._drag_vel[1], self._drag_vel[0])
            if self._drag_hold > PLAYDEAD_HOLD_TIME and not self._playdead:
                self._playdead = True
                self._emit_sound("limp", 0.7)

        # 2. 松手后从装死中恢复
        elif self._release_limp > 0.0:
            self._release_limp -= dt
            if self._release_limp <= 0.0:
                self._release_limp = 0.0
                self.trigger_freeze_and_panic(
                    self.pos - Vector2D(math.cos(self.angle), math.sin(self.angle)) * 40.0,
                    duration=self.panic_duration,
                )

        # 3. 触须后收 -> 僵直 -> 逃窜 的 0.12s 预警窗口
        if self.prestartle_timer > 0.0:
            self.prestartle_timer -= dt
            if self.prestartle_timer <= 0.0:
                src = self._prestartle_source
                self._prestartle_source = None
                if src is not None and self.state not in (EntityState.DRAGGED, EntityState.PANIC):
                    self.trigger_freeze_and_panic(src, duration=self.panic_duration)

        if self._probe_timer > 0.0:
            self._probe_timer = max(0.0, self._probe_timer - dt)
        if self._splay_boost > 0.0:
            self._splay_boost = max(0.0, self._splay_boost - dt / 0.2)
        if self._feed_pulse > 0.0:
            self._feed_pulse = max(0.0, self._feed_pulse - dt * 2.2)

        # 4. 离开进食状态就释放食物位点，避免"占着不吃"
        if self.state != EntityState.EAT and self.eating_food is not None:
            try:
                self.eating_food.release(self.id)
            except Exception:
                pass
            self.eating_food = None

    def begin_drag(self, grab_pos: Vector2D) -> None:
        """被鼠标捏住：进入 DRAGGED，六足开始乱蹬。"""
        self.state = EntityState.DRAGGED
        self.drag_active = True
        self.drag_target = Vector2D(self.pos.x, self.pos.y)
        self._drag_vel = [0.0, 0.0]
        self._drag_hold = 0.0
        self._playdead = False
        self._release_limp = 0.0
        self.prestartle_timer = 0.0
        self._prestartle_source = None
        self.target_speed = 0.0
        self.current_speed = 0.0
        self.alertness = min(1.0, self.alertness + 0.3)
        self._emit_sound("grab", 0.6)

    def drag_to(self, pos: Vector2D) -> None:
        if self.drag_active:
            self.drag_target = Vector2D(pos.x, pos.y)

    def trigger_freeze_and_panic(self, source_pos: Vector2D, duration: float = 2.4) -> None:
        """软壳期覆盖：被碰一下只警觉，不僵直、不狂逃。

        真实蟑螂蜕皮后几小时内是"软壳"，连爬动都费劲；这里如果照常惊慌狂逃，
        就会出现"白色软壳虫以 430px/s 冲刺"的穿帮画面。
        """
        if self.molt_timer > 0.0:
            self.threat_vector = source_pos - self.pos
            self.alertness = min(1.0, self.alertness + 0.45)
            self.target_speed = 0.0
            return
        super().trigger_freeze_and_panic(source_pos, duration)

    def end_drag(self) -> None:
        """松手：挣扎过的立刻逃，装死过的先瘫一会儿再逃。"""
        was_playing_dead = self._playdead
        self.drag_active = False
        self._drag_vel = [0.0, 0.0]
        self._drag_hold = 0.0
        self._playdead = False
        self.alertness = min(1.0, self.alertness + 0.5)
        self._splay_boost = 1.0
        self._emit_sound("drop", 0.8)
        if was_playing_dead:
            self._release_limp = PLAYDEAD_RELEASE_LIMP
            self.state = EntityState.DRAGGED
            return
        if self.molt_timer > 0.0:
            # 软壳：被放回桌面只能瘫着缓一缓，不能 430px/s 冲刺
            self.state = EntityState.MOLTING
            self.target_speed = 0.0
            self.current_speed = 0.0
            return
        self.panic_timer = max(self.panic_timer, 1.5)
        self.state = EntityState.PANIC
        self.target_angle = normalize_angle(self.angle + random.uniform(-0.5, 0.5))
        self.target_speed = self.panic_speed
        self.current_speed = max(self.current_speed, self.panic_speed * 0.55)

    def begin_prestartle(self, source: Vector2D, delay: float = 0.12) -> None:
        """先收触须，再僵直 —— 活物的顺序不是同时发生的。"""
        if self.prestartle_timer > 0.0 or self.drag_active:
            return
        # 刚蜕壳的个体是软壳，像刚拆线的人一样动不了：不预警、不僵直、不逃。
        if self.molt_timer > 0.0:
            return
        self.prestartle_timer = max(0.05, delay)
        self._prestartle_source = Vector2D(source.x, source.y)
        self.threat_vector = source - self.pos
        self.alertness = min(1.0, self.alertness + 0.25)
        self.target_speed = 0.0

    def start_probe(self, duration: float = 0.45) -> None:
        """静止时的"探一探"：身体前倾、触须加密扫探。"""
        self._probe_timer = max(self._probe_timer, duration)

    def bite(self) -> None:
        """咬下一口：腹部收缩脉冲 + 咀嚼声。"""
        self._feed_pulse = 1.0
        self._emit_sound("munch", 0.55)

    def _emit_sound(self, kind: str, strength: float) -> None:
        if self.on_sound is not None:
            self.on_sound(kind, strength, self)

    def _local_to_world(self, lx: float, ly: float) -> tuple[float, float]:
        """体坐标 -> 屏幕坐标（必须与 render() 的变换链完全一致）。"""
        deg = math.radians(self.angle) + math.pi / 2.0 + math.radians(self.leg_rig.body_yaw)
        cos_d, sin_d = math.cos(deg), math.sin(deg)
        ox = self.leg_rig.body_sway + lx * self.scale
        oy = self.leg_rig.body_bob + ly * self.scale
        return (self.pos.x + ox * cos_d - oy * sin_d,
                self.pos.y + ox * sin_d + oy * cos_d)

    def _emit_trails(self, dt: float) -> None:
        """把落地事件与腹部蹭痕交给痕迹层。"""
        if self.state == EntityState.DRAGGED:
            return
        if self.on_footstep is not None:
            for lx, ly, side, strength in self.leg_rig.plant_events:
                wx, wy = self._local_to_world(lx, ly)
                self.on_footstep(wx, wy, side, strength, self.individual_tone)
        if self.on_smear is not None and self.current_speed > 26.0:
            self._smear_timer -= dt
            if self._smear_timer <= 0.0:
                self._smear_timer = random.uniform(1.4, 3.2)
                wx, wy = self._local_to_world(0.0, self.body_len * 0.16)
                self.on_smear(wx, wy, self.angle, self.body_len * self.scale * 0.5)

    def _update_antennae_aim(self, dt: float) -> None:
        clock = self._anim_clock
        state = self.state
        deploy = self.pose.value("ant_deploy", 1.0)

        # 基础朝向：正前方 (-y) 两侧张开
        spread = 0.22 + 0.16 * deploy
        aim_l = -math.pi / 2.0 - spread
        aim_r = -math.pi / 2.0 + spread

        # 静止时缓慢左右扫探
        wander = math.sin(clock * 0.9) * 0.16 + math.sin(clock * 2.3 + 1.1) * 0.06
        aim_l += wander
        aim_r += wander * 0.85

        # 威胁方位：越近、越警觉，触须越直接指向刺激源。
        # 两根触须分开 ±0.22 rad 追踪，形成"双侧定位"的立体感；
        # 即使光标很远也保留 0.22 的基底权重 —— 桌上任何一点动静它都能察觉到。
        if self.threat_vector is not None and self.threat_vector.length_squared() > 1.0:
            rel = angle_diff(self.angle, self.threat_vector.angle())
            threat_aim = math.atan2(-math.cos(rel), math.sin(rel))
            w = clamp(0.30 + 0.70 * self.threat_proximity + 0.25 * self.alertness, 0.0, 0.97)
            aim_l = _lerp_angle(aim_l, threat_aim - 0.22, w)
            aim_r = _lerp_angle(aim_r, threat_aim + 0.22, w)

        if self.prestartle_timer > 0.0:
            # 预警窗口：触须在 ~0.1s 内急速后收并绷直，身体还来不及动
            aim_l = _lerp_angle(aim_l, 1.72, 0.9)
            aim_r = _lerp_angle(aim_r, 1.42, 0.9)
        elif state in (EntityState.PANIC, EntityState.FREEZE):
            # 受惊时触须后伏并高频颤动
            aim_l = _lerp_angle(aim_l, 1.75, 0.85)
            aim_r = _lerp_angle(aim_r, 1.39, 0.85)
        elif state == EntityState.DRAGGED:
            # 被抓：两根触须各自狂扫，方向不再一致
            aim_l = _lerp_angle(aim_l, -0.2 + math.sin(clock * 17.0) * 1.1, 0.75)
            aim_r = _lerp_angle(aim_r, 0.3 + math.sin(clock * 13.0 + 2.1) * 1.1, 0.75)
        elif state == EntityState.MATING:
            aim_l = _lerp_angle(aim_l, 1.55, 0.7)
            aim_r = _lerp_angle(aim_r, 1.25, 0.7)
        elif state == EntityState.EAT:
            # 进食时触须前伸搭在食物上，低频轻扫
            aim_l = _lerp_angle(aim_l, -math.pi / 2.0 - 0.34, 0.72)
            aim_r = _lerp_angle(aim_r, -math.pi / 2.0 + 0.34, 0.72)
        elif state == EntityState.GROOMING:
            # 被梳理的一侧触须向下弯到前足之间
            curl = 1.0 if self.grooming_target == "left" else 0.0
            aim_l = _lerp_angle(aim_l, -0.55, 0.85) if curl else aim_l
            aim_r = _lerp_angle(aim_r, 0.55, 0.85) if not curl else aim_r
            self.pose.set_target({"ant_curl": 0.55})
        elif state == EntityState.DEAD:
            aim_l = _lerp_angle(aim_l, 1.6, 0.9)
            aim_r = _lerp_angle(aim_r, 1.54, 0.9)

        # 行为层用高频噪声模拟触角末端的随机抖动（真实蟑螂从不完全静止）
        jitter = 0.03 if state not in (EntityState.FREEZE, EntityState.DEAD) else 0.0
        self._aim_l = normalize_angle(aim_l + random.uniform(-jitter, jitter))
        self._aim_r = normalize_angle(aim_r + random.uniform(-jitter, jitter))

    # ------------------------------------------------------------------ 渲染

    def render(self, painter: QPainter) -> None:
        painter.save()

        painter.translate(self.pos.x, self.pos.y)
        deg = math.degrees(self.angle) + 90.0 + self.leg_rig.body_yaw
        painter.rotate(deg)
        # 步态造成的横向摆与轻微起伏；探嗅时整个身体向前探一点
        probe = self.pose.value("probe")
        # 交配时的挺动：沿体轴（局部 +y = 尾部朝向对方）前后位移。
        # 这只是绘制位移，不改 self.pos —— 配对距离、点击命中都还按真实坐标算。
        mating_push = self._mating_rig.push if self.state == EntityState.MATING else 0.0
        painter.translate(self.leg_rig.body_sway,
                          self.leg_rig.body_bob - 1.5 * probe + mating_push)
        # 再绕尾端扭摆：尾部是锁合点，所以身体是"绕着接点摆"，不是整体平移。
        # 1.8° 在 70px 体长上让头部划出 ±2px 的弧，30fps 下能看清是在用力。
        if self.state == EntityState.MATING:
            tail_y = self.body_len * 0.46
            painter.translate(0.0, tail_y)
            painter.rotate(self._mating_rig.twist)
            painter.translate(0.0, -tail_y)
        painter.scale(self.scale, self.scale)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        realistic = self.render_style == "realistic"
        palette = dict(PALETTE_REALISTIC if realistic else PALETTE_SIMPLE)
        palette["tone"] = self.individual_tone
        detail = 2 if self.scale > 0.72 else 1
        quality = Cockroach.quality
        if quality < 2:
            detail = min(detail, 1)
        simple_legs = (not realistic) or quality == 0

        # 极简档（>110 只才启用）：六足与触须是 1~2px 的细线，抗锯齿描边的开销
        # 是全帧最大的单项（200 只时约 9ms/帧）。这里只对矢量肢体关掉 AA，
        # 躯干贴图仍保留 SmoothPixmapTransform —— 旋转的照片贴图一旦最近邻采样
        # 边缘会明显锯齿跳动，那个视觉损失比细线锯齿大得多。
        vector_aa = quality > 0
        if not vector_aa:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 1. 真实接触柔和阴影
        if self.shadow_intensity > 0.02 and quality > 0:
            self._render_contact_shadow(painter)

        # 2. 六足（覆盖在躯干下缘，先画则被躯干压住基节，符合俯视遮挡关系）
        if self._has_body_sprite or not realistic:
            self.leg_rig.draw(painter, palette, simple=simple_legs, detail=detail)

        # 2.5 交配光晕：从躯干缝隙里透出来的暖光，必须压在躯干下面
        if self.state == EntityState.MATING:
            self._mating_rig.draw_under(painter, self.body_len, self.gender, quality)

        # 3. 躯干（交配时随挺动做纵向挤压：只有躯干在挤，六足不拉 ——
        #    腿跟着缩放会像橡皮虫）
        squeeze = self._mating_rig.squeeze if self.state == EntityState.MATING else 1.0
        if abs(squeeze - 1.0) > 1e-3:
            painter.save()
            painter.scale(1.0, squeeze)
            if realistic:
                self._render_realistic_body(painter)
            else:
                self._render_simple_pipeline(painter)
            painter.restore()
        elif realistic:
            self._render_realistic_body(painter)
        else:
            self._render_simple_pipeline(painter)

        # 4. 触须（位于背板之上，最后画）
        self.antenna_rig.draw(
            painter,
            palette["antenna"],
            simple=not realistic,
            detail=detail,
        )

        # 5. 交配特效：每拍一圈扩散波纹 + 结合处湿光
        if self.state == EntityState.MATING:
            self._mating_rig.draw_over(
                painter, self.body_len, self.body_w, self.gender, quality
            )

        if not vector_aa:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.restore()

    def render_mating_hearts(self, painter: QPainter) -> None:
        """交配心形，单独一遍画在所有虫体之上。

        不能塞在 ``render()`` 里：两只虫是分别 render 的，先画的那只撒的心形
        会被后画的那只身体盖掉一半。这一遍由绘制循环在全部虫体之后调用。
        """
        if self.state != EntityState.MATING or not self._mating_rig.hearts:
            return
        self._mating_rig.draw_hearts(painter, painter.transform(), self.scale)

    @classmethod
    def _get_shadow_pixmap(cls) -> QPixmap:
        if cls._shadow_pixmap is None:
            size = 128
            pix = QPixmap(size, size)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            grad = QRadialGradient(size / 2.0, size / 2.0 + 4.0, size * 0.5)
            grad.setColorAt(0.0, QColor(10, 10, 15, 255))
            grad.setColorAt(0.65, QColor(10, 10, 15, 115))
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawEllipse(QRectF(0, 4, size, size - 4))
            p.end()
            cls._shadow_pixmap = pix
        return cls._shadow_pixmap

    def _render_contact_shadow(self, painter: QPainter) -> None:
        pix = self._get_shadow_pixmap()
        w = self.body_w * 1.45
        h = self.body_len * 1.20
        # 被拎起来时影子拉远、变淡 —— 这是"离开桌面"的唯一视觉线索
        lifted = 1.0 if self.state == EntityState.DRAGGED else 0.0
        dx = 2.5 - 4.0 * lifted
        dy = 3.5 + 7.0 * lifted
        painter.save()
        painter.setOpacity(float(clamp(self.shadow_intensity, 0.0, 1.0)) * (1.0 - 0.45 * lifted))
        painter.drawPixmap(
            QRectF(-w / 2.0 + dx, -h / 2.0 + dy, w * (1.0 + 0.16 * lifted), h * (1.0 + 0.10 * lifted)),
            pix, QRectF(pix.rect()),
        )
        painter.restore()

    def _render_realistic_body(self, painter: QPainter) -> None:
        sprite = self._get_current_sprite()
        if sprite is not None and not sprite.isNull():
            rect = QRectF(-self.body_w / 2.0, -self.body_len / 2.0, self.body_w, self.body_len)
            if self.state == EntityState.PANIC:
                # 惊慌时躯干随高频步态轻微抖动
                rect.translate(random.uniform(-0.7, 0.7), random.uniform(-0.7, 0.7))
            # 进食时腹板节律收缩，咬下一口的那一瞬收缩最明显
            feed_scale = 1.0
            if self.state == EntityState.EAT:
                feed_scale = 1.0 + 0.028 * math.sin(self._anim_clock * 8.0) - 0.045 * self._feed_pulse

            # 嫩壳期：躯干整体略微透光（新壳还很软），这一段透明度不能漏到
            # 后面的触须上，所以统一用 save/restore 包住。
            body_opacity = 0.92 if self.molt_timer > 0 else 1.0
            painter.save()
            painter.setOpacity(body_opacity)
            if feed_scale != 1.0:
                painter.scale(1.0, feed_scale)
            painter.drawPixmap(rect.toRect(), sprite)

            # 白化回落：molt_timer 归零后白贴图并不是"消失"，而是以递减的
            # 不透明度叠在正常贴图上，颜色因此是化开的。只有蜕皮刚结束的一两秒
            # 里才有这一层，最多十几只，开销可忽略。
            alpha = self.teneral_alpha
            if 0.02 < alpha < 1.0:
                white = ResourceManager.get_sprite("cockroach_white_body")
                if white is not None and not white.isNull():
                    painter.setOpacity(alpha)
                    painter.drawPixmap(rect.toRect(), white)
            painter.restore()
        else:
            self._render_realistic_vector(painter)

    def _get_current_sprite(self) -> Optional[QPixmap]:
        """躯干贴图（腿与触须已被裁掉，由骨架程序化绘制）。"""
        name = self._current_body_sprite_name()
        pix = ResourceManager.get_sprite(name)
        if pix and not pix.isNull():
            return pix
        # 退化路径：没有躯干贴图时用完整贴图（此时不绘制程序化六足）
        if self.molt_timer > 0:
            return ResourceManager.get_sprite("cockroach_white")
        if self.stage == LifeStage.NYMPH:
            return ResourceManager.get_sprite("cockroach_nymph")
        return ResourceManager.get_sprite("cockroach_adult")

    # ------------------------------------------------------------- 矢量躯干

    def _render_realistic_vector(self, painter: QPainter) -> None:
        self._render_realistic_cerci(painter)
        self._render_realistic_chitinous_body(painter)
        self._render_realistic_pronotum_and_head(painter)

    def _render_realistic_cerci(self, painter: QPainter) -> None:
        pen = QPen(QColor(60, 25, 12), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        wy = self.body_len * 0.44
        painter.drawLine(QPointF(-6.0, wy), QPointF(-15.0, wy + self.body_len * 0.20))
        painter.drawLine(QPointF(6.0, wy), QPointF(15.0, wy + self.body_len * 0.20))

    def _render_realistic_chitinous_body(self, painter: QPainter) -> None:
        w, h = self.body_w, self.body_len * 0.86
        top = -self.body_len * 0.06
        grad = QLinearGradient(-w / 2.0, 0, w / 2.0, 0)
        grad.setColorAt(0.0, QColor(35, 15, 8))
        grad.setColorAt(0.25, QColor(85, 38, 18))
        grad.setColorAt(0.5, QColor(145, 65, 30))
        grad.setColorAt(0.75, QColor(85, 38, 18))
        grad.setColorAt(1.0, QColor(30, 12, 6))

        painter.setPen(QPen(QColor(20, 8, 4), 2.0))
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(QRectF(-w / 2.0, top, w, h), w * 0.28, w * 0.28)

        if self.stage == LifeStage.ADULT:
            painter.setPen(QPen(QColor(25, 10, 5), 1.6))
            painter.drawLine(QPointF(0.0, top), QPointF(0.0, top + h))
        else:
            painter.setPen(QPen(QColor(20, 8, 4), 1.8))
            for k in range(1, 5):
                y_seg = top + h * k / 5.0
                painter.drawLine(QPointF(-w / 2.0 + 2.0, y_seg), QPointF(w / 2.0 - 2.0, y_seg))

    def _render_realistic_pronotum_and_head(self, painter: QPainter) -> None:
        w = self.body_w
        head_y = -self.body_len * 0.50
        p_grad = QLinearGradient(0, head_y, 0, head_y + self.body_len * 0.22)
        p_grad.setColorAt(0.0, QColor(110, 50, 22))
        p_grad.setColorAt(0.5, QColor(165, 75, 32))
        p_grad.setColorAt(1.0, QColor(50, 22, 10))

        painter.setPen(QPen(QColor(25, 10, 5), 2.0))
        painter.setBrush(QBrush(p_grad))
        painter.drawEllipse(
            QRectF(-w * 0.50, head_y + self.body_len * 0.06, w, self.body_len * 0.20)
        )

        painter.setBrush(QBrush(QColor(30, 12, 5, 200)))
        painter.drawEllipse(
            QRectF(-w * 0.26, head_y + self.body_len * 0.12, w * 0.52, self.body_len * 0.09)
        )

        painter.setBrush(QBrush(QColor(20, 8, 4)))
        painter.drawEllipse(QRectF(-w * 0.26, head_y, w * 0.52, self.body_len * 0.10))
        painter.setBrush(QBrush(QColor(5, 5, 5)))
        painter.drawEllipse(QRectF(-w * 0.22, head_y + self.body_len * 0.015, w * 0.16, w * 0.16))
        painter.drawEllipse(QRectF(w * 0.06, head_y + self.body_len * 0.015, w * 0.16, w * 0.16))

    def _render_simple_pipeline(self, painter: QPainter) -> None:
        self._render_simple_body(painter)
        self._render_simple_wings_and_pronotum(painter)

    def _render_simple_body(self, painter: QPainter) -> None:
        w, h = self.body_w * 0.96, self.body_len * 0.90
        painter.setPen(QPen(QColor(60, 25, 10), 1.8))
        painter.setBrush(QBrush(QColor(160, 95, 45)))
        painter.drawRoundedRect(
            QRectF(-w / 2.0, -self.body_len * 0.08, w, h), w * 0.26, w * 0.26
        )

    def _render_simple_wings_and_pronotum(self, painter: QPainter) -> None:
        L, W = self.body_len, self.body_w
        head_y = -0.50 * L

        if self.stage == LifeStage.ADULT:
            painter.setPen(QPen(QColor(90, 45, 20, 180), 1.4))
            painter.setBrush(QBrush(QColor(235, 210, 170, 160)))
            for sgn in (-1.0, 1.0):
                path = QPainterPath()
                path.moveTo(0.0, -0.06 * L)
                path.quadTo(sgn * 0.46 * W, 0.26 * L, sgn * 0.16 * W, 0.80 * L)
                path.quadTo(0.0, 0.58 * L, 0.0, -0.06 * L)
                painter.drawPath(path)

        painter.setPen(QPen(QColor(60, 25, 10), 1.8))
        painter.setBrush(QBrush(QColor(135, 75, 30)))
        painter.drawRoundedRect(
            QRectF(-0.44 * W, head_y + 0.04 * L, 0.88 * W, 0.34 * L), 0.18 * W, 0.18 * W
        )

        painter.setBrush(QBrush(QColor(80, 40, 15)))
        painter.drawEllipse(QRectF(-0.24 * W, head_y - 0.01 * L, 0.48 * W, 0.10 * L))
        painter.setBrush(QBrush(QColor(20, 15, 15)))
        painter.drawEllipse(QRectF(-0.21 * W, head_y + 0.008 * L, 0.15 * W, 0.15 * W))
        painter.drawEllipse(QRectF(0.06 * W, head_y + 0.008 * L, 0.15 * W, 0.15 * W))


def _lerp_angle(a: float, b: float, t: float) -> float:
    """角度插值（走最短弧）。"""
    return a + angle_diff(a, b) * clamp(t, 0.0, 1.0)
