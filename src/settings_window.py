"""客户端配置与控制中心窗口。

支持表现风格、生命周期、自动/轻量有性繁殖模式、感应距离、雌雄比例与能耗参数配置。
"""

from __future__ import annotations
import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QIcon, QFont, QColor, QKeySequence
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QGroupBox,
    QLabel,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QFormLayout,
    QProgressBar,
    QKeySequenceEdit,
)

from .config import Config, DEFAULT_CONFIG
from .resources import ResourceManager
from .utils.hotkeys import normalize_hotkey

logger = logging.getLogger("roachHub.settings")

SETTINGS_STYLE = """
QWidget {
    background-color: #1a1a20;
    color: #e2e2ec;
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}
QWidget#brandHeader {
    background-color: transparent;
}
QLabel#brandTitle {
    font-size: 17px;
    font-weight: 700;
    color: #f4ede4;
}
QLabel#brandSub {
    font-size: 11px;
    color: #8d8578;
}
QLabel#brandHint {
    font-size: 11px;
    color: #7c7469;
    padding-right: 4px;
}
QTabWidget::pane {
    border: 1px solid #323242;
    background: #22222c;
    border-radius: 8px;
    padding: 8px;
}
QTabBar::tab {
    background: #1a1a20;
    color: #9292a4;
    padding: 8px 18px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    border: 1px solid transparent;
}
QTabBar::tab:selected {
    background: #22222c;
    color: #ffb86c;
    border-bottom: 2px solid #ffb86c;
    font-weight: bold;
}
QTabBar::tab:hover {
    color: #f0f0ff;
}
QGroupBox {
    border: 1px solid #38384a;
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: bold;
    color: #ffb86c;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
}
QLabel {
    color: #cfd0dd;
}
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
    background-color: #141418;
    border: 1px solid #444458;
    border-radius: 4px;
    padding: 4px 8px;
    color: #ffffff;
    min-height: 22px;
}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {
    border: 1px solid #ffb86c;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid #55556a;
    background: #141418;
}
QCheckBox::indicator:checked {
    background-color: #ffb86c;
    border-color: #ffb86c;
}
QPushButton {
    background-color: #303040;
    border: 1px solid #48485e;
    border-radius: 5px;
    padding: 6px 16px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #3c3c50;
    border-color: #ffb86c;
}
QPushButton#primaryBtn {
    background-color: #d4762c;
    border: 1px solid #ff953f;
    color: #ffffff;
    font-weight: bold;
}
QPushButton#primaryBtn:hover {
    background-color: #e58234;
}
QPushButton#burstBtn {
    background-color: #a3210f;
    border: 1px solid #ff5a36;
    color: #ffffff;
    font-weight: bold;
    font-size: 15px;
    padding: 12px 16px;
    border-radius: 7px;
}
QPushButton#burstBtn:hover {
    background-color: #bf2a13;
}
QPushButton#burstBtn:pressed {
    background-color: #7d1809;
}
QProgressBar {
    border: 1px solid #3a3a4c;
    border-radius: 5px;
    background: #141418;
    height: 14px;
    text-align: center;
    color: #e2e2ec;
    font-size: 11px;
}
QProgressBar::chunk {
    background-color: #c2531f;
    border-radius: 4px;
}
QLabel#statLabel {
    color: #cfd0dd;
    font-size: 12px;
}
QLabel#hotkeyOk {
    color: #6fcf72;
}
QLabel#hotkeyBad {
    color: #ff7a6b;
}
QLabel#toast {
    color: #6fcf72;
    font-size: 12px;
}
QKeySequenceEdit {
    background-color: #141418;
    border: 1px solid #444458;
    border-radius: 4px;
    padding: 3px 6px;
    color: #ffffff;
    min-height: 22px;
}
"""


class SettingsWindow(QWidget):
    """设置控制中心窗口（用户口中的「控制台」）。

    启动时默认弹出；全局热键可随时隐藏/召唤。
    第一个 Tab 是「虫巢控制台」——放的是即时动作（爆发/批量投放），不需要保存。
    """

    config_applied = Signal()
    # 面板的位置/大小/显隐变了 —— 覆盖层要据此在屏幕上「挖洞」让出这块地，
    # 否则蟑螂爬到面板上就会把按钮的点击吃掉（见 overlay_window.set_hole_rect）。
    geometry_changed = Signal()

    def __init__(
        self,
        config: Config,
        on_applied: Optional[Callable[[], None]] = None,
        on_clear_trails: Optional[Callable[[], None]] = None,
        on_preview_voice: Optional[Callable[[], None]] = None,
        on_nest_burst: Optional[Callable[[], int]] = None,
        on_spawn_roaches: Optional[Callable[..., list]] = None,
        on_spawn_foods: Optional[Callable[..., list]] = None,
        on_clear_all: Optional[Callable[[], None]] = None,
        on_query_stats: Optional[Callable[[], dict]] = None,
        on_toggle_sim_visible: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.on_clear_trails = on_clear_trails
        self.on_preview_voice = on_preview_voice
        self.on_nest_burst = on_nest_burst
        self.on_spawn_roaches = on_spawn_roaches
        self.on_spawn_foods = on_spawn_foods
        self.on_clear_all = on_clear_all
        self.on_query_stats = on_query_stats
        self.on_toggle_sim_visible = on_toggle_sim_visible
        if on_applied:
            self.config_applied.connect(on_applied)

        self.setWindowTitle("roachHub - 高拟真控制台")
        self.setWindowIcon(ResourceManager.get_app_icon())
        self.resize(880, 830)
        self.setStyleSheet(SETTINGS_STYLE)

        self._init_ui()
        self.load_from_config()

        # 状态区与热键生效状态每 0.5 秒刷新一次（窗口不可见时自动跳过，不浪费）
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()
        self._refresh_stats()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 14)
        main_layout.setSpacing(10)

        main_layout.addWidget(self._build_header())

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # 0. 虫巢控制台（启动后默认停在这一页）
        self.tabs.addTab(self._build_console_tab(), "🪳 控制台")

        # 1. 拟真与渲染风格 Tab
        self.tabs.addTab(self._build_render_tab(), "🎨 表现风格")

        # 2. 行为习性与感知 Tab
        self.tabs.addTab(self._build_behavior_tab(), "🧠 行为习性")

        # 3. 生命周期与繁衍系统 Tab (重点完善)
        self.tabs.addTab(self._build_lifecycle_tab(), "🌱 繁衍生长")

        # 4. 窗口显示 Tab
        self.tabs.addTab(self._build_display_tab(), "🖥️ 窗口")

        # 5. 互动与氛围 Tab（拖拽 / 痕迹 / 音效）
        self.tabs.addTab(self._build_atmosphere_tab(), "🖱️ 互动氛围")

        # 6. 常规与热键 Tab
        self.tabs.addTab(self._build_general_tab(), "⌨️ 快捷键")

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.reset_btn = QPushButton("🔄 恢复默认值")
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        btn_layout.addWidget(self.reset_btn)

        self.toast_label = QLabel("")
        self.toast_label.setObjectName("toast")
        btn_layout.addWidget(self.toast_label)

        btn_layout.addStretch()

        self.cancel_btn = QPushButton("关闭")
        self.cancel_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("💾 保存并应用")
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(self.save_btn)

        main_layout.addLayout(btn_layout)

    # ------------------------------------------------------------ 虫巢控制台

    def _build_header(self) -> QWidget:
        """品牌头：徽章 logo + 名字 + 一行副标题。"""
        header = QWidget()
        header.setObjectName("brandHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(10)

        badge = QLabel()
        badge.setPixmap(ResourceManager.get_app_icon().pixmap(32, 32))
        badge.setFixedSize(32, 32)
        badge.setScaledContents(False)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(badge)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        title = QLabel("roachHub")
        title.setObjectName("brandTitle")
        sub = QLabel("高拟真桌面生态模拟器")
        sub.setObjectName("brandSub")
        text.addWidget(title)
        text.addWidget(sub)
        row.addLayout(text)

        row.addStretch()

        self.label_brand_hint = QLabel("")
        self.label_brand_hint.setObjectName("brandHint")
        row.addWidget(self.label_brand_hint)
        return header

    def _build_console_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        # ---- 群落状态 ----
        stat_group = QGroupBox("当前群落")
        stat_box = QVBoxLayout(stat_group)
        stat_box.setSpacing(6)

        self.label_stat_total = QLabel("画面内 0 / 0 只")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        self.label_stat_total.setFont(f)
        stat_box.addWidget(self.label_stat_total)

        self.bar_capacity = QProgressBar()
        self.bar_capacity.setRange(0, 100)
        self.bar_capacity.setTextVisible(True)
        stat_box.addWidget(self.bar_capacity)

        self.label_stat_detail = QLabel("成虫 ♀0 ♂0 · 若虫 0 · 卵鞘 0 · 碎屑 0")
        self.label_stat_detail.setObjectName("statLabel")
        stat_box.addWidget(self.label_stat_detail)

        self.label_stat_runtime = QLabel("画质 LOD2 · 帧率上限 60 FPS · 桌面蟑螂：显示中")
        self.label_stat_runtime.setObjectName("statLabel")
        stat_box.addWidget(self.label_stat_runtime)

        layout.addWidget(stat_group)

        # ---- 虫巢爆发 ----
        burst_group = QGroupBox("虫巢爆发")
        burst_box = QVBoxLayout(burst_group)
        burst_box.setSpacing(6)

        self.btn_burst = QPushButton("⚡ 虫巢爆发 —— 一键刷满屏")
        self.btn_burst.setObjectName("burstBtn")
        self.btn_burst.setMinimumHeight(46)
        self.btn_burst.setToolTip("把桌面蟑螂立刻补到上限，并让全体受惊四散")
        self.btn_burst.clicked.connect(self._on_burst_clicked)
        burst_box.addWidget(self.btn_burst)

        burst_hint = QLabel(
            "把桌面补到「生物上限」，整个巢同时炸开（全部受惊逃窜）。"
            "已达上限时不再增加，但依然会炸一次。上限在「表现风格」或下面批量投放处可改。"
        )
        burst_hint.setWordWrap(True)
        burst_hint.setStyleSheet("color: #8f90a0;")
        burst_box.addWidget(burst_hint)

        layout.addWidget(burst_group)

        # ---- 批量投放蟑螂 ----
        batch_group = QGroupBox("批量投放蟑螂")
        batch_box = QVBoxLayout(batch_group)
        batch_box.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel("数量"))
        self.spin_spawn_count = QSpinBox()
        self.spin_spawn_count.setRange(1, 999)
        self.spin_spawn_count.setValue(20)
        self.spin_spawn_count.setSuffix(" 只")
        row1.addWidget(self.spin_spawn_count)

        row1.addWidget(QLabel("阶段"))
        self.combo_spawn_stage = QComboBox()
        self.combo_spawn_stage.addItem("混合（成虫＋若虫）", "mixed")
        self.combo_spawn_stage.addItem("全部成虫", "adult")
        self.combo_spawn_stage.addItem("全部若虫", "nymph")
        row1.addWidget(self.combo_spawn_stage, 1)

        self.btn_spawn_batch = QPushButton("➕ 投放")
        self.btn_spawn_batch.setObjectName("primaryBtn")
        self.btn_spawn_batch.clicked.connect(self._on_spawn_batch_clicked)
        row1.addWidget(self.btn_spawn_batch)
        batch_box.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        wait = QLabel("快投")
        row2.addWidget(wait)
        for n in (10, 50, 100):
            b = QPushButton(f"+{n}")
            b.clicked.connect(lambda _=False, c=n: self._do_spawn(c))
            row2.addWidget(b)
        row2.addStretch()

        self.btn_clear_all = QPushButton("🧹 驱散所有蟑螂与物体")
        self.btn_clear_all.clicked.connect(self._on_clear_all_clicked)
        row2.addWidget(self.btn_clear_all)
        batch_box.addLayout(row2)

        batch_hint = QLabel(
            "批量投放会自动截断到剩余容量，不会突破「生物上限」。"
            "快投按钮用的是当前「阶段」设置。"
        )
        batch_hint.setWordWrap(True)
        batch_hint.setStyleSheet("color: #8f90a0;")
        batch_box.addWidget(batch_hint)

        layout.addWidget(batch_group)

        # ---- 批量投放食物 ----
        food_group = QGroupBox("批量投放食物")
        food_box = QHBoxLayout(food_group)
        food_box.setSpacing(8)
        food_box.addWidget(QLabel("数量"))
        self.spin_food_count = QSpinBox()
        self.spin_food_count.setRange(1, 40)
        self.spin_food_count.setValue(5)
        self.spin_food_count.setSuffix(" 块")
        food_box.addWidget(self.spin_food_count)
        self.btn_spawn_foods = QPushButton("🧀 投放碎屑")
        self.btn_spawn_foods.clicked.connect(self._on_spawn_foods_clicked)
        food_box.addWidget(self.btn_spawn_foods)
        food_box.addStretch()
        layout.addWidget(food_group)

        # ---- 桌面可见性 ----
        vis_group = QGroupBox("桌面显示")
        vis_box = QHBoxLayout(vis_group)
        self.btn_toggle_sim = QPushButton("👁 隐藏桌面蟑螂")
        self.btn_toggle_sim.clicked.connect(self._on_toggle_sim_clicked)
        vis_box.addWidget(self.btn_toggle_sim)
        vis_box.addStretch()
        layout.addWidget(vis_group)

        layout.addStretch()
        return widget

    # ------------------------------------------------------------ 控制台动作

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """每次弹出都立刻刷一遍状态，别让用户对着"0/0 只"等半秒。"""
        super().showEvent(event)
        self._refresh_stats()
        self.geometry_changed.emit()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().hideEvent(event)
        self.geometry_changed.emit()

    def moveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().moveEvent(event)
        self.geometry_changed.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        self.geometry_changed.emit()

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """激活状态变化也要通知。

        面板被别的窗口压到后面（或被重新抬起来）时，覆盖层的"洞"必须立刻跟着变：
        留在原地的话，屏幕上会有一块**虫子走进去就消失**的矩形 —— 面板明明躲在
        浏览器后面，用户只能看见那个洞。250ms 轮询是兜底，这里是即时路径。
        """
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.ActivationChange,
            QEvent.Type.WindowStateChange,
        ):
            self.geometry_changed.emit()

    def _refresh_stats(self) -> None:
        if self.on_query_stats is None or not self.isVisible():
            return
        try:
            s = self.on_query_stats()
        except Exception:
            logger.debug("刷新控制台状态失败", exc_info=True)
            return

        alive, cap = int(s.get("alive", 0)), max(1, int(s.get("cap", 1)))
        self.label_stat_total.setText(f"画面内 {alive} / {cap} 只")
        self.bar_capacity.setRange(0, cap)
        self.bar_capacity.setValue(min(alive, cap))
        self.bar_capacity.setFormat(f"{alive}/{cap}")
        self.label_stat_detail.setText(
            f"成虫 ♀{s.get('females', 0)} ♂{s.get('males', 0)} · "
            f"若虫 {s.get('nymphs', 0)} · 卵鞘 {s.get('eggs', 0)} · 碎屑 {s.get('foods', 0)}"
        )
        self.label_stat_runtime.setText(
            f"画质 LOD{s.get('quality', 2)} · 帧率上限 {s.get('fps', 0)} FPS · "
            f"桌面蟑螂：{'显示中' if s.get('sim_visible', True) else '已隐藏'}"
        )
        self.btn_toggle_sim.setText(
            "👁 隐藏桌面蟑螂" if s.get("sim_visible", True) else "👁 显示桌面蟑螂"
        )
        self._refresh_hotkey_status(s.get("hotkeys", {}))

    def _refresh_hotkey_status(self, hotkeys: dict) -> None:
        pairs = (
            ("console", self.label_hotkey_console),
            ("visible", self.label_hotkey_visible),
            ("burst", self.label_hotkey_burst),
        )
        for name, label in pairs:
            info = hotkeys.get(name) or {}
            spec = str(info.get("spec", ""))
            if not spec:
                label.setText("未设置")
                label.setObjectName("statLabel")
            elif info.get("ok"):
                label.setText("已生效")
                label.setObjectName("hotkeyOk")
            else:
                label.setText(f"未生效：{info.get('error', '注册失败')}")
                label.setObjectName("hotkeyBad")
            label.setStyleSheet("")     # 强制按新 objectName 重新解析样式
            label.style().unpolish(label)
            label.style().polish(label)

    def _on_burst_clicked(self) -> None:
        if self.on_nest_burst is None:
            return
        added = int(self.on_nest_burst() or 0)
        if added > 0:
            self._toast(f"⚡ 虫巢爆发：新增 {added} 只")
        else:
            self._toast("⚡ 已达生物上限，巢炸了但没再增加")
        self._refresh_stats()

    def _do_spawn(self, count: int) -> int:
        if self.on_spawn_roaches is None:
            return 0
        stage_key = self.combo_spawn_stage.currentData()
        stage = None
        if stage_key == "adult":
            from .entities.base_entity import LifeStage
            stage = LifeStage.ADULT
        elif stage_key == "nymph":
            from .entities.base_entity import LifeStage
            stage = LifeStage.NYMPH
        added = self.on_spawn_roaches(count, stage)
        n = len(added) if added is not None else 0
        self._toast(f"已投放 {n} 只" if n else "已达上限，没有新增")
        self._refresh_stats()
        return n

    def _on_spawn_batch_clicked(self) -> None:
        self._do_spawn(self.spin_spawn_count.value())

    def _on_spawn_foods_clicked(self) -> None:
        if self.on_spawn_foods is None:
            return
        out = self.on_spawn_foods(self.spin_food_count.value())
        n = len(out) if out is not None else 0
        self._toast(f"已投放 {n} 块碎屑")
        self._refresh_stats()

    def _on_clear_all_clicked(self) -> None:
        if self.on_clear_all:
            self.on_clear_all()
            self._toast("已驱散所有蟑螂与物体")
            self._refresh_stats()

    def _on_toggle_sim_clicked(self) -> None:
        if self.on_toggle_sim_visible:
            visible = self.on_toggle_sim_visible()
            self._toast("桌面蟑螂已显示" if visible else "桌面蟑螂已隐藏")
            self._refresh_stats()

    def _toast(self, text: str) -> None:
        """底部一行即时反馈 —— 这些动作立即生效，不该用弹窗打断。"""
        self.toast_label.setText(text)
        QTimer.singleShot(4000, lambda: self.toast_label.setText(""))

    def _build_render_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("外观风格与光学拟真")
        form = QFormLayout(group)
        form.setSpacing(10)

        self.combo_render_style = QComboBox()
        self.combo_render_style.addItem("📸 写实模式 (默认 - 高精度贴图/油亮倒刺/真实解剖)", "realistic")
        self.combo_render_style.addItem("🎨 简洁模式 (清爽卡通矢量/无刺激轮廓)", "simple")
        form.addRow("表现风格切换:", self.combo_render_style)

        self.spin_shadow = QDoubleSpinBox()
        self.spin_shadow.setRange(0.0, 1.0)
        self.spin_shadow.setSingleStep(0.05)
        form.addRow("桌面接触阴影强度 (0~1):", self.spin_shadow)

        self.check_teneral = QCheckBox(
            "显示刚蜕皮的奶白色嫩壳（真实习性：新壳未硬化，约 3.6 秒内逐渐变深）"
        )
        self.check_teneral.setToolTip(
            "若虫每蜕一次皮都会先变成乳白半透明，随后颜色化回正常。\n"
            "关掉后蜕皮过程不再变白，但仍会留下蜕下的空壳。"
        )
        form.addRow(self.check_teneral)

        self.spin_max_roaches = QSpinBox()
        self.spin_max_roaches.setRange(1, 999)
        form.addRow("桌面生物上限 (只):", self.spin_max_roaches)

        self.spin_scale_variation = QDoubleSpinBox()
        self.spin_scale_variation.setRange(0.0, 0.4)
        self.spin_scale_variation.setSingleStep(0.05)
        form.addRow("个体体型随机差异比例:", self.spin_scale_variation)

        self.spin_walk_speed = QDoubleSpinBox()
        self.spin_walk_speed.setRange(20.0, 260.0)
        form.addRow("漫步最高速度 (像素/秒):", self.spin_walk_speed)

        self.spin_panic_speed = QDoubleSpinBox()
        self.spin_panic_speed.setRange(150.0, 900.0)
        form.addRow("惊慌冲刺极速 (像素/秒):", self.spin_panic_speed)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def _build_behavior_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("自然生物习性与鼠标感知")
        form = QFormLayout(group)
        form.setSpacing(10)

        self.spin_thigmotaxis = QDoubleSpinBox()
        self.spin_thigmotaxis.setRange(0.0, 1.0)
        self.spin_thigmotaxis.setSingleStep(0.05)
        form.addRow("趋边性偏好强度 (贴壁游走倾向):", self.spin_thigmotaxis)

        self.spin_grooming = QDoubleSpinBox()
        self.spin_grooming.setRange(0.0, 1.0)
        self.spin_grooming.setSingleStep(0.05)
        form.addRow("停顿清理梳理触角频率:", self.spin_grooming)

        self.check_circadian = QCheckBox("启用昼夜节律 (夜间 20:00~06:00 亢奋活跃，白天多隐蔽)")
        form.addRow(self.check_circadian)

        self.spin_panic_dur = QDoubleSpinBox()
        self.spin_panic_dur.setRange(0.5, 10.0)
        form.addRow("恐慌受惊逃跑持续时长 (秒):", self.spin_panic_dur)

        self.spin_food_interval = QDoubleSpinBox()
        self.spin_food_interval.setRange(3.0, 120.0)
        form.addRow("桌面食物生成间隔 (秒):", self.spin_food_interval)

        self.spin_max_food = QSpinBox()
        self.spin_max_food.setRange(1, 20)
        form.addRow("桌面食物碎屑上限 (块):", self.spin_max_food)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def _build_lifecycle_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 1. 繁殖系统配置组
        reproduce_group = QGroupBox("繁殖系统模式与参数")
        rep_form = QFormLayout(reproduce_group)
        rep_form.setSpacing(9)

        self.combo_reproduce_mode = QComboBox()
        self.combo_reproduce_mode.addItem("自动繁殖模式 (无性别直接受孕产卵)", "auto")
        self.combo_reproduce_mode.addItem("轻量有性繁殖模式 (雌雄相遇尾对尾交尾)", "sexual")
        rep_form.addRow("繁殖模式选择:", self.combo_reproduce_mode)

        self.spin_mating_radius = QDoubleSpinBox()
        self.spin_mating_radius.setRange(50.0, 500.0)
        self.spin_mating_radius.setSingleStep(10.0)
        rep_form.addRow("有性寻偶感应距离 (像素):", self.spin_mating_radius)

        self.spin_female_ratio = QDoubleSpinBox()
        self.spin_female_ratio.setRange(0.1, 0.9)
        self.spin_female_ratio.setSingleStep(0.05)
        rep_form.addRow("雌性比例 (仅有性模式生效):", self.spin_female_ratio)

        self.spin_min_energy = QDoubleSpinBox()
        self.spin_min_energy.setRange(20.0, 95.0)
        rep_form.addRow("繁殖所需最低能量值:", self.spin_min_energy)

        self.spin_cost_energy = QDoubleSpinBox()
        self.spin_cost_energy.setRange(5.0, 50.0)
        rep_form.addRow("每次产卵消耗能量:", self.spin_cost_energy)

        self.spin_reproduce_cd = QDoubleSpinBox()
        self.spin_reproduce_cd.setRange(5.0, 300.0)
        rep_form.addRow("成虫繁殖冷却时间 (秒):", self.spin_reproduce_cd)

        self.check_allow_near_cap = QCheckBox("允许在生物数量接近上限时仍强行繁殖")
        rep_form.addRow(self.check_allow_near_cap)

        layout.addWidget(reproduce_group)

        # 2. 卵鞘与成长演化组
        growth_group = QGroupBox("卵鞘形态与蜕皮成长")
        g_form = QFormLayout(growth_group)
        g_form.setSpacing(9)

        self.combo_ootheca_mode = QComboBox()
        self.combo_ootheca_mode.addItem("母体拖卵 (德国小蠊风格)", "carried")
        self.combo_ootheca_mode.addItem("桌面隐蔽产卵 (美洲大蠊风格)", "dropped")
        self.combo_ootheca_mode.addItem("随机决定", "random")
        g_form.addRow("卵鞘产出与存放方式:", self.combo_ootheca_mode)

        self.spin_egg_time = QDoubleSpinBox()
        self.spin_egg_time.setRange(3.0, 300.0)
        g_form.addRow("单个卵鞘孵化所需时间 (秒):", self.spin_egg_time)

        self.spin_nymph_count = QSpinBox()
        self.spin_nymph_count.setRange(1, 6)
        g_form.addRow("单个卵鞘破壳若虫数量 (只):", self.spin_nymph_count)

        self.spin_max_instars = QSpinBox()
        self.spin_max_instars.setRange(2, 7)
        g_form.addRow("若虫蜕皮龄期总数 (龄):", self.spin_max_instars)

        self.spin_instar_time = QDoubleSpinBox()
        self.spin_instar_time.setRange(3.0, 300.0)
        g_form.addRow("若虫每龄蜕皮耗时 (秒):", self.spin_instar_time)

        self.spin_lifespan = QDoubleSpinBox()
        self.spin_lifespan.setRange(0.0, 3600.0)
        g_form.addRow("成虫自然寿命 (0为永生):", self.spin_lifespan)

        layout.addWidget(growth_group)
        layout.addStretch()
        return widget

    def _build_display_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("透明覆盖窗口")
        form = QFormLayout(group)
        form.setSpacing(10)

        self.check_always_on_top = QCheckBox("窗口始终置顶 (Always on Top)")
        form.addRow(self.check_always_on_top)

        self.check_multi_screen = QCheckBox("跨全显示器漫游 (Multi-screen)")
        form.addRow(self.check_multi_screen)

        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(30, 144)
        form.addRow("目标渲染帧率 (FPS):", self.spin_fps)

        self.spin_min_fps = QSpinBox()
        self.spin_min_fps.setRange(15, 60)
        form.addRow("最低帧率下限 (FPS):", self.spin_min_fps)
        fps_hint = QLabel(
            "帧预算自适应：帧耗时超标先降肢体精度（LOD2→1→0），画质见底才降到「最低帧率」。"
            "这个下限是「不卡死」的保险丝 —— 宁可 30 FPS 稳定，也不硬撑 60 FPS 把消息循环挤爆。"
        )
        fps_hint.setWordWrap(True)
        fps_hint.setStyleSheet("color: #8f90a0;")
        form.addRow(fps_hint)

        self.spin_padding = QDoubleSpinBox()
        self.spin_padding.setRange(10.0, 100.0)
        form.addRow("屏幕边缘缓冲距离 (像素):", self.spin_padding)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def _build_atmosphere_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 1. 抓取与挣扎
        grab_group = QGroupBox("抓取与挣扎")
        g_form = QFormLayout(grab_group)
        g_form.setSpacing(10)
        self.check_drag = QCheckBox("启用「捏起并拖拽」：按住蟑螂可拎起来，它会蹬腿挣扎")
        g_form.addRow(self.check_drag)
        hint = QLabel("拖住超过 6 秒它会装死，松手后先瘫一会儿再逃。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8f90a0;")
        g_form.addRow(hint)
        layout.addWidget(grab_group)

        # 2. 足迹与油渍
        trail_group = QGroupBox("桌面痕迹（足迹 / 油渍）")
        t_form = QFormLayout(trail_group)
        t_form.setSpacing(10)
        self.check_trails = QCheckBox("爬过之后留下淡淡的足印与湿痕")
        t_form.addRow(self.check_trails)

        self.spin_trail_fade = QDoubleSpinBox()
        self.spin_trail_fade.setRange(1.0, 30.0)
        self.spin_trail_fade.setSingleStep(0.5)
        self.spin_trail_fade.setSuffix(" 秒")
        t_form.addRow("痕迹淡出时长:", self.spin_trail_fade)

        self.btn_clear_trails = QPushButton("🧹 立即擦掉桌面痕迹")
        self.btn_clear_trails.clicked.connect(self._on_clear_trails_clicked)
        t_form.addRow(self.btn_clear_trails)
        layout.addWidget(trail_group)

        # 3. 音效
        audio_group = QGroupBox("窸窣音效")
        a_form = QFormLayout(audio_group)
        a_form.setSpacing(10)
        self.check_audio = QCheckBox("启用事件音效（落地轻响 / 咬食 / 受惊唰声）")
        a_form.addRow(self.check_audio)

        self.spin_audio_volume = QDoubleSpinBox()
        self.spin_audio_volume.setRange(0.0, 1.0)
        self.spin_audio_volume.setSingleStep(0.05)
        a_form.addRow("音效总音量 (0~1):", self.spin_audio_volume)

        self.spin_audio_budget = QSpinBox()
        self.spin_audio_budget.setRange(2, 60)
        a_form.addRow("每秒音效次数上限:", self.spin_audio_budget)

        audio_hint = QLabel("全屏 150 只时落地事件每秒上千次，限流是必须的。托盘菜单可随时静音。")
        audio_hint.setWordWrap(True)
        audio_hint.setStyleSheet("color: #8f90a0;")
        a_form.addRow(audio_hint)
        layout.addWidget(audio_group)

        # 4. 抓取音效（独立开关：它比窸窣声响得多，扰民风险评估完全不同）
        voice_group = QGroupBox("抓取音效")
        v_form = QFormLayout(voice_group)
        v_form.setSpacing(10)
        self.check_voice = QCheckBox("捏住蟑螂时播放「你干嘛~哎呦」")
        v_form.addRow(self.check_voice)

        self.check_voice_stop = QCheckBox("松手立刻掐断（默认关：让整句说完）")
        v_form.addRow(self.check_voice_stop)

        self.spin_voice_volume = QDoubleSpinBox()
        self.spin_voice_volume.setRange(0.0, 1.0)
        self.spin_voice_volume.setSingleStep(0.05)
        v_form.addRow("抓取音效音量 (0~1):", self.spin_voice_volume)

        self.btn_preview_voice = QPushButton("🔊 试听")
        self.btn_preview_voice.clicked.connect(self._on_preview_voice_clicked)
        v_form.addRow(self.btn_preview_voice)

        voice_hint = QLabel(
            "素材是一整句 3.45 秒的「你干嘛~哎呦」（原始 MP3 有 17 秒，其中 13 秒是数字静音）。"
            "默认松手不中断 —— 实际拖拽通常不到 1 秒，一松手就掐会让「哎呦」永远听不见；"
            "想要利落感就勾上「松手立刻掐断」。托盘的总静音也会压掉它。"
        )
        voice_hint.setWordWrap(True)
        voice_hint.setStyleSheet("color: #8f90a0;")
        v_form.addRow(voice_hint)
        layout.addWidget(voice_group)

        layout.addStretch()
        return widget

    def _on_clear_trails_clicked(self) -> None:
        if self.on_clear_trails:
            self.on_clear_trails()

    def _on_preview_voice_clicked(self) -> None:
        if self.on_preview_voice:
            self.on_preview_voice()

    def _build_general_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("全局热键（应用没有焦点时也生效）")
        form = QFormLayout(group)
        form.setSpacing(10)

        intro = QLabel(
            "点进输入框后直接按组合键即可录入，像微信的 Ctrl+Alt+W 那样随时召唤。"
            "清空输入框 = 禁用该热键。至少需要一个修饰键（Ctrl / Alt / Shift / Win）。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #8f90a0;")
        form.addRow(intro)

        self.edit_hotkey_console, box, self.label_hotkey_console = self._hotkey_row()
        form.addRow("显示 / 隐藏控制台:", box)

        self.edit_hotkey_visible, box, self.label_hotkey_visible = self._hotkey_row()
        form.addRow("隐藏 / 显示桌面蟑螂:", box)

        self.edit_hotkey_burst, box, self.label_hotkey_burst = self._hotkey_row()
        form.addRow("虫巢爆发（刷满屏）:", box)

        conflict_hint = QLabel(
            "热键是系统独占的：如果提示「已被其他程序占用」，换一个组合即可。"
            "Ctrl+Alt+W 常被微信占用，所以默认没用它。"
        )
        conflict_hint.setWordWrap(True)
        conflict_hint.setStyleSheet("color: #8f90a0;")
        form.addRow(conflict_hint)

        layout.addWidget(group)

        startup_group = QGroupBox("启动行为")
        s_form = QFormLayout(startup_group)
        s_form.setSpacing(10)
        self.check_open_console = QCheckBox("启动 roachHub 时自动打开控制台")
        s_form.addRow(self.check_open_console)
        self.check_start_hidden = QCheckBox("启动时隐藏桌面蟑螂（只留托盘与控制台）")
        s_form.addRow(self.check_start_hidden)
        layout.addWidget(startup_group)

        lang_group = QGroupBox("界面语言")
        l_form = QFormLayout(lang_group)
        self.combo_language = QComboBox()
        self.combo_language.addItem("简体中文", "zh_CN")
        self.combo_language.addItem("English", "en_US")
        l_form.addRow("界面语言 (Language):", self.combo_language)
        layout.addWidget(lang_group)

        layout.addStretch()
        return widget

    def _hotkey_row(self):
        """一个热键录入框 + 它的生效状态标签。返回 (edit, container, status_label)。"""
        edit = QKeySequenceEdit()
        edit.setMaximumSequenceLength(1)
        edit.setClearButtonEnabled(True)
        edit.setMinimumWidth(200)

        status = QLabel("未设置")
        status.setObjectName("statLabel")
        status.setWordWrap(True)

        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(edit)
        row.addWidget(status, 1)
        return edit, container, status

    def load_from_config(self) -> None:
        cfg = self.config

        # 表现风格
        style = str(cfg.get("render.style", "realistic"))
        idx = self.combo_render_style.findData(style)
        if idx >= 0:
            self.combo_render_style.setCurrentIndex(idx)
        self.spin_shadow.setValue(float(cfg.get("render.shadow_intensity", 0.42)))
        self.check_teneral.setChecked(bool(cfg.get("render.show_teneral", True)))
        self.spin_max_roaches.setValue(int(cfg.get("simulation.max_roaches", 15)))
        self.spin_scale_variation.setValue(float(cfg.get("creatures.cockroach.scale_variation", 0.15)))
        self.spin_walk_speed.setValue(float(cfg.get("creatures.cockroach.max_walk_speed", 120.0)))
        self.spin_panic_speed.setValue(float(cfg.get("creatures.cockroach.panic_speed", 430.0)))

        # 行为
        self.spin_thigmotaxis.setValue(float(cfg.get("behavior.thigmotaxis_weight", 0.55)))
        self.spin_grooming.setValue(float(cfg.get("behavior.grooming_frequency", 0.35)))
        self.check_circadian.setChecked(bool(cfg.get("behavior.circadian_rhythm", True)))
        self.spin_panic_dur.setValue(float(cfg.get("creatures.cockroach.panic_duration", 2.4)))
        self.spin_food_interval.setValue(float(cfg.get("simulation.food_spawn_interval", 12.0)))
        self.spin_max_food.setValue(int(cfg.get("simulation.max_food_count", 8)))

        # 繁殖系统 (重点)
        rep_mode = str(cfg.get("lifecycle.reproduce_mode", "auto"))
        r_idx = self.combo_reproduce_mode.findData(rep_mode)
        if r_idx >= 0:
            self.combo_reproduce_mode.setCurrentIndex(r_idx)
        self.spin_mating_radius.setValue(float(cfg.get("lifecycle.mating_search_radius", 180.0)))
        self.spin_female_ratio.setValue(float(cfg.get("lifecycle.female_ratio", 0.5)))
        self.spin_min_energy.setValue(float(cfg.get("lifecycle.reproduce_min_energy", 55.0)))
        self.spin_cost_energy.setValue(float(cfg.get("lifecycle.reproduce_cost_energy", 25.0)))
        self.spin_reproduce_cd.setValue(float(cfg.get("lifecycle.reproduce_cooldown", 28.0)))
        self.check_allow_near_cap.setChecked(bool(cfg.get("lifecycle.allow_reproduce_near_cap", False)))

        # 卵鞘与成长
        ootheca_mode = str(cfg.get("lifecycle.ootheca_mode", "carried"))
        o_idx = self.combo_ootheca_mode.findData(ootheca_mode)
        if o_idx >= 0:
            self.combo_ootheca_mode.setCurrentIndex(o_idx)
        self.spin_egg_time.setValue(float(cfg.get("lifecycle.egg_incubation_time", 15.0)))
        self.spin_nymph_count.setValue(int(cfg.get("lifecycle.nymphs_per_ootheca_max", 3)))
        self.spin_max_instars.setValue(int(cfg.get("lifecycle.max_instars", 4)))
        self.spin_instar_time.setValue(float(cfg.get("lifecycle.instar_duration", 18.0)))
        self.spin_lifespan.setValue(float(cfg.get("lifecycle.adult_lifespan", 300.0)))

        # 显示
        self.check_always_on_top.setChecked(bool(cfg.get("window.always_on_top", True)))
        self.check_multi_screen.setChecked(bool(cfg.get("window.multi_screen", False)))
        self.spin_fps.setValue(int(cfg.get("app.target_fps", 60)))
        self.spin_min_fps.setValue(int(cfg.get("performance.min_fps", 30)))
        self.spin_padding.setValue(float(cfg.get("simulation.boundary_padding", 45.0)))

        # 互动与氛围
        self.check_drag.setChecked(bool(cfg.get("interaction.drag_enabled", True)))
        self.check_trails.setChecked(bool(cfg.get("trails.enabled", True)))
        self.spin_trail_fade.setValue(float(cfg.get("trails.fade_seconds", 6.0)))
        self.check_audio.setChecked(bool(cfg.get("audio.enabled", True)))
        self.spin_audio_volume.setValue(float(cfg.get("audio.volume", 0.45)))
        self.spin_audio_budget.setValue(int(cfg.get("audio.max_per_second", 18)))
        self.check_voice.setChecked(bool(cfg.get("grab_voice.enabled", True)))
        self.check_voice_stop.setChecked(bool(cfg.get("grab_voice.stop_on_release", False)))
        self.spin_voice_volume.setValue(float(cfg.get("grab_voice.volume", 0.7)))

        # 常规与热键
        hk_console = str(cfg.get("app.hotkey_toggle_console", "Ctrl+Alt+R"))
        self._set_hotkey_edit(self.edit_hotkey_console, hk_console)
        self._set_hotkey_edit(self.edit_hotkey_visible, str(cfg.get("app.hotkey_toggle_visible", "Ctrl+Alt+H")))
        self._set_hotkey_edit(self.edit_hotkey_burst, str(cfg.get("app.hotkey_nest_burst", "Ctrl+Alt+B")))
        self.check_open_console.setChecked(bool(cfg.get("app.open_console_on_start", True)))
        self.check_start_hidden.setChecked(bool(cfg.get("app.start_hidden", False)))
        self.label_brand_hint.setText(
            f"{hk_console} 收起 / 召唤" if hk_console else "控制台热键已关闭"
        )

    @staticmethod
    def _set_hotkey_edit(edit: QKeySequenceEdit, spec: str) -> None:
        seq = QKeySequence(spec) if spec else QKeySequence()
        edit.setKeySequence(seq)

    @staticmethod
    def _read_hotkey_edit(edit: QKeySequenceEdit) -> str:
        seq = edit.keySequence()
        if seq.isEmpty():
            return ""
        return seq.toString(QKeySequence.SequenceFormat.PortableText)

    def _on_save_clicked(self) -> None:
        cfg = self.config

        cfg.set("render.style", self.combo_render_style.currentData())
        cfg.set("render.shadow_intensity", self.spin_shadow.value())
        cfg.set("render.show_teneral", self.check_teneral.isChecked())
        cfg.set("simulation.max_roaches", self.spin_max_roaches.value())
        cfg.set("creatures.cockroach.scale_variation", self.spin_scale_variation.value())
        cfg.set("creatures.cockroach.max_walk_speed", self.spin_walk_speed.value())
        cfg.set("creatures.cockroach.panic_speed", self.spin_panic_speed.value())

        cfg.set("behavior.thigmotaxis_weight", self.spin_thigmotaxis.value())
        cfg.set("behavior.grooming_frequency", self.spin_grooming.value())
        cfg.set("behavior.circadian_rhythm", self.check_circadian.isChecked())
        cfg.set("creatures.cockroach.panic_duration", self.spin_panic_dur.value())
        cfg.set("simulation.food_spawn_interval", self.spin_food_interval.value())
        cfg.set("simulation.max_food_count", self.spin_max_food.value())

        # 繁殖系统 (重点保存)
        cfg.set("lifecycle.reproduce_mode", self.combo_reproduce_mode.currentData())
        cfg.set("lifecycle.mating_search_radius", self.spin_mating_radius.value())
        cfg.set("lifecycle.female_ratio", self.spin_female_ratio.value())
        cfg.set("lifecycle.reproduce_min_energy", self.spin_min_energy.value())
        cfg.set("lifecycle.reproduce_cost_energy", self.spin_cost_energy.value())
        cfg.set("lifecycle.reproduce_cooldown", self.spin_reproduce_cd.value())
        cfg.set("lifecycle.allow_reproduce_near_cap", self.check_allow_near_cap.isChecked())

        cfg.set("lifecycle.ootheca_mode", self.combo_ootheca_mode.currentData())
        cfg.set("lifecycle.egg_incubation_time", self.spin_egg_time.value())
        cfg.set("lifecycle.nymphs_per_ootheca_max", self.spin_nymph_count.value())
        cfg.set("lifecycle.max_instars", self.spin_max_instars.value())
        cfg.set("lifecycle.instar_duration", self.spin_instar_time.value())
        cfg.set("lifecycle.adult_lifespan", self.spin_lifespan.value())

        cfg.set("window.always_on_top", self.check_always_on_top.isChecked())
        cfg.set("window.multi_screen", self.check_multi_screen.isChecked())
        cfg.set("app.target_fps", self.spin_fps.value())
        cfg.set("performance.min_fps", self.spin_min_fps.value())
        cfg.set("simulation.boundary_padding", self.spin_padding.value())

        # 快捷键与启动行为：先校验，非法就整体放弃保存（别写进去一个用不了的键）
        hotkeys = {
            "app.hotkey_toggle_console": ("显示/隐藏控制台", self._read_hotkey_edit(self.edit_hotkey_console)),
            "app.hotkey_toggle_visible": ("隐藏/显示桌面蟑螂", self._read_hotkey_edit(self.edit_hotkey_visible)),
            "app.hotkey_nest_burst": ("虫巢爆发", self._read_hotkey_edit(self.edit_hotkey_burst)),
        }
        for key, (label, spec) in hotkeys.items():
            norm = normalize_hotkey(spec)
            if norm is None:
                QMessageBox.warning(
                    self, "热键写法无法识别",
                    f"「{label}」的热键 {spec!r} 无法识别。\n"
                    "请至少包含一个修饰键（Ctrl / Alt / Shift / Win）加一个主键，例如 Ctrl+Alt+R。",
                )
                return
            cfg.set(key, norm)

        cfg.set("app.open_console_on_start", self.check_open_console.isChecked())
        cfg.set("app.start_hidden", self.check_start_hidden.isChecked())

        # 互动与氛围
        cfg.set("interaction.drag_enabled", self.check_drag.isChecked())
        cfg.set("trails.enabled", self.check_trails.isChecked())
        cfg.set("trails.fade_seconds", self.spin_trail_fade.value())
        cfg.set("audio.enabled", self.check_audio.isChecked())
        cfg.set("audio.volume", self.spin_audio_volume.value())
        cfg.set("audio.max_per_second", self.spin_audio_budget.value())
        cfg.set("grab_voice.enabled", self.check_voice.isChecked())
        cfg.set("grab_voice.stop_on_release", self.check_voice_stop.isChecked())
        cfg.set("grab_voice.volume", self.spin_voice_volume.value())

        success = cfg.save()
        if not success:
            QMessageBox.warning(self, "保存失败", "写入配置文件失败。")
            return

        # 这里会同步调用 app.apply_runtime_settings → 重新注册热键
        self.config_applied.emit()
        self._refresh_stats()

        failures = []
        if self.on_query_stats is not None:
            try:
                stats = self.on_query_stats()
                for name, info in (stats.get("hotkeys") or {}).items():
                    if info.get("spec") and not info.get("ok"):
                        failures.append(f"{info.get('spec')}（{name}）：{info.get('error')}")
            except Exception:
                logger.debug("查询热键状态失败", exc_info=True)

        if failures:
            QMessageBox.warning(
                self, "配置已保存，但热键没生效",
                "设置已经保存并生效，但下列全局热键没能注册成功：\n\n"
                + "\n".join(failures)
                + "\n\n热键是系统独占的，通常是被其它程序（微信、输入法、录屏工具）占用了，"
                  "换一个组合再保存即可。",
            )
        else:
            # 控制台是长期开着的面板，保存后不该把它关掉 —— 用底部提示代替弹窗
            self._toast("💾 已保存并即时生效")

    def _on_reset_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            "重置配置",
            "确定要将所有繁殖与生命周期设置恢复为出厂默认值吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            import copy
            self.config._data = copy.deepcopy(DEFAULT_CONFIG)
            self.load_from_config()
