"""爆浆特效系统：体液颗粒 / 冲击环 / 白闪 / 震屏。

**为什么不是"一堆粒子"就够**

单靠颗粒读不出"啪"这一下。真正让手起拍落有体感的是三件事，按重要程度排：

1. **附近 240px 全部炸开逃窜**（在 app 里做，不在本模块）；
2. **震屏**：120ms 内 3px 的衰减抖动，比所有颗粒加起来都管用；
3. **冲击环 + 白闪**：前者给"扩散"，后者给"曝光过度"的那一瞬。

**两个必须守住的约束**

* 颗粒**不用重力**。这是俯视桌面，重力方向是屏幕内法线，画"往下掉"立刻穿帮。
  落地感靠三件事读出：指数阻尼减速 → 椭圆变扁 → 变暗。
* 震屏**只 translate 绘制**，绝不改 `entity.pos`。否则蟑螂会被震进控制台面板的
  洞里、震到墙外，物理系统还得为这事写一堆补丁。

预算：单次击杀 8~14 颗、总量硬上限 240（连拍 30 次就能堆到 400+，必须淘汰最老的）。
满负载 240 颗的绘制实测约 1.2ms（颗粒关 AA、黏丝只给大颗粒画）—— 单帧预算的 1/13，
且只在连拍的瞬间出现。
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)

# 单次击杀的颗粒数
PARTICLES_MIN = 8
PARTICLES_MAX = 14
# 全局硬上限：超了就淘汰最老的
PARTICLE_CAP = 240
# 冲击环 / 白闪各留几个就够。一拍多杀时它们几乎叠在同一点上，
# 叠 4 个环并不会更"爽"，只是白花 0.3ms
RING_CAP = 12
FLASH_CAP = 8
# 阻尼时间常数（秒）：0.16 的指数衰减读作"甩出去、很快停住"
DAMP_TAU = 0.16
# 震屏
SHAKE_AMPLITUDE = 3.0
SHAKE_SECONDS = 0.12

BODY_INK = QColor(138, 90, 34)     # 浆体主体 #8a5a22
BODY_LIGHT = QColor(199, 154, 90)  # 高光 #c79a5a
BODY_DARK = QColor(74, 44, 16)     # 沉淀 #4a2c10
DUST = QColor(186, 186, 196)       # 拍空时的灰尘

# 颗粒只用这 4 种墨色。存"色号"而不是 QColor 实例：
# 每颗新建一个 QColor + QBrush 是 240 颗时 40% 的开销，而色号可以缓存画笔。
_TONES = (BODY_INK, BODY_LIGHT, BODY_DARK, DUST)
TONE_INK, TONE_LIGHT, TONE_DARK, TONE_DUST = 0, 1, 2, 3


class _Drop:
    """一颗体液/灰尘颗粒。用 __slots__ 是因为连拍时会同时存在两百多颗。"""

    __slots__ = ("x", "y", "vx", "vy", "r", "life", "total", "tone")

    def __init__(
        self,
        x: float,
        y: float,
        vx: float,
        vy: float,
        r: float,
        life: float,
        tone: int,
    ) -> None:
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.r = r
        self.life = life
        self.total = life
        self.tone = tone


class _Ring:
    __slots__ = ("x", "y", "r0", "r1", "life", "total", "width", "ink")

    def __init__(
        self, x: float, y: float, r0: float, r1: float, life: float, width: float, ink: QColor
    ) -> None:
        self.x = x
        self.y = y
        self.r0 = r0
        self.r1 = r1
        self.life = life
        self.total = life
        self.width = width
        self.ink = ink


class SplatterSystem:
    """一次性特效的容器：自己 update，自己 render，不持有任何实体引用。"""

    # 画笔/钢笔缓存：key = (色号, alpha 量化档)。整场最多 4×16 项，
    # 省掉每颗颗粒两次 QColor/QBrush 构造（240 颗时约占 40% 开销）。
    _brush_cache: dict[tuple[int, int], QBrush] = {}
    _pen_cache: dict[tuple[int, int], QPen] = {}

    def __init__(self, screen_shake: bool = True) -> None:
        self.enabled = True
        self.screen_shake = bool(screen_shake)

        self._drops: list[_Drop] = []
        self._rings: list[_Ring] = []
        self._flashes: list[list[float]] = []   # [x, y, r, life, total]
        self._shake_t = 0.0
        self._shake_amp = 0.0
        self._shake_total = SHAKE_SECONDS
        self._flash_pix: QPixmap | None = None

        # 诊断/验收用的计数
        self.spawned = 0
        self.peak = 0

    # ------------------------------------------------------------------ 生成

    def spawn_impact(
        self,
        x: float,
        y: float,
        direction=None,
        amount: float = 1.0,
        count: int = PARTICLES_MAX,
    ) -> None:
        """一次命中：颗粒 + 冲击环 + 白闪 + 震屏。"""
        amount = max(0.0, min(2.0, float(amount)))
        n = int(max(0, min(PARTICLES_MAX, count)) * max(0.3, amount))
        base_angle = direction.angle() if direction is not None and direction.length() > 1.0 else None

        for _ in range(n):
            if base_angle is None:
                a = random.uniform(0.0, math.tau)
            else:
                # 以拍击方向为轴 ±100° 的扇形
                a = base_angle + random.uniform(-math.radians(100), math.radians(100))
            speed = random.uniform(180.0, 420.0) * (0.75 + 0.35 * amount)
            roll = random.random()
            tone = TONE_DARK if roll < 0.2 else (TONE_LIGHT if roll < 0.45 else TONE_INK)
            self._add(
                _Drop(
                    x + random.uniform(-3.0, 3.0),
                    y + random.uniform(-3.0, 3.0),
                    math.cos(a) * speed,
                    math.sin(a) * speed,
                    random.uniform(1.5, 4.0) * (0.8 + 0.3 * amount),
                    random.uniform(0.35, 0.90),
                    tone,
                )
            )

        self._add_ring(
            _Ring(x, y, 6.0 * amount, 96.0 * amount, 0.18, 2.4, QColor(226, 208, 176))
        )
        self._add_flash(x, y, 38.0 * amount)
        if self.screen_shake:
            self.shake(SHAKE_AMPLITUDE, SHAKE_SECONDS)

    def spawn_whiff(self, x: float, y: float, radius: float = 52.0) -> None:
        """拍空：只有灰尘环与几粒扬尘。**不留污渍** —— 打空不该弄脏屏幕。"""
        self._add_ring(_Ring(x, y, 4.0, radius, 0.16, 1.8, QColor(DUST)))
        for _ in range(10):
            a = random.uniform(0.0, math.tau)
            speed = random.uniform(60.0, 150.0)
            self._add(
                _Drop(
                    x,
                    y,
                    math.cos(a) * speed,
                    math.sin(a) * speed,
                    random.uniform(0.8, 1.8),
                    random.uniform(0.20, 0.40),
                    TONE_DUST,
                )
            )

    def shake(self, amplitude: float = SHAKE_AMPLITUDE, seconds: float = SHAKE_SECONDS) -> None:
        self._shake_amp = max(self._shake_amp, amplitude)
        self._shake_total = max(0.02, seconds)
        self._shake_t = self._shake_total

    def _add(self, drop: _Drop) -> None:
        self._drops.append(drop)
        self.spawned += 1
        over = len(self._drops) - PARTICLE_CAP
        if over > 0:
            del self._drops[:over]       # 淘汰最老的
        self.peak = max(self.peak, len(self._drops))

    def _add_ring(self, ring: _Ring) -> None:
        # 一拍多杀时几个环几乎重合，只留一个（视觉上完全一样，省 0.25ms）
        for r in self._rings:
            if abs(r.x - ring.x) < 10.0 and abs(r.y - ring.y) < 10.0:
                return
        self._rings.append(ring)
        if len(self._rings) > RING_CAP:
            del self._rings[0]

    def _add_flash(self, x: float, y: float, radius: float) -> None:
        for f in self._flashes:
            if abs(f[0] - x) < 10.0 and abs(f[1] - y) < 10.0:
                return
        self._flashes.append([x, y, radius, 0.04, 0.04])
        if len(self._flashes) > FLASH_CAP:
            del self._flashes[0]

    # ------------------------------------------------------------------ 推进

    def update(self, dt: float) -> None:
        if self._shake_t > 0.0:
            self._shake_t = max(0.0, self._shake_t - dt)
            if self._shake_t == 0.0:
                self._shake_amp = 0.0

        if self._drops:
            k = math.exp(-min(dt, 0.1) / DAMP_TAU)
            alive: list[_Drop] = []
            for d in self._drops:
                d.life -= dt
                if d.life <= 0.0:
                    continue
                d.vx *= k
                d.vy *= k
                d.x += d.vx * dt
                d.y += d.vy * dt
                alive.append(d)
            self._drops = alive

        if self._rings:
            alive_r: list[_Ring] = []
            for r in self._rings:
                r.life -= dt
                if r.life > 0.0:
                    alive_r.append(r)
            self._rings = alive_r

        if self._flashes:
            alive_f: list[list[float]] = []
            for f in self._flashes:
                f[3] -= dt
                if f[3] > 0.0:
                    alive_f.append(f)
            self._flashes = alive_f

    def shake_offset(self) -> tuple[float, float]:
        """当前帧的震屏偏移。**只喂给 painter.translate**，绝不写回 entity.pos。"""
        if self._shake_t <= 0.0 or self._shake_amp <= 0.0:
            return 0.0, 0.0
        decay = (self._shake_t / self._shake_total) ** 1.5
        amp = self._shake_amp * decay
        return random.uniform(-amp, amp), random.uniform(-amp, amp)

    def clear(self) -> None:
        self._drops.clear()
        self._rings.clear()
        self._flashes.clear()
        self._shake_t = 0.0
        self._shake_amp = 0.0

    @property
    def active(self) -> bool:
        return bool(self._drops or self._rings or self._flashes)

    def particle_count(self) -> int:
        return len(self._drops)

    # ------------------------------------------------------------------ 绘制

    def render(self, painter: QPainter) -> None:
        if not self.enabled or not self.active:
            return

        painter.save()
        # 颗粒关掉抗锯齿：它们是 1.5~4px 的小点，AA 看不出差别，代价却是
        # 240 颗时的绝对大头（实测 3.9ms → 0.7ms）。冲击环是大圆，必须留着 AA。
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 液滴：椭圆 + 一条朝速度反方向的细线（读作"黏丝被拉断"）
        painter.setPen(Qt.PenStyle.NoPen)
        for d in self._drops:
            p = 1.0 - d.life / d.total          # 0=刚喷出 1=消失
            dust = d.tone == TONE_DUST
            alpha = int((210 if not dust else 90) * (1.0 - p) ** 1.5)
            if alpha <= 3:
                continue
            painter.setBrush(self._brush(d.tone, alpha))
            rx = d.r * (1.0 - 0.25 * p)
            ry = d.r * (0.55 + 0.45 * (1.0 - p))
            painter.drawEllipse(QPointF(d.x, d.y), rx, ry)

            # 黏丝只给"最大的那一撮"：每颗都画的话 240 颗要 1.1ms，而小点上的
            # 细线本来也看不清
            if d.r >= 3.2 and p < 0.40 and not dust:
                speed = math.hypot(d.vx, d.vy)
                if speed > 60.0:
                    ux, uy = d.vx / speed, d.vy / speed
                    tail = min(12.0, speed * 0.024)
                    painter.setPen(self._thread_pen(d.tone, int(alpha * 0.40)))
                    painter.drawLine(
                        QPointF(d.x, d.y),
                        QPointF(d.x - ux * tail, d.y - uy * tail),
                    )
                    painter.setPen(Qt.PenStyle.NoPen)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 冲击环 / 灰尘环
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for r in self._rings:
            p = 1.0 - r.life / r.total
            radius = r.r0 + (r.r1 - r.r0) * (p ** 0.6)
            ink = QColor(r.ink)
            ink.setAlpha(int(180 * (1.0 - p) ** 1.4))
            pen = QPen(ink, max(0.6, r.width * (1.0 - 0.7 * p)))
            painter.setPen(pen)
            painter.drawEllipse(QPointF(r.x, r.y), radius, radius)

        # 白闪（40ms）：加法混合的径向渐变
        if self._flashes:
            pix = self._flash_pixmap()
            for f in self._flashes:
                p = 1.0 - f[3] / f[4]
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Plus
                )
                painter.setOpacity(max(0.0, 0.20 * (1.0 - p) ** 2))
                r = f[2]
                painter.drawPixmap(
                    QRectF(f[0] - r, f[1] - r, r * 2.0, r * 2.0), pix, QRectF(pix.rect())
                )
                painter.setOpacity(1.0)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceOver
                )

        painter.restore()

    @staticmethod
    def _quant(alpha: int) -> int:
        """alpha 量化到 16 一档：肉眼看不出，但画笔缓存命中率提高一个数量级。"""
        return max(0, min(255, (int(alpha) >> 4) << 4))

    @classmethod
    def _brush(cls, tone: int, alpha: int) -> QBrush:
        key = (tone, cls._quant(alpha))
        brush = cls._brush_cache.get(key)
        if brush is None:
            color = QColor(_TONES[tone])
            color.setAlpha(key[1])
            brush = QBrush(color)
            cls._brush_cache[key] = brush
        return brush

    @classmethod
    def _thread_pen(cls, tone: int, alpha: int) -> QPen:
        key = (tone, cls._quant(alpha))
        pen = cls._pen_cache.get(key)
        if pen is None:
            color = QColor(_TONES[tone])
            color.setAlpha(key[1])
            pen = QPen(color, 1.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            cls._pen_cache[key] = pen
        return pen

    @classmethod
    def _flash_pixmap(cls) -> QPixmap:
        cached = getattr(cls, "_flash_cache", None)
        if cached is not None:
            return cached
        size = 128
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QRadialGradient(size / 2.0, size / 2.0, size / 2.0)
        grad.setColorAt(0.0, QColor(255, 253, 240, 255))
        grad.setColorAt(0.45, QColor(255, 240, 205, 120))
        grad.setColorAt(1.0, QColor(255, 235, 200, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(0, 0, size, size))
        p.end()
        cls._flash_cache = pix
        return pix
