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
        on_spawn_roach: Callable[..., object],
        on_spawn_food: Callable[..., object],
        on_clear_roaches: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_clear_trails: Optional[Callable[[], None]] = None,
        on_toggle_audio: Optional[Callable[[], bool]] = None,
        on_spawn_roaches: Optional[Callable[..., object]] = None,
        on_spawn_foods: Optional[Callable[..., object]] = None,
        on_nest_burst: Optional[Callable[[], int]] = None,
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
        self.on_spawn_roaches = on_spawn_roaches
        self.on_spawn_foods = on_spawn_foods
        self.on_nest_burst = on_nest_burst

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

        self.console_action = QAction("🖥️ 打开控制台", menu)
        self.console_action.triggered.connect(self.on_open_settings)
        menu.addAction(self.console_action)

        menu.addSeparator()

        # ⚡ 虫巢爆发放在最显眼的位置：这是"一键刷满屏"的入口
        self.burst_action = QAction("⚡ 虫巢爆发（一键刷满）", menu)
        self.burst_action.triggered.connect(self._handle_nest_burst)
        menu.addAction(self.burst_action)

        # 批量投放做成子菜单：一次就能放 10/50/100 只，不必连点一百次
        spawn_menu = QMenu("➕ 投放蟑螂", menu)
        for label, count in (("投放一只", 1), ("投放十只", 10),
                             ("投放五十只", 50), ("投放一百只", 100)):
            act = QAction(label, spawn_menu)
            act.triggered.connect(lambda _=False, c=count: self._handle_spawn_roaches(c))
            spawn_menu.addAction(act)
        menu.addMenu(spawn_menu)

        food_menu = QMenu("🧀 投放食物", menu)
        for label, count in (("投放一块碎屑", 1), ("投放十块碎屑", 10)):
            act = QAction(label, food_menu)
            act.triggered.connect(lambda _=False, c=count: self._handle_spawn_foods(c))
            food_menu.addAction(act)
        menu.addMenu(food_menu)

        menu.addSeparator()

        self.toggle_action = QAction("隐藏桌面蟑螂", menu)
        self.toggle_action.triggered.connect(self._handle_toggle)
        menu.addAction(self.toggle_action)

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

    # ------------------------------------------------------------ 热键文案

    def set_hotkey_hints(self, console: str = "", visible: str = "", burst: str = "") -> None:
        """把已生效的热键写进菜单文案（微信也是这么提示用户的）。"""
        self.console_action.setText(f"🖥️ 打开控制台\t{console}" if console else "🖥️ 打开控制台")
        self.burst_action.setText(
            f"⚡ 虫巢爆发（一键刷满）\t{burst}" if burst else "⚡ 虫巢爆发（一键刷满）"
        )
        base = "隐藏桌面蟑螂" if self.toggle_action.text().startswith("隐藏") else "显示桌面蟑螂"
        self.toggle_action.setText(f"{base}\t{visible}" if visible else base)

    def update_population_detail(self, females: int, males: int, nymphs: int, eggs: int) -> None:
        """更新展示详细的群落性别与生命阶段统计。"""
        total = females + males + nymphs
        self.status_action.setText(
            f"🪳 存活: {total} 只 (成虫 ♀{females} ♂{males} / 若虫 {nymphs} / 卵鞘 {eggs})"
        )
        self.setToolTip(
            f"roachHub - 生态: 成虫♀{females} ♂{males} | 若虫{nymphs} | 卵鞘{eggs}"
        )

    # ------------------------------------------------------------ 菜单动作

    def _handle_nest_burst(self) -> None:
        if self.on_nest_burst:
            self.on_nest_burst()

    def _handle_spawn_roaches(self, count: int) -> None:
        if self.on_spawn_roaches is not None:
            self.on_spawn_roaches(count)
        elif count == 1:
            self.on_spawn_roach()

    def _handle_spawn_foods(self, count: int) -> None:
        if self.on_spawn_foods is not None:
            self.on_spawn_foods(count)
        elif count == 1:
            self.on_spawn_food()

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
        # 参考微信：单击托盘图标 = 召唤控制台（已在最前时只是 raise，不会把用户的
        # 未保存修改吞掉）。双击不再绑定其它动作 —— Qt 会先发 Trigger 再发 DoubleClick，
        # 两个动作叠在一起只会让人困惑。
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.on_open_settings()
