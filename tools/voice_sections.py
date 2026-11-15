"""打印素材里每个"浊音段"（音节）的时间范围，用来定裁切点。

    QT_QPA_PLATFORM=offscreen python tools/voice_sections.py

为什么不用能量阈值：说话里的停顿（换气、塞音闭塞）只掉到 -25~-30dB，
远高于静音阈值，但听起来仍是连贯的一句话；反过来，"哎——"这种拖长的
单音能量几乎恒定，很容易被当成背景音乐。

真正可靠的依据是**基频（F0）**：浊音段自相关置信度高、F0 稳定在
130~340Hz；清音 / 静音 / 混响尾巴的置信度会掉到 0.3 附近，F0 乱跳。
本工具对 40ms 帧做自相关，把置信度 > 0.55 的连续帧合并成段。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.prepare_voice import SR, decode_mono  # noqa: E402

HOP = int(0.01 * SR)
FRAME = int(0.04 * SR)
F0_LO, F0_HI = int(SR / 450), int(SR / 70)  # 70~450Hz
CONF_MIN = 0.55
MIN_DB = -45.0
# 段内允许的断点数（低于置信度但不该切段的帧，例如 2.7s 处自相关半频跳变）
BRIDGE = 5


def f0_track(samples: list[int]) -> list[tuple[float, float, float, float]]:
    out: list[tuple[float, float, float, float]] = []
    for i in range(0, len(samples) - FRAME, HOP):
        seg = samples[i : i + FRAME]
        energy = sum(v * v for v in seg)
        rms = math.sqrt(energy / len(seg)) / 32768.0
        db = 20.0 * math.log10(rms) if rms > 1e-6 else -99.0
        best, best_lag = 0.0, 0
        if db >= MIN_DB:
            for lag in range(F0_LO, F0_HI):
                num = e1 = e2 = 0.0
                for j in range(0, FRAME - lag, 2):
                    a, b = seg[j], seg[j + lag]
                    num += a * b
                    e1 += a * a
                    e2 += b * b
                r = num / math.sqrt(e1 * e2 + 1e-9)
                if r > best:
                    best, best_lag = r, lag
        out.append((i / SR, db, SR / best_lag if best_lag else 0.0, best))
    return out


def voiced_runs(track: list[tuple[float, float, float, float]]) -> list[tuple[float, float]]:
    runs: list[tuple[float, float]] = []
    start: float | None = None
    last_voiced = 0.0
    gap = 0
    for t, _db, _f0, conf in track:
        if conf >= CONF_MIN:
            if start is None:
                start = t
            last_voiced = t
            gap = 0
        elif start is not None:
            gap += 1
            if gap > BRIDGE:
                runs.append((start, last_voiced + 0.04))
                start = None
                gap = 0
    if start is not None:
        runs.append((start, last_voiced + 0.04))
    return runs


def main() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    src = ROOT / "assets/sounds/grab.mp3"
    samples, _ = decode_mono(src)
    samples = samples[: int(4.2 * SR)]

    print(f"分析 {src.name}（前 4.2 秒）\n")
    track = f0_track(samples)
    runs = voiced_runs(track)

    print(f"{'#':>2}  {'起':>6} {'止':>6} {'时长':>6}  F0 范围        说明")
    for idx, (a, b) in enumerate(runs, 1):
        seg = [(f0, db) for t, db, f0, c in track if a <= t < b and c >= CONF_MIN and f0 > 0]
        f0s = [f for f, _ in seg]
        dbs = [d for _, d in seg]
        lo, hi = (min(f0s), max(f0s)) if f0s else (0.0, 0.0)
        name = "浊音段" if b - a > 0.12 else "极短（可能是爆音）"
        print(
            f"{idx:>2}  {a:6.2f} {b:6.2f} {b - a:6.2f}  "
            f"{lo:5.0f}~{hi:5.0f}Hz  {name}，峰值 {max(dbs) if dbs else -99:.1f}dB"
        )

    if runs:
        print(f"\n整体有声区 {runs[0][0]:.2f}s ~ {runs[-1][1]:.2f}s")
        print(
            "提示：句间停顿不会断段（能量仍在 -25dB 以上），所以上面每个编号是一整个"
            "音节簇，不是字。\n要听完整一句就把窗口覆盖到最后一个段，别按能量谷切。"
        )


if __name__ == "__main__":
    main()
