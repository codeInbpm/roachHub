"""配置加载、持久化与内存更新模块。"""

from __future__ import annotations
import os
import sys
import logging
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
        # 点击穿透已统一为 WM_NCHITTEST 原生命中测试（Windows），
        # 旧的 click_through_mode="mask" 已废弃，不再读取。
        "always_on_top": True,
        "multi_screen": False,
    },
    "interaction": {
        "drag_enabled": True,
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


class Config:
    """应用程序配置容器。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        if config_path is None:
            base_dir = Path(__file__).resolve().parent.parent
            self.config_path = base_dir / "config.toml"
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
