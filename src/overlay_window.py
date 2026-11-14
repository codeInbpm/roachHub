"""全屏透明置顶覆盖窗口与精准鼠标穿透处理。"""

from __future__ import annotations
import logging
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
)
from PySide6.QtWidgets import QWidget

from .entities.base_entity import BaseEntity
from .systems.trails import TrailCanvas
from .utils.math_utils import Vector2D
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

        # 鼠标穿透策略：Windows 走 WM_NCHITTEST（不碰窗口形状），其它平台退回
        # QRegion 遮罩（低频更新）。详见 utils/win_hit_test.py 的说明。
        self._native_hit_test = supports_native_hit_test()
        self._hit_filter: Optional[ClickThroughFilter] = None
        self._mask_timer = 0.0
        self._paint_ms = 0.0
        self._paint_trails_ms = 0.0
        self._paint_calls = 0
        self._paint_t0 = 0.0
        self._paint_t1 = 0.0
        self._paint_entities_ms = 0.0

        self._init_window()

    def _init_window(self) -> None:
        """初始化窗口属性、无边框与背景透明度。"""
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SubWindow
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowFlags(flags)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self.resize_to_screen()

        if self._native_hit_test:
            # 必须在窗口创建之后安装（要拿 hwnd）
            self._hit_filter = ClickThroughFilter(
                self,
                hit_test=lambda x, y: self.hit_test_at(x, y) is not None,
                interactive=lambda: self._is_visible_state,
            )
            QGuiApplication.instance().installNativeEventFilter(self._hit_filter)
            logger.info("鼠标穿透: WM_NCHITTEST（无窗口遮罩）")
        else:
            logger.info("鼠标穿透: QRegion 遮罩（低频更新）")

    def resize_to_screen(self) -> None:
        """自适应主屏幕分辨率。"""
        screen = QGuiApplication.primaryScreen()
        if screen:
            geom = screen.geometry()
            self.setGeometry(geom)
            self.trails.resize(geom.width(), geom.height())
            logger.info("覆盖窗口已匹配屏幕尺寸: %dx%d", geom.width(), geom.height())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            hwnd = int(self.winId())
            PlatformManager.setup_window_styles(hwnd)
        except Exception as e:
            logger.warning("配置底层平台样式异常: %s", e)

    def set_entities(self, entities: List[BaseEntity]) -> None:
        """更新当前帧待渲染的全部生态实体（蟑螂、卵鞘、食物）。"""
        self.entities = entities
        if not self._native_hit_test:
            self._update_click_mask()
        self.update()

    def _update_click_mask(self) -> None:
        """非 Windows 平台的兜底：QRegion 遮罩。

        **绝不能在每帧调用**（以前就是每帧调，导致窗口形状 33 次/秒重排 → 闪烁）。
        这里只按 4Hz 更新，而且遮罩只用于"能不能点到"，不再追求像素级精确。
        """
        if self._native_hit_test:
            return

        if not self._is_visible_state or not self.entities:
            self.setMask(QRegion())
            return

        combined = QRegion()
        for entity in self.entities:
            if not entity.is_alive:
                continue
            rect = entity.get_bounding_rect().toRect()
            combined = combined.united(QRegion(rect.adjusted(-4, -4, 4, 4)))
        self.setMask(combined)

    def tick_mask(self, dt: float) -> None:
        """遮罩兜底路径的低频心跳（Windows 下是空操作）。"""
        if self._native_hit_test:
            return
        self._mask_timer += dt
        if self._mask_timer >= 0.25:
            self._mask_timer = 0.0
            self._update_click_mask()

    def set_simulation_visible(self, visible: bool) -> None:
        """切换生物与覆盖层的可见性。"""
        self._is_visible_state = visible
        if visible:
            self.show()
            self._update_click_mask()
        else:
            if not self._native_hit_test:
                self.setMask(QRegion())
            self.update()

    # -------------------------------------------------------------- 痕迹图层

    def add_footprint(self, wx: float, wy: float, strength: float, tone: float) -> None:
        self.trails.add_footprint(wx, wy, strength, tone)

    def add_smear(self, wx: float, wy: float, angle: float, length: float) -> None:
        self.trails.add_smear(wx, wy, angle, length)

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
        """
        for entity in reversed(self.entities):
            if not entity.is_alive:
                continue
            dx = x - entity.pos.x
            dy = y - entity.pos.y
            radius = self._hit_radius(entity)
            if dx * dx + dy * dy > radius * radius:
                continue
            if entity.contains_point(x, y):
                return entity
        return None

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
        """命中生物即"捏住"它；命中其它物体仍按点击处理。"""
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
                event.accept()
                return

        event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
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
        """按层级分步绘制：痕迹 -> 食物 -> 卵鞘 -> 蟑螂。"""
        if not self._is_visible_state:
            return

        t0 = time.perf_counter()
        self._paint_t0 = t0
        self._paint_calls += 1
        painter = QPainter(self)
        try:
            # 0. 足迹与油渍（最底层，压在所有实体下面）
            _t = time.perf_counter()
            self.trails.render(painter)
            self._paint_trails_ms = (time.perf_counter() - _t) * 1000.0

            # 1. 绘制底层食物
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
        finally:
            painter.end()
            self._paint_ms = (time.perf_counter() - t0) * 1000.0
            self._paint_t1 = time.perf_counter()
