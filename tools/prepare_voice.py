"""把素材 MP3 裁成完整的「你干嘛~哎呦」抓取音效（`assets/sounds/grab.wav`）。

    QT_QPA_PLATFORM=offscreen python tools/prepare_voice.py
    QT_QPA_PLATFORM=offscreen python tools/prepare_voice.py --start 0.17 --length 2.0

为什么需要这个工具（而不是运行时直接播 MP3）：

1. **`QSoundEffect` 只吃未压缩 WAV**。MP3 得走 `QMediaPlayer`，而它的启动延迟
   在 100~300ms，抓取这种"手一碰就该响"的事件等不起。
2. **素材里有 13 秒数字静音**。源文件是 CBR 64kbps，静音同样按 209 字节/帧占位，
   所以 135KB / 16.95 秒里真正有声音的只有前 3.9 秒。不裁掉就要白播 13 秒。
3. **裁切点需要试**。`--start/--length` 可调，改完重跑一次即可，不必改代码。

裁切链：解码 → 自动探测有声区 → 取窗口 → 12ms 淡入 / 120ms 淡出 → 峰值归一化 → 写 WAV。

**贴士**：判断句子边界不能只看能量 —— 说话里的停顿（换气、塞音）只掉到 -25~-30dB，
远高于静音阈值，但听起来是连贯的一句话。可靠得多的依据是**基频轨迹**：
浊音的 F0 稳定在 130~340Hz，清音/静音段自相关置信度会掉到 0.3 以下。
`tools/voice_sections.py` 就是干这个的，改窗口前先跑它看分段。
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SR = 22050

# 默认裁切窗口（秒）。依据 10ms 步进的自相关基频轨迹，整句分 5 个浊音段：
#   0.18~0.25  F0 334→311   高音起头（"你"）
#   0.32~0.47  F0 225→181   （"干"）
#   0.48~1.87  F0 266→298   最响的一段（"嘛~"拖长）
#   1.97~2.10  F0 248→240   （"哎"起音）  ← 之前误判成"换气间隙"，把它切丢了
#   2.20~3.15  F0 216→190   稳定长音（"哎——"）
#   3.23~3.62  F0 170→140   下行收尾（"呦"），之后 -40dB 只剩混响尾巴
DEFAULT_START = 0.17
DEFAULT_LENGTH = 3.45


def decode_mono(path: Path) -> tuple[list[int], int]:
    """把音频文件解码成 22050Hz 单声道 16bit 样本。

    **坑**：`QAudioDecoder` 在这个构建里会把输出补零到一个固定的总帧数
    （实测源 16.95 秒被解成 56 秒），所以不能用"总长度"判断有效区，
    必须靠能量阈值探测真正的结尾。补出来的全是 0，不影响检测。
    """
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

    fmt = QAudioFormat()
    fmt.setSampleRate(SR)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

    dec = QAudioDecoder()
    dec.setAudioFormat(fmt)
    dec.setSource(QUrl.fromLocalFile(str(path)))

    chunks: list[bytes] = []

    def on_ready() -> None:
        while dec.bufferAvailable():
            buf = dec.read()
            if buf.isValid():
                chunks.append(bytes(buf.constData()[: buf.byteCount()]))

    dec.bufferReady.connect(on_ready)
    loop = QEventLoop()
    dec.finished.connect(loop.quit)
    error: list[str] = []
    dec.error.connect(lambda e: (error.append(dec.errorString()), loop.quit()))
    QTimer.singleShot(120_000, loop.quit)
    dec.start()
    loop.exec()
    if error or not chunks:
        raise RuntimeError(f"解码失败: {error or '无数据'}")

    raw = b"".join(chunks)
    samples = list(struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2]))
    return samples, SR


def energy_envelope(samples: list[int], win: int) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for i in range(0, len(samples) - win, win):
        seg = samples[i : i + win]
        rms = math.sqrt(sum(v * v for v in seg) / len(seg)) / 32768.0
        db = 20.0 * math.log10(rms) if rms > 1e-7 else -99.0
        out.append((i / SR, db))
    return out


def content_bounds(env: list[tuple[float, float]], floor_db: float = -55.0) -> tuple[float, float]:
    voiced = [t for t, db in env if db > floor_db]
    return (voiced[0], voiced[-1] + 0.02) if voiced else (0.0, 0.0)


def slice_and_shape(
    samples: list[int], start: float, length: float, fade_in: float, fade_out: float
) -> list[float]:
    a = max(0, int(start * SR))
    b = min(len(samples), a + int(length * SR))
    seg = [s / 32768.0 for s in samples[a:b]]

    n = len(seg)
    fi = max(1, int(fade_in * SR))
    fo = max(1, int(fade_out * SR))
    for i in range(min(fi, n)):
        seg[i] *= i / fi
    for i in range(min(fo, n)):
        seg[n - 1 - i] *= i / fo
    return seg


def write_wav(path: Path, samples: list[float], peak: float) -> None:
    scale = (peak / max(1e-6, max(abs(s) for s in samples))) if samples else 0.0
    payload = b"".join(
        struct.pack("<h", max(-32767, min(32767, int(s * scale * 32767)))) for s in samples
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(payload)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="assets/sounds/grab.mp3")
    ap.add_argument("--out", default="assets/sounds/grab.wav")
    ap.add_argument("--start", type=float, default=DEFAULT_START)
    ap.add_argument("--length", type=float, default=DEFAULT_LENGTH)
    ap.add_argument("--fade-in", type=float, default=0.012)
    ap.add_argument("--fade-out", type=float, default=0.12)
    ap.add_argument("--peak", type=float, default=0.92)
    args = ap.parse_args()

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    src = (ROOT / args.src).resolve()
    out = (ROOT / args.out).resolve()
    if not src.exists():
        raise SystemExit(f"找不到素材: {src}")

    samples, _ = decode_mono(src)
    env = energy_envelope(samples, int(0.02 * SR))
    begin, end = content_bounds(env)
    print(f"源文件      {src.name}  {len(samples) / SR:.2f}s（含解码器补零）")
    print(f"有声区间    {begin:.2f}s ~ {end:.2f}s  ({end - begin:.2f}s)")

    clip = slice_and_shape(samples, args.start, args.length, args.fade_in, args.fade_out)
    if not clip:
        raise SystemExit("裁切窗口内没有样本")

    clip_env = energy_envelope([int(s * 32767) for s in clip], int(0.05 * SR))
    peak_db = max(db for _, db in clip_env)
    write_wav(out, clip, args.peak)

    print(f"裁切窗口    {args.start:.2f}s + {len(clip) / SR:.2f}s")
    print(f"峰值        {peak_db:.1f} dBFS → 归一化到 {args.peak:.2f}")
    print(f"输出        {out.relative_to(ROOT)}  {out.stat().st_size / 1024:.1f} KB")
    print("\n窗口内 50ms 包络：")
    for t, db in clip_env:
        print(f"  {t:4.2f}s {db:6.1f} " + "#" * max(0, min(46, int(db + 50))))


if __name__ == "__main__":
    main()
