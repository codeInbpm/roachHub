"""足迹与油渍痕迹图层。

蟑螂爬过桌面会留下极淡的湿痕与足印 —— 这是"桌面生物"品类里最能骗过眼睛的
细节之一，成本却比做 3D 低得多。实现要点：

* **半分辨率画布**：整层只在内存里维护一张 0.5 倍图，等比放大合成到屏幕。
  4K 屏下内存 8 MB、每次淡出只是一次 fillRect，代价可忽略；
* **乘性淡出**：每帧用 DestinationOut 全幅填充，alpha 按 exp(-dt/tau) 衰减，
  等价于一次乘法，不需要遍历像素；
* **不做逐点回收**：痕迹靠自然淡出消失，只有长时间无新增时才整层清空，
  避免"擦除最早的点"带来的重绘复杂度。
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPixmap, QRadialGradient


class TrailCanvas:
    """足迹 / 油渍图层。"""

    def __init__(
        self,
        width: int,
        height: int,
        scale: float = 0.5,
        fade_seconds: float = 6.0,
        max_prints: int = 3000,
    ) -> None:
        self.scale = max(0.25, min(1.0, scale))
        self.fade_tau = max(0.4, fade_seconds / 3.2)
        self.fade_seconds = fade_seconds
        self.max_prints = max(200, max_prints)
        self.enabled = True

        self.width = 0
        self.height = 0
        # 落笔队列：200 只同屏时每秒上千次落地，逐个 QPainter 建/毁要 2~8ms/帧。
        # 改成攒到 tick() 里一次性画完，整帧只开一个 QPainter。
        self._pending_fp: list[tuple[float, float, float, float]] = []
        self._pending_sm: list[tuple[float, float, float, float]] = []
        self._pending_sp: list[tuple[float, float, float]] = []   # 拍击飞溅的小斑点
        self._max_pending = 600

        self.image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(Qt.GlobalColor.transparent)
        self.resize(width, height)

        self.count = 0                 # 当前"存活"的笔数估值（随淡出一起衰减，用于预算）
        self.total_prints = 0          # 累计落笔数（诊断/验收用，clear 时归零）
        self._has_content = False
        self._since_add = 99.0
        self._fade_acc = 0.0           # 累积到一次有效填充的衰减量
        self._smear_stamp: QPixmap | None = None

    # ------------------------------------------------------------------ 管理

    def resize(self, width: int, height: int) -> None:
        w = max(1, int(width * self.scale))
        h = max(1, int(height * self.scale))
        if w == self.width and h == self.height:
            return
        self.width, self.height = w, h
        self.image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(Qt.GlobalColor.transparent)
        self.count = 0
        self.total_prints = 0
        self._has_content = False
        self._pending_fp.clear()
        self._pending_sm.clear()
        self._pending_sp.clear()

    def clear(self) -> None:
        self.image.fill(Qt.GlobalColor.transparent)
        self.count = 0
        self.total_prints = 0
        self._has_content = False
        self._since_add = 99.0
        self._fade_acc = 0.0
        self._pending_fp.clear()
        self._pending_sm.clear()
        self._pending_sp.clear()

    # ------------------------------------------------------------------ 落笔

    def add_footprint(
        self,
        wx: float,
        wy: float,
        strength: float = 0.6,
        tone: float = 1.0,
    ) -> None:
        """登记一枚足印（真正落笔在下一个 tick，整帧合并成一次绘制）。"""
        if not self.enabled:
            return
        x = wx * self.scale
        y = wy * self.scale
        if not (0.0 <= x < self.width and 0.0 <= y < self.height):
            return
        if len(self._pending_fp) < self._max_pending:
            self._pending_fp.append((x, y, strength, tone))
        self.count += 1
        self.total_prints += 1
        self._has_content = True
        self._since_add = 0.0

    def add_smear(self, wx: float, wy: float, angle: float, length: float) -> None:
        """腹部拖过桌面留下的油渍（同样延后到 tick 统一绘制）。"""
        if not self.enabled:
            return
        x = wx * self.scale
        y = wy * self.scale
        if not (0.0 <= x < self.width and 0.0 <= y < self.height):
            return
        if len(self._pending_sm) < self._max_pending:
            self._pending_sm.append((x, y, angle, length))
        self.count += 1
        self.total_prints += 1
        self._has_content = True
        self._since_add = 0.0

    def add_splat(self, wx: float, wy: float, radius: float = 24.0) -> None:
        """拍死一只虫时，往痕迹层甩几枚小斑点 + 一条细拖尾。

        主污渍是挂在尸体上的（22 秒后随尸体一起消失），这里只留"溅出去的那几滴"，
        它们按痕迹层的正常节奏淡出 —— 于是爆完几秒后桌上还有一点点痕迹，
        但不会永远脏着。打空**不**调这个（空拍不留污渍）。
        """
        if not self.enabled:
            return
        x = wx * self.scale
        y = wy * self.scale
        if not (0.0 <= x < self.width and 0.0 <= y < self.height):
            return
        n0 = len(self._pending_sp)
        for _ in range(random.randint(3, 6)):
            a = random.uniform(0.0, math.tau)
            dist = radius * self.scale * random.uniform(0.55, 1.55)
            r = max(1.0, radius * self.scale * random.uniform(0.07, 0.20))
            sx = x + math.cos(a) * dist
            sy = y + math.sin(a) * dist
            if 0.0 <= sx < self.width and 0.0 <= sy < self.height:
                self._pending_sp.append((sx, sy, r))

        # 一条顺着力道甩出去的细拖尾
        a = random.uniform(0.0, math.tau)
        steps = 4
        for i in range(1, steps + 1):
            dist = radius * self.scale * (1.1 + 0.32 * i)
            r = max(0.8, radius * self.scale * (0.14 - 0.025 * i))
            sx = x + math.cos(a) * dist
            sy = y + math.sin(a) * dist
            if 0.0 <= sx < self.width and 0.0 <= sy < self.height:
                self._pending_sp.append((sx, sy, r))

        self.count += len(self._pending_sp) - n0
        self.total_prints += 1
        self._has_content = True
        self._since_add = 0.0

    def _flush(self) -> None:
        """把这一帧攒下的足迹 / 油渍 / 飞溅一次画完。"""
        if not self._pending_fp and not self._pending_sm and not self._pending_sp:
            return

        p = QPainter(self.image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)

        for x, y, strength, tone in self._pending_fp:
            # 个体色调影响深浅：偏亮的个体分泌物更浅
            alpha = int((24.0 + 34.0 * strength) * (1.12 - 0.10 * tone))
            alpha = max(10, min(78, alpha))
            r = (1.05 + 1.05 * strength) * self.scale * 2.0
            p.setBrush(QBrush(QColor(30, 21, 15, alpha)))
            p.drawEllipse(QPointF(x, y), r, r * 0.82)
            # 两个更小的爪垫点，凑近看才分辨得出
            p.setBrush(QBrush(QColor(24, 16, 11, min(96, alpha + 22))))
            p.drawEllipse(QPointF(x - r * 0.62, y + r * 0.20), r * 0.36, r * 0.30)
            p.drawEllipse(QPointF(x + r * 0.62, y + r * 0.20), r * 0.36, r * 0.30)

        if self._pending_sm:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.setOpacity(0.45)
            stamp = self._get_smear_stamp()
            src = QRectF(stamp.rect())
            for x, y, angle, length in self._pending_sm:
                w = max(5.0, length * self.scale * 1.15)
                h = max(2.5, w * 0.42)
                p.save()
                p.translate(x, y)
                # 长轴沿身体前进方向（旋转角与 render 的 deg 差 90°，这里直接用 angle）
                p.rotate(math.degrees(angle))
                p.drawPixmap(QRectF(-w / 2.0, -h / 2.0, w, h), stamp, src)
                p.restore()
            p.setOpacity(1.0)

        # 拍击飞溅：几点深褐 + 拖尾（"溅出去的那几滴"，不会随尸体消失）
        if self._pending_sp:
            p.setBrush(QBrush(QColor(72, 46, 22, 158)))
            for x, y, r in self._pending_sp:
                p.drawEllipse(QPointF(x, y), r, r * 0.82)

        p.end()

        self._pending_fp.clear()
        self._pending_sm.clear()
        self._pending_sp.clear()

    @classmethod
    def _get_smear_stamp(cls) -> QPixmap:
        if not hasattr(cls, "_stamp_cache"):
            size = 64
            pix = QPixmap(size, size)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            grad = QRadialGradient(size / 2.0, size / 2.0, size / 2.0)
            grad.setColorAt(0.0, QColor(26, 20, 14, 150))
            grad.setColorAt(0.55, QColor(26, 20, 14, 60))
            grad.setColorAt(1.0, QColor(26, 20, 14, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawEllipse(QRectF(2, size * 0.22, size - 4, size * 0.56))
            p.end()
            cls._stamp_cache = pix
        return cls._stamp_cache

    # ------------------------------------------------------------------ 衰减

    def tick(self, dt: float) -> None:
        """乘性淡出。没有痕迹时直接返回，不产生任何绘制。

        注意：8 位合成器对极小的 alpha 会**量化掉**（alpha=2 的 DestinationOut 填充
        对 alpha=60 的像素完全没有效果），所以这里把逐帧的衰减量累加，攒到一次
        能真正生效的幅度（约 4%）再统一填充 —— 视觉上仍是连续的淡出。
        """
        if not self._has_content:
            self._flush()
            self._since_add += dt
            return

        # 先落笔再衰减：本帧新增的痕迹不该被同一帧的淡出提前变淡
        self._flush()

        self._since_add += dt
        self._fade_acc += 1.0 - math.exp(-min(dt, 0.1) / self.fade_tau)

        amount = self._fade_acc
        # 超出预算时加速淡出，防止长时间累积把整屏糊住
        if self.count > self.max_prints:
            amount = min(0.85, amount * 4.0)
        # 单次衰减量不能太小：8 位合成器按 round(dst·(1-a/255)) 计算，
        # 当 a·dst/255 < 0.5 时结果被舍回原值，痕迹会永远停在某个灰度上（实测停在 11）。
        # 抬到 0.16（≈alpha 40）后，残留底噪降到 alpha≈3，肉眼不可见且会继续衰减到 0。
        if amount < 0.16:
            return
        self._fade_acc = 0.0

        p = QPainter(self.image)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        p.fillRect(self.image.rect(), QColor(0, 0, 0, int(255.0 * min(0.92, amount))))
        p.end()

        # 笔数估值跟着一起衰减：否则一旦历史累计破 3000，就永久停在"超预算"状态，
        # 淡出会一直是 4 倍速（配置的 6 秒变成 1.5 秒），而且虫少了也恢复不回来。
        self.count = int(self.count * (1.0 - min(0.92, amount)))

        # 很久没有新痕迹（约 1.6 倍淡出时长）就整层清空，回到零成本状态
        if self._since_add > self.fade_seconds * 1.6:
            self.clear()

    def render(self, painter: QPainter) -> None:
        if not self.enabled or not self._has_content:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(
            QRectF(0.0, 0.0, self.width / self.scale, self.height / self.scale), self.image
        )
        painter.restore()
