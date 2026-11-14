"""客户端配置与控制中心窗口。

支持表现风格、生命周期、自动/轻量有性繁殖模式、感应距离、雌雄比例与能耗参数配置。
"""

from __future__ import annotations
import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QFont, QColor
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
)

from .config import Config, DEFAULT_CONFIG
from .resources import ResourceManager

logger = logging.getLogger("roachHub.settings")

SETTINGS_STYLE = """
QWidget {
    background-color: #1a1a20;
    color: #e2e2ec;
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
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
"""


class SettingsWindow(QWidget):
    """设置控制中心窗口。"""

    config_applied = Signal()

    def __init__(
        self,
        config: Config,
        on_applied: Optional[Callable[[], None]] = None,
        on_clear_trails: Optional[Callable[[], None]] = None,
        on_preview_voice: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.on_clear_trails = on_clear_trails
        self.on_preview_voice = on_preview_voice
        if on_applied:
            self.config_applied.connect(on_applied)

        self.setWindowTitle("roachHub - 高拟真控制中心与设置")
        self.setWindowIcon(ResourceManager.get_tray_icon())
        self.resize(660, 760)
        self.setStyleSheet(SETTINGS_STYLE)

        self._init_ui()
        self.load_from_config()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # 1. 拟真与渲染风格 Tab
        self.tabs.addTab(self._build_render_tab(), "🎨 表现风格与拟真")

        # 2. 行为习性与感知 Tab
        self.tabs.addTab(self._build_behavior_tab(), "🧠 生物感知与习性")

        # 3. 生命周期与繁衍系统 Tab (重点完善)
        self.tabs.addTab(self._build_lifecycle_tab(), "🌱 繁衍与生命周期")

        # 4. 窗口显示 Tab
        self.tabs.addTab(self._build_display_tab(), "🖥️ 窗口显示")

        # 5. 互动与氛围 Tab（拖拽 / 痕迹 / 音效）
        self.tabs.addTab(self._build_atmosphere_tab(), "🖱️ 互动与氛围")

        # 6. 常规与热键 Tab
        self.tabs.addTab(self._build_general_tab(), "⚙️ 快捷键与通用")

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.reset_btn = QPushButton("🔄 恢复默认值")
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        btn_layout.addWidget(self.reset_btn)

        btn_layout.addStretch()

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("💾 保存并应用")
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(self.save_btn)

        main_layout.addLayout(btn_layout)

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

        group = QGroupBox("快捷键与语言")
        form = QFormLayout(group)
        form.setSpacing(10)

        self.edit_hotkey_toggle = QLineEdit()
        form.addRow("切换生物显示热键:", self.edit_hotkey_toggle)

        self.edit_hotkey_settings = QLineEdit()
        form.addRow("打开设置中心热键:", self.edit_hotkey_settings)

        self.combo_language = QComboBox()
        self.combo_language.addItem("简体中文", "zh_CN")
        self.combo_language.addItem("English", "en_US")
        form.addRow("界面语言 (Language):", self.combo_language)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def load_from_config(self) -> None:
        cfg = self.config

        # 表现风格
        style = str(cfg.get("render.style", "realistic"))
        idx = self.combo_render_style.findData(style)
        if idx >= 0:
            self.combo_render_style.setCurrentIndex(idx)
        self.spin_shadow.setValue(float(cfg.get("render.shadow_intensity", 0.42)))
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

        # 常规
        self.edit_hotkey_toggle.setText(str(cfg.get("app.hotkey_toggle_visible", "Ctrl+Alt+R")))
        self.edit_hotkey_settings.setText(str(cfg.get("app.hotkey_open_settings", "Ctrl+Alt+S")))

    def _on_save_clicked(self) -> None:
        cfg = self.config

        cfg.set("render.style", self.combo_render_style.currentData())
        cfg.set("render.shadow_intensity", self.spin_shadow.value())
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

        cfg.set("app.hotkey_toggle_visible", self.edit_hotkey_toggle.text().strip())
        cfg.set("app.hotkey_open_settings", self.edit_hotkey_settings.text().strip())

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
        if success:
            self.config_applied.emit()
            QMessageBox.information(self, "配置成功", "繁殖与高拟真配置已成功保存并即时生效！")
            self.close()
        else:
            QMessageBox.warning(self, "保存失败", "写入配置文件失败。")

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
