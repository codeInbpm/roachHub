"""食物实体：照片贴图 + 腐败过程。

一条食物的一生分两段：

* **新鲜期**：营养几乎不打折，蟑螂吃得最多。
* **腐败期**：``freshness`` 从 1 掉到 0，期间持续流失营养（最多 45%）、
  整体发暗、边缘长出霉点、内部爬出蛆、底部渗出暗色液体。
  ``freshness`` 归零时化成一坨干壳消失（``death_reason = "rotted"``）。

为什么要有腐败：不加这条规则，桌面上的食物会一直留到被吃完，堆满上限后
就不再生成新的（"食物堆积"）。腐败同时解决了三个问题——自动回收、
给食物加了"时效"、以及让桌面越放越脏。

绘制性能：造型走 ``food_sprites`` 的位图缓存，一次 ``drawPixmap`` 完成；
霉点/蛆/渗液才是代码画的，每块食物最多十来次椭圆，且只在腐败后段才出现。
"""

from __future__ import annotations
import math
import random
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QPixmap

from .base_entity import BaseEntity, EntityState, LifeStage
from .food_kinds import FoodKind, DEFAULT_KEY, get_kind
from .food_sprites import pixmap_for

# 啃食中的位点会持续被"续期"，超过这个秒数没有续期即视为离开（个体死亡/被打断）
_SLOT_TTL = 2.5

_TAU = math.tau

# 贴图里物体大约占画布的 92%，反推绘制框 = 2r / 0.92
_SPRITE_BOX = 2.12

# 贴图超采样倍率：缓存一张比显示尺寸大 1.7 倍的位图，绘制时由 Qt 做一次
# 高质量的降采样。这样即便食物只占 30 像素，边缘和表面纹理也不会发糊。
# 内存代价：单张 60x60x4 ≈ 14KB，20 种全加载 ≈ 280KB，可以忽略。
_SPRITE_SUPERSAMPLE = 1.7

# 腐败到头时营养总共流失的比例
_ROT_NUTRITION_LOSS = 0.45

# 腐败阈值：超过这些值才出现对应表现
_ROT_MOLD = 0.28
_ROT_GRUB = 0.55
_ROT_OOZE = 0.72


_GRUB_PM: QPixmap | None = None


def _wrap_pi(a: float) -> float:
    """把角度折到 (-π, π]。"""
    return (a + math.pi) % _TAU - math.pi


def _grub_pixmap(dpr: float) -> QPixmap:
    """一条"头朝上"的白蛆，模块级缓存，进程里只画一次。

    原来每条蛆每帧要 4×2 个椭圆 + 1 个头，三蛆十二食物就是九百多次
    drawEllipse，占掉 4 ms/帧。换成一张 32x32 的小图后只剩 drawPixmap，
    扭动靠每帧改旋转角实现，成本几乎归零。
    """
    global _GRUB_PM
    if _GRUB_PM is not None:
        return _GRUB_PM
    size = 32
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(dpr, dpr)
    p.setPen(Qt.PenStyle.NoPen)
    cx = size * 0.5
    # 五节，从头到尾逐渐变细。节距必须小于两倍半径，否则会断成"白珠子串"
    seg_y = (4.0, 9.4, 15.0, 20.6, 26.0)
    seg_r = (4.4, 4.6, 4.2, 3.6, 2.9)
    for cy, rr in zip(seg_y, seg_r):
        p.setBrush(QBrush(QColor(249, 244, 226)))
        p.drawEllipse(QRectF(cx - rr, cy - rr * 0.92, rr * 2.0, rr * 1.84))
    # 背脊高光
    p.setBrush(QBrush(QColor(255, 255, 250, 150)))
    for (cy, rr) in zip(seg_y, seg_r):
        p.drawEllipse(QRectF(cx - rr * 0.46, cy - rr * 0.52,
                             rr * 0.84, rr * 0.38))
    # 头部暗点
    p.setBrush(QBrush(QColor(148, 132, 102, 220)))
    p.drawEllipse(QRectF(cx - 1.3, 3.0, 2.6, 2.6))
    p.end()
    pm.setDevicePixelRatio(dpr)
    _GRUB_PM = pm
    return pm


class Food(BaseEntity):
    """桌面散落的食物（腐肉、蛆团、霉面包、老鼠屎……）。

    食物是有营养量的：被啃食时体积随之缩小，啃完即消失。同时提供有限的
    "进食位点"，避免一整群蟑螂叠在同一个点上。

    具体长什么样、多少营养、几个位点、能放多久，全部来自
    ``food_kinds.FoodKind``；这个类只保留四件事：位点管理、按剩余营养缩放、
    腐败推进、以及"贴图 + 霉点 + 蛆 + 渗液"的绘制。
    """

    # 食物不是生物：不能参与 NeedsSystem 的代谢，否则一块没人吃的食物
    # 会在 334 秒后能量归零、掉血、自己饿死消失（营养还剩 40）。
    metabolizes = False

    def __init__(
        self,
        x: float,
        y: float,
        nutrition: float | None = None,
        kind: str | FoodKind | None = None,
    ) -> None:
        k = kind if isinstance(kind, FoodKind) else get_kind(kind or DEFAULT_KEY)
        r = k.radius
        super().__init__(x=x, y=y, base_width=r * 2.0, base_height=r * 1.84, scale=1.0)

        self.kind: FoodKind = k
        self.kind_key: str = k.key
        self.name: str = k.name
        # 0 = 原图，1~3 = 三档腐蚀染色（预烘在 assets/sprites/food/<key>_dN.png）
        self._sprite_tiers: tuple[str, ...] = (
            k.sprite, f"{k.sprite}_d1", f"{k.sprite}_d2", f"{k.sprite}_d3")

        # nutrition 显式传入时以传入为准（测试与脚本会自己定值）
        self.nutrition: float = float(nutrition) if nutrition is not None else k.nutrition
        self.max_nutrition: float = max(1e-6, self.nutrition)
        self.state = EntityState.IDLE
        self.stage = LifeStage.ADULT

        # 进食位点：id -> 最近一次续期时刻
        self.eaters: dict[str, float] = {}
        # 每个位点占的"方位角"（相对食物中心）。只用来让新来的虫绕到空位那侧，
        # 不参与碰撞——所以旧值过期丢掉也没关系。
        self.slot_angles: dict[str, float] = {}
        self.eating_slots: int = k.slots
        self._clock: float = 0.0

        # 腐败
        self.shelf_life: float = max(1.0, k.shelf_life)
        self._grub_min_rot: float = k.grub_min_rot
        self.age: float = 0.0
        self.freshness: float = 1.0
        # 被啃掉的总量。用它算腐败加速，而不是用 ratio ——
        # ratio 里还混着"腐败自己流失的营养"，会形成正反馈，
        # 让标称 100 秒的保质期实际 84 秒就结束。
        self.eaten_nutrition: float = 0.0

        # 每个个体自己的造型扰动，避免同种食物长得一模一样
        self._wobble = random.uniform(0.0, _TAU)
        self._tilt = random.uniform(-16.0, 16.0)
        self._blob = [random.uniform(0.80, 1.14) for _ in range(7)]
        # 蛆：贴在食物边缘，各自有相位（扭动不同步）。
        # 角度按 120° 均分再抖动 —— 纯随机容易出现三条挤在同一侧，
        # 缩到桌面上就是一坨白斑；均分又太规整，所以要抖。
        self._grub_pts = [(i * _TAU / 3.0 + random.uniform(-0.55, 0.55),
                           random.uniform(0.86, 1.02)) for i in range(3)]
        self._grub_phase = [random.uniform(0.0, _TAU) for _ in range(3)]

    # ------------------------------------------------------------------ 位点

    def _prune(self) -> None:
        stale = [k for k, t in self.eaters.items() if self._clock - t > _SLOT_TTL]
        for k in stale:
            self.eaters.pop(k, None)
            self.slot_angles.pop(k, None)

    def has_room(self, roach_id: str) -> bool:
        """是否还能容纳这个个体（已在其中的人始终有位置）。"""
        self._prune()
        if roach_id in self.eaters:
            return True
        return len(self.eaters) < self.eating_slots

    def claim(self, roach_id: str, angle: float | None = None) -> bool:
        """占一个位点。``angle`` 是这只虫选的方位角，用于给后来者让位。"""
        self._prune()
        if roach_id in self.eaters:
            self.eaters[roach_id] = self._clock
            if angle is not None:
                self.slot_angles[roach_id] = angle
            return True
        if len(self.eaters) >= self.eating_slots:
            return False
        self.eaters[roach_id] = self._clock
        if angle is not None:
            self.slot_angles[roach_id] = angle
        return True

    def open_slot_angle(self, from_pos, extra_taken=()) -> float:
        """挑一个离"已经在吃的那几只"最远的空位方位角。

        没有这个，两三只从同一侧走过来的虫会停在同一个点上叠成一坨——
        ``eating_slots`` 只限制数量，不限制方位。挑法很简单：从"正对着
        自己来向"开始转一圈取样，取与已占方位夹角最大的一档。

        ``extra_taken`` 是"还在路上、已经预定了方位"的其他个体，
        要一起避开；否则三只同时出发的虫会在同一帧里选中同一个角。
        """
        base = math.atan2(from_pos.y - self.pos.y, from_pos.x - self.pos.x)
        taken = [a for k, a in self.slot_angles.items() if k in self.eaters]
        taken.extend(extra_taken)
        if not taken or self.eating_slots <= 1:
            return base
        best_a = base
        best_gap = -1.0
        for i in range(24):
            a = base + i * (_TAU / 24.0)
            gap = min(abs(_wrap_pi(a - t)) for t in taken)
            if gap > best_gap + 1e-6:
                best_gap = gap
                best_a = a
        return best_a

    def release(self, roach_id: str) -> None:
        self.eaters.pop(roach_id, None)
        self.slot_angles.pop(roach_id, None)

    @property
    def ratio(self) -> float:
        """剩余营养比例（用于体积收缩）。"""
        return max(0.0, min(1.0, self.nutrition / self.max_nutrition))

    @property
    def visual_radius(self) -> float:
        """当前画在桌面上的半径（含"越吃越小"的收缩）。

        行为层要用它决定"虫停在哪"：食物半径 17~35 px，跨度两倍，
        再用固定停靠距离就会出现大块食物被虫体压住的情况。
        """
        return self.kind.radius * (0.45 + 0.55 * self.ratio)

    @property
    def rot(self) -> float:
        """腐败程度 0~1（0 = 新鲜，1 = 彻底烂掉）。"""
        return 1.0 - self.freshness

    @property
    def edible_note(self) -> str:
        """给控制台/悬停卡片用的一句话状态。"""
        r = self.rot
        if r < _ROT_MOLD:
            return "还新鲜"
        if r < _ROT_GRUB:
            return "开始发霉"
        if r < _ROT_OOZE:
            return "长蛆了"
        return "烂透了"

    # ------------------------------------------------------------------ 更新

    def update(self, dt: float) -> None:
        # 注意：BaseEntity.update 里已经有 self.age += dt，这里**不能**再加一次。
        # 第一版重复累加，导致"100 秒烂光"实际 48 秒就烂没了。
        super().update(dt)
        if not self.is_alive:
            return

        self._clock += dt
        self._wobble += dt * 1.6
        if self.eaters and int(self._clock * 4) % 4 == 0:
            self._prune()

        # 被啃过的更容易坏：汁水外溢、伤口暴露，腐败速度最多快 1.9 倍
        bitten = min(1.0, self.eaten_nutrition / self.max_nutrition)
        speed = 1.0 + bitten * 0.9
        # 基类加的是 1 倍 dt，这里只补"加速"出来的那一截
        self.age += dt * (speed - 1.0)

        fresh = 1.0 - self.age / self.shelf_life
        if fresh > 0.0:
            self.freshness = fresh
            # 腐败本身就是微生物在吃这堆东西：营养按比例漏掉，
            # 视觉上体积随之收缩，和"被蟑螂啃"用同一个 ratio 通道。
            leak = self.max_nutrition * _ROT_NUTRITION_LOSS / self.shelf_life * speed * dt
            self.nutrition = max(0.0, self.nutrition - leak)
        else:
            self.freshness = 0.0
            self.nutrition = 0.0
            self.is_alive = False
            self.death_reason = "rotted"
            self.eaters.clear()
            self.slot_angles.clear()

    def consume(self, amount: float) -> float:
        """被生物啃食，返回实际吃到的营养。"""
        eaten = min(self.nutrition, amount)
        self.nutrition -= eaten
        self.eaten_nutrition += eaten
        if self.nutrition <= 0.0:
            self.nutrition = 0.0
            self.is_alive = False
            self.death_reason = "eaten_up"
            self.eaters.clear()
            self.slot_angles.clear()
        return eaten

    def get_bounding_rect(self) -> QRectF:
        r = self.visual_radius
        return QRectF(self.pos.x - r, self.pos.y - r, r * 2, r * 2)

    def on_click(self, click_pos) -> None:
        """点食物不该把它吓跑：食物只是被顶得晃一下。"""
        self._wobble += 0.5

    # ------------------------------------------------------------------ 绘制

    def render(self, painter: QPainter) -> None:
        k = self.kind
        painter.save()
        painter.translate(self.pos.x, self.pos.y)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # 体积随剩余营养线性收缩（下限 0.45，避免"最后一刻突然消失"）
        ratio = 0.45 + 0.55 * self.ratio
        # 被啃食时轻微晃动，看起来像被顶动
        if self.eaters:
            painter.rotate(math.sin(self._wobble * 6.0) * 1.8)
        painter.scale(ratio, ratio)
        painter.rotate(self._tilt)

        r = k.radius
        rot = self.rot

        # 贴地投影交给素材自带的那层（抠图时保留下来了）。这里只补一层极淡的
        # 椭圆，给浅色桌面上加一点"压住桌面"的感觉。alpha 必须很低——
        # 第一版用了 30/34，在白色桌面上直接变成一圈灰色圆盘，比食物本身还显眼。
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(20, 14, 9, 14)))
        painter.drawEllipse(QRectF(-r * 1.00, -r * 0.76, r * 2.00, r * 1.60))

        # 腐败靠"换一张已经染脏的贴图"，而不是往下面垫暗色椭圆。
        # 椭圆形状和食物形状无关，垫上去在浅色桌面上就是一个灰玻璃球。
        dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
        want = int(r * _SPRITE_BOX * dpr * _SPRITE_SUPERSAMPLE)
        px = None
        tier = 0 if rot < 0.30 else (1 if rot < 0.58 else (2 if rot < 0.82 else 3))
        if tier:
            px = pixmap_for(self._sprite_tiers[tier], want, dpr)
        if px is None:
            px = pixmap_for(k.sprite, want, dpr)

        if px is not None:
            half = r * _SPRITE_BOX * 0.5
            painter.drawPixmap(QRectF(-half, -half, half * 2.0, half * 2.0), px,
                               QRectF(0.0, 0.0, float(px.width()), float(px.height())))
        else:
            # 贴图缺失时的降级：至少还是个有颜色的碎块，不至于空白
            painter.setPen(QPen(QColor(*k.edge), 1.4))
            painter.setBrush(QBrush(QColor(*k.body)))
            self._draw_blob(painter, k, r)

        self._render_rot(painter, r, rot)
        painter.restore()

    # -- 腐败表现 --------------------------------------------------------

    def _render_rot(self, p: QPainter, r: float, rot: float) -> None:
        """只画"会动的部分"：蛆 + 渗液。

        菌斑是静态的，已经烘进 _d1/_d2/_d3 三档贴图（``prepare_food_sprites``），
        运行时不再重画 —— 否则 12 块全烂的食物要 4 ms/帧。

        剩下这两样也必须"不规则"才不像贴纸：
        - 蛆用预渲染的 5 节小虫，节距小于两倍半径，否则会断成"白珠子串"
        - 渗液是"主体 + 末端坠一滴"，不是一根细长黑椭圆（那像黑棍）
        """
        p.setPen(Qt.PenStyle.NoPen)

        # 菌斑不在这里画：它是静态的，已经烘进 _d1/_d2/_d3 三档贴图里了。
        # 这里只剩"会动的东西"——蛆，以及"贴着食物往下淌"的渗液。
        if rot <= self._grub_min_rot and rot <= _ROT_OOZE:
            return

        # 2) 蛆：从边缘探出来扭，数量随腐败度 1 → 3
        if rot > self._grub_min_rot:
            g0 = self._grub_min_rot
            grub_span = min(1.0, max(0.0, (rot - g0) / max(0.05, 1.0 - g0)))
            n_grub = 1 + int(grub_span * 2.99)
            dpr = p.device().devicePixelRatioF() if p.device() else 1.0
            pm = _grub_pixmap(dpr)
            # 0.50 而不是 0.66：蛆在食物上占 1/4 直径就够醒目了，
            # 再大会把食物盖住，三条一起看就像一朵白花
            side = r * 0.58
            for i in range(min(n_grub, 3)):
                ang, rad = self._grub_pts[i]
                # 蛆身一半在食物上、一半探出去
                bx = math.cos(ang) * rad * r * 0.80
                by = math.sin(ang) * rad * r * 0.74
                ph = self._clock * 6.5 + self._grub_phase[i]
                p.save()
                p.translate(bx, by)
                # sprite 是"头朝上"的，先转到朝向，再叠一点扭动
                p.rotate(math.degrees(ang) + 90.0 + math.sin(ph) * 16.0)
                p.drawPixmap(QRectF(-side * 0.5, -side * 0.5, side, side), pm,
                             QRectF(0.0, 0.0, float(pm.width()), float(pm.height())))
                p.restore()

        # 3) 渗液：从底部往下流的细痕，末端收细
        p.setPen(Qt.PenStyle.NoPen)
        if rot > _ROT_OOZE:
            ooze = (rot - _ROT_OOZE) / (1.0 - _ROT_OOZE)
            a = int(80 + 100 * ooze)
            # 每道 = 一条"从食物边缘淌下来"的主体 + 末端坠着的一小滴。
            # 直接画细长椭圆会变成两根黑棍，必须让宽度往下收。
            for dx, w, h in ((-0.30, 0.15, 0.30), (0.18, 0.17, 0.40), (0.46, 0.11, 0.22)):
                p.setBrush(QBrush(QColor(58, 38, 22, a)))
                p.drawEllipse(QRectF(dx * r, r * 0.34, r * w, r * h))
                p.drawEllipse(QRectF(dx * r + r * w * 0.22, r * (0.34 + h * 0.72),
                                     r * w * 0.56, r * w * 0.56))

    # -- 贴图缺失时的兜底造型 ---------------------------------------------

    def _draw_blob(self, p: QPainter, k: FoodKind, r: float) -> None:
        n = len(self._blob)
        pts = []
        for i in range(n):
            a = i / n * _TAU
            rr = r * self._blob[i]
            pts.append(QPointF(math.cos(a) * rr, math.sin(a) * rr * 0.86))

        path = QPainterPath()
        mid = QPointF((pts[-1].x() + pts[0].x()) * 0.5, (pts[-1].y() + pts[0].y()) * 0.5)
        path.moveTo(mid)
        for i in range(n):
            cur = pts[i]
            nxt = pts[(i + 1) % n]
            path.quadTo(cur, QPointF((cur.x() + nxt.x()) * 0.5, (cur.y() + nxt.y()) * 0.5))
        path.closeSubpath()
        p.drawPath(path)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(*k.accent, 130)))
        p.drawEllipse(QRectF(-r * 0.3, -r * 0.36, r * 0.4, r * 0.3))
