"""系统托盘图标与上下文控制菜单。"""

from __future__ import annotations
import logging
from typing import Callable, Optional

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from .resources import ResourceManager

logger = logging.getLogger("roachHub.tray")


class RoachTrayIcon(QSystemTrayIcon):
    """系统托盘管理组件。"""

    def __init__(
        self,
        on_toggle_visible: Callable[[], bool],
        on_spawn_roach: Callable[[], None],
        on_spawn_food: Callable[[], None],
        on_clear_roaches: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_clear_trails: Optional[Callable[[], None]] = None,
        on_toggle_audio: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__()
        self.on_toggle_visible = on_toggle_visible
        self.on_spawn_roach = on_spawn_roach
        self.on_spawn_food = on_spawn_food
        self.on_clear_roaches = on_clear_roaches
        self.on_open_settings = on_open_settings
        self.on_quit = on_quit
        self.on_clear_trails = on_clear_trails
        self.on_toggle_audio = on_toggle_audio

        self.setIcon(ResourceManager.get_tray_icon())
        self.setToolTip("roachHub - 高拟真生物模拟器")

        self._init_menu()
        self.activated.connect(self._on_tray_activated)

    def _init_menu(self) -> None:
        menu = QMenu()

        self.status_action = QAction("🪳 生态: 0 只生物", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)

        menu.addSeparator()

        settings_action = QAction("⚙️ 打开设置中心...", menu)
        settings_action.triggered.connect(self.on_open_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        self.toggle_action = QAction("隐藏桌面蟑螂", menu)
        self.toggle_action.triggered.connect(self._handle_toggle)
        menu.addAction(self.toggle_action)

        spawn_action = QAction("➕ 投放一只新蟑螂", menu)
        spawn_action.triggered.connect(self.on_spawn_roach)
        menu.addAction(spawn_action)

        food_action = QAction("🧀 投放一块食物碎屑", menu)
        food_action.triggered.connect(self.on_spawn_food)
        menu.addAction(food_action)

        menu.addSeparator()

        self.trail_action = QAction("🧹 擦掉桌面痕迹", menu)
        self.trail_action.triggered.connect(self._handle_clear_trails)
        menu.addAction(self.trail_action)

        self.audio_action = QAction("🔇 静音（全部音效）", menu)
        self.audio_action.triggered.connect(self._handle_toggle_audio)
        menu.addAction(self.audio_action)

        menu.addSeparator()

        clear_action = QAction("🧹 驱散所有蟑螂与物体", menu)
        clear_action.triggered.connect(self.on_clear_roaches)
        menu.addAction(clear_action)

        menu.addSeparator()

        quit_action = QAction("❌ 退出 roachHub", menu)
        quit_action.triggered.connect(self.on_quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    def update_population_detail(self, females: int, males: int, nymphs: int, eggs: int) -> None:
        """更新展示详细的群落性别与生命阶段统计。"""
        total = females + males + nymphs
        self.status_action.setText(
            f"🪳 存活: {total} 只 (成虫 ♀{females} ♂{males} / 若虫 {nymphs} / 卵鞘 {eggs})"
        )
        self.setToolTip(
            f"roachHub - 生态: 成虫♀{females} ♂{males} | 若虫{nymphs} | 卵鞘{eggs}"
        )

    def _handle_toggle(self) -> None:
        is_visible = self.on_toggle_visible()
        self.toggle_action.setText("隐藏桌面蟑螂" if is_visible else "显示桌面蟑螂")

    def _handle_clear_trails(self) -> None:
        if self.on_clear_trails:
            self.on_clear_trails()

    def _handle_toggle_audio(self) -> None:
        if not self.on_toggle_audio:
            return
        enabled = self.on_toggle_audio()
        self.audio_action.setText(
            "🔇 静音（全部音效）" if enabled else "🔊 开启全部音效"
        )

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._handle_toggle()
