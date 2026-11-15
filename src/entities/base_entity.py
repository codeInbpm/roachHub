"""生物实体基类与枚举定义。"""

from __future__ import annotations
import uuid
import math
import random
from enum import Enum, auto
from typing import Optional, Any

from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QPainter

from ..utils.math_utils import Vector2D, clamp, normalize_angle, angle_diff


class EntityState(Enum):
    """生物行为状态。"""
    IDLE = auto()      # 停顿静止、触须微动探嗅
    WALK = auto()      # 正常游走漫步
    PANIC = auto()     # 惊吓爆发式极速逃窜 (Escape sprint)
    FREEZE = auto()    # 近距离惊恐短暂僵直 (Startle freeze)
    GROOMING = auto()  # 停顿用前足梳理触角 (Grooming)
    EAT = auto()       # 正在进食
    DRAGGED = auto()   # 被捏住提起，六足腾空挣扎
    MOLTING = auto()   # 蜕皮脱壳
    MATING = auto()    # 产卵繁衍
    DEAD = auto()      # 死亡


class LifeStage(Enum):
    """生命周期阶段。"""
    EGG = auto()       # 卵 / 卵鞘 (Ootheca)
    NYMPH = auto()     # 若虫 (小体型、无翅、多龄期)
    ADULT = auto()     # 成虫 (有翅、体型大、具备繁殖能力)
    DEAD = auto()      # 死亡尸体


class BaseEntity:
    """所有桌面生物与物体的基类。"""

    # 能否被鼠标"认领"（抓取 / 惊吓）。蜕下来的空壳之类只是布景，设为 False。
    interactive: bool = True

    def __init__(
        self,
        x: float = 0.0,
        y: float = 0.0,
        base_width: float = 38.0,
        base_height: float = 72.0,
        scale: float = 1.0,
    ) -> None:
        self.id: str = str(uuid.uuid4())[:8]

        # 空间状态
        self.pos: Vector2D = Vector2D(x, y)
        self.velocity: Vector2D = Vector2D(0.0, 0.0)
        self.angle: float = 0.0
        self.target_angle: float = 0.0
        self.current_speed: float = 0.0
        self.target_speed: float = 0.0

        # 基础尺寸
        self.base_width: float = base_width
        self.base_height: float = base_height
        self.scale: float = scale

        # 个体微小遗传差异 (染色体色调、足长差异)
        self.individual_tone: float = random.uniform(0.88, 1.12)
        self.individual_leg_scale: float = random.uniform(0.92, 1.08)

        # 状态机与生命周期
        self.state: EntityState = EntityState.IDLE
        self.stage: LifeStage = LifeStage.NYMPH
        self.is_alive: bool = True

        # 若虫多龄期 (Instar) 机制
        self.instar: int = 1
        self.max_instars: int = 4
        self.instar_age: float = 0.0
        self.instar_duration: float = 18.0
        self.molt_timer: float = 0.0

        # 生理指标
        self.energy: float = 100.0
        self.health: float = 100.0
        self.age: float = 0.0
        self.max_age: float = 300.0

        # 繁殖与卵鞘
        self.gender: str = "female"
        self.reproduce_cooldown: float = 30.0
        self.reproduce_timer: float = 10.0
        self.is_carrying_ootheca: bool = False
        self.carried_ootheca: Optional[Any] = None

        # 应激、恐惧与警觉系统 (高拟真核心)
        self.panic_timer: float = 0.0
        self.fear_level: float = 0.0
        self.alertness: float = 0.0         # 警觉度 (受到多次刺激后蓄积，使行为更亢奋)
        self.freeze_timer: float = 0.0      # 瞬间惊恐僵直倒计时 (0.1~0.2s)
        self.grooming_timer: float = 0.0    # 梳理触角计时
        self.grooming_target: str = "left"  # 梳理哪一侧触角
        self.threat_vector: Optional[Vector2D] = None # 当前感知到的威胁相对方向
        self.circadian_factor: float = 1.0  # 昼夜活跃因子

        # 渲染风格参数
        self.render_style: str = "realistic"
        self.shadow_intensity: float = 0.42

        # 机动性
        self.turn_rate: float = 3.8
        self.panic_turn_rate: float = 13.0

    @property
    def x(self) -> float:
        return self.pos.x

    @x.setter
    def x(self, val: float) -> None:
        self.pos.x = val

    @property
    def y(self) -> float:
        return self.pos.y

    @y.setter
    def y(self, val: float) -> None:
        self.pos.y = val

    @property
    def width(self) -> float:
        return self.base_width * self.scale

    @property
    def height(self) -> float:
        return self.base_height * self.scale

    def get_bounding_rect(self) -> QRectF:
        """获取包围盒，配合长触须与腿部放大。"""
        radius = max(self.width, self.height) * 0.95
        return QRectF(
            self.pos.x - radius,
            self.pos.y - radius,
            radius * 2,
            radius * 2,
        )

    def contains_point(self, px: float, py: float) -> bool:
        """精确命中。"""
        dx = px - self.pos.x
        dy = py - self.pos.y
        cos_a = math.cos(-self.angle)
        sin_a = math.sin(-self.angle)
        local_x = dx * cos_a - dy * sin_a
        local_y = dx * sin_a + dy * cos_a

        half_w = (self.width / 2.0) * 1.35
        half_h = (self.height / 2.0) * 1.35
        if half_w <= 0 or half_h <= 0:
            return False

        return ((local_x / half_w) ** 2 + (local_y / half_h) ** 2) <= 1.0

    def trigger_freeze_and_panic(self, source_pos: Vector2D, duration: float = 2.4) -> None:
        """分级应激：瞬间惊恐僵直 (0.12s) 后爆发极速逃跑。"""
        self.threat_vector = source_pos - self.pos
        self.alertness = min(1.0, self.alertness + 0.45) # 蓄积警觉度
        self.panic_timer = duration
        self.fear_level = 1.0

        # 短暂僵直触发 (昆虫受到突发强烈刺激的瞬间静止防御反应)
        self.state = EntityState.FREEZE
        self.freeze_timer = random.uniform(0.10, 0.16)
        self.target_speed = 0.0

        # 确定逃跑目标方向 (背离刺激源)
        flee_vec = self.pos - source_pos
        if flee_vec.length_squared() < 1e-4:
            self.target_angle = Vector2D.random_direction().angle()
        else:
            self.target_angle = flee_vec.angle()

    def on_click(self, click_pos: Vector2D) -> None:
        self.trigger_freeze_and_panic(click_pos)

    def update(self, dt: float) -> None:
        self.age += dt
        self.instar_age += dt

        # 警觉度平滑衰减
        if self.alertness > 0:
            self.alertness = max(0.0, self.alertness - dt * 0.12)

        # 惊恐僵直倒计时 -> 进入高速逃窜
        if self.state == EntityState.FREEZE:
            self.freeze_timer -= dt
            if self.freeze_timer <= 0:
                self.state = EntityState.PANIC

        # 恐慌倒计时
        if self.panic_timer > 0:
            self.panic_timer = max(0.0, self.panic_timer - dt)
            self.fear_level = clamp(self.panic_timer / 2.0, 0.0, 1.0)
            if self.panic_timer == 0 and self.state == EntityState.PANIC:
                self.state = EntityState.WALK

        # 梳理触角倒计时
        if self.state == EntityState.GROOMING:
            self.grooming_timer -= dt
            if self.grooming_timer <= 0:
                self.state = EntityState.WALK

        # 蜕皮动效倒计时
        if self.molt_timer > 0:
            self.molt_timer = max(0.0, self.molt_timer - dt)
            if self.molt_timer == 0 and self.state == EntityState.MOLTING:
                self.state = EntityState.WALK

        # 繁殖冷却更新
        if self.stage == LifeStage.ADULT and self.reproduce_timer > 0:
            self.reproduce_timer = max(0.0, self.reproduce_timer - dt)

    def render(self, painter: QPainter) -> None:
        pass

    def consume_energy(self, amount: float) -> None:
        self.energy = max(0.0, self.energy - amount)
        if self.energy <= 0.0:
            self.health = max(0.0, self.health - 2.5)
            if self.health <= 0.0:
                self.die("starvation")

    def feed(self, amount: float) -> None:
        self.energy = min(100.0, self.energy + amount)
        self.health = min(100.0, self.health + 10.0)

    def die(self, reason: str = "natural") -> None:
        self.is_alive = False
        self.state = EntityState.DEAD
        self.stage = LifeStage.DEAD
