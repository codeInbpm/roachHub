"""蜕下来的空壳（exuvia）。

蟑螂每蜕一次皮，旧角质层会完整地留在原地 —— 一只半透明的、奶白色的空壳，
头胸腹分节都还在，只是没有肉、没有腿的力气。桌面上的这层"白"，在真实蟑螂
身上就是从这具空壳开始的：刚蜕完皮的个体新壳还没硬化、色素也没沉积，整只
是乳白半透明的，几小时后才慢慢变深。

这个实体只做三件事：
* 留在蜕皮的位置（轻微干瘪、逐渐变淡）；
* 不参与任何行为/碰撞/命中测试（`interactive = False`，点它等于点空气）；
* 到期自动消失，不占人口上限。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

from ..resources import ResourceManager
from .base_entity import BaseEntity, EntityState, LifeStage

# 空壳留在桌上的时长（秒）：够用户看清"白的是壳"，又不至于一直脏着屏幕
EXUVIA_LIFETIME = 10.0


class Exuvia(BaseEntity):
    """蜕下的空壳。不可抓、不可吓、不计数。"""

    interactive = False

    def __init__(
        self,
        x: float,
        y: float,
        angle: float = 0.0,
        scale: float = 1.0,
        base_width: float = 38.0,
        base_height: float = 72.0,
        lifetime: float = EXUVIA_LIFETIME,
    ) -> None:
        super().__init__(x=x, y=y, base_width=base_width, base_height=base_height, scale=scale)
        self.angle = angle
        self.state = EntityState.DEAD
        self.stage = LifeStage.DEAD
        self.lifetime = max(2.0, float(lifetime))
        self.age = 0.0

        pix = ResourceManager.get_sprite("cockroach_white_body")
        self._aspect = (pix.width() / pix.height()) if (pix and not pix.isNull()) else 0.41

    # 空壳不做任何决策
    def update(self, dt: float) -> None:
        self.age += dt
        if self.age >= self.lifetime:
            self.is_alive = False

    def get_bounding_rect(self) -> QRectF:
        r = max(self.width, self.height) * 0.6
        return QRectF(self.pos.x - r, self.pos.y - r, r * 2, r * 2)

    def render(self, painter: QPainter) -> None:
        pix = ResourceManager.get_sprite("cockroach_white_body")
        if pix is None or pix.isNull():
            return

        t = min(1.0, self.age / self.lifetime)
        # 前 40% 时间保持可见（让人看清是壳），之后线性淡出
        opacity = 0.70 * (1.0 - max(0.0, t - 0.4) / 0.6) ** 1.35
        if opacity <= 0.01:
            return

        body_len = self.base_height * 0.98 * self.scale
        body_w = body_len * self._aspect
        rect = QRectF(-body_w / 2.0, -body_len / 2.0, body_w, body_len)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(self.pos.x, self.pos.y)
        painter.rotate(math.degrees(self.angle) + 90.0)

        # 贴地阴影：没有它，空壳会像贴在屏幕上的贴纸，而不是落在桌面上
        painter.save()
        shadow = self._shared_shadow()
        if shadow is not None:
            painter.setOpacity(opacity * 0.55)
            sw, sh = body_w * 1.5, body_len * 1.25
            painter.drawPixmap(
                QRectF(-sw / 2.0 + 2.0, -sh / 2.0 + 3.0, sw, sh),
                shadow,
                QRectF(shadow.rect()),
            )
        painter.restore()

        # 空壳干瘪塌陷：横向略收、纵向略压，而且会随时间再瘪一点
        painter.scale(0.96 - 0.05 * t, 0.94 - 0.06 * t)
        painter.setOpacity(opacity)
        painter.drawPixmap(rect.toRect(), pix)
        painter.restore()

    @staticmethod
    def _shared_shadow():
        """复用蟑螂那套预渲染的接触阴影（避免为每种实体各存一张）。"""
        try:
            from .cockroach import Cockroach

            return Cockroach._get_shadow_pixmap()
        except Exception:  # 无 GUI / 贴图缺失时安静降级
            return None
