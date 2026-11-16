"""拍死瞬间蟑螂发出的那一声。

## 它是什么

默认是 `myinstants` —— 由外部素材 `myinstants(2).mp3` 原样裁出来的**完整一句**
（2.42 秒：开头「啊哈」+ 中间一句 + 收尾「打咩打咩」），三个升调变体对应三种"死法"。
另有 `myinstants_short`（同一素材只留「啊哈 + 打咩打咩」，1.51 秒）、
`yamero`（「やめろ / 住手」1.10~1.38 秒）、
`aiyo`（半声「哎哟」0.25~0.44 秒）可切。

四套素材的**长度差了 10 倍**（0.25 → 2.42 秒），这不只是换文件，调度规则必须跟着换：
短的可以密集连发，2.4 秒的完整一句在同一秒里叠两声就谁也听不清。见「长音的代价」。

## 长音的代价：三条规则被改掉

| | 短素材（哎哟 0.25~0.44s） | 中素材（yamero 1.10~1.38s） | 完整一句（2.42s） |
| --- | --- | --- | --- |
| 双杀错峰 | 80ms | **470ms**（0.34 × 时长，否则两声完全重叠） | **不叠**（只叫一声） |
| 1 秒预算 | 4 声 | **2 声**（4 声 × 1.2 秒 = 满屏糊音） | **1 声**（+1.5 秒冷却） |
| 同变体重触发 | 直接 `stop()` 重播 | **换空闲实例**，让前一声说完 | **等它说完**（冷却期内拍不响） |

第三条是关键：`QSoundEffect` 一个实例只有一条播放线，`stop()` + `play()`
会把上一声**从中间掐断**。短素材 0.3 秒被掐听不出来，2.4 秒被掐就是
"啊… 啊… 啊…"的结巴，而且永远喊不到最后那句「打咩打咩」。
所以每个变体都准备 2 个实例轮流用；完整一句另外加一道 **0.85 × 时长的冷却**，
把重叠压到只剩 0.36 秒的收尾余音 —— 一句话本来就该说完再喊下一句。

## 为什么还要 `splat`

`splat`（拍面接触的合成音，0.18 秒）和这一声**必须同时存在**：
只有「啪叽」像在拍一块肉，只有人声像拍在空气上。人声**晚 25ms** 起音 ——
先接触、后出声，这才是挤压的物理顺序，两个瞬态也不会糊在一起。

## 三条硬规则

1. **拍空不叫。** `victims = 0` 直接返回，只有 `whiff` 的气声。
2. **多杀不成噪音墙。** 一次最多排 2 声，外加 1 秒硬预算
   （0.75 秒以内的短素材 4 声，更长的 2 声，2.4 秒的完整一句只有 1 声 —— 见 ``BUDGET_SHORT``）。
3. **静音就是静音。** 托盘总静音（`audio.enabled`）由调用方 `app` 检查，
   本类只认自己的开关。
"""

from __future__ import annotations

import logging
import time
import wave
from pathlib import Path

logger = logging.getLogger("roachHub.squash_voice")

#: 人声相对「啪叽」的延迟：先接触后出声
LEAD_MS = 25
#: 1 秒窗口内的播放硬上限。**按素材长度分档**：
#: 0.75 秒以内（半声、单字）可以密集连发；更长的一声需要留出说完的时间，
#: 否则同一句会被叠成"噪音墙"而不是"两只虫在叫"。
BUDGET_SHORT = 4
BUDGET_LONG = 2
BUDGET_LINE = 1
#: 分档阈值（秒）。取在"半声惨叫"（≈0.45）与"完整一句"（≈1.1）之间
SHORT_CLIP = 0.75
#: 「完整一句」阈值（秒）。超过它的一声本身就是一句完整的话，
#: 调度规则再换一档：多杀不叠、连拍不叠、带冷却。
#: 取 1.45：卡在 yamero（1.38，中素材）与这次的剪辑版（1.51）之间 ——
#: 1.5 秒的句子按"中素材"跑会 1 秒两声，两句互相咬掉对方的结尾。
LINE_LONG = 1.45
#: 完整一句的冷却比例：前 85% 播完之前再拍不响。
#: 最后那句「打咩打咩」落在 71%~100% 处，0.85 收在它之后 ——
#: 再短就会出现"打咩刚起头就被下一句顶掉"，真正想听的那半句永远听不全。
LINE_COOLDOWN = 0.85
BUDGET_WINDOW = 1.0
#: 每组素材每个变体开几个播放实例（见模块文档"长音的代价"第三条）
VOICES_PER_VARIANT = 2

#: 可选素材组。键写进 `config.toml` 的 `squash_voice.variant`。
SOUND_SETS: dict[str, tuple[str, ...]] = {
    "myinstants": (
        "assets/sounds/myinstants_1.wav",
        "assets/sounds/myinstants_2.wav",
        "assets/sounds/myinstants_3.wav",
    ),
    "myinstants_short": (
        "assets/sounds/myinstants_short_1.wav",
        "assets/sounds/myinstants_short_2.wav",
        "assets/sounds/myinstants_short_3.wav",
    ),
    "yamero": (
        "assets/sounds/yamero_1.wav",
        "assets/sounds/yamero_2.wav",
        "assets/sounds/yamero_3.wav",
    ),
    "aiyo": (
        "assets/sounds/squash_1.wav",
        "assets/sounds/squash_2.wav",
        "assets/sounds/squash_3.wav",
    ),
}

#: 下拉框里显示的名字。加素材组时**两边一起加**，设置面板按这个顺序建列表。
SOUND_SET_LABELS: dict[str, str] = {
    "myinstants": "🔊 完整一句：啊哈…打咩打咩（2.42 秒）",
    "myinstants_short": "✂️ 啊哈 + 打咩打咩（1.51 秒）",
    "yamero": "🗣️ yamero「やめろ / 住手」",
    "aiyo": "😩 哎哟（抓取那句的半声惨叫）",
}

DEFAULT_SET = "myinstants"


class SquashVoice:
    """每组素材的每个变体各持若干 `QSoundEffect`，可同时发声。"""

    def __init__(
        self,
        paths: tuple[str, ...] | list[str] | None = None,
        enabled: bool = True,
        volume: float = 0.75,
        variant: str = DEFAULT_SET,
    ) -> None:
        self.enabled = bool(enabled)
        self.volume = max(0.0, min(1.0, float(volume)))
        self.variant = variant if variant in SOUND_SETS else DEFAULT_SET
        self.available = False
        self._voices: list[list] = []     # [变体][实例]
        self._durations: list[float] = []
        self._hits: list[float] = []      # 最近 BUDGET_WINDOW 内的播放时间戳
        self._line_until = 0.0            # 完整一句的冷却终点（perf_counter 时刻）
        self._closed = False
        self.played = 0                   # 累计真正播出的声数（诊断用）

        if paths is None:
            paths = SOUND_SETS[self.variant]
        self._load(tuple(paths))

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _resolve(path: str | Path) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        # 与 GrabVoice 一致：相对路径以项目根目录为基准
        return (Path(__file__).resolve().parent.parent.parent / p).resolve()

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate() or 1)
        except Exception:
            return 0.0

    def _load(self, paths: tuple[str, ...]) -> None:
        loaded = 0
        for raw in paths:
            path = self._resolve(raw)
            if not path.exists():
                logger.warning("惨叫素材缺失，跳过: %s", path)
                continue
            dur = self._wav_duration(path)
            try:
                voices = [self._make_effect(path) for _ in range(VOICES_PER_VARIANT)]
            except Exception as e:  # 没有音频设备就静默降级
                logger.warning("惨叫音效不可用，已降级为静音: %s", e)
                break
            self._voices.append(voices)
            self._durations.append(dur)
            loaded += 1

        self.available = loaded > 0
        if self.available:
            logger.info(
                "拍死音效就绪: %d 个变体 × %d 实例（%s）",
                loaded,
                len(self._voices[0]),
                self.variant,
            )
        else:
            logger.warning("拍死音效不可用（缺素材或无音频设备）")

    @staticmethod
    def _make_effect(path: Path):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect

        eff = QSoundEffect()
        eff.setSource(QUrl.fromLocalFile(str(path)))
        eff.setLoopCount(1)
        return eff

    @property
    def long_line(self) -> bool:
        """这组素材是不是"完整一句"（>= ``LINE_LONG``），要走另一套调度。"""
        return bool(self._durations) and max(self._durations) >= LINE_LONG

    @property
    def budget(self) -> int:
        """这一组素材 1 秒内最多允许几声（按最长变体分档，见 ``BUDGET_SHORT``）。"""
        if not self._durations:
            return BUDGET_LONG
        longest = max(self._durations)
        if longest >= LINE_LONG:
            return BUDGET_LINE
        return BUDGET_SHORT if longest < SHORT_CLIP else BUDGET_LONG

    @property
    def line_cooldown(self) -> float:
        """完整一句的冷却秒数（详见 ``LINE_COOLDOWN``）。"""
        if not self._durations:
            return 0.0
        return max(self._durations) * LINE_COOLDOWN

    def _echo_ms(self) -> int:
        """多杀的第二声错开多久。

        按最长变体的 34% 算：短素材（0.44s）落在 150ms 的下限里，
        长素材（1.38s）是 470ms —— 太近会糊成一个更响的同一声。
        """
        if not self._durations:
            return 80
        return int(max(80, min(500, 0.34 * max(self._durations) * 1000.0)))

    def plan(self, victims: int) -> list[tuple[int, int]]:
        """(延迟ms, 变体下标) 列表。纯函数，方便离屏验收。"""
        n = len(self._voices)
        if victims <= 0 or n == 0:
            return []
        if victims == 1:
            return [(LEAD_MS, 0)]
        if victims == 2:
            return [(LEAD_MS, 0), (LEAD_MS + self._echo_ms(), 1 % n)]
        # 3 只以上：两个变体一起叫（音高不同，齐叫不会糊）
        return [(LEAD_MS, 0), (LEAD_MS + self._echo_ms(), (n - 1) % n)]

    def _gain(self, victims: int) -> float:
        """多杀时整体抬一点音量，但别盖过 splat。长素材本来就长，抬得更少。"""
        boost = 1.0 + 0.05 * (min(max(victims, 1), 3) - 1)
        return max(0.0, min(1.0, self.volume * boost))

    def _fire(self, index: int, volume: float) -> None:
        if self._closed or not self.enabled:
            return
        if index < 0 or index >= len(self._voices):
            return
        voices = self._voices[index]
        try:
            # 优先用空闲实例：让上一声把话说完，而不是从中间掐断
            target = next((v for v in voices if not v.isPlaying()), voices[0])
            target.setVolume(volume)
            if target.isPlaying():
                target.stop()
            target.play()
            self.played += 1
        except Exception:
            pass

    # ------------------------------------------------------------------ 播放

    def _schedule(self, delay_ms: int, index: int, volume: float) -> None:
        """按延迟触发：0 就立刻发，否则交给 QTimer（不阻塞主循环）。"""
        if delay_ms <= 0:
            self._fire(index, volume)
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(delay_ms, lambda i=index, v=volume: self._fire(i, v))

    def play(self, victims: int) -> int:
        """按击杀数决定叫几声，返回实际排入的声数（0~2）。"""
        if not (self.enabled and self.available):
            return 0
        if self.volume <= 0.01 or victims <= 0:
            return 0

        now = time.perf_counter()
        volume = self._gain(victims)

        if self.long_line:
            # 完整一句：不叠。冷却期内再拍不响，也不因为多杀就多叫一声 ——
            # 2.4 秒的句子叠两句，听到的只有一句的前半 + 另一句的后半。
            if now < self._line_until:
                return 0
            self._line_until = now + max(self._durations) * LINE_COOLDOWN
            self._schedule(LEAD_MS, 0, volume)
            return 1

        self._hits = [t for t in self._hits if now - t < BUDGET_WINDOW]
        scheduled = 0
        budget = self.budget
        for delay_ms, index in self.plan(victims):
            if len(self._hits) >= budget:
                break
            # 记账用「预计触发时刻」，这样连拍时预算按真实出声节奏消耗
            self._hits.append(now + delay_ms / 1000.0)
            scheduled += 1
            self._schedule(delay_ms, index, volume)
        return scheduled

    def preview(self, volume: float | None = None, all_variants: bool = True) -> None:
        """试听：忽略开关与预算（设置面板的按钮用）。

        默认把三个变体依次播一遍，间隔按素材**实际时长**排 ——
        写死一个常量的话，换成 1.38 秒的长素材就会三个声音叠在一起。
        完整一句（2.4 秒）只播第一个变体：三个连播要 7 秒多，
        中途还停不下来，试听按钮会变成"点了就后悔"。
        """
        if not self.available:
            return
        vol = self.volume if volume is None else max(0.0, min(1.0, float(volume)))
        if vol <= 0.01:
            return
        if not all_variants or self.long_line:
            self._fire(0, vol)
            return

        from PySide6.QtCore import QTimer

        at = 0.0
        for i in range(len(self._voices)):
            delay = int(at * 1000.0)
            if delay <= 0:
                self._fire(i, vol)
            else:
                QTimer.singleShot(delay, lambda idx=i, v=vol: self._fire(idx, v))
            at += (self._durations[i] if i < len(self._durations) else 1.0) + 0.18

    def stop(self) -> None:
        """硬停：关开关、静音、退出时用。"""
        self._line_until = 0.0
        for voices in self._voices:
            for eff in voices:
                try:
                    eff.stop()
                except Exception:
                    pass

    def close(self) -> None:
        """退出时调用：停播并拒绝后续的延迟触发。"""
        self._closed = True
        self.stop()

    # ------------------------------------------------------------------ 配置

    def apply_config(self, enabled: bool, volume: float) -> None:
        was = self.enabled
        self.enabled = bool(enabled)
        self.volume = max(0.0, min(1.0, float(volume)))
        # 关掉开关时正在叫的那声要立刻停，否则"关了还在叫"
        if was and not self.enabled:
            self.stop()

    def set_variant(self, variant: str) -> None:
        """换成另一组素材（myinstants / yamero / aiyo），改完设置即时生效。

        重新建实例而不是复用：两套素材的**时长不同**，`_echo_ms()` 与试听
        间隔都按时长算，缓存旧值会让新素材的错峰全错。
        """
        variant = variant if variant in SOUND_SETS else DEFAULT_SET
        if variant == self.variant and self.available:
            return
        if self._closed:
            return
        self.stop()
        self._voices.clear()
        self._durations.clear()
        self.variant = variant
        self._load(SOUND_SETS[variant])
        logger.info("拍死音效切换为 %s", variant)
