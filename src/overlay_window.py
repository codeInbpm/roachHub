"""全屏透明置顶覆盖窗口与精准鼠标穿透处理。"""

from __future__ import annotations
import logging
import sys
import time
from typing import List, Callable, Optional

from PySide6.QtCore import Qt, QRect, QPoint, QRectF
from PySide6.QtGui import (
    QPainter,
    QPaintEvent,
    QMouseEvent,
    QRegion,
    QGuiApplication,
    QCursor,
    QColor,
    QFont,
    QPen,
    QBrush,
)
from PySide6.QtWidgets import QWidget

from .entities.base_entity import BaseEntity
from .systems.splatter import SplatterSystem
from .systems.swatter import Swatter
from .systems.trails import TrailCanvas
from .utils.math_utils import Vector2D
from .utils.mac_hit_test import CocoaClickThrough, supports_cocoa_hit_test
from .utils.platform_utils import PlatformManager
from .utils.win_hit_test import ClickThroughFilter, supports_native_hit_test

logger = logging.getLogger("roachHub.window")


class OverlayWindow(QWidget):
    """透明置顶全屏覆盖窗口。"""

    def __init__(
        self,
        on_entity_clicked: Optional[Callable[[BaseEntity, Vector2D], None]] = None,
        on_entity_dropped: Optional[Callable[[BaseEntity], None]] = None,
        on_grab_started: Optional[Callable[[BaseEntity], None]] = None,
        trail_scale: float = 0.5,
        trail_fade: float = 6.0,
    ) -> None:
        super().__init__()
        self.on_entity_clicked = on_entity_clicked
        self.on_entity_dropped = on_entity_dropped
        self.on_grab_started = on_grab_started
        self.entities: List[BaseEntity] = []
        self._is_visible_state: bool = True

        # 足迹 / 油渍图层（半分辨率，独立于实体渲染顺序，永远画在最底层）
        self.trails = TrailCanvas(self.width(), self.height(), trail_scale, trail_fade)

        # 抓取拖拽
        self._drag_entity: Optional[BaseEntity] = None
        self._drag_grab_offset = Vector2D(0.0, 0.0)
        self.drag_enabled: bool = True

        # 苍蝇拍与爆浆特效（由 app 注入；缺省时全部为空操作）
        self.swatter: Optional[Swatter] = None
        self.splatter: Optional[SplatterSystem] = None

        # 鼠标穿透策略（三档，判据是**同一份** `accepts_mouse_at`）：
        #   win32 → WM_NCHITTEST 逐点问系统（Windows，最精确、零轮询）
        #   cocoa → NSWindow.ignoresMouseEvents 逐帧开关（macOS，见 mac_hit_test.py）
        #   mask  → QRegion 遮罩 4Hz 重建（其它平台兜底）
        if supports_native_hit_test():
            self._hit_mode = "win32"
        elif supports_cocoa_hit_test():
            self._hit_mode = "cocoa"
        else:
            self._hit_mode = "mask"
        self._native_hit_test = self._hit_mode != "mask"
        self._hit_filter: Optional[ClickThroughFilter] = None
        self._cocoa: Optional[CocoaClickThrough] = None
        self._cocoa_accepting: Optional[bool] = None

        # 「洞」与「死区」—— 覆盖层必须把这两块地让出来，否则桌面会点不动：
        #   * hole:     控制台面板所在的矩形（画也不画、点也不接管），
        #               否则蟑螂爬到面板上就把按钮的点击吃了；
        #   * work_rect: 屏幕工作区，任务栏 / 托盘永远不在它里面。
        #               全屏覆盖窗口压在任务栏上时，托盘图标上的每一只蟑螂
        #               都会变成一块"吃点击的补丁"→ 用户看到的就是"右下角卡住、
        #               什么都点不了"。实测 WindowFromPoint 在托盘位置返回我们的
        #               窗口，而空桌面返回 TrayNotifyWnd，就是这个差别。
        # 洞是**区域**不是矩形：面板被别的窗口挡住一部分时，只有露出来的那部分
        # 该挖空；被挡住的部分不该挖（否则浏览器前面会出现"看不见但抓不到"的空洞）。
        self._hole_region: Optional[QRegion] = None
        self._hole_rect: Optional[QRect] = None
        self._clip_region: Optional[QRegion] = None
        self._work_rect: Optional[QRect] = None
        self._mask_timer = 0.0
        self._paint_ms = 0.0

        # 悬停卡片：默认什么都不显示，鼠标停在蟑螂身上（或点过它）才浮出
        # 能量/健康/状态。刻意不做成全屏 HUD —— 那会让桌面一直挂着一层
        # "调试信息"，跟"伪装成真虫子"这个前提冲突。
        self._hover_entity: Optional[BaseEntity] = None
        self._hover_until: float = 0.0
        self._hover_ms: float = 0.0
        self._paint_trails_ms = 0.0
        self._paint_calls = 0
        self._paint_t0 = 0.0
        self._paint_t1 = 0.0
        self._paint_entities_ms = 0.0

        self._init_window()

    def _init_window(self) -> None:
        """初始化窗口属性、无边框与背景透明度。"""
        # 窗口类型按平台分道（QTBUG-97004）：
        #   * Windows 用 SubWindow —— 不进任务栏、不抢焦点，一切正常；
        #   * macOS 必须用 Tool —— Qt 6 的 cocoa 插件里 WA_TranslucentBackground
        #     只在 Tool 类型的窗口上落地，SubWindow 上是哑的：窗口"存在"但
        #     整层不显示 —— 控制台/托盘/音效全都活着，桌面却一只虫都看不见，
        #     就是 v0.1.0 在 mac 上的现象。
        if sys.platform == "darwin":
            type_flag = Qt.WindowType.Tool
        else:
            type_flag = Qt.WindowType.SubWindow
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | type_flag
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowFlags(flags)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self.resize_to_screen()

        if self._hit_mode == "win32":
            # 必须在窗口创建之后安装（要拿 hwnd）
            self._hit_filter = ClickThroughFilter(
                self,
                hit_test=self.accepts_mouse_at,
                interactive=lambda: self._is_visible_state,
            )
            QGuiApplication.instance().installNativeEventFilter(self._hit_filter)
            logger.info("鼠标穿透: WM_NCHITTEST（无窗口遮罩）")
        elif self._hit_mode == "cocoa":
            self._cocoa = CocoaClickThrough(self)
            self._cocoa.attach()
        else:
            logger.info("鼠标穿透: QRegion 遮罩（低频更新）")

    def resize_to_screen(self) -> None:
        """自适应主屏幕分辨率。"""
        screen = QGuiApplication.primaryScreen()
        if screen:
            geom = screen.geometry()
            avail = screen.availableGeometry()
            self.setGeometry(geom)
            self.trails.resize(geom.width(), geom.height())
            # 工作区（本地坐标）：任务栏露出来的那条永远不接管鼠标
            self._work_rect = avail.translated(-geom.topLeft())
            self._rebuild_clip()
            logger.info("覆盖窗口已匹配屏幕尺寸: %dx%d（工作区 %dx%d）",
                        geom.width(), geom.height(), avail.width(), avail.height())

    # -------------------------------------------------------- 洞与死区

    def set_hole_rect(self, rect: Optional[QRect]) -> None:
        """设定"让位矩形"（窗口内坐标）：控制台面板占的地方。

        None = 没有洞。只在控制台显示/移动/缩放/隐藏时调用，不是每帧。
        """
        region = QRegion(QRect(rect)) if rect is not None else None
        self.set_hole_region(region)

    def set_hole_region(self, region: Optional[QRegion]) -> None:
        """设定"让位区域"（窗口内坐标）：面板真正露出来的那几块。

        传 None 或空区域 = 整屏都归蟑螂（面板没露着的时候必须这样，否则屏幕上
        会留下一个"虫子走进去就消失"的洞 —— 那正是用户报的问题）。
        """
        if region is not None and region.isEmpty():
            region = None
        prev = self._hole_region
        if prev is None and region is None:
            return
        if prev is not None and region is not None and prev == region:
            return
        self._hole_region = region
        self._hole_rect = region.boundingRect() if region is not None else None
        self._rebuild_clip()
        self.update()

    def hole_rect(self) -> Optional[QRect]:
        return self._hole_rect

    def hole_region(self) -> Optional[QRegion]:
        return self._hole_region

    def _rebuild_clip(self) -> None:
        """预算好绘制裁剪区（整屏减去洞）。每帧只 setClipRegion，不重算。"""
        if self._hole_region is None:
            self._clip_region = None
        else:
            self._clip_region = QRegion(self.rect()).subtracted(self._hole_region)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        self._rebuild_clip()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            hwnd = int(self.winId())
            PlatformManager.setup_window_styles(hwnd)
        except Exception as e:
            logger.warning("配置底层平台样式异常: %s", e)
        # cocoa 穿透在 __init__ 里可能拿不到 NSWindow（视图还没挂进窗口），
        # 每次显示时补一次；attach 本身幂等
        if self._hit_mode == "cocoa" and self._cocoa is not None \
                and not self._cocoa.ready:
            self._cocoa.attach()

    def set_entities(self, entities: List[BaseEntity]) -> None:
        """更新当前帧待渲染的全部生态实体（蟑螂、卵鞘、食物）。"""
        self.entities = entities
        self._refresh_hover()
        if not self._native_hit_test:
            self._update_click_mask()
        self.update()

    # -------------------------------------------------------------- 悬停卡片

    def _refresh_hover(self) -> None:
        """每帧用全局光标重新判定悬停对象。

        不能用 mouseMoveEvent 记下的位置：蟑螂会自己跑开，鼠标不动的话
        事件不再来，卡片就会一直挂在原处。用 ``QCursor.pos()`` 每帧重算，
        卡片自然跟着虫走。

        代价：一次 ``hit_test_at``（200 只时约 0.1ms，且带平方距离预筛）。
        只在覆盖层可见时执行，持拍时直接跳过。
        """
        t0 = time.perf_counter()
        try:
            sw = self.swatter
            if sw is not None and sw.armed:
                self._hover_entity = None
                return

            cur = self._hover_entity
            if cur is not None and not cur.is_alive:
                self._hover_entity = None
                cur = None

            g = QCursor.pos()
            local = self.mapFromGlobal(g)
            hit: Optional[BaseEntity] = None
            if self.rect().contains(local):
                hit = self.hit_test_at(float(local.x()), float(local.y()))

            if hit is not None and hit.__class__.__name__ == "Cockroach":
                self._hover_entity = hit
                # 悬停状态每帧续期；停手后 0.3s 自动收起
                self._hover_until = time.monotonic() + 0.30
            elif cur is None or time.monotonic() > self._hover_until:
                self._hover_entity = None
        finally:
            self._hover_ms = (time.perf_counter() - t0) * 1000.0

    def _render_hover_card(self, painter: QPainter) -> None:
        """在悬停个体旁边画一张小卡片：能量 / 健康 / 状态。

        默认不画 —— 桌面平时必须是"干净的桌面"，这些数字只在用户主动
        指向某只虫时才浮出来。
        """
        e = self._hover_entity
        if e is None or not getattr(e, "is_alive", False):
            return
        if e.__class__.__name__ != "Cockroach":
            return

        # 取消挖空裁剪：卡片画在面板边缘时不该被切掉半张
        painter.save()
        painter.setClipping(False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = 158.0, 88.0
        px, py = e.pos.x, e.pos.y
        x = px + max(28.0, e.width * 0.5 + 16.0)
        if x + w > self.width() - 8.0:
            x = px - max(28.0, e.width * 0.5 + 16.0) - w
        y = py - h * 0.5
        x = max(8.0, min(x, self.width() - w - 8.0))
        y = max(8.0, min(y, self.height() - h - 8.0))

        card = QRectF(x, y, w, h)
        painter.setPen(QPen(QColor(255, 255, 255, 42), 1.0))
        painter.setBrush(QBrush(QColor(18, 20, 24, 238)))
        painter.drawRoundedRect(card, 9.0, 9.0)

        # 标题：阶段 + 性别（雌虫顺带标出还能产几次）
        title_font = QFont("Microsoft YaHei UI", 9)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(242, 243, 245))

        if getattr(e, "stage", None) is not None and e.stage.name == "NYMPH":
            title = f"若虫 {getattr(e, 'instar', 1)} 龄"
        else:
            gender = "♀" if getattr(e, "gender", "female") == "female" else "♂"
            title = f"成虫 {gender}"
        painter.drawText(QRectF(x + 11, y + 7, w - 60, 16),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

        if e.stage.name != "NYMPH":
            broods = int(getattr(e, "broods_left", 0))
            tag = f"可产卵 {broods}" if broods > 0 else "待受精"
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            painter.setPen(QColor(150, 154, 162))
            painter.drawText(QRectF(x + 11, y + 7, w - 22, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tag)

        energy = float(getattr(e, "energy", 0.0))
        health = float(getattr(e, "health", 100.0))
        self._draw_stat_bar(painter, x + 11, y + 27, w - 22, "能量", energy,
                            self._energy_color(energy), f"{energy:.0f}")
        self._draw_stat_bar(painter, x + 11, y + 49, w - 22, "健康", health,
                            self._health_color(health), f"{health:.0f}")

        text, color = self._hover_status(e, energy, health)
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.setPen(color)
        painter.drawText(QRectF(x + 11, y + 68, w - 22, 14),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

        painter.restore()

    @staticmethod
    def _draw_stat_bar(
        p: QPainter, x: float, y: float, w: float,
        label: str, value: float, color: QColor, text: str,
    ) -> None:
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.setPen(QColor(148, 152, 160))
        p.drawText(QRectF(x, y, w * 0.7, 13),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        p.setPen(QColor(228, 230, 234))
        p.drawText(QRectF(x, y, w, 13),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(QRectF(x, y + 14.0, w, 4.5), 2.2, 2.2)
        frac = max(0.0, min(1.0, value / 100.0))
        if frac > 0.0:
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, y + 14.0, max(2.5, w * frac), 4.5), 2.2, 2.2)

    @staticmethod
    def _energy_color(v: float) -> QColor:
        if v <= 0.0:
            return QColor(226, 75, 74)
        if v < 20.0:
            return QColor(232, 138, 62)
        if v < 45.0:
            return QColor(224, 176, 64)
        return QColor(96, 186, 120)

    @staticmethod
    def _health_color(v: float) -> QColor:
        if v < 40.0:
            return QColor(226, 75, 74)
        if v < 85.0:
            return QColor(224, 176, 64)
        return QColor(96, 186, 120)

    @staticmethod
    def _hover_status(e: BaseEntity, energy: float, health: float) -> tuple[str, QColor]:
        """一句话说清"它现在怎么样" —— 这是识别"会不会饿死"的入口。"""
        red = QColor(236, 110, 108)
        orange = QColor(240, 156, 84)
        amber = QColor(230, 190, 96)
        green = QColor(126, 200, 142)
        gray = QColor(160, 164, 170)

        if energy <= 0.0:
            return ("能量耗尽 · 正在掉血，约 0.7 秒后死亡" if health > 0.0 else "已饿死", red)
        if energy < 20.0:
            return "饥饿 · 速度已下降，走不远了", orange
        if energy < 40.0:
            return "偏饿 · 正在四处找吃的", amber

        state_name = getattr(getattr(e, "state", None), "name", "")
        if state_name == "MATING":
            # 带上进度：交配是唯一"有明确终点"的状态，光写"交尾中"看不出还要等多久
            dur = float(getattr(e, "mating_duration", 0.0) or 0.0)
            prog = float(getattr(e, "mating_progress", 0.0))
            pct = int(round(100.0 * prog / dur)) if dur > 1e-3 else 0
            return f"交尾中 · {max(0, min(99, pct))}%", QColor(186, 150, 240)
        if state_name == "DRAGGED":
            return "被捏住了", QColor(130, 176, 240)
        if state_name in ("PANIC", "FREEZE"):
            return "受惊 · 糖原在烧", orange
        if state_name == "EAT":
            return "进食中", green
        if state_name == "MOLTING":
            return "蜕皮中", gray
        if health < 60.0:
            return "体力未恢复 · 需要进食", amber
        return "状态良好", green

    def _update_click_mask(self) -> None:
        """非 Windows 平台的兜底：QRegion 遮罩。

        **绝不能在每帧调用**（以前就是每帧调，导致窗口形状 33 次/秒重排 → 闪烁）。
        这里只按 4Hz 更新，而且遮罩只用于"能不能点到"，不再追求像素级精确。
        """
        if self._native_hit_test:
            return

        if not self._is_visible_state:
            self.setMask(QRegion())
            return

        # 持拍时整屏接管（非 Windows 的兜底路径；Windows 走 NCHITTEST）
        if self.swatter is not None and self.swatter.armed:
            combined = QRegion(self._work_rect) if self._work_rect is not None else QRegion(self.rect())
            if self._hole_region is not None:
                combined = combined.subtracted(self._hole_region)
            self.setMask(combined)
            return

        if not self.entities:
            self.setMask(QRegion())
            return

        combined = QRegion()
        for entity in self.entities:
            if not entity.is_alive or not entity.interactive:
                continue
            rect = entity.get_bounding_rect().toRect()
            combined = combined.united(QRegion(rect.adjusted(-4, -4, 4, 4)))
        # 同样要让出任务栏与控制台面板（Windows 走 NCHITTEST，这里是别的平台兜底）
        if self._work_rect is not None:
            combined = combined.intersected(QRegion(self._work_rect))
        if self._hole_region is not None:
            combined = combined.subtracted(self._hole_region)
        self.setMask(combined)

    def tick_mask(self, dt: float) -> None:
        """鼠标穿透的心跳（每帧由 app 调用）。

        * win32：空操作 —— 命中测试是系统回调驱动的，不需要我们轮询；
        * cocoa：每帧判一次"光标下有没有该接管的东西"，然后开关窗口的
          `ignoresMouseEvents`。为什么必须每帧：mac 上没有"逐点命中"的通知机制，
          只能自己问 `QCursor.pos()`，而这个判断一旦慢了半拍，用户点下去的就是
          桌面图标而不是虫子。
        * mask：4Hz 重建遮罩（构建 QRegion 不便宜，低频才不闪）。
        """
        if self._hit_mode == "win32":
            return
        if self._hit_mode == "cocoa":
            self._tick_cocoa()
            return
        self._mask_timer += dt
        if self._mask_timer >= 0.25:
            self._mask_timer = 0.0
            self._update_click_mask()

    def _tick_cocoa(self) -> None:
        """macOS：把"这个点该不该接管鼠标"翻译成窗口的 ignoresMouseEvents 开关。"""
        if self._cocoa is None or not self._cocoa.ready:
            return
        try:
            pos = QCursor.pos()
            self._cocoa.set_accepts(self.accepts_mouse_at(pos.x(), pos.y()))
        except Exception:
            logger.debug("macOS 穿透轮询失败", exc_info=True)

    def release_click_through(self) -> None:
        """退出前恢复窗口的鼠标接收，别给系统留一个"点不动的洞"。"""
        if self._cocoa is not None:
            self._cocoa.release()

    def set_simulation_visible(self, visible: bool) -> None:
        """切换生物与覆盖层的可见性。"""
        self._is_visible_state = visible
        if visible:
            self.show()
            self._update_click_mask()
        else:
            # mac：立刻把鼠标还给桌面，别等下一帧轮询
            if self._cocoa is not None:
                self._cocoa.set_accepts(False)
            if not self._native_hit_test:
                self.setMask(QRegion())
            self.update()

    # -------------------------------------------------------------- 痕迹图层

    def add_footprint(self, wx: float, wy: float, strength: float, tone: float) -> None:
        self.trails.add_footprint(wx, wy, strength, tone)

    def add_smear(self, wx: float, wy: float, angle: float, length: float) -> None:
        self.trails.add_smear(wx, wy, angle, length)

    def add_splat(self, wx: float, wy: float, radius: float = 24.0) -> None:
        """拍死的虫往痕迹层甩几滴（主污渍在尸体上，这里只留飞溅）。"""
        self.trails.add_splat(wx, wy, radius)

    def tick_trails(self, dt: float) -> None:
        self.trails.tick(dt)

    def clear_trails(self) -> None:
        self.trails.clear()

    def set_trails_enabled(self, enabled: bool) -> None:
        self.trails.enabled = enabled
        if not enabled:
            self.trails.clear()

    def set_trails_fade(self, seconds: float) -> None:
        self.trails.fade_tau = max(0.4, float(seconds) / 3.2)
        self.trails.fade_seconds = float(seconds)

    # -------------------------------------------------------------- 鼠标交互

    def hit_test_at(self, x: float, y: float) -> Optional[BaseEntity]:
        """窗口内坐标 → 命中的实体（没有则 None）。

        两次筛：先用平方距离砍掉绝大多数（不做 sqrt / 三角函数），再对候选做
        旋转椭圆精确判定。200 只同屏时单次命中测试约 0.1ms，鼠标移动时才会调用。

        先过两道闸门：
        1. 落在控制台面板的洞 → 一律不认（让点击直达按钮）；
        2. 落在任务栏 / 托盘（非工作区）→ 一律不认。
        这两处如果让蟑螂"认领"，用户就会觉得那块屏幕卡死了。
        """
        ix, iy = int(x), int(y)
        hole = self._hole_region
        if hole is not None and hole.contains(QPoint(ix, iy)):
            return None
        work = self._work_rect
        if work is not None and not work.contains(ix, iy):
            return None

        for entity in reversed(self.entities):
            if not entity.is_alive or not entity.interactive:
                continue
            dx = x - entity.pos.x
            dy = y - entity.pos.y
            radius = self._hit_radius(entity)
            if dx * dx + dy * dy > radius * radius:
                continue
            if entity.contains_point(x, y):
                return entity
        return None

    def accepts_mouse_at(self, x: float, y: float) -> bool:
        """这个点要不要接管鼠标（`WM_NCHITTEST` 的唯一判据）。

        `ClickThroughFilter` 的回调只吃 `bool`，而 `hit_test_at` 返回的是实体，
        所以中间必须有这一层。两件事在这里合流：

        * 平时：命中实体才认领（保持"只在蟑螂身上接收鼠标"的老行为）；
        * 持拍时：**整屏认领**，但仍然让出控制台面板的洞与任务栏 ——
          空拍必须有反馈，否则挥空毫无手感；而右下角必须永远点得动。

        不改这一处，症状是"每次挥空都在框选桌面图标"：程序不报错、拍子照样
        跟着动，但每一下空拍都在桌面上拉出一个选择框。
        """
        if not self._is_visible_state:
            return False
        ix, iy = int(x), int(y)
        hole = self._hole_region
        if hole is not None and hole.contains(QPoint(ix, iy)):
            return False
        work = self._work_rect
        if work is not None and not work.contains(ix, iy):
            return False
        sw = self.swatter
        if sw is not None and sw.armed:
            return True
        return self.hit_test_at(x, y) is not None

    def attach_swatter(self, swatter: Optional[Swatter]) -> None:
        """注入拍子。只做持有，回调接线由 app 负责（它还要同步托盘文案）。"""
        self.swatter = swatter

    def sync_swatter_cursor(self) -> None:
        """持拍时藏光标（拍面中心那个小点才是真正的准星），放下立刻还原。

        收拍**必须**还原成 ArrowCursor：不还原，用户会觉得"鼠标没了"。
        """
        if self.swatter is not None and self.swatter.armed:
            self.setCursor(Qt.CursorShape.BlankCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if not self._native_hit_test:
            # 遮罩路径平时 4Hz 才更新一次；拿起/放下要立刻生效
            self._update_click_mask()
        else:
            # mac 的轮询路径同理：持拍/收拍要当帧生效，否则第一下就空了
            self._tick_cocoa()
        self.update()

    @staticmethod
    def _hit_radius(entity: BaseEntity) -> float:
        """包围椭圆的保守外接半径（含 1.35 的命中裕量与 4px 边缘）。"""
        r = getattr(entity, "_hit_radius_cache", None)
        if r is None:
            half_w = entity.width * 0.5 * 1.35
            half_h = entity.height * 0.5 * 1.35
            r = (half_w * half_w + half_h * half_h) ** 0.5 + 4.0
            entity._hit_radius_cache = r
        return r

    def _hit_test(self, x: float, y: float) -> Optional[BaseEntity]:
        return self.hit_test_at(x, y)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """持拍时左键=挥拍、右键=放下；否则命中生物即"捏住"它。"""
        sw = self.swatter
        if sw is not None and sw.armed:
            # 持拍期间整屏的鼠标都归我们：这里必须在最前面截断，
            # 否则左键会先走去拖拽，把"拍"变成"捏"。
            if event.button() == Qt.MouseButton.RightButton:
                sw.disarm("right_click")
            elif event.button() == Qt.MouseButton.LeftButton:
                p = event.position()
                gp = self.mapToGlobal(QPoint(int(p.x()), int(p.y())))
                sw.swing(Vector2D(float(gp.x()), float(gp.y())))
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            click_point = event.position()
            cx, cy = click_point.x(), click_point.y()
            click_vec = Vector2D(cx, cy)

            hit_entity = self._hit_test(cx, cy)

            if hit_entity is not None:
                logger.debug("命中实体: %s (坐标 %.1f, %.1f)", hit_entity.id, cx, cy)
                if self.drag_enabled and hasattr(hit_entity, "begin_drag"):
                    self._drag_entity = hit_entity
                    self._drag_grab_offset = Vector2D(
                        hit_entity.pos.x - cx, hit_entity.pos.y - cy
                    )
                    hit_entity.begin_drag(click_vec)
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    if self.on_grab_started:
                        self.on_grab_started(hit_entity)
                else:
                    hit_entity.on_click(click_vec)
                if self.on_entity_clicked:
                    self.on_entity_clicked(hit_entity, click_vec)
                # 点过之后卡片钉住 4 秒：鼠标一移开就消失的话，等于什么都没看到
                if hit_entity.__class__.__name__ == "Cockroach":
                    self._hover_entity = hit_entity
                    self._hover_until = time.monotonic() + 4.0
                event.accept()
                return

        event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # 持拍时位置由 app 每帧读全局光标驱动（比事件更稳），这里不改光标形状
        if self.swatter is not None and self.swatter.armed:
            event.accept()
            return

        pos = event.position()
        if self._drag_entity is not None:
            target = Vector2D(
                pos.x() + self._drag_grab_offset.x,
                pos.y() + self._drag_grab_offset.y,
            )
            # 夹在屏幕内，否则会被丢出可视区域
            target.x = min(max(target.x, 6.0), self.width() - 6.0)
            target.y = min(max(target.y, 6.0), self.height() - 6.0)
            self._drag_entity.drag_to(target)
            event.accept()
            return

        # 悬停在生物上时换成"可抓取"手势，这是唯一能提示"可以捏它"的线索
        hit = self._hit_test(pos.x(), pos.y())
        self.setCursor(
            Qt.CursorShape.OpenHandCursor if hit is not None else Qt.CursorShape.ArrowCursor
        )
        event.ignore()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.swatter is not None and self.swatter.armed:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_entity is not None:
            entity = self._drag_entity
            self._drag_entity = None
            try:
                entity.end_drag()
            except Exception as e:
                logger.warning("结束拖拽异常: %s", e)
            if self.on_entity_dropped:
                self.on_entity_dropped(entity)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        event.ignore()

    def paintEvent(self, event: QPaintEvent) -> None:
        """按层级分步绘制：痕迹 -> 空壳/尸体 -> 食物 -> 卵鞘 -> 蟑螂 -> 爆浆 -> 拍子。"""
        if not self._is_visible_state:
            return

        t0 = time.perf_counter()
        self._paint_t0 = t0
        self._paint_calls += 1
        painter = QPainter(self)
        try:
            # 控制台面板那块挖空：蟑螂走到面板边缘就"钻到下面去"，
            # 既不会盖住文字，也不会抢走按钮的点击。
            clip = self._clip_region
            if clip is not None:
                painter.setClipRegion(clip)

            # 0. 足迹与油渍（最底层，压在所有实体下面）
            _t = time.perf_counter()
            self.trails.render(painter)
            self._paint_trails_ms = (time.perf_counter() - _t) * 1000.0

            # 震屏只 translate 绘制，绝不改 entity.pos ——
            # 否则蟑螂会被"震"进面板的洞里、震到墙外，物理系统还要写补丁。
            # 痕迹层不参与震动（它是桌面本身，抖起来反而穿帮）。
            shake = self.splatter.shake_offset() if self.splatter is not None else (0.0, 0.0)
            if shake != (0.0, 0.0):
                painter.translate(shake[0], shake[1])

            # 1. 空壳与尸体（都趴在桌面上，压在活虫下面）
            for entity in self.entities:
                if entity.is_alive and entity.__class__.__name__ == "Exuvia":
                    entity.render(painter)
            for entity in self.entities:
                if entity.is_alive and entity.__class__.__name__ == "SquashedRoach":
                    entity.render(painter)

            for entity in self.entities:
                if entity.is_alive and entity.__class__.__name__ == "Food":
                    entity.render(painter)

            # 2. 绘制独立放置的卵鞘 (拖带的卵鞘会由母体或在此绘制)
            for entity in self.entities:
                if entity.is_alive and entity.__class__.__name__ == "Ootheca":
                    entity.render(painter)

            # 3. 绘制蟑螂 (若虫与成虫)
            _t = time.perf_counter()
            for entity in self.entities:
                if entity.is_alive and entity.__class__.__name__ == "Cockroach":
                    entity.render(painter)
            self._paint_entities_ms = (time.perf_counter() - _t) * 1000.0

            # 3.5 交配心形：等所有虫体都画完再叠一层。
            #     塞在单只虫的 render 里的话，先画的那只撒的心形会被另一只挡住一半。
            for entity in self.entities:
                if entity.is_alive and entity.__class__.__name__ == "Cockroach":
                    entity.render_mating_hearts(painter)

            # 4. 爆浆：颗粒 / 冲击环 / 白闪（压在虫身上，"糊一脸"才对）
            if self.splatter is not None:
                self.splatter.render(painter)

            if shake != (0.0, 0.0):
                painter.translate(-shake[0], -shake[1])

            # 4.5 悬停卡片：压在虫身上、拍子之下，不参与震屏
            self._render_hover_card(painter)

            # 5. 拍子永远在最上面，而且**不参与震屏**（否则准星会飘）
            if self.swatter is not None:
                self.swatter.render(painter, float(self.width()), float(self.height()))
        finally:
            painter.end()
            self._paint_ms = (time.perf_counter() - t0) * 1000.0
            self._paint_t1 = time.perf_counter()
