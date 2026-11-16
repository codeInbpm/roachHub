"""交配动作与特效：尾对尾的律动 + 三样"看得见"的东西。

## 为什么要单独一个模块

交配在这套系统里原本是"最安静的状态"：两只虫尾对尾站 2.2 秒，姿态目标里只有
``tremor=0.45`` 一个抖动通道，画面上就是一个暗色小黏液点在高频闪。玩家看不出
它们在干什么，也看不出进度 —— 只能从"过一会儿多了一枚卵鞘"倒推。

所以这里把它拆成**动作**和**样式**两层：

## 动作：三拍一组的挺动

真实蟑螂交尾时雄性沿体轴反复挺动，频率由慢到快，末端一阵急促，最后抽离。
对应三个量（都由 ``u = progress / duration`` 直接算，不累积状态，暂停/变速都不漂）：

- ``phase``：相位。频率从 1.6Hz 沿 smoothstep 升到 4.8Hz，
  用**解析积分**求相位 —— 不解析的话变速处会出现"跳拍"（正弦相位突变 = 抽搐）；
- ``push``：沿体轴的位移。雄性朝尾部方向顶出去 2.2px（≈体长 3%），雌性被顶得
  同向跟随 45%。两者朝同一世界方向走，所以尾部始终贴合，
  画面是"整对在耸动"而不是两只虫互相穿插；
- ``squeeze``：腹部纵向挤压 ±2%，反相。这是"用力"的第三条线索，
  只有躯干在挤，六足不动 —— 腿跟着拉伸会像橡皮虫。

幅度包络：前 10% 从 0 涨起来（对接），后 12% 落回 0（抽离）。
所以开始和结束都是"软"的，不会突然定住。

## 样式：光晕 / 波纹 / 心形

1. **结合处光晕**：预渲染的径向渐变贴图，半径随拍呼吸（±5%），暖橙偏粉。
   画在躯干之前，所以是"从身体缝隙里透出来的光"，不是浮在背上的圆盘。
2. **每拍一圈扩散波纹**：只画一个环，用 ``frac(phase/τ)`` 当年龄。
   4.8Hz 时 0.2 秒扩完一圈，连起来就是心跳。
3. **每拍一颗心形**：预渲染 QPixmap，从结合处向两侧飘散、摇摆、淡出，最多 6 颗。
   心形用两段三次贝塞尔画（不是字符 "♥"）—— 字符依赖字体，离屏环境没有字体
   会变成方框（踩过）。

三样全部只画在**雄虫**身上：两头各画一份会叠成双倍亮度，看起来像过曝。

## 开销

稳态每对：1 次 drawPixmap（光晕）+ 1 个描边椭圆 + ≤6 次 drawPixmap（心形）。
心形贴图与光晕贴图都是模块级缓存，稳态零分配。``quality == 0``（>110 只的极简档）
直接跳过全部特效 —— 那时腿上连抗锯齿都关了，画心形没有意义。
"""

from __future__ import annotations

import math
import random
from typing import List, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)

TAU = math.tau

# ---- 动作参数 ---------------------------------------------------------------

#: 挺动频率（Hz）：起手慢、末端急
FREQ_BASE = 1.6
FREQ_SWING = 3.2
#: 频率爬升区间（归一化进度）。落在 0.14~0.86 之间，两头都留出"对接/抽离"的余地
FREQ_RAMP = (0.14, 0.86)

#: 幅度包络：0 到 ATTACH_U 涨起来，DETACH_U 到 1 落回去
ATTACH_U = 0.10
DETACH_U = 0.88

#: 挺动幅度（像素，未乘 scale）。70px 体长 → 4.2px ≈ 体长 6%。
#: 2.2px 时在 1:1 缩放下几乎看不出来（截图里 8 帧像同一张）；
#: 6% 是"明显在动但不像弹簧"的上限。
PUSH_PX = 4.2
#: 雌性被顶得同向跟随的比例。1.0 = 完全同步（看起来像一起平移），
#: 0 = 雌性完全不动（尾部会被顶穿）；0.45 是"被顶着挪"的临界感
FEMALE_FOLLOW = 0.45
#: 抽离位移：最后 12% 里向后退开这么多像素，让"结束了"能被看见
DETACH_PX = 9.0
#: 以尾端为轴的左右扭摆（度）。纯平移在 30fps 下容易看成"抖"，
#: 绕尾端扭一下，头部划出的弧才是肉眼能读到的"在用力"。
TWIST_DEG = 1.8
#: 腹部纵向挤压幅度（正负百分比）
MALE_SQUEEZE = 0.032
FEMALE_SQUEEZE = 0.020

# ---- 特效参数 ---------------------------------------------------------------

HEART_MAX = 8
HEART_LIFE = (1.2, 1.8)
#: 心形生成点：结合处（尾部）的局部坐标，单位是体长的倍数
HEART_ORIGIN_Y = 0.46


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _smoothstep(t: float) -> float:
    t = _clamp01(t)
    return t * t * (3.0 - 2.0 * t)


def phase_at(u: float, duration: float) -> float:
    """相位（弧度）。频率在进度上变化，所以对 ``2π·duration·freq(u)`` 做解析积分。

    freq(u) = FREQ_BASE + FREQ_SWING · smoothstep((u-a)/(b-a))，
    积分 = FREQ_BASE·u + FREQ_SWING·(b-a)·(t³ - t⁴/2)，t 是归一化后的进度。
    只要积分是闭式的，相位就是 u 的纯函数：暂停、跳变、改时长都不会"跳拍"。
    """
    u = _clamp01(u)
    a, b = FREQ_RAMP
    t = _clamp01((u - a) / (b - a))
    integral = FREQ_BASE * u + FREQ_SWING * (b - a) * (t ** 3 - t ** 4 / 2.0)
    return TAU * max(0.0, duration) * integral


def envelope(u: float) -> float:
    """挺动幅度包络 0~1：两端软启动、软收尾。"""
    u = _clamp01(u)
    if u < ATTACH_U:
        return _smoothstep(u / ATTACH_U)
    if u > DETACH_U:
        return _smoothstep((1.0 - u) / (1.0 - DETACH_U))
    return 1.0


# ---- 预渲染贴图（模块级缓存） ------------------------------------------------

_aura_pixmap: QPixmap | None = None
_heart_pixmaps: dict[int, QPixmap] = {}


def _aura_pixmap_get(size: int = 96) -> QPixmap:
    """结合处光晕：中心暖橙、边缘透明的径向渐变。

    预渲染而不是每帧画 QRadialGradient —— 渐变构造是这一段最贵的操作，
    而它的形状根本不变，只有透明度在变，交给 ``setOpacity`` 就够了。
    """
    global _aura_pixmap
    if _aura_pixmap is not None and _aura_pixmap.width() == size:
        return _aura_pixmap
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    c = size / 2.0
    grad = QRadialGradient(c, c, c)
    # 多段小落差：只有五段大色阶时，放大到 100px 会看出"玫瑰花瓣"一样的环带
    # 中心不能太白：奶白心在暗色桌面上像"喷了一团白雾"，
    # 偏橙的暖光才像从身体缝隙里透出来的
    grad.setColorAt(0.00, QColor(255, 168, 104, 178))
    grad.setColorAt(0.20, QColor(250, 140, 92, 142))
    grad.setColorAt(0.42, QColor(236, 108, 92, 92))
    grad.setColorAt(0.64, QColor(208, 80, 102, 44))
    grad.setColorAt(0.84, QColor(182, 66, 108, 15))
    grad.setColorAt(1.00, QColor(170, 60, 110, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawEllipse(QRectF(0, 0, size, size))
    p.end()
    _aura_pixmap = pix
    return pix


def _heart_pixmap_get(size: int = 64) -> QPixmap:
    """心形：两段三次贝塞尔 + 白描边 + 柔光。

    不用字符 "♥"：字体缺失时（离屏平台 `QFontDatabase.families()` 返回 0）
    会画成方框，而这张贴图必须任何环境都一样。
    """
    cached = _heart_pixmaps.get(size)
    if cached is not None:
        return cached
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    s = float(size)
    cx, cy = s / 2.0, s * 0.54
    r = s * 0.40
    path = QPainterPath()
    path.moveTo(cx, cy + 0.62 * r)
    path.cubicTo(cx - 0.98 * r, cy - 0.10 * r, cx - 0.52 * r, cy - 0.92 * r, cx, cy - 0.34 * r)
    path.cubicTo(cx + 0.52 * r, cy - 0.92 * r, cx + 0.98 * r, cy - 0.10 * r, cx, cy + 0.62 * r)
    path.closeSubpath()

    # 外发光：先描一道宽而淡的边
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 214, 232, 70), s * 0.10,
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)

    grad = QRadialGradient(cx - r * 0.25, cy - r * 0.35, r * 1.25)
    grad.setColorAt(0.0, QColor(255, 246, 250))
    grad.setColorAt(0.45, QColor(255, 168, 200))
    grad.setColorAt(1.0, QColor(224, 86, 140))
    p.setPen(QPen(QColor(255, 238, 246, 235), max(1.0, s * 0.035)))
    p.setBrush(QBrush(grad))
    p.drawPath(path)
    p.end()
    _heart_pixmaps[size] = pix
    return pix


class MatingRig:
    """一只虫的交配动作 + 粒子。雄雌各持一个，参数按性别取。"""

    __slots__ = ("phase", "push", "squeeze", "twist", "energy", "hearts",
                 "_last_beat", "_rng", "_junction")

    def __init__(self) -> None:
        self.phase = 0.0
        self.push = 0.0        # 局部 +y = 朝尾部（也就是朝对方）
        self.squeeze = 1.0
        self.twist = 0.0       # 绕尾端的扭摆角度（度）
        self.energy = 0.0      # 幅度包络，同时用作特效强度
        # 心形粒子：[x, y, vx, vy, age, sway_phase, life, size]，**世界坐标**
        self.hearts: List[List[float]] = []
        self._last_beat = -1
        self._rng = random.Random()
        self._junction: Tuple[float, float] | None = None

    # ------------------------------------------------------------------ 推进

    @property
    def active(self) -> bool:
        return self.energy > 0.0 or bool(self.hearts)

    def reset(self) -> None:
        self.phase = 0.0
        self.push = 0.0
        self.squeeze = 1.0
        self.twist = 0.0       # 绕尾端的扭摆角度（度）
        self.energy = 0.0
        self.hearts.clear()
        self._last_beat = -1

    def update(self, dt: float, u: float, duration: float, gender: str,
               junction: Tuple[float, float] | None = None) -> None:
        """按进度推进相位与包络，并（雄虫）按拍撒心形。

        ``junction`` 是结合处的**世界坐标**。心形必须在世界坐标里飘：
        交配的两只虫朝向相反（尾对尾），雄性转了 180°，局部 -y 是屏幕向下 ——
        用它做"上浮"会让心形钻进自己身体里，而且横着配对的虫更乱。
        """
        male = gender == "male"
        u = _clamp01(u)
        self.phase = phase_at(u, duration)
        self.energy = envelope(u)
        wob = math.sin(self.phase)

        follow = 1.0 if male else FEMALE_FOLLOW
        # 抽离：最后一段两只各向后退开，方向都是"远离对方"（局部 -y）
        detach = _smoothstep((u - (DETACH_U - 0.02)) / (1.0 - (DETACH_U - 0.02)))
        self.push = PUSH_PX * self.energy * wob * follow - DETACH_PX * detach
        sq = MALE_SQUEEZE if male else FEMALE_SQUEEZE
        self.squeeze = 1.0 + sq * wob * self.energy * (1.0 if male else -1.0)
        # 扭摆与挺动错开 90°：顶出去的时候身体是直的，收回时才左右晃
        self.twist = TWIST_DEG * self.energy * math.cos(self.phase) * (1.0 if male else -0.55)

        self._junction = junction
        self._tick_hearts(dt, male)

    def _tick_hearts(self, dt: float, male: bool) -> None:
        # 1. 旧粒子推进（世界坐标：永远往屏幕上方飘，与虫体朝向无关）
        alive: List[List[float]] = []
        for h in self.hearts:
            h[4] += dt                     # age
            h[0] += (h[2] + math.sin(h[5] * 6.2) * 9.0) * dt
            h[1] += h[3] * dt
            h[5] += dt * 3.0
            if h[4] < h[6]:
                alive.append(h)
        self.hearts = alive

        # 2. 跨拍生成：phase 单调递增，整数拍数变化即"这一拍到了"
        beat = int(self.phase / TAU)
        if beat != self._last_beat:
            if self._last_beat >= 0 and male and self.energy > 0.15:
                if len(self.hearts) < HEART_MAX:
                    self._spawn_heart()
            self._last_beat = beat

    def _spawn_heart(self) -> None:
        rng = self._rng
        jx, jy = self._junction or (0.0, 0.0)
        side = -1.0 if rng.random() < 0.5 else 1.0
        life = rng.uniform(*HEART_LIFE)
        self.hearts.append([
            jx + side * rng.uniform(1.0, 6.0),     # x（世界）
            jy - rng.uniform(2.0, 7.0),            # y（世界）：从结合处略偏上起
            side * rng.uniform(10.0, 24.0),        # vx：向两侧飘散
            -rng.uniform(22.0, 36.0),              # vy：往屏幕上方浮
            0.0,                                   # age
            rng.uniform(0.0, TAU),                 # 摇摆相位
            life,
            rng.uniform(0.85, 1.20),               # 尺寸倍数
        ])

    # ------------------------------------------------------------------ 绘制

    def draw_under(self, p: QPainter, body_len: float, gender: str,
                   quality: int = 2) -> None:
        """结合处光晕（画在躯干之前，像从缝隙里透出来）。"""
        if quality <= 0 or gender != "male" or self.energy <= 0.01:
            return
        r = body_len * 0.36 * (1.0 + 0.09 * math.sin(self.phase))
        y = body_len * HEART_ORIGIN_Y
        pix = _aura_pixmap_get()
        # 亮度跟着拍子走：每拍开头最亮、再落下去，小尺寸下的"在动"主要靠这个
        beat = (1.0 - (self.phase / TAU) % 1.0) ** 2
        p.save()
        p.setOpacity(0.30 + 0.22 * self.energy + 0.30 * beat * self.energy)
        rect = QRectF(-r, y - r, r * 2.0, r * 2.0)
        p.drawPixmap(rect, pix, QRectF(pix.rect()))
        p.restore()

    def draw_over(self, p: QPainter, body_len: float, body_w: float, gender: str,
                  quality: int = 2) -> None:
        """波纹 + 湿光 + 心形（画在触须之后，最上层）。"""
        if quality <= 0:
            return
        y = body_len * HEART_ORIGIN_Y

        if gender == "male" and self.energy > 0.01:
            # 0. 缝隙热斑：光晕在躯干下面只能漏出一圈，叠一层在躯干上
            #    才是"结合处自己在发光"（纯 alpha 叠加，不用加法混合）
            pix = _aura_pixmap_get()
            hr = body_w * 0.78 * (1.0 + 0.10 * math.sin(self.phase))
            p.save()
            p.setOpacity(float(0.18 + 0.22 * self.energy))
            p.drawPixmap(QRectF(-hr, y - hr * 0.9, hr * 2.0, hr * 1.8),
                         pix, QRectF(pix.rect()))
            p.restore()

            # 1. 一拍一圈的扩散波纹：用 frac(phase/τ) 当年龄，只画一个环
            age = (self.phase / TAU) % 1.0
            rad = 6.0 + 22.0 * age
            alpha = int(215.0 * (1.0 - age) ** 3 * self.energy)
            if alpha > 3:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(242, 124, 100, alpha), 2.2))
                p.drawEllipse(QPointF(0.0, y), rad * 0.80, rad)

            # 2. 结合处的湿光：暖色高光压在暗色分泌液上
            p.setPen(Qt.PenStyle.NoPen)
            # 分泌液：只在结合处压一道横向暗斑。
            # 试过叠高光 —— 深色桌面上会变成一个发亮的"胶囊"，比不加更假
            p.setBrush(QColor(56, 24, 12, 120 + int(50 * self.energy)))
            p.drawEllipse(QRectF(-body_w * 0.19, y - 0.5, body_w * 0.38, body_w * 0.15))

    def draw_hearts(self, p: QPainter, world_transform, scale: float = 1.0) -> None:
        """心形粒子：切回世界坐标系再画。

        这样它们永远正立、永远往屏幕上方飘，与这对虫是横着还是竖着配对无关。
        ``scale`` 只用来让心形跟着虫体大小走（幼虫/大只不会一样大）。
        """
        if not self.hearts:
            return
        pix = _heart_pixmap_get()
        p.save()
        if world_transform is not None:
            p.setTransform(world_transform)
        for h in self.hearts:
            t = h[4] / h[6]
            if t >= 1.0:
                continue
            # 淡入 12%、淡出 45%
            a = 1.0 if t < 0.55 else (1.0 - t) / 0.45
            if t < 0.12:
                a *= t / 0.12
            size = 21.0 * h[7] * (0.78 + 0.42 * t) * max(0.5, scale)
            p.save()
            p.setOpacity(max(0.0, min(1.0, a)))
            p.translate(h[0], h[1])
            p.rotate(math.sin(h[5]) * 18.0)
            p.drawPixmap(QRectF(-size / 2.0, -size / 2.0, size, size),
                         pix, QRectF(pix.rect()))
            p.restore()
        p.restore()


def cache_stats() -> Tuple[int, int]:
    """(贴图张数, 近似内存 KB)。验收用。"""
    n = 1 if _aura_pixmap is not None else 0
    n += len(_heart_pixmaps)
    kb = 0
    if _aura_pixmap is not None:
        kb += _aura_pixmap.width() * _aura_pixmap.height() * 4
    for pix in _heart_pixmaps.values():
        kb += pix.width() * pix.height() * 4
    return n, kb // 1024
