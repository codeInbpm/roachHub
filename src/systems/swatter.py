"""苍蝇拍 / 蟑螂拍。

它不是一个常驻道具，而是**对输入闸门的一次受控临时放开**：拿起时整屏归我们，
右键 / 热键 / 12 秒闲置就把桌面还回去。

`AR在上` 状态机::

    IDLE ──(托盘/热键)──> ARMED ──(左键)──> WINDUP 70ms ──> RECOVER 200ms ──┐
      ^                     │                                                 │
      └──(右键/热键/超时)────┘ <───────────────────────────────────────────────┘
                                    冷却 280ms（WINDUP 起算）

两个数值不是拍脑袋定的：

* **70ms**：60FPS 下约 4.2 帧，刚好读得出"手在动"；低于 3 帧读不出挥拍，
  高于 100ms 玩家会产生"我明明点到了"的挫败感。
* **判定半径 ≥ 55px**（蟑螂的 `perception_panic_radius`）：贴到 55px 内它会先
  僵直 0.12s，我们 70ms 的挥拍正好落在那个窗口里，体感是"我压住了它"。
  半径更小就会出现"刚贴近它反而跑了"，非常难受。

拍面**预渲染成 QPixmap**：每帧循环 drawLine 400 次要 3~5ms，一次性贴图只要 0.05ms。
半透明（α 0.82）不是妥协 —— 真苍蝇拍本来就透光，正好解决"道具挡住目标"。
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from ..utils.math_utils import Vector2D, clamp

STATE_IDLE = "idle"
STATE_ARMED = "armed"
STATE_WINDUP = "windup"
STATE_RECOVER = "recover"

# 拍面尺寸（基准局部坐标，像素）；实际大小 = 基准 × visual_scale
HEAD_W = 168.0
HEAD_H = 208.0
# 预渲染贴图比拍面大一圈，给包边和高光留余地
PAD = 10.0
PIX_W = HEAD_W + PAD * 2.0
PIX_H = HEAD_H + PAD * 2.0
# 默认缩放：基准尺寸下拍面宽 168 > 判定直径 131，会"看着盖住却没死"；
# 0.70 后拍面 118x146，略小于判定圈，手感变成"擦到边就算中"。
DEFAULT_VISUAL_SCALE = 0.70
# 待机时的最大倾斜：略微倾斜才有"握在手里"的感觉
ARMED_TILT = -6.0
# 下压瞬间的起始角（WINDUP 从这里转到 0）
WINDUP_TILT = -38.0


class Swatter:
    """拍子本体：状态机 + 跟随 + 绘制。命中结算由外部回调负责。"""

    _handle_cache: Optional[dict] = None

    def __init__(self, config: Optional[dict] = None) -> None:
        self.enabled = True
        self.head_radius = 44.0
        self.swing_time = 0.070
        self.recover_time = 0.200
        self.cooldown = 0.280
        self.idle_disarm = 12.0
        self.splat_amount = 1.0
        self.screen_shake = True
        self.max_victims = 4
        self.detail = 2                 # 由主循环按画质档位写入（0 = 极简）
        self.visual_scale = DEFAULT_VISUAL_SCALE

        #: (落点, 拍击方向) -> None；由 app 负责真正结算命中
        self.on_impact: Optional[Callable[[Vector2D, Vector2D], None]] = None
        #: 状态变化（拿起/放下）时通知覆盖层同步光标与鼠标接管
        self.on_state_changed: Optional[Callable[[], None]] = None
        #: (原因) -> None
        self.on_disarm: Optional[Callable[[str], None]] = None

        self.armed = False
        self.state = STATE_IDLE
        self.pos = Vector2D(0.0, 0.0)
        self._target = Vector2D(0.0, 0.0)
        self._aim = Vector2D(1.0, 0.0)
        self._hit = Vector2D(0.0, 0.0)

        self._t = 0.0
        self._cd = 0.0
        self._idle = 0.0
        self._clock = 0.0
        self._prev_angle = 0.0
        self._impacted = False

        self.kills = 0                  # 累计拍死（托盘/控制台展示，不持久化）

        self.apply_config(config or {})

    # ------------------------------------------------------------------ 配置

    def apply_config(self, cfg: dict) -> None:
        self.enabled = bool(cfg.get("enabled", True))
        self.head_radius = clamp(float(cfg.get("head_radius", 44.0)), 12.0, 90.0)
        self.swing_time = max(0.02, float(cfg.get("swing_ms", 70)) / 1000.0)
        self.recover_time = max(0.0, float(cfg.get("recover_ms", 200)) / 1000.0)
        self.cooldown = max(self.swing_time, float(cfg.get("cooldown_ms", 280)) / 1000.0)
        self.idle_disarm = max(0.0, float(cfg.get("idle_disarm_seconds", 12.0)))
        self.splat_amount = clamp(float(cfg.get("splat_amount", 1.0)), 0.0, 2.0)
        self.screen_shake = bool(cfg.get("screen_shake", True))
        # 只影响观感，不改判定：判定半径仍然是 head_radius（见 radius_for）
        self.visual_scale = clamp(
            float(cfg.get("visual_scale", DEFAULT_VISUAL_SCALE)), 0.45, 1.30
        )
        if not self.enabled and self.armed:
            self.disarm("disabled")

    # ------------------------------------------------------------------ 状态

    def arm(self, cursor: Optional[Vector2D] = None) -> bool:
        """拿起拍子。返回是否真的拿起来了。"""
        if not self.enabled:
            return False
        if cursor is not None:
            self._target = Vector2D(cursor.x, cursor.y)
        if self.armed:
            self._idle = 0.0
            return True
        self.armed = True
        self.state = STATE_ARMED
        self.pos = Vector2D(self._target.x, self._target.y)
        self._t = 0.0
        self._cd = 0.0
        self._idle = 0.0
        self._impacted = False
        self._notify()
        return True

    def disarm(self, reason: str = "manual") -> bool:
        """放下拍子。桌面立刻回到"能点"的状态。"""
        if not self.armed:
            return False
        self.armed = False
        self.state = STATE_IDLE
        self._impacted = False
        self._t = 0.0
        self._cd = 0.0
        self._notify()
        if self.on_disarm is not None:
            self.on_disarm(reason)
        return True

    def toggle(self, cursor: Optional[Vector2D] = None) -> bool:
        if self.armed:
            self.disarm("toggle")
            return False
        return self.arm(cursor)

    def swing(self, hit: Optional[Vector2D] = None) -> bool:
        """起手。真正的伤害在 WINDUP 结束的那一帧结算（先动，后判）。"""
        if not self.armed or self.state != STATE_ARMED or self._cd > 0.0:
            return False
        self.state = STATE_WINDUP
        self._t = 0.0
        self._impacted = False
        self._prev_angle = self._current_angle()
        self._cd = self.cooldown
        self._idle = 0.0
        if hit is not None:
            self._hit = Vector2D(hit.x, hit.y)
        else:
            self._hit = Vector2D(self.pos.x, self.pos.y)
        return True

    def _notify(self) -> None:
        if self.on_state_changed is not None:
            self.on_state_changed()

    # ------------------------------------------------------------------ 推进

    def update(self, dt: float, cursor: Optional[Vector2D] = None) -> None:
        if not self.armed:
            return

        self._clock += dt
        if cursor is not None:
            self._target.x = cursor.x
            self._target.y = cursor.y

        dx = self._target.x - self.pos.x
        dy = self._target.y - self.pos.y
        # lerp 0.55/帧 @60FPS ≈ 指数系数 18/s：跟手但留一点点"甩"的惯性
        k = 1.0 - math.exp(-dt * 18.0)
        self.pos.x += dx * k
        self.pos.y += dy * k

        # 拍击方向 = 光标位移方向（静止时保留上一次，方向只用于颗粒扇形）
        dist = math.hypot(dx, dy)
        if dist > 2.0:
            nx, ny = dx / dist, dy / dist
            ax = self._aim.x * 0.82 + nx * 0.18
            ay = self._aim.y * 0.82 + ny * 0.18
            length = math.hypot(ax, ay)
            if length > 1e-4:
                self._aim = Vector2D(ax / length, ay / length)
        # "闲置"= 没动也没挥拍。只看挥拍的话，蹲着瞄半天会被强行收拍
        if dist > 3.0:
            self._idle = 0.0

        if self._cd > 0.0:
            self._cd = max(0.0, self._cd - dt)

        self._idle += dt
        if self.idle_disarm > 0.0 and self.state == STATE_ARMED and self._idle > self.idle_disarm:
            self.disarm("idle")
            return

        if self.state == STATE_WINDUP:
            self._t += dt
            if self._t >= self.swing_time:
                if not self._impacted:
                    self._impacted = True
                    if self.on_impact is not None:
                        self.on_impact(self._hit, self._aim)
                self.state = STATE_RECOVER
                self._prev_angle = 0.0
                self._t = 0.0
        elif self.state == STATE_RECOVER:
            self._t += dt
            if self._t >= self.recover_time:
                self.state = STATE_ARMED
                self._t = 0.0

    # ------------------------------------------------------------------ 命中

    def radius_for(self, entity) -> float:
        """一只实体的有效判定半径：拍面半径 + 体型补偿。"""
        return self.head_radius + 0.30 * max(entity.width, entity.height)

    def can_hit(self, entity, point: Vector2D) -> bool:
        return entity.pos.distance_to(point) <= self.radius_for(entity)

    # ------------------------------------------------------------------ 姿态

    def _current_angle(self) -> float:
        if self.state == STATE_WINDUP:
            p = min(1.0, self._t / self.swing_time) if self.swing_time > 0 else 1.0
            ease = 1.0 - (1.0 - p) ** 3
            return WINDUP_TILT * (1.0 - ease)
        if self.state == STATE_RECOVER:
            p = min(1.0, self._t / self.recover_time) if self.recover_time > 0 else 1.0
            return 9.0 * math.sin(math.pi * p) * (1.0 - p)
        return ARMED_TILT + 1.5 * math.sin(self._clock * 1.7)

    def _pose(self) -> tuple[float, float, float, float, float, float]:
        """(角度, sx, sy, 影 dx, 影 dy, 影 alpha)。"""
        angle = self._current_angle()
        if self.state == STATE_WINDUP:
            p = min(1.0, self._t / self.swing_time) if self.swing_time > 0 else 1.0
            s = 0.94 + 0.12 * math.sin(math.pi * p)
            shadow = (10.0 - 8.0 * p, 12.0 - 10.0 * p, 0.28 + 0.22 * p)
            return angle, s, s, shadow[0], shadow[1], shadow[2]
        if self.state == STATE_RECOVER:
            p = min(1.0, self._t / self.recover_time) if self.recover_time > 0 else 1.0
            # 砸下去的那一帧纵向 0.92，然后回弹过冲
            sy = 0.92 + 0.12 * min(1.0, p / 0.30) - 0.04 * max(0.0, p - 0.30) / 0.70
            return angle, 1.0, sy, 2.0, 2.0, 0.50
        return angle, 1.0, 1.0, 10.0, 12.0, 0.28

    # ------------------------------------------------------------------ 绘制

    def render(self, painter: QPainter, screen_w: float, screen_h: float) -> None:
        if not self.armed:
            return

        # 屏幕四周一圈琥珀色细边：唯一"整屏已经被拍子接管"的全局提示
        pulse = 0.72 + 0.28 * math.sin(self._clock * 2.6)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 176, 64, int(96 * pulse)), 2.0))
        painter.drawRect(QRectF(1.0, 1.0, max(0.0, screen_w - 2.0), max(0.0, screen_h - 2.0)))
        painter.restore()

        angle, sx, sy, sdx, sdy, salpha = self._pose()
        vs = self.visual_scale

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(self.pos.x, self.pos.y)

        # 1. 影子 —— 唯一的"高度"线索，没有它拍子像贴在屏幕上的一张纸
        painter.save()
        painter.translate(sdx * vs, sdy * vs)
        painter.rotate(angle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(255 * salpha)))
        painter.drawEllipse(
            QPointF(0.0, 0.0), HEAD_W * 0.46 * vs, HEAD_H * 0.44 * vs
        )
        painter.restore()

        # 2. 运动模糊的残影（只在挥拍那 4 帧里画一次）
        pad_w, pad_h = PIX_W * vs, PIX_H * vs
        if self.state == STATE_WINDUP:
            delta = self._prev_angle - angle
            if abs(delta) > 0.8:
                painter.save()
                painter.rotate(delta)
                painter.setOpacity(0.22)
                pixm = self._head_pixmap(self.detail, vs)
                painter.drawPixmap(
                    QRectF(-pad_w / 2.0, -pad_h / 2.0, pad_w, pad_h),
                    pixm,
                    QRectF(pixm.rect()),
                )
                painter.restore()
                painter.setOpacity(1.0)

        painter.rotate(angle)
        painter.scale(sx, sy)

        # 3. 手柄 → 4. 拍面
        self._draw_handle(painter, vs)
        pix = self._head_pixmap(self.detail, vs)
        painter.drawPixmap(
            QRectF(-pad_w / 2.0, -pad_h / 2.0, pad_w, pad_h),
            pix,
            QRectF(pix.rect()),
        )
        painter.restore()

        # 5. 瞄准点：光标被藏起来了，没有这个点用户算不准落点
        painter.save()
        painter.translate(self.pos.x, self.pos.y)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(64, 44, 22, 120))
        painter.drawEllipse(QPointF(0.0, 0.0), 2.4, 2.4)
        painter.restore()

    # ------------------------------------------------------------- 预渲染资源

    @classmethod
    def _head_pixmap(cls, detail: int = 2, scale: float = 1.0) -> QPixmap:
        detail = 0 if detail <= 0 else (1 if detail == 1 else 2)
        scale = clamp(float(scale), 0.30, 1.50)
        # 按 (画质档, 缩放档) 缓存：缩放变了要重渲染，否则位图被二次采样会发虚
        key = (detail, int(round(scale * 100)))
        cache = getattr(cls, "_pix_cache", None)
        if cache is None:
            cache = {}
            cls._pix_cache = cache
        if key in cache:
            return cache[key]

        w, h = PIX_W * scale, PIX_H * scale
        dpr = 1.0
        try:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                dpr = max(1.0, float(screen.devicePixelRatio()))
        except Exception:
            dpr = 1.0

        pix = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)

        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(w / 2.0, h / 2.0)
        p.scale(scale, scale)

        rx, ry = HEAD_W / 2.0, HEAD_H / 2.0
        ellipse = QPainterPath()
        ellipse.addEllipse(QPointF(0.0, 0.0), rx, ry)

        # 半透明网面：能看见底下的虫。0.82 实测太实（缩小后整只虫糊在下面），
        # 0.74 才留得住轮廓；靠包边 + 网格 + 影子维持"实体感"。
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(238, 229, 209, int(255 * 0.74)))
        p.drawPath(ellipse)

        # 网格（只在椭圆内）
        p.setClipPath(ellipse)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if detail >= 2:
            step_x, step_y = 13.0, 15.0
            grid_alpha = 96
        elif detail == 1:
            step_x, step_y = 26.0, 30.0
            grid_alpha = 84
        else:
            step_x, step_y = 42.0, 52.0
            grid_alpha = 70
        # 线宽反算：缩小 0.7 倍后 1.0 会变成 0.7px，抗锯齿下整张网糊成一片
        grid_w = 1.0 / scale
        p.setPen(QPen(QColor(96, 82, 60, grid_alpha), grid_w))
        x = -rx
        while x <= rx:
            p.drawLine(QPointF(x, -ry), QPointF(x, ry))
            x += step_x
        y = -ry
        while y <= ry:
            p.drawLine(QPointF(-rx, y), QPointF(rx, y))
            y += step_y
        p.setClipping(False)

        # 包边 + 一道高光弧，避免整块看起来像塑料
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(78, 64, 46, 214), 3.0))
        p.drawPath(ellipse)
        p.setPen(QPen(QColor(255, 250, 236, 120), 1.6))
        p.drawArc(QRectF(-rx * 0.94, -ry * 0.94, rx * 1.88, ry * 1.88), 40 * 16, 70 * 16)
        p.end()

        cache[key] = pix
        return pix

    @classmethod
    def _draw_handle(cls, painter: QPainter, scale: float = 1.0) -> None:
        scale = clamp(float(scale), 0.30, 1.50)
        key = int(round(scale * 100))
        cache = cls._handle_cache
        if not isinstance(cache, dict):
            cache = {}
            cls._handle_cache = cache
        if key not in cache:
            x0, y0 = 0.0, HEAD_H * 0.36
            x1, y1 = 76.0, HEAD_H * 0.36 + 238.0
            half0, half1 = 8.5, 14.0
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy) or 1.0
            px, py = -dy / length, dx / length

            path = QPainterPath()
            path.moveTo(x0 + px * half0, y0 + py * half0)
            path.lineTo(x1 + px * half1, y1 + py * half1)
            path.quadTo(x1 + px * half1 + 3.0, y1 + py * half1 + 9.0,
                        x1 - px * half1 + 1.0, y1 - py * half1 + 8.0)
            path.lineTo(x0 - px * half0, y0 - py * half0)
            path.closeSubpath()
            grad = QLinearGradient(x0, y0, x1, y1)
            grad.setColorAt(0.0, QColor(138, 95, 60))    # 暖木色 #8a5f3c
            grad.setColorAt(1.0, QColor(92, 58, 34))     # #5c3a22
            cache[key] = (path, grad)
        path, grad = cache[key]

        painter.save()
        painter.scale(scale, scale)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawPath(path)

        # 握把三道防滑纹（沿手柄轴线算，不能写死坐标 —— 手柄是斜的）
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(46, 28, 16, 150), 2.0))
        x0, y0 = 0.0, HEAD_H * 0.36
        x1, y1 = 76.0, HEAD_H * 0.36 + 238.0
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy) or 1.0
        px, py = -dy / length, dx / length
        for t in (0.70, 0.80, 0.90):
            cx, cy = x0 + dx * t, y0 + dy * t
            painter.drawLine(
                QPointF(cx + px * 10.0, cy + py * 10.0),
                QPointF(cx - px * 10.0, cy - py * 10.0),
            )
        painter.restore()
