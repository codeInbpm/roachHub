"""六足三足步态 (Tripod Gait) 与程序化肢体骨骼。

蟑螂行走使用「交替三足步态」：左前 / 右中 / 左后 构成一组三角支撑，另外三条腿
同时抬摆，两组相位差半周期，因此任何时刻都有三条腿着地。本模块用两段式 IK
（股节 + 胫节）在俯视投影下驱动每条腿：

  * 支撑相 (stance)：脚端在体坐标系里向后平移 —— 等价于脚掌钉在地面不动；
  * 摆动相 (swing)：脚端抬起并快速前摆，抬腿在俯视图里表现为脚端向躯干收拢。

因此动画是"由速度驱动的连续运动"，而不是几张预渲染帧的循环，帧与帧之间天然连贯，
且任何速度下步幅、步频都自动匹配，不会出现溜冰式滑步。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen

from .pose import PoseBlender

# (序号, 足端静息方位角[度, 0=正外侧, 负=朝前], 触及半径[×体长], 股节[×触及], 胫节[×触及])
LEG_TABLE = (
    (0, -40.0, 0.30, 0.62, 0.60),
    (1, 4.0, 0.29, 0.62, 0.60),
    (2, 50.0, 0.36, 0.60, 0.58),
)

# 髋部位置：(体长方向比例, 半体宽方向比例)
HIP_Y = (-0.31, -0.04, 0.20)
HIP_X = (0.80, 0.92, 0.78)

# 交替三足步态相位：左前/右中/左后 同相，其余反相
PHASE = {
    (-1, 0): 0.0, (-1, 1): 0.5, (-1, 2): 0.0,
    (1, 0): 0.5, (1, 1): 0.0, (1, 2): 0.5,
}


@dataclass
class _Leg:
    side: int
    index: int
    hip: tuple[float, float]
    l1: float
    l2: float
    rest: tuple[float, float]     # 足端相对髋部的静息偏移
    phase: float
    foot: list[float] = field(default_factory=lambda: [0.0, 0.0])
    knee: list[float] = field(default_factory=lambda: [0.0, 0.0])
    lift: float = 0.0
    micro: list[float] = field(default_factory=lambda: [0.0, 0.0])
    micro_to: list[float] = field(default_factory=lambda: [0.0, 0.0])
    micro_t: float = 1.0
    micro_wait: float = 0.0
    was_swing: bool = False
    prev_lift: float = 0.0


class LegRig:
    """六足骨架：步态相位 -> 足端轨迹 -> 两段式 IK -> 锥形肢体绘制。"""

    def __init__(self, body_len: float, body_width: float, leg_scale: float = 1.0) -> None:
        self.body_len = float(body_len)
        self.half_w = float(body_width) * 0.5
        self.leg_scale = float(leg_scale)

        self.cycle = random.random()
        self.stride = 0.0
        self.cycle_freq = 0.0
        self.body_sway = 0.0
        self.body_yaw = 0.0
        self.body_bob = 0.0

        self.legs: list[_Leg] = []
        for index, theta_deg, reach_r, l1k, l2k in LEG_TABLE:
            reach = reach_r * self.body_len * self.leg_scale
            theta = math.radians(theta_deg)
            for side in (-1, 1):
                hip = (side * HIP_X[index] * self.half_w, HIP_Y[index] * self.body_len)
                rest = (side * math.cos(theta) * reach, math.sin(theta) * reach)
                leg = _Leg(
                    side=side,
                    index=index,
                    hip=hip,
                    l1=l1k * reach,
                    l2=l2k * reach,
                    rest=rest,
                    phase=PHASE[(side, index)],
                )
                leg.foot = [hip[0] + rest[0], hip[1] + rest[1]]
                leg.micro_wait = random.uniform(0.4, 4.0)
                self.legs.append(leg)

        # 本帧"脚掌落地"事件：(体坐标 x, y, 侧别, 落点力度 0~1)
        # 供痕迹层与音效消费，每帧开头清空。
        self.plant_events: list[tuple[float, float, int, float]] = []
        self._max_reach = max(l.l1 + l.l2 for l in self.legs)

    # ------------------------------------------------------------------ 更新

    def update(self, dt: float, speed: float, pose: PoseBlender) -> None:
        stride_pose = pose.value("stride", 1.0)
        splay = pose.value("splay", 0.3)
        lift_amt = pose.value("lift", 1.0)
        tremor = pose.value("tremor", 0.0)
        groom = pose.value("groom", 0.0)
        hold = pose.value("hold", 0.0)
        flail = pose.value("flail", 0.0)
        dead = pose.value("dead", 0.0)
        anchored = pose.value("anchored", 0.0)
        duty = pose.value("duty", 0.62)

        self.plant_events.clear()

        # 落点平滑系数：与 dt 有关，但与"哪条腿"无关 —— 以前放在 _update_leg 里，
        # 6 条腿各自算一次 exp()（200 只时每帧 1200 次），提到这里只算 1 次。
        foot_k = 1.0 - math.exp(-dt / 0.045)

        # 被捏在手上时脚踩不到地面，但腿仍要蹬 —— flail 允许"原地踏步"
        moving = (speed > 2.0 and anchored < 0.95) or flail > 0.05

        # 步频随速度上升：静止时相位停走，避免"原地踏步"
        self.cycle_freq = min(7.2, 0.9 + speed * 0.038)
        if flail > 0.05:
            self.cycle_freq = max(self.cycle_freq, 3.0 + 7.5 * flail)
        if moving:
            self.cycle = (self.cycle + dt * self.cycle_freq) % 1.0

        # 步幅 = 每周期位移的一半（脚在半个周期内后推一个步幅）
        # 略微放大的步幅（+8%）让肢体动作在实际像素尺寸下更易读，不影响物理位移
        max_stride = 0.62 * self._max_reach
        target_stride = (speed / max(1e-3, self.cycle_freq)) * 1.08 if moving else 0.0
        if flail > 0.05:
            target_stride = max(target_stride, flail * 0.42 * max_stride)
        target_stride = min(target_stride, max_stride)
        self.stride += (target_stride - self.stride) * min(1.0, dt * 9.0)

        # 身体随步态轻微左右摆与偏航（相位为周期两倍：每一组三足落地一次侧倾一次）
        wob = min(1.0, self.stride / max(1.0, 0.22 * self.body_len))
        self.body_sway = math.sin(self.cycle * math.tau) * 0.55 * wob
        self.body_yaw = math.sin(self.cycle * math.tau) * 1.1 * wob
        self.body_bob = math.cos(self.cycle * math.tau * 2.0) * 0.35 * wob

        for leg in self.legs:
            self._update_leg(leg, dt, speed, stride_pose, splay, lift_amt,
                             tremor, groom, hold, flail, dead, anchored, duty, moving,
                             foot_k)

        # 梳理/交配等状态下身体不参与步态摆动
        if groom > 0.4 or dead > 0.4:
            damp = 1.0 - max(groom, dead)
            self.body_sway *= damp
            self.body_yaw *= damp

    def _update_leg(
        self,
        leg: _Leg,
        dt: float,
        speed: float,
        stride_pose: float,
        splay: float,
        lift_amt: float,
        tremor: float,
        groom: float,
        hold: float,
        flail: float,
        dead: float,
        anchored: float,
        duty: float,
        moving: bool,
        foot_k: float,
    ) -> None:
        # 1) 静息位姿：splay 越大足端越外展，形成"压低下盘"的 crouch
        splay_k = 1.0 + 0.16 * splay
        base_x = leg.hip[0] + leg.rest[0] * splay_k
        base_y = leg.hip[1] + leg.rest[1] * splay_k

        # 2) 步态分量
        p = (self.cycle + leg.phase) % 1.0
        stride = self.stride * stride_pose * (1.0 - anchored)
        if p < duty:
            u = p / max(1e-4, duty)
            offset = -stride * 0.5 + stride * u
            swing_lift = 0.0
        else:
            u = (p - duty) / max(1e-4, 1.0 - duty)
            ease = 0.5 - 0.5 * math.cos(math.pi * u)
            offset = stride * 0.5 - stride * ease
            swing_lift = math.sin(math.pi * u)

        # 摆动相 -> 支撑相 的那一刻就是"落地"：把落点交给痕迹层与音效。
        # 用摆动结束时的抬腿量当作落点力度，轻放与踩实自然区分开。
        is_swing = p >= duty
        if leg.was_swing and not is_swing and leg.prev_lift > 0.15:
            strength = (0.35 + 0.65 * leg.prev_lift) * (1.0 - 0.75 * flail) * (1.0 - dead)
            if strength > 0.08:
                self.plant_events.append((leg.foot[0], leg.foot[1], leg.side, strength))
        leg.was_swing = is_swing
        leg.prev_lift = swing_lift

        tx = base_x
        ty = base_y + offset

        # 抬腿：俯视投影下表现为足端向髋部收拢
        if swing_lift > 0.0:
            k = swing_lift * lift_amt * 0.26
            tx += (leg.hip[0] - tx) * k
            ty += (leg.hip[1] - ty) * k

        # 3) 静止时的微调步（tarsal adjustment）：偶尔抬一只脚换个落点
        leg.micro_t = min(1.0, leg.micro_t + dt * 3.2)
        if leg.micro_t >= 1.0 and not moving and groom < 0.3 and dead < 0.3:
            leg.micro_wait -= dt
            if leg.micro_wait <= 0.0:
                leg.micro_to = [random.uniform(-1.6, 1.6), random.uniform(-1.6, 1.6)]
                leg.micro_t = 0.0
                leg.micro_wait = random.uniform(1.2, 5.5)
        if leg.micro_t < 1.0:
            micro_ease = 0.5 - 0.5 * math.cos(math.pi * leg.micro_t)
            mx = leg.micro[0] + (leg.micro_to[0] - leg.micro[0]) * micro_ease
            my = leg.micro[1] + (leg.micro_to[1] - leg.micro[1]) * micro_ease
            tx += mx
            ty += my
            lift_now = math.sin(math.pi * min(1.0, leg.micro_t))
            tx += (leg.hip[0] - tx) * lift_now * 0.07
            ty += (leg.hip[1] - ty) * lift_now * 0.07
            if leg.micro_t >= 1.0:
                leg.micro = [leg.micro_to[0], leg.micro_to[1]]

        # 4) 梳理触角：前足抬到头部反复搓动
        if leg.index == 0 and groom > 0.02:
            rub = math.sin(self.cycle * math.tau * 7.0 + leg.side * 1.1)
            gx = leg.hip[0] * 0.35 + leg.side * 2.5 + rub * 1.6
            gy = -self.body_len * 0.40 + rub * 1.2
            tx += (gx - tx) * groom
            ty += (gy - ty) * groom
            if groom > 0.5:
                tx += (leg.hip[0] - tx) * 0.05 * (1.0 + rub)

        # 4b) 抱着食物啃：两只前足收到口器前方，小幅搓动
        if leg.index == 0 and hold > 0.02:
            rub = math.sin(self.cycle * math.tau * 2.4 + leg.side * 1.3)
            hx_ = leg.hip[0] * 0.62 + leg.side * 2.2 + rub * 0.8
            hy_ = -self.body_len * 0.33 + rub * 0.9
            tx += (hx_ - tx) * hold
            ty += (hy_ - ty) * hold

        # 5) 死亡蜷缩：足端收回腹面
        if dead > 0.0:
            tx += (leg.hip[0] * 0.55 - tx) * dead
            ty += (leg.hip[1] + 3.0 - ty) * dead

        # 6) 颤抖（受惊僵直 / 交配抽搐）：高频小幅抖动
        if tremor > 0.01:
            amp = tremor * (1.4 if speed < 5.0 else 0.9)
            tx += random.uniform(-amp, amp)
            ty += random.uniform(-amp, amp)

        # 7) 落点平滑，避免状态切换时足端瞬移
        leg.foot[0] += (tx - leg.foot[0]) * foot_k
        leg.foot[1] += (ty - leg.foot[1]) * foot_k
        leg.lift = swing_lift

        # 8) 两段式 IK
        self._solve_knee(leg)

    @staticmethod
    def _solve_knee(leg: _Leg) -> None:
        """余弦定理两段式 IK。

        这里每帧要跑 6×N 次（200 只 = 1200 次/帧），所以刻意不用 hypot/atan2：
        `base` 的 cos/sin 就是 dx/d、dy/d 本身，再用和角公式展开 base±a，
        把 5 次三角函数降到 3 次，并用原地写入避免 list/tuple 分配。
        """
        hx, hy = leg.hip
        dx = leg.foot[0] - hx
        dy = leg.foot[1] - hy
        d = math.sqrt(dx * dx + dy * dy)
        l1, l2 = leg.l1, leg.l2
        if d < 1e-3:
            leg.knee[0] = hx
            leg.knee[1] = hy
            return
        limit_hi = (l1 + l2) * 0.995
        limit_lo = abs(l1 - l2) + 1e-3
        if d > limit_hi or d < limit_lo:
            d0 = d
            d = limit_hi if d > limit_hi else limit_lo
            scale = d / d0
            dx *= scale
            dy *= scale

        cos_a = (l1 * l1 + d * d - l2 * l2) / (2.0 * l1 * d)
        if cos_a > 1.0:
            cos_a = 1.0
        elif cos_a < -1.0:
            cos_a = -1.0
        a = math.acos(cos_a)
        ca = math.cos(a)
        sa = math.sin(a)
        ux = dx / d          # cos(base)
        uy = dy / d          # sin(base)

        # 关节向体外侧弯折（俯视图里膝点是腿的最外点）：只比较两个候选的横向分量
        out_plus = (ux * ca - uy * sa) * leg.side
        out_minus = (ux * ca + uy * sa) * leg.side
        if out_plus >= out_minus:
            leg.knee[0] = hx + l1 * (ux * ca - uy * sa)
            leg.knee[1] = hy + l1 * (uy * ca + ux * sa)
        else:
            leg.knee[0] = hx + l1 * (ux * ca + uy * sa)
            leg.knee[1] = hy + l1 * (uy * ca - ux * sa)

    # ------------------------------------------------------------------ 绘制

    def draw(self, painter: QPainter, palette: dict, simple: bool = False, detail: int = 2) -> None:
        """按相位绘制六条腿。

        性能：每条腿合并成一个 QPainterPath（股节 + 关节 + 胫节 + 跗节一次性填充）
        再叠加一条刺毛路径，整只虫 6 条腿只需 12 次绘制调用，150 只同屏仍可跑满帧。
        """
        main = QColor(palette["leg"])
        spine = QColor(palette["spine"])
        tone = palette.get("tone", 1.0)
        if tone != 1.0:
            main = QColor(
                min(255, int(main.red() * tone)),
                min(255, int(main.green() * tone)),
                min(255, int(main.blue() * tone)),
            )
        dark = main.darker(115)

        if simple:
            # 极简档：六条腿合并为一条路径一次性描边，1 次绘制调用
            path = QPainterPath()
            for leg in self.legs:
                hx, hy = leg.hip
                kx, ky = leg.knee
                fx, fy = leg.foot
                path.moveTo(hx, hy)
                path.lineTo(kx, ky)
                path.lineTo(fx, fy)
            painter.setPen(QPen(main, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            return

        painter.setPen(Qt.PenStyle.NoPen)
        for leg in self.legs:
            self._draw_leg(painter, leg, main, dark, spine, simple, detail)

    def _draw_leg(
        self,
        painter: QPainter,
        leg: _Leg,
        main: QColor,
        dark: QColor,
        spine: QColor,
        simple: bool,
        detail: int,
    ) -> None:
        hx, hy = leg.hip
        kx, ky = leg.knee
        fx, fy = leg.foot

        if simple:
            painter.setPen(QPen(main, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(hx, hy), QPointF(kx, ky))
            painter.drawLine(QPointF(kx, ky), QPointF(fx, fy))
            return

        # 跗节：从足端再伸出一小节，让腿"踩实"地面而不是悬空
        dx, dy = fx - kx, fy - ky
        d = math.hypot(dx, dy) or 1.0
        tx = fx + dx / d * 2.6 - leg.side * 0.7
        ty = fy + dy / d * 2.6

        w0, w1, w2 = 3.3, 2.3, 1.2
        path = QPainterPath()
        self._add_tapered(path, (hx, hy), (kx, ky), w0, w1)
        path.addEllipse(QPointF(kx, ky), w1 * 0.52, w1 * 0.52)
        self._add_tapered(path, (kx, ky), (fx, fy), w1, w2)
        self._add_tapered(path, (fx, fy), (tx, ty), w2 * 0.9, 0.5)
        # 基节：落在躯干侧缘，遮住贴图裁切留下的接口
        path.addEllipse(QPointF(hx - leg.side * 1.4, hy), 3.2, 2.7)

        painter.setBrush(QBrush(main))
        painter.drawPath(path)

        if detail >= 2:
            sp = QPainterPath()
            nx = -(fy - ky) / d
            ny = (fx - kx) / d
            sign = 1.0 if nx * leg.side >= 0 else -1.0
            for t in (0.46, 0.76):
                sx = kx + (fx - kx) * t
                sy = ky + (fy - ky) * t
                sp.moveTo(sx, sy)
                sp.lineTo(sx + nx * 2.0 * sign, sy + ny * 2.0 * sign)
            painter.setPen(QPen(spine, 0.85))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(sp)
            painter.setPen(Qt.PenStyle.NoPen)

    @staticmethod
    def _add_tapered(
        path: QPainterPath,
        p0: tuple[float, float],
        p1: tuple[float, float],
        w0: float,
        w1: float,
    ) -> None:
        """把一段锥形肢体作为子路径加入路径（一次填充，无需逐段绘制）。"""
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < 0.05:
            return
        nx, ny = -dy / length, dx / length
        h0, h1 = w0 * 0.5, w1 * 0.5
        path.moveTo(p0[0] + nx * h0, p0[1] + ny * h0)
        path.lineTo(p1[0] + nx * h1, p1[1] + ny * h1)
        path.lineTo(p1[0] - nx * h1, p1[1] - ny * h1)
        path.lineTo(p0[0] - nx * h0, p0[1] - ny * h0)
        path.closeSubpath()
