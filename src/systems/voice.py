"""抓取时播放的整句「你干嘛~哎呦」。

与 `ScuttleAudio` 分开，因为两者的定位完全不同：

|            | 窸窣音效                    | 抓取音效                      |
| ---------- | --------------------------- | ----------------------------- |
| 触发       | 脚步 / 咬食，每秒几十次      | 用户按下的那一下，偶发         |
| 素材       | 程序化合成，几十毫秒         | 外部素材，3.45 秒（一整句）    |
| 限流的必要性 | 必须（否则糊成白噪声）     | 不需要，但要防手抖连点         |
| 默认音量   | 0.45 底噪级                 | 0.7 明确可闻                   |
| 用户的容忍度 | 高（几乎听不见）          | 低（很显眼，必须有独立开关）    |

因此：独立配置段 `[grab_voice]`、独立开关、独立音量，唯一的耦合是托盘的**总静音**
会把它一起压下去 —— 用户按了静音就该真的安静。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("roachHub.voice")

# 手抖连点（比如双击 / 三击）时不要把同一句话叠三遍
_RETRIGGER_GUARD = 0.12


class GrabVoice:
    """单实例的语音播放器：按下播一句，默认让它说完。

    **为什么默认不在松手时掐断**：素材是一整句「你干嘛~哎呦」，尾音占了将近
    一半时长（1.97~3.62s）。而实际拖拽绝大多数只有 0.3~0.8 秒 —— 一松手就掐，
    等于「哎呦」永远听不见。想要利落感的话把 `stop_on_release` 打开即可。
    """

    def __init__(
        self,
        path: str | Path = "assets/sounds/grab.wav",
        enabled: bool = True,
        volume: float = 0.7,
        stop_on_release: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.volume = max(0.0, min(1.0, float(volume)))
        self.stop_on_release = bool(stop_on_release)
        self.available = False
        self._effect = None
        self._last_play = 0.0

        self._path = self._resolve(path)
        try:
            self._init_effect()
            self.available = True
            logger.info("抓取音效就绪: %s", self._path.name)
        except Exception as e:  # 没有音频设备 / 素材缺失都静默降级
            logger.warning("抓取音效不可用，已降级为静音: %s", e)
            self.available = False

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _resolve(path: str | Path) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        # 配置里写相对路径时，以项目根目录为基准（src/ 的上两级）
        return (Path(__file__).resolve().parent.parent.parent / p).resolve()

    def _init_effect(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect

        if not self._path.exists():
            raise FileNotFoundError(
                f"{self._path} 不存在；先跑 tools/prepare_voice.py 从 MP3 生成"
            )
        eff = QSoundEffect()
        eff.setSource(QUrl.fromLocalFile(str(self._path)))
        eff.setLoopCount(1)
        eff.setVolume(self.volume)
        self._effect = eff

    # ------------------------------------------------------------------ 播放

    def play(self) -> None:
        if not (self.enabled and self.available and self._effect is not None):
            return
        if self.volume <= 0.01:
            return
        # setSource 是异步的：素材损坏时 available 在构造期仍是 True，
        # 这里补一次状态检查，避免每次抓取都静默失败。
        if self._is_broken():
            return
        now = time.perf_counter()
        if now - self._last_play < _RETRIGGER_GUARD:
            return
        self._last_play = now
        self._effect.setVolume(self.volume)
        # 从头播：上一句还没说完就又抓了一只时，直接重来而不是叠着放
        self._effect.stop()
        self._effect.play()

    def _is_broken(self) -> bool:
        """素材加载失败时停用自己。

        `setSource` 是异步的，构造期 `available` 可能乐观地是 True；素材损坏要到
        第一次 `play()` 前后才暴露成 `Status.Error`。没有 `status()` 的对象（测试桩）
        按"没坏"处理 —— 不该因为缺个方法就把功能关掉。
        """
        status_fn = getattr(self._effect, "status", None)
        if not callable(status_fn):
            return False
        try:
            from PySide6.QtMultimedia import QSoundEffect

            if status_fn() == QSoundEffect.Status.Error:
                if self.available:
                    logger.warning("抓取音效加载失败，已停用: %s", self._path)
                self.available = False
                return True
        except Exception:
            return False
        return False

    def release(self) -> None:
        """松手回调：默认什么都不做，让这一句自然说完。

        想让"松开即静音"的人（`stop_on_release = true`）走 `stop()`。
        """
        if self.stop_on_release:
            self.stop()

    def stop(self) -> None:
        """硬停：总静音、退出、关开关时用，不管配置如何都必须立刻安静。"""
        if self._effect is None:
            return
        try:
            self._effect.stop()
        except Exception:
            pass

    def preview(self, volume: float | None = None) -> None:
        """试听：忽略开关状态，用指定音量播一遍（设置面板的按钮用）。"""
        if not (self.available and self._effect is not None):
            return
        vol = self.volume if volume is None else max(0.0, min(1.0, float(volume)))
        if vol <= 0.01:
            return
        self._last_play = 0.0  # 绕过防连点，试听就该点了就响
        self._effect.setVolume(vol)
        self._effect.stop()
        self._effect.play()

    def is_playing(self) -> bool:
        return bool(self._effect is not None and self._effect.isPlaying())

    # ------------------------------------------------------------------ 配置

    def apply_config(
        self, enabled: bool, volume: float, stop_on_release: bool | None = None
    ) -> None:
        was = self.enabled
        self.enabled = bool(enabled)
        self.volume = max(0.0, min(1.0, float(volume)))
        if stop_on_release is not None:
            self.stop_on_release = bool(stop_on_release)
        if self._effect is not None:
            self._effect.setVolume(self.volume)
            # 关掉开关时正在响的那句要立刻停，否则"关了还在叫"
            if was and not self.enabled:
                self.stop()
