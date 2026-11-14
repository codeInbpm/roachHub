"""程序化窸窣音效。

不做持续环境音，只做**由事件触发的短音**：脚掌落地一声轻响、咬一口的脆响、
受惊冲刺的"唰"、松手落地的闷响。素材在首次运行时用标准库合成到临时目录
（不需要额外的音频文件，也不需要 numpy）。

三条硬约束：
1. **限流**：全屏 150 只时每秒上千次落地，必须做全局预算，否则喇叭会糊成白噪声；
2. **距离衰减**：以光标位置当作"注意力中心"，远处的虫只保留很低的底噪；
3. **可关**：扰民风险最高的一项，config / 托盘 / 设置面板三处都能静音。
"""

from __future__ import annotations

import logging
import math
import random
import struct
import tempfile
import time
import wave
from pathlib import Path

logger = logging.getLogger("roachHub.audio")

_RATE = 44100
_VERSION = "v1"

# 各音效的合成参数：长度(秒)、音色重心(Hz)、包络衰减指数
_SPECS = {
    "step": (0.045, 3200.0, 26.0),
    "rustle": (0.200, 4200.0, 9.0),
    "munch": (0.070, 1800.0, 34.0),
    "thud": (0.130, 320.0, 16.0),
}

# 每类音效的并发实例数（池化复用，避免重复 play 时互相打断）
_POOL = {"step": 8, "rustle": 3, "munch": 3, "thud": 2}

# 播放预算：step 计 1，其他计 3，合计不得超过 max_per_second
_COST = {"step": 1, "rustle": 3, "munch": 3, "thud": 3}


def _synth(kind: str) -> list[float]:
    """合成一段单声道 16bit 样本（-1~1 浮点）。"""
    dur, center, decay = _SPECS[kind]
    n = int(dur * _RATE)
    out: list[float] = []
    prev = 0.0
    lowpass = 0.0
    # 简单一阶滤波，把白噪声推向目标频段
    hp = 1.0 - min(0.95, center / (_RATE * 0.5))
    for i in range(n):
        t = i / n
        white = random.uniform(-1.0, 1.0)
        # 一阶高通 + 一阶低通，粗略带通
        hpv = white - prev * (1.0 - hp)
        prev = white
        lowpass += (hpv - lowpass) * 0.55
        env = math.exp(-decay * t) * min(1.0, t * 60.0)
        if kind == "rustle":
            # 腿部连续刮擦：叠加 ~28Hz 的幅度调制
            env *= 0.55 + 0.45 * abs(math.sin(math.pi * 28.0 * i / _RATE))
        elif kind == "munch":
            # 两下脆响
            env = math.exp(-decay * t) + 0.75 * math.exp(-decay * abs(t - 0.42) * 1.6)
        elif kind == "thud":
            # 低频撞击 + 一点体感
            env *= 0.6
            lowpass = lowpass * 0.35 + math.sin(math.tau * 95.0 * i / _RATE) * 0.6
        out.append(lowpass * env * 0.9)
    return out


def _write_wav(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(
            b"".join(
                struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in samples
            )
        )


class ScuttleAudio:
    """事件驱动的音效播放器（带全局预算与静音开关）。"""

    def __init__(
        self,
        enabled: bool = True,
        volume: float = 0.45,
        max_per_second: int = 18,
    ) -> None:
        self.enabled = bool(enabled)
        self.volume = max(0.0, min(1.0, float(volume)))
        self.max_per_second = max(1, int(max_per_second))
        self.available = False

        self._pools: dict[str, list] = {}
        self._round: dict[str, int] = {}
        self._budget_t0 = time.perf_counter()
        self._budget_n = 0

        try:
            self._init_effects()
            self.available = True
            logger.info("音效引擎就绪（%d 类，%d 个实例）",
                        len(self._pools), sum(len(v) for v in self._pools.values()))
        except Exception as e:  # 没有可用音频设备时静默降级，不影响模拟
            logger.warning("音效不可用，已降级为静音: %s", e)
            self.available = False

    # ------------------------------------------------------------------ 初始化

    def _init_effects(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect

        cache = Path(tempfile.gettempdir()) / f"roachhub_sfx_{_VERSION}"
        cache.mkdir(parents=True, exist_ok=True)

        for kind in _SPECS:
            wav = cache / f"{kind}.wav"
            if not wav.exists():
                _write_wav(wav, _synth(kind))
            url = QUrl.fromLocalFile(str(wav))
            pool = []
            for _ in range(_POOL[kind]):
                eff = QSoundEffect()
                eff.setSource(url)
                eff.setLoopCount(1)
                eff.setVolume(self.volume * 0.4)
                pool.append(eff)
            self._pools[kind] = pool
            self._round[kind] = 0

    # ------------------------------------------------------------------ 播放

    def play(self, kind: str, volume: float) -> None:
        pool = self._pools.get(kind)
        if not pool or not self.enabled or not self.available:
            return
        if volume <= 0.01 or self.volume <= 0.0:
            return

        now = time.perf_counter()
        if now - self._budget_t0 >= 1.0:
            self._budget_t0 = now
            self._budget_n = 0
        cost = _COST.get(kind, 1)
        if self._budget_n + cost > self.max_per_second:
            return
        self._budget_n += cost

        idx = self._round[kind] % len(pool)
        self._round[kind] = idx + 1
        eff = pool[idx]
        eff.setVolume(max(0.0, min(1.0, volume * self.volume)))
        eff.play()

    # ------------------------------------------------------------------ 音量曲线

    @staticmethod
    def step_volume(distance: float, strength: float) -> float:
        """落点音量：越近越响，轻放与踩实区分开，远处保留一层极低底噪。"""
        prox = max(0.10, min(1.0, 1.0 - distance / 700.0))
        return prox * (0.10 + 0.30 * max(0.0, min(1.0, strength)))

    def stop_all(self) -> None:
        for pool in self._pools.values():
            for eff in pool:
                try:
                    eff.stop()
                except Exception:
                    pass

    def apply_config(self, enabled: bool, volume: float, max_per_second: int) -> None:
        was_enabled = self.enabled
        self.enabled = bool(enabled)
        self.volume = max(0.0, min(1.0, float(volume)))
        self.max_per_second = max(1, int(max_per_second))
        if was_enabled and not self.enabled:
            self.stop_all()

    def toggle_mute(self) -> bool:
        self.enabled = not self.enabled
        if not self.enabled:
            self.stop_all()
        return self.enabled
