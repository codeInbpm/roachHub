"""写实风格大尺寸卵鞘 (Ootheca) 实体类。

支持写实高清精灵图与油亮坚硬红褐色胶囊渲染。
"""

from __future__ import annotations
import math
import random
from typing import Optional, Any

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient, QPixmap

from .base_entity import BaseEntity, EntityState, LifeStage
from ..resources import ResourceManager
from ..utils.math_utils import Vector2D


class Ootheca(BaseEntity):
    """写实大尺寸蟑螂卵鞘实体。"""

    def __init__(
        self,
        x: float,
        y: float,
        parent: Optional[BaseEntity] = None,
        incubation_time: float = 15.0,
        is_carried: bool = False,
    ) -> None:
        # 放大为基础长 42 像素，宽 26 像素
        super().__init__(x=x, y=y, base_width=26.0, base_height=42.0, scale=1.0)
        self.stage = LifeStage.EGG
        self.state = EntityState.IDLE

        self.parent = parent
        self.is_carried = is_carried
        self.incubation_time = incubation_time
        self.incubation_timer = incubation_time

        # 孵化微动感
        self.pulse_phase = random.uniform(0.0, math.pi * 2)
        self.shake_timer = 0.0

    @property
    def progress(self) -> float:
        return 1.0 - (self.incubation_timer / max(0.1, self.incubation_time))

    @property
    def is_hatched(self) -> bool:
        return self.incubation_timer <= 0.0

    def update(self, dt: float) -> None:
        super().update(dt)

        self.pulse_phase += dt * 3.5
        if self.shake_timer > 0:
            self.shake_timer = max(0.0, self.shake_timer - dt)

        if self.incubation_timer > 0:
            self.incubation_timer = max(0.0, self.incubation_timer - dt)

        # 附着在母体尾部
        if self.is_carried and self.parent and self.parent.is_alive:
            tail_offset = (self.parent.height * 0.48)
            offset_x = -math.cos(self.parent.angle) * tail_offset
            offset_y = -math.sin(self.parent.angle) * tail_offset
            self.pos = self.parent.pos + Vector2D(offset_x, offset_y)
            self.angle = self.parent.angle
        elif self.is_carried and (not self.parent or not self.parent.is_alive):
            self.is_carried = False
            self.parent = None

    def on_click(self, click_pos: Vector2D) -> None:
        self.shake_timer = 0.4

    def render(self, painter: QPainter) -> None:
        painter.save()
        painter.translate(self.pos.x, self.pos.y)

        deg = math.degrees(self.angle) + 90.0
        painter.rotate(deg)

        pulse = 1.0 + math.sin(self.pulse_phase) * 0.03
        if self.shake_timer > 0:
            shake = math.sin(self.shake_timer * 40.0) * 2.2
            painter.translate(shake, 0)

        painter.scale(pulse, pulse)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # 优先读取写实大尺寸卵鞘贴图
        sprite = ResourceManager.get_sprite("ootheca")
        if sprite and not sprite.isNull():
            target_w = self.base_width * 1.55
            target_h = self.base_height * 1.55
            rect = QRectF(-target_w / 2.0, -target_h / 2.0, target_w, target_h)
            painter.drawPixmap(rect.toRect(), sprite)
        else:
            self._render_realistic_vector(painter)

        painter.restore()

    def _render_realistic_vector(self, painter: QPainter) -> None:
        """油亮写实卵鞘大尺寸矢量绘制。"""
        w, h = 22.0, 36.0
        rect = QRectF(-w / 2.0, -h / 2.0, w, h)

        grad = QLinearGradient(-w / 2.0, 0, w / 2.0, 0)
        grad.setColorAt(0.0, QColor(30, 10, 5))
        grad.setColorAt(0.3, QColor(110, 42, 18))
        grad.setColorAt(0.55, QColor(165, 65, 28))
        grad.setColorAt(0.8, QColor(90, 32, 14))
        grad.setColorAt(1.0, QColor(25, 8, 4))

        painter.setPen(QPen(QColor(15, 5, 2), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(rect, 6.0, 6.0)

        painter.setPen(QPen(QColor(20, 8, 4), 1.2))
        for step_y in range(int(-h / 2.0 + 4), int(h / 2.0 - 3), 3):
            painter.drawLine(QPointF(-w / 2.0 + 3.0, float(step_y)), QPointF(w / 2.0 - 3.0, float(step_y)))

        painter.setBrush(QBrush(QColor(20, 8, 4)))
        painter.drawRect(QRectF(-w / 2.0 + 2.0, -h / 2.0 - 3.0, w - 4.0, 3.5))
