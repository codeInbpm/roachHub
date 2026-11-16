"""被拍扁的蟑螂尸体（含主污渍）。

**为什么必须是独立实体，而不是 `Cockroach` 的子类**

1. `EcologySystem.update()` 会在 `is_alive=False` 的**下一帧直接回收**实体。
   也就是说"死掉的蟑螂"根本没有机会被画出来 —— 想有尸体，就必须让它
   `is_alive=True`，自己计时到期再置 False（和 `Exuvia` 完全一样的套路）。
2. `MovementSystem` / `BehaviorSystem` / `ReproductionSystem` 全都按
   `isinstance(e, Cockroach)` 筛人。子类会被趋边性推着走、被当成可交配对象、
   被计入种群上限。所以它只能是一个**平级的独立类**，并且 `interactive = False`
   （点它等于点空气，也不会进 `hit_test_at`）。

渲染分五层（从下到上）：主污渍 → 压扁的躯干 → 外翻的六足 → 摊平的触须 →
体液高光。最后一层是"湿"的可读性来源，省掉它整具尸体就变成一张干贴纸。
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen

from ..resources import ResourceManager
from .base_entity import BaseEntity, EntityState, LifeStage

# 污渍主色（比虫体浅一档的泥褐，压在桌面上才不会像一块补丁）
STAIN_INK = QColor(107, 74, 36)
# 干涸的深色沉淀
STAIN_DARK = QColor(74, 44, 16)
# 体液高光（浅黄），"湿"就靠这一层
STAIN_HIGHLIGHT = QColor(199, 154, 90)

# 砸下去的那一下：0.06 秒内纵向从 0.92 压到 0.46
PRESS_SECONDS = 0.06


class SquashedRoach(BaseEntity):
    """一具被拍扁的蟑螂 + 它身下的那摊污渍。"""

    interactive = False
    metabolizes = False

    def __init__(
        self,
        x: float,
        y: float,
        angle: float = 0.0,
        scale: float = 1.0,
        base_width: float = 38.0,
        base_height: float = 72.0,
        stage: LifeStage = LifeStage.ADULT,
        gender: str = "female",
        sprite_name: str = "cockroach_adult_body",
        individual_tone: float = 1.0,
        lifetime: float = 22.0,
        splat_amount: float = 1.0,
    ) -> None:
        super().__init__(
            x=x, y=y, base_width=base_width, base_height=base_height, scale=scale
        )
        self.angle = angle
        self.stage = stage
        self.gender = gender
        self.state = EntityState.DEAD
        self.is_alive = True          # ← 关键：靠自己的 age 到期才置 False
        self.age = 0.0
        self.lifetime = max(3.0, float(lifetime))
        self.individual_tone = float(individual_tone)
        self.splat_amount = max(0.0, float(splat_amount))
        self._sprite_name = sprite_name

        pix = ResourceManager.get_sprite(sprite_name)
        self._aspect = (
            (pix.width() / pix.height()) if (pix and not pix.isNull()) else (1.0 / 1.85)
        )

        # 污渍形状**生成一次就不再变**：每帧随机会让整摊"沸腾"，非常出戏
        self._blobs: list[tuple[float, float, float, float]] = []
        self._tails: list[tuple[float, float, float]] = []
        self._spots: list[tuple[float, float, float]] = []
        self._build_splat()

    # ------------------------------------------------------------------ 形状

    def _build_splat(self) -> None:
        bl = self.base_height * 0.98
        bw = bl * self._aspect
        amount = self.splat_amount

        # 3~5 个圆叠成一摊不规则的 blob，中心再补一个大的压住接缝。
        # 整体要比虫体宽一圈：否则躯干会把污渍完全盖住，看起来就"没有浆"。
        n = random.randint(3, 5)
        self._blobs.append((0.0, 0.0, bw * 0.58 * amount, bl * 0.50 * amount))
        for _ in range(n):
            a = random.uniform(0.0, math.tau)
            rr = random.uniform(0.35, 1.0)
            cx = math.cos(a) * bw * 0.80 * rr * amount
            cy = math.sin(a) * bl * 0.62 * rr * amount
            r = random.uniform(0.26, 0.50) * bw * max(0.6, amount)
            self._blobs.append((cx, cy, r, r * random.uniform(0.62, 0.92)))

        # 1~2 条毛细拖尾：顺着被拍的方向甩出去
        for _ in range(random.randint(1, 2)):
            a = random.uniform(0.0, math.tau)
            length = random.uniform(0.55, 1.10) * bl * 0.5 * max(0.7, amount)
            self._tails.append((a, length, random.uniform(0.8, 1.6)))

        # 2~3 枚浅黄体液高光
        for _ in range(random.randint(2, 3)):
            a = random.uniform(0.0, math.tau)
            rr = random.uniform(0.10, 0.55)
            self._spots.append(
                (
                    math.cos(a) * bw * 0.55 * rr,
                    math.sin(a) * bl * 0.45 * rr,
                    random.uniform(1.6, 3.4),
                )
            )

    # ------------------------------------------------------------------ 生命周期

    def update(self, dt: float) -> None:
        """只走自己的钟。**不要**调 super().update()：那会把惊恐/僵直那套
        状态机重新跑一遍，一具尸体不该有 panic_timer。"""
        self.age += dt
        if self.age >= self.lifetime:
            self.is_alive = False     # 交给 EcologySystem 回收，不新增回收逻辑

    def get_bounding_rect(self) -> QRectF:
        r = max(self.width, self.height) * 0.75
        return QRectF(self.pos.x - r, self.pos.y - r, r * 2, r * 2)

    # ------------------------------------------------------------------ 渲染

    def render(self, painter: QPainter) -> None:
        bl = self.base_height * 0.98
        bw = bl * self._aspect

        t = min(1.0, self.age / self.lifetime)
        # 前 70% 保持，最后 30% 淡出
        fade = 1.0 if t < 0.70 else max(0.0, (1.0 - t) / 0.30)
        if fade <= 0.01:
            return
        # 砸下去：0.06 秒内纵向 0.92 → 0.46，横向同时摊开
        press = min(1.0, self.age / PRESS_SECONDS)
        sy = 0.92 - 0.46 * press
        sx = 1.10 + 0.16 * press

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(self.pos.x, self.pos.y)
        painter.rotate(math.degrees(self.angle) + 90.0)
        painter.scale(self.scale, self.scale)

        # L0 主污渍（合并成一条 path，避免半透明圆叠加出现深色接缝）
        stain = QPainterPath()
        # ⚠️ 必须显式设 WindingFill：默认的奇偶规则会把重叠区域**掏空**，
        # 几个圆叠在一起就成了花瓣，而不是一摊浆。
        stain.setFillRule(Qt.FillRule.WindingFill)
        for cx, cy, rx, ry in self._blobs:
            stain.addEllipse(QPointF(cx, cy), rx, ry)
        ink = QColor(STAIN_INK)
        ink.setAlpha(int(168 * fade))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(ink))
        painter.drawPath(stain)

        # 虫体底下那一层更浓的"浆"
        core = QColor(STAIN_DARK)
        core.setAlpha(int(72 * fade))
        painter.setBrush(QBrush(core))
        painter.drawEllipse(QPointF(0.0, 0.0), bw * 0.44, bl * 0.34)

        # 毛细拖尾
        for a, length, width in self._tails:
            path = QPainterPath()
            path.moveTo(0.0, 0.0)
            mx = math.cos(a) * length * 0.5 + math.cos(a + 1.2) * length * 0.18
            my = math.sin(a) * length * 0.5 + math.sin(a + 1.2) * length * 0.18
            path.quadTo(mx, my, math.cos(a) * length, math.sin(a) * length)
            pen = QPen(QColor(107, 74, 36, int(105 * fade)), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)

        # L1 外翻的六足：比正常步态摊得更开，前 0.4 秒还在抽搐。
        # 必须**先画**：腿根落在躯干下面，画在后面会变成压在尸体上的几条线。
        twitch = 0.0
        if self.age < 0.4:
            twitch = (1.0 - self.age / 0.4) ** 2
        leg_pen = QPen(QColor(96, 54, 28, int(205 * fade)), 1.5)
        leg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        leg_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(leg_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        legs = QPainterPath()
        for side in (-1.0, 1.0):
            for i in range(3):
                by = -bl * 0.22 + i * bl * 0.20
                j = math.sin(self.age * 120.0 + i * 2.1 + side) * 3.0 * twitch
                legs.moveTo(side * bw * 0.44, by)
                legs.lineTo(side * bw * 0.74, by + (i - 1) * bl * 0.06 + j * 0.5)
                legs.lineTo(side * bw * (1.02 + 0.06 * i), by + (i - 1) * bl * 0.13 + j)
        painter.drawPath(legs)

        # L2 压扁的躯干
        sprite = ResourceManager.get_sprite(self._sprite_name)
        painter.save()
        painter.scale(sx, sy)
        if sprite is not None and not sprite.isNull():
            rect = QRectF(-bw / 2.0, -bl / 2.0, bw, bl)
            painter.setOpacity(0.92 * fade)
            painter.drawPixmap(rect, sprite, QRectF(sprite.rect()))
            # 压掉贴图的高光：死掉的壳不该反光
            dull = QColor(STAIN_DARK)
            dull.setAlpha(int(58 * fade))
            painter.setBrush(QBrush(dull))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(0.0, bl * 0.06), bw * 0.42, bl * 0.34)
        else:
            painter.setBrush(QBrush(QColor(58, 32, 16, int(210 * fade))))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(0.0, 0.0), bw * 0.52, bl * 0.46)
        painter.restore()
        painter.setOpacity(1.0)

        # L3 触须：两条向两侧摊平的曲线（贴着桌面，不是竖着的）
        sway = math.sin(self.age * 3.0) * 1.6
        ant_pen = QPen(QColor(58, 26, 13, int(195 * fade)), 1.2)
        ant_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(ant_pen)
        antennae = QPainterPath()
        for side in (-1.0, 1.0):
            antennae.moveTo(side * bw * 0.20, -bl * 0.42)
            antennae.quadTo(
                side * bw * 0.70,
                -bl * 0.54,
                side * bw * 0.92,
                -bl * 0.10 + sway * side,
            )
        painter.drawPath(antennae)

        # L4 体液高光
        painter.setPen(Qt.PenStyle.NoPen)
        hl = QColor(STAIN_HIGHLIGHT)
        hl.setAlpha(int(178 * fade))
        painter.setBrush(QBrush(hl))
        for sx_, sy_, r in self._spots:
            painter.drawEllipse(QPointF(sx_, sy_), r * sx, r * sy * 0.78)

        painter.restore()

    @classmethod
    def from_roach(cls, roach, lifetime: float = 22.0, splat_amount: float = 1.0):
        """从一只活着的蟑螂拍出快照。调用方负责把它 `die()` 掉。"""
        try:
            sprite_name = roach._current_body_sprite_name()
        except Exception:
            sprite_name = "cockroach_adult_body"
        return cls(
            x=roach.pos.x,
            y=roach.pos.y,
            angle=roach.angle,
            scale=roach.scale,
            base_width=roach.base_width,
            base_height=roach.base_height,
            stage=roach.stage,
            gender=roach.gender,
            sprite_name=sprite_name,
            individual_tone=roach.individual_tone,
            lifetime=lifetime,
            splat_amount=splat_amount,
        )
