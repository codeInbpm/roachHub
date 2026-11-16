"""配置加载、持久化与内存更新模块。"""

from __future__ import annotations
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("roachHub.config")

# 默认全局配置回退
DEFAULT_CONFIG: Dict[str, Any] = {
    "app": {
        "title": "roachHub - 高拟真桌面生物模拟器",
        "target_fps": 60,
        "debug_mode": False,
        "start_hidden": False,
        "log_level": "INFO",
        "language": "zh_CN",
        # 启动即弹出控制台（用户要求：不用先去右下角点托盘图标）
        "open_console_on_start": True,
        # 三个**全局**热键（RegisterHotKey，应用没有焦点时也生效）
        # Ctrl+Alt+W 被微信等程序长期占用，注册必然失败，所以默认用 R/H/B。
        "hotkey_toggle_console": "Ctrl+Alt+R",
        "hotkey_toggle_visible": "Ctrl+Alt+H",
        "hotkey_nest_burst": "Ctrl+Alt+B",
        # 第 4 个热键：拿起/放下蟑螂拍（持拍期间整屏接管鼠标）
        "hotkey_swatter": "Ctrl+Alt+S",
    },
    "render": {
        "style": "realistic",
        "shadow_intensity": 0.42,
        "antennae_intensity": 1.0,
        # 刚蜕壳的个体先是奶白半透明的新壳，几秒内颜色化回正常色 —— 这是真实
        # 蟑螂的"嫩壳期"。有人会觉得白色像贴图错误，所以给一个总开关。
        "show_teneral": True,
    },
    "window": {
        # 点击穿透由平台层自动选路，不再配置：Windows = WM_NCHITTEST 逐点询问，
        # macOS = NSWindow.ignoresMouseEvents 逐帧开关，其余平台 = QRegion 遮罩 4Hz。
        # 旧的 click_through_mode="mask" 已废弃，不再读取。
        "always_on_top": True,
        "multi_screen": False,
    },
    "interaction": {
        "drag_enabled": True,
    },
    "swatter": {
        # 总开关（关掉后托盘 / 热键都不可用，覆盖层永远保持"只在虫身上收鼠标"）
        "enabled": True,
        # 拍面判定半径：必须 ≥ 蟑螂的 perception_panic_radius(55)，否则会出现
        # "刚贴近它反而跑了"。默认 44 + 0.30×体长 ≈ 65px
        "head_radius": 44.0,
        # 拍子的观感大小（只影响画面，不改判定）。1.0 = 原始尺寸，
        # 那时拍面宽 168 > 判定直径 131，会"看着盖住却没死"；0.70 更贴手。
        "visual_scale": 0.70,
        "swing_ms": 70,              # 下压时长（4.2 帧 @60FPS，读得出"手在动"）
        "recover_ms": 200,           # 抬拍恢复
        "cooldown_ms": 280,          # 两次挥拍最小间隔（从 WINDUP 起算）
        "idle_disarm_seconds": 12.0, # 闲置自动收拍（0 = 不自动）
        "splat_amount": 1.0,         # 爆浆量倍率 0~2（颗粒数 / 污渍大小）
        "linger_seconds": 22.0,      # 尸体 + 主污渍停留
        "max_particles": 14,         # 单次击杀颗粒上限
        "screen_shake": True,        # 命中震屏
        "scare_radius": 240.0,       # 拍击惊逃半径（0 = 不惊动邻居）
        "can_squash_ootheca": True,  # 能不能把拖着的卵鞘一起拍爆
        "count_kills": True,         # 托盘 / 控制台统计"已拍死 N 只"
    },
    "trails": {
        "enabled": True,
        "resolution_scale": 1.0,
        "fade_seconds": 6.0,
        "max_prints": 3000,
    },
    "audio": {
        "enabled": True,
        "volume": 0.45,
        "max_per_second": 18,
    },
    "grab_voice": {
        "enabled": True,
        "volume": 0.7,
        "file": "assets/sounds/grab.wav",
        # 默认让整句「你干嘛~哎呦」说完：拖拽通常不到 1 秒，松手即停会让「哎呦」永远听不见
        "stop_on_release": False,
    },
    "squash_voice": {
        # 拍死时蟑螂喊的那一声。variant 见 squash_voice.SOUND_SETS：
        # "myinstants"（默认，完整一句 2.42 秒）/ "myinstants_short"（1.51 秒剪辑）
        # / "yamero" / "aiyo"
        "enabled": True,
        "volume": 0.75,
        "variant": "myinstants",
    },
    "performance": {
        # true = 关闭帧预算自适应器，固定用 fixed_quality_level 指定的画质
        "fixed_quality": False,
        "fixed_quality_level": 2,
        # 最低可接受帧率：自适应器把周期放慢到这个值就不再降（再低就明显卡顿了）
        "min_fps": 30,
    },
    "simulation": {
        "initial_roaches": 3,
        "max_roaches": 15,
        "boundary_padding": 45.0,
        "boundary_behavior": "rebound",
        "food_spawn_interval": 12.0,
        "max_food_count": 8,
        # 自动投放的食物种类："random" = 按营养加权随机，或 food_kinds 里的 key
        "food_kind": "random",
    },
    "behavior": {
        "thigmotaxis_weight": 0.55,
        "grooming_frequency": 0.35,
        "circadian_rhythm": True,
        "perception_far_radius": 260.0,
        "perception_mid_radius": 120.0,
        "perception_panic_radius": 55.0,
    },
    "lifecycle": {
        "reproduce_mode": "auto",
        "female_ratio": 0.5,
        "mating_search_radius": 180.0,
        "reproduce_min_energy": 55.0,
        "reproduce_cost_energy": 25.0,
        "reproduce_cooldown": 28.0,
        "allow_reproduce_near_cap": False,
        # 一次交配能支撑几次产卵（雌虫储精）；0 无效，最小 1
        "fertilized_broods": 2,
        # 简化交配（auto 模式）与尾对尾交尾（sexual 模式）的时长
        "auto_mating_time": 1.5,
        "mating_duration": 2.2,
        "ootheca_mode": "carried",
        "egg_incubation_time": 15.0,
        "nymphs_per_ootheca_min": 1,
        "nymphs_per_ootheca_max": 3,
        "max_instars": 4,
        "instar_duration": 18.0,
        "adult_lifespan": 300.0,
    },
    "creatures": {
        "cockroach": {
            "min_walk_speed": 65.0,
            "max_walk_speed": 120.0,
            "panic_speed": 430.0,
            "panic_duration": 2.4,
            "normal_turn_rate": 3.8,
            "panic_turn_rate": 14.0,
            "base_length": 72.0,
            "base_width": 38.0,
            "scale_variation": 0.15,
            "leg_swing_frequency": 17.0,
            "energy_decay_rate": 0.6,
            "starvation_speed_penalty": 0.5,
        }
    },
}


def _deep_update(source: dict, overrides: dict) -> dict:
    """递归合并字典配置。"""
    for key, value in overrides.items():
        if isinstance(value, dict) and key in source and isinstance(source[key], dict):
            source[key] = _deep_update(source[key], value)
        else:
            source[key] = value
    return source


def _parse_toml_fallback(content: str) -> dict:
    """轻量 TOML 行级基础解析回退。"""
    result: dict = {}
    current_section = result
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section_path = line[1:-1].strip().split(".")
            curr = result
            for part in section_path:
                curr = curr.setdefault(part, {})
            current_section = curr
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.split("#")[0].strip()
            if val.lower() == "true":
                parsed_val: Any = True
            elif val.lower() == "false":
                parsed_val = False
            elif val.startswith('"') and val.endswith('"'):
                parsed_val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                parsed_val = val[1:-1]
            else:
                try:
                    parsed_val = int(val) if "." not in val else float(val)
                except ValueError:
                    parsed_val = val
            current_section[key] = parsed_val
    return result


def _format_value(v: Any) -> str:
    """格式化 Python 值为 TOML 标量。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    elif isinstance(v, (int, float)):
        return str(v)
    elif isinstance(v, str):
        return f'"{v}"'
    return f'"{str(v)}"'


def default_config_path() -> Path:
    """默认配置文件位置（含"首次运行从包里播种"）。

    开发期 = 项目根的 config.toml；Windows 便携包 = exe 旁边的 config.toml；
    macOS `.app` = `~/Library/Application Support/roachHub/config.toml`
    （打包进 .app 里的那份只当模板，不改它，否则会破坏代码签名）。

    打包时会把一份调好的 config.toml 一起发进去，所以首次运行要**把它复制到
    可写位置**，而不是直接用 `DEFAULT_CONFIG` 重写一遍 —— 否则用户精心调过的
    参数在 mac 上第一次启动就全没了。
    """
    from .utils.app_paths import bundle_dir, data_dir

    target = data_dir() / "config.toml"
    if not target.exists():
        seed = bundle_dir() / "config.toml"
        if seed != target and seed.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(seed, target)
                logger.info("已从随包模板播种配置：%s", target)
            except OSError as e:
                logger.warning("播种配置失败（将使用内置默认值）: %s", e)
    return target


class Config:
    """应用程序配置容器。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        if config_path is None:
            self.config_path = default_config_path()
        else:
            self.config_path = Path(config_path)

        self._data: Dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        import copy
        self._data = copy.deepcopy(DEFAULT_CONFIG)

        if not self.config_path.exists():
            logger.info("未找到配置文件 %s，使用默认配置并自动写入", self.config_path)
            self.save()
            return

        try:
            data = None
            if sys.version_info >= (3, 11):
                import tomllib
                with open(self.config_path, "rb") as f:
                    data = tomllib.load(f)
            else:
                try:
                    import tomli
                    with open(self.config_path, "rb") as f:
                        data = tomli.load(f)
                except ImportError:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        data = _parse_toml_fallback(f.read())

            if data and isinstance(data, dict):
                _deep_update(self._data, data)
                logger.info("成功加载配置文件: %s", self.config_path)
        except Exception as e:
            logger.error("读取配置文件失败，使用默认配置: %s", e)

    def get(self, path: str, default: Any = None) -> Any:
        keys = path.split(".")
        current = self._data
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        return current

    def set(self, path: str, value: Any) -> None:
        keys = path.split(".")
        current = self._data
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value

    def save(self) -> bool:
        """将当前内存配置保存写回 TOML 文件。"""
        try:
            lines = ["# roachHub - 高拟真全面蟑螂模拟器 自动保存的配置文件\n"]

            def write_section(prefix: str, d: dict):
                scalars = {}
                subsections = {}
                for k, v in d.items():
                    if isinstance(v, dict):
                        subsections[k] = v
                    else:
                        scalars[k] = v

                if prefix:
                    lines.append(f"\n[{prefix}]")
                for k, v in scalars.items():
                    lines.append(f"{k} = {_format_value(v)}")

                for sub_k, sub_v in subsections.items():
                    sub_prefix = f"{prefix}.{sub_k}" if prefix else sub_k
                    write_section(sub_prefix, sub_v)

            write_section("", self._data)

            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines).strip() + "\n")
            logger.info("配置已成功写回: %s", self.config_path)
            return True
        except Exception as e:
            logger.error("保存配置文件失败: %s", e)
            return False

    @property
    def raw(self) -> Dict[str, Any]:
        return self._data
