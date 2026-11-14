"""卡通食物/食物碎屑实体类。"""

from __future__ import annotations
import math
import random
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush

from .base_entity import BaseEntity, EntityState, LifeStage

# 啃食中的位点会持续被"续期"，超过这个秒数没有续期即视为离开（个体死亡/被打断）
_SLOT_TTL = 2.5


class Food(BaseEntity):
    """桌面散落的食物碎屑（如小奶酪、饼干碎）。

    食物是有营养量的：被啃食时体积随之缩小，啃完即消失。同时提供有限的
    "进食位点"，避免一整群蟑螂叠在同一个点上。
    """

    def __init__(
        self,
        x: float,
        y: float,
        nutrition: float = 40.0,
    ) -> None:
        super().__init__(x=x, y=y, base_width=22.0, base_height=22.0, scale=1.0)
        self.nutrition: float = nutrition
        self.max_nutrition: float = nutrition
        self.state = EntityState.IDLE
        self.stage = LifeStage.ADULT

        self.food_type = random.choice([0, 1])

        # 进食位点：id -> 最近一次续期时刻
        self.eaters: dict[str, float] = {}
        self.eating_slots: int = 2 if nutrition >= 30.0 else 1
        self._clock: float = 0.0
        self._wobble = random.uniform(0.0, math.tau)

    # ------------------------------------------------------------------ 位点

    def _prune(self) -> None:
        stale = [k for k, t in self.eaters.items() if self._clock - t > _SLOT_TTL]
        for k in stale:
            self.eaters.pop(k, None)

    def has_room(self, roach_id: str) -> bool:
        """是否还能容纳这个个体（已在其中的人始终有位置）。"""
        self._prune()
        if roach_id in self.eaters:
            return True
        return len(self.eaters) < self.eating_slots

    def claim(self, roach_id: str) -> bool:
        self._prune()
        if roach_id in self.eaters:
            self.eaters[roach_id] = self._clock
            return True
        if len(self.eaters) >= self.eating_slots:
            return False
        self.eaters[roach_id] = self._clock
        return True

    def release(self, roach_id: str) -> None:
        self.eaters.pop(roach_id, None)

    @property
    def ratio(self) -> float:
        """剩余营养比例（用于体积收缩）。"""
        return max(0.0, min(1.0, self.nutrition / max(1e-6, self.max_nutrition)))

    # ------------------------------------------------------------------ 更新

    def update(self, dt: float) -> None:
        super().update(dt)
        self._clock += dt
        self._wobble += dt * 1.6
        if self.eaters and int(self._clock * 4) % 4 == 0:
            self._prune()

    def consume(self, amount: float) -> float:
        """被生物啃食，返回实际吃到的营养。"""
        eaten = min(self.nutrition, amount)
        self.nutrition -= eaten
        if self.nutrition <= 0.0:
            self.nutrition = 0.0
            self.is_alive = False
            self.eaters.clear()
        return eaten

    def get_bounding_rect(self) -> QRectF:
        r = 16.0 * (0.45 + 0.55 * self.ratio)
        return QRectF(self.pos.x - r, self.pos.y - r, r * 2, r * 2)

    def on_click(self, click_pos) -> None:
        """点食物不该把它吓跑：食物只是被顶得晃一下。"""
        self._wobble += 0.5

    def render(self, painter: QPainter) -> None:
        painter.save()
        painter.translate(self.pos.x, self.pos.y)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 体积随剩余营养线性收缩（下限 0.45，避免"最后一刻突然消失"）
        ratio = 0.45 + 0.55 * self.ratio
        # 被啃食时轻微晃动，看起来像被顶动
        if self.eaters:
            painter.rotate(math.sin(self._wobble * 6.0) * 1.6)
        painter.scale(ratio, ratio)

        if self.food_type == 0:
            # 奶酪小三角
            painter.setPen(QPen(QColor(190, 130, 20), 1.6))
            painter.setBrush(QBrush(QColor(255, 205, 50)))
            painter.drawRoundedRect(QRectF(-10.0, -8.0, 20.0, 16.0), 4.0, 4.0)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(230, 175, 30)))
            painter.drawEllipse(QRectF(-5.0, -4.0, 4.0, 4.0))
            painter.drawEllipse(QRectF(2.0, 1.0, 5.0, 5.0))
        else:
            # 烤饼干屑
            painter.setPen(QPen(QColor(160, 95, 30), 1.6))
            painter.setBrush(QBrush(QColor(235, 175, 95)))
            painter.drawEllipse(QRectF(-9.0, -9.0, 18.0, 18.0))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(110, 60, 20)))
            painter.drawEllipse(QRectF(-3.5, -3.5, 2.2, 2.2))
            painter.drawEllipse(QRectF(2.0, 2.0, 2.2, 2.2))

        # 被啃过之后边缘出现细碎的啃痕（不挖透明洞：覆盖层上做 DestinationOut
        # 在不同合成路径下可能变成黑块，用暗色小弧更安全）
        if self.ratio < 0.85:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(120, 78, 30, 90)))
            bite = (1.0 - self.ratio) * 5.0
            painter.drawEllipse(QRectF(4.0, -8.0, bite, bite * 0.8))
            painter.drawEllipse(QRectF(-7.0, 2.0, bite * 0.8, bite))

        painter.restore()
