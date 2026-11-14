"""触须 (Antennae) 运动链。

蟑螂的触须是它最主要的感觉器官，几乎时刻不停地在扫探：从基部到尖端传递一道
行波 (traveling wave)，越靠近尖端摆幅越大，因此是「根部稳、梢部活」的鞭状运动，
而不是整根刚性摆动。

实现：多段链式结构，逐段累加弯曲量；整根触须的朝向由一个带滞后的弹簧追随目标
方向（威胁方位 / 梳理目标），因此转向时机体会先动、触须稍后跟上，形成自然的延迟感。
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen


SEGMENTS = 7


class _Antenna:
    __slots__ = ("side", "base", "seg_len", "n", "aim", "aim_target", "phase", "points", "bend")

    def __init__(self, side: int, base: tuple[float, float], seg_len: float, n: int = SEGMENTS) -> None:
        self.side = side
        self.base = base
        self.seg_len = seg_len
        self.n = n
        self.aim = -math.pi / 2.0 + side * 0.22
        self.aim_target = self.aim
        self.phase = random.uniform(0.0, math.tau)
        self.bend = side * 0.055
        # 复用同一批 [x, y] 槽位：以前每帧新建 8 个元组，200 只时是 10 万个小对象/秒，
        # GC 压力会直接体现成周期性卡顿。
        self.points: list[list[float]] = [[base[0], base[1]] for _ in range(n + 1)]

    def update(
        self,
        dt: float,
        aim_target: float,
        wave_amp: float,
        freq: float,
        curl: float,
        follow_k: float | None = None,
    ) -> None:
        # 朝向追随：时间常数小 = 反应快，但保留一点滞后
        self.aim_target = aim_target
        k = follow_k if follow_k is not None else 1.0 - math.exp(-dt / 0.085)
        self.aim += (aim_target - self.aim) * k

        self.phase = (self.phase + dt * freq * math.tau) % math.tau

        # 弯曲：常态向外微弯，梳理时大幅内卷
        bend = self.side * (0.048 + 0.10 * curl)

        pts = self.points
        x, y = self.base[0], self.base[1]
        pts[0][0] = x
        pts[0][1] = y
        length = self.seg_len
        n = self.n
        for i in range(n):
            t = (i + 1) / n
            wave = wave_amp * (t ** 1.35) * math.sin(self.phase - i * 0.85)
            ang = self.aim + bend * (i + 1) + wave
            x += math.cos(ang) * length
            y += math.sin(ang) * length
            slot = pts[i + 1]
            slot[0] = x
            slot[1] = y
            length *= 0.94


class AntennaRig:
    """左右两根触须的整体驱动与绘制。"""

    def __init__(self, body_len: float, body_width: float) -> None:
        self.body_len = float(body_len)
        self.seg_len = 0.145 * self.body_len
        bx = 0.17 * float(body_width) * 0.5
        by = -0.48 * self.body_len
        self.left = _Antenna(-1, (-bx, by), self.seg_len)
        self.right = _Antenna(1, (bx, by), self.seg_len)

    def update(
        self,
        dt: float,
        aim_left: float,
        aim_right: float,
        wave_amp: float,
        freq: float,
        curl: float,
    ) -> None:
        # exp() 每帧两次没必要 —— 两根触须的 dt 相同
        k = 1.0 - math.exp(-dt / 0.085)
        self.left.update(dt, aim_left, wave_amp, freq, curl, k)
        self.right.update(dt, aim_right, wave_amp, freq, curl, k)

    def draw(self, painter: QPainter, color: QColor, simple: bool = False, detail: int = 2) -> None:
        """整根触须合并成两条路径（近端粗 / 远端细）描边，减少绘制调用。"""
        base_w = 1.3 if simple else 1.55
        tip_w = 0.55 if simple else 0.6
        for ant in (self.left, self.right):
            pts = ant.points
            n = len(pts) - 1
            if n < 2:
                continue
            if detail < 2:
                # 低精度档：隔点取样，弧线仍成立但像素量减半
                sample = pts[::2] + [pts[-1]]
                one = QPainterPath()
                one.moveTo(sample[0][0], sample[0][1])
                for px, py in sample[1:]:
                    one.lineTo(px, py)
                painter.setPen(QPen(color, 1.1 if not simple else 1.2,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawPath(one)
                continue
            half = max(1, n // 2)
            near = QPainterPath()
            near.moveTo(pts[0][0], pts[0][1])
            for i in range(1, half + 1):
                near.lineTo(pts[i][0], pts[i][1])
            far = QPainterPath()
            far.moveTo(pts[half][0], pts[half][1])
            for i in range(half + 1, n + 1):
                far.lineTo(pts[i][0], pts[i][1])

            painter.setPen(QPen(color, base_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(near)
            painter.setPen(QPen(color, tip_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(far)
